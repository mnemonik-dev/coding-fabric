#!/usr/bin/env python3
"""Validate a non-secret staging candidate and render its Compose configuration.

No network requests, subprocesses, secret loading or deployment side effects.
Only the isolated application stack is managed; production/fabric are excluded.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

STACK = "/opt/universal-paywall-staging"
PROJECT = "universal-paywall-staging"
IMAGE_RE = re.compile(r"ghcr\.io/[a-z0-9-]+/[a-z0-9-]+@sha256:[a-f0-9]{64}\Z")
SHA_RE = re.compile(r"[a-f0-9]{40}\Z")
HOST_RE = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")


class InvalidManifest(ValueError):
    """Candidate cannot be used without correcting its non-secret configuration."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise InvalidManifest(message)


def keys(value: Any, expected: set[str], label: str) -> None:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == expected, f"{label} has missing or unknown fields")


def string(value: Any, label: str) -> str:
    require(isinstance(value, str) and bool(value), f"{label} must be a non-empty string")
    return value


def secret_path(value: Any) -> str:
    path = string(value, "secret file path")
    require(
        re.fullmatch(re.escape(STACK) + r"/secrets/[a-z0-9][a-z0-9._-]*", path) is not None,
        "secret files must be direct children of the staging secrets directory",
    )
    return path


def validate(data: Any) -> dict[str, Any]:
    keys(data, {"version", "environment", "target_host", "candidate", "predecessor",
                "facilitator", "operators"}, "manifest")
    require(type(data["version"]) is int and data["version"] == 1, "unsupported manifest version")
    require(data["environment"] == "staging", "this deployer manages staging only")
    require(HOST_RE.fullmatch(string(data["target_host"], "target_host")) is not None,
            "invalid SSH target host")
    keys(data["candidate"], {"mnemonic_commit", "paywall_commit", "mcp_image",
                             "facilitator_image", "approval_image"}, "candidate")
    for field, value in data["candidate"].items():
        pattern = SHA_RE if field.endswith("_commit") else IMAGE_RE
        require(pattern.fullmatch(string(value, field)) is not None,
                f"{field} must be an immutable commit or GHCR digest reference")
    keys(data["predecessor"], {"mcp_image", "facilitator_image"}, "predecessor")
    for field, value in data["predecessor"].items():
        require(IMAGE_RE.fullmatch(string(value, field)) is not None,
                "predecessor images must use digests")
    f = data["facilitator"]
    keys(f, {"env_file", "receipt_key_file", "data_volume"}, "facilitator")
    secret_path(f["env_file"])
    secret_path(f["receipt_key_file"])
    require(f["data_volume"] == PROJECT + "_facilitator-payment-store",
            "facilitator must adopt the existing staging payment volume")
    operators = data["operators"]
    require(isinstance(operators, list) and 1 <= len(operators) <= 2,
            "provide O1 and optionally O2")
    names, domains, volumes, identities, env_files = set(), set(), set(), set(), {f["env_file"]}
    for o in operators:
        keys(o, {"id", "public_url", "env_file", "identity_file", "data_volume"}, "operator")
        require(string(o["id"], "operator id") in {"o1", "o2"}, "operator id must be o1 or o2")
        require(o["id"] not in names, "operator IDs must be unique")
        names.add(o["id"])
        url = urlsplit(string(o["public_url"], "public_url"))
        require(url.scheme == "https" and url.hostname is not None and
                url.netloc == url.hostname and url.path == "" and
                not url.query and not url.fragment and
                HOST_RE.fullmatch(url.hostname) is not None and
                "staging" in url.hostname.split(".")[0].split("-"),
                "operator URL must be a staging HTTPS origin without credentials or path")
        require(url.hostname not in domains, "operator domains must be unique")
        domains.add(url.hostname)
        secret_path(o["env_file"])
        secret_path(o["identity_file"])
        require(o["env_file"] not in env_files, "each service requires its own environment file")
        env_files.add(o["env_file"])
        require(o["identity_file"] not in identities, "operators must not share identity files")
        identities.add(o["identity_file"])
        expected = PROJECT + ("_mcp-staging-data" if o["id"] == "o1" else "_mcp-o2-data")
        require(o["data_volume"] == expected, "operator must use its designated staging volume")
        require(o["data_volume"] not in volumes, "operators must not share data volumes")
        volumes.add(o["data_volume"])
    require("o1" in names, "O1 is required")
    require(not identities.intersection(env_files | {f["receipt_key_file"]}),
            "identity, receipt and environment files must be separate")
    require(f["receipt_key_file"] not in env_files, "receipt key must not be an environment file")
    return data


