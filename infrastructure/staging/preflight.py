#!/usr/bin/env python3
"""Read-only host checks. Never emit container environments or secret contents."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
from pathlib import Path

from manifest import PROJECT, STACK, InvalidManifest, require, validate


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    # Docker/Compose errors can contain interpolated credentials. Do not relay them.
    require(result.returncode == 0, f"{args[0]} inspection failed; inspect privately on host")
    return result.stdout


def inspect(kind, name):
    return json.loads(command("docker", kind, "inspect", name))[0]


def environment(container):
    return dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)


def check_container(container, image, service, volume, identity_source, identity_target):
    config = container["Config"]
    labels = config.get("Labels") or {}
    require(labels.get("com.docker.compose.project") == PROJECT, "wrong Compose project")
    require(labels.get("com.docker.compose.service") == service, "wrong Compose service")
    require(config["Image"] == image, f"{service}: predecessor image changed")
    require(container["State"]["Running"], f"{service}: predecessor is not running")
    mounts = {m["Destination"]: m for m in container["Mounts"]}
    data = mounts.get("/data", {})
    require(data.get("Type") == "volume" and data.get("Name") == volume,
            f"{service}: existing data volume differs")
    # Adopt the legacy mount or a predecessor already using the current path.
    # Prefer the current path so a legacy mount cannot conceal signer drift.
    if service == "mcp" and "/keypair/identity.json" in mounts:
        identity_target = "/keypair/identity.json"
    key = mounts.get(identity_target, {})
    require(key.get("Type") == "bind" and key.get("Source") == identity_source,
            f"{service}: existing key mount differs")


def check_effective(service, env, previous=None):
    """Validate resolved Compose environment in memory, without logging values."""
    if service == "facilitator":
        expected = {"CHAIN_ID": "5042002", "NETWORK": "arc-testnet",
                    "EXACT_PAYMENTS_ENABLED": "1", "PAYMENT_STORE_PATH": "/data/payments.json",
                    "USDC_ADDRESS": "0x3600000000000000000000000000000000000000",
                    "USDC_EIP712_NAME": "USDC", "USDC_EIP712_VERSION": "2"}
        required = ["ARC_RPC_URL", "FACILITATOR_KEY", "SERVICE_API_KEYS", "SERVICE_ID",
                    "SERVICE_PAY_TO", "RECEIPT_KEY_ID", "SESSION_STAKE_VAULT_FACTORY"]
        stable = ["FACILITATOR_KEY", "SERVICE_PAY_TO", "SERVICE_ID", "RECEIPT_KEY_ID"]
    else:
        expected = {"DATABASE_PATH": "/data/attestations.db", "ANCHORING_NETWORK": "devnet",
                    "MNEMONIC_CONFIG_DIR": "/keypair",
                    "MNEMONIC_KEYPAIR_PATH": "/keypair/identity.json", "STORAGE_MODE": "full"}
        required = ["MCP_JWT_SECRET", "MCP_PUBLIC_BASE_URL", "EMBED_PROVIDER"]
        stable = ["MCP_JWT_SECRET", "MCP_PUBLIC_BASE_URL"]
        require(env.get("PAYMENT_MODE") in {"none", "x402"}, "unsupported MCP payment mode")
        require(bool(env.get("IRYS_GATEWAY_URL") or env.get("ARWEAVE_URL")), "missing storage gateway")
    for name, value in expected.items():
        require(env.get(name) == value, f"{service}: incorrect or missing {name}")
    for name in required:
        require(bool(env.get(name)), f"{service}: missing {name}")
    if previous is not None:
        for name in stable:
            require(previous.get(name) == env.get(name), f"{service}: unreviewed change to {name}")


def restricted_file(path):
    p = Path(path)
    info = p.lstat()
    require(stat.S_ISREG(info.st_mode) and not p.is_symlink(), "secret must be a regular file")
    require(info.st_mode & 0o077 == 0, "secret permissions must exclude group and others")


def run(data, compose_path, check_images=False):
    validate(data)
    require(len(data["operators"]) == 1, "O2 adoption needs its own reviewed predecessor; render-only for now")
    o = data["operators"][0]
    f = data["facilitator"]
    require(Path(STACK).is_dir() and not Path(STACK).is_symlink(), "staging directory is missing or symlinked")
    require(not Path(STACK + "/secrets").is_symlink(), "staging secrets directory cannot be a symlink")
    for path in (o["env_file"], o["identity_file"], f["env_file"], f["receipt_key_file"]):
        restricted_file(path)
    previous = {}
    for service, image_key, volume, key_source, key_target in (
        ("mcp", "mcp_image", o["data_volume"], o["identity_file"], "/keypair/id.json"),
        ("facilitator", "facilitator_image", f["data_volume"], f["receipt_key_file"],
         "/run/secrets/receipt-private-key.pem"),
    ):
        container = inspect("container", PROJECT + "-" + service + "-1")
        check_container(container, data["predecessor"][image_key], service, volume, key_source, key_target)
        previous[service] = environment(container)
        path_key = "DATABASE_PATH" if service == "mcp" else "PAYMENT_STORE_PATH"
        expected_path = "/data/attestations.db" if service == "mcp" else "/data/payments.json"
        require(previous[service].get(path_key) == expected_path, "live financial path differs")
    resolved = json.loads(command("docker", "compose", "-p", PROJECT, "-f", str(compose_path),
                                  "config", "--format", "json"))
    for service in ("mcp", "facilitator"):
        check_effective(service, resolved["services"][service]["environment"], previous[service])
    inspect("network", "vaultwarden_vaultwarden")
    if check_images:
        for key, commit_key in (("mcp_image", "mnemonic_commit"),
                                ("facilitator_image", "paywall_commit"),
                                ("approval_image", "paywall_commit")):
            image = inspect("image", data["candidate"][key])
            labels = image["Config"].get("Labels") or {}
            require(labels.get("org.opencontainers.image.revision") == data["candidate"][commit_key],
                    f"{key}: source revision label does not match candidate")
    return {"status": "preflight_passed", "images_checked": check_images,
            "environment": "staging", "production_modified": False,
            "limitations": ["No RPC, storage, payment, backup or rollback acceptance established"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("compose", type=Path)
    parser.add_argument("--check-images", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(json.loads(args.manifest.read_text()), args.compose, args.check_images)))
    except (InvalidManifest, OSError, ValueError, KeyError, TypeError):
        # Do not print exceptions from parsing secret-bearing Compose output.
        parser.exit(2, "Staging preflight failed. Check reviewed paths, permissions, pins and required configuration privately on host. No services changed.\n")


if __name__ == "__main__":
    main()