def render(data: Any) -> dict[str, Any]:
    data = validate(data)
    candidate, facilitator = data["candidate"], data["facilitator"]
    # Immutable asset volume: a new init job cannot clear files used by an old MCP.
    asset_id = candidate["approval_image"].split("sha256:")[1]
    volumes: dict[str, Any] = {
        "approval-assets": {"name": PROJECT + "_approval-" + asset_id},
        "payment-data": {"external": True, "name": facilitator["data_volume"]},
    }
    services: dict[str, Any] = {
        "approval-ui": {
            "image": candidate["approval_image"], "restart": "no",
            "network_mode": "none", "volumes": ["approval-assets:/approval-ui"],
        },
        "facilitator": {
            "image": candidate["facilitator_image"], "restart": "unless-stopped",
            "env_file": [facilitator["env_file"]],
            "environment": {"PORT": "8403", "PAYMENT_STORE_PATH": "/data/payments.json",
                            "RECEIPT_PRIVATE_KEY_FILE": "/run/secrets/receipt-private-key.pem"},
            "volumes": ["payment-data:/data", {"type": "bind",
                "source": facilitator["receipt_key_file"],
                "target": "/run/secrets/receipt-private-key.pem", "read_only": True,
                "bind": {"create_host_path": False}}],
            "ports": ["127.0.0.1:8403:8403"], "networks": ["default"],
            "healthcheck": {"test": ["CMD", "node", "-e",
                "fetch('http://127.0.0.1:8403/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"],
                "interval": "15s", "timeout": "5s", "retries": 5},
        },
    }
    for o in data["operators"]:
        name = "mcp" if o["id"] == "o1" else "mcp-o2"
        volume = "data-" + o["id"]
        volumes[volume] = {"external": True, "name": o["data_volume"]}
        services[name] = {
            "image": candidate["mcp_image"], "restart": "unless-stopped",
            "env_file": [o["env_file"]],
            "environment": {"DATABASE_PATH": "/data/attestations.db",
                "MNEMONIC_CONFIG_DIR": "/keypair",
                "MCP_PUBLIC_BASE_URL": o["public_url"], "MNEMONIC_APPROVAL_UI_DIST": "/approval-ui",
                "UNIVERSAL_PAYWALL_URL": "http://facilitator:8403"},
            "volumes": [volume + ":/data", "approval-assets:/approval-ui:ro",
                {"type": "bind", "source": o["identity_file"],
                  "target": "/keypair/identity.json", "read_only": True,
                 "bind": {"create_host_path": False}}],
            "depends_on": {"facilitator": {"condition": "service_healthy"},
                           "approval-ui": {"condition": "service_completed_successfully"}},
            "networks": {"default": {}, "ingress": {"aliases": [PROJECT + "-" + o["id"]]}},
            "ports": ["127.0.0.1:" + ("3001" if o["id"] == "o1" else "3002") + ":3000"],
        }
    for name, service in services.items():
        service["security_opt"] = ["no-new-privileges:true"]
        service["logging"] = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
        if name != "approval-ui":
            service["tmpfs"] = ["/tmp"]
    return {"name": PROJECT, "services": services, "volumes": volumes,
            "networks": {"default": {"name": PROJECT + "_default"},
                         "ingress": {"external": True, "name": "vaultwarden_vaultwarden"}}}


def caddy(data: Any) -> str:
    data = validate(data)
    return "\n".join(
        f"{urlsplit(o['public_url']).hostname} {{\n"
        f"  reverse_proxy {PROJECT}-{o['id']}:3000\n}}\n" for o in data["operators"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        data = validate(json.loads(args.manifest.read_text()))
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "docker-compose.json").write_text(json.dumps(render(data), indent=2) + "\n")
            (args.output / "paywall-staging.conf").write_text(caddy(data))
            (args.output / "candidate.json").write_text(json.dumps(data, indent=2) + "\n")
        print("Staging manifest valid; no deployment performed.")
    except (InvalidManifest, ValueError, OSError) as error:
        parser.exit(2, f"Invalid staging manifest: {error}\n")


if __name__ == "__main__":
    main()
