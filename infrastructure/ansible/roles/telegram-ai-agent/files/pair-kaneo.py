#!/usr/bin/env python3
"""One-time device-code pairing helper for Kaneo's MCP server.

Drives the OAuth 2.0 device flow exposed by Kaneo at
``/api/auth/device/code`` + ``/api/auth/device/token``. Prints a user
code, asks the operator to approve in a browser, then writes the
resulting session bearer to ``/etc/telegram-ai-agent/.env`` so the
telegram-ai-agent systemd unit picks it up on next restart.

Run this on the VM as the ``op`` user after deploy:

    ssh op@<vm> 'sudo -u op python3 /opt/coding-fabric/scripts/pair-kaneo.py'

Or locally if the operator already has ``/etc/hosts`` and tailscale up:

    KANEO_BASE_URL=https://kaneo.mnemonic-fabric.ts:8443 \\
    ./scripts/pair-kaneo.py

Token lifetime is 30 days per Kaneo's session model. Re-pair before
expiry (operator gets pinged in the ``ops`` Telegram topic when the
token starts returning 401).
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from pathlib import Path
from urllib import error, request

DEFAULT_BASE_URL = os.environ.get(
    "KANEO_BASE_URL", "https://kaneo.mnemonic-fabric.ts:8443"
)
DEFAULT_ENV_FILE = Path("/etc/telegram-ai-agent/.env")
CLIENT_ID = "kaneo-mcp"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _post_json(url: str, payload: dict, timeout: int = 15) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Caddy internal CA — same posture as ansible role
    try:
        with request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode() or "{}")
            return resp.status, data
    except error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode() or "{}")
        except Exception:
            data = {"error": str(exc)}
        return exc.code, data


def request_device_code(base_url: str) -> dict:
    status, body = _post_json(
        f"{base_url}/api/auth/device/code", {"client_id": CLIENT_ID}
    )
    if status != 200:
        sys.exit(f"device_code request failed (HTTP {status}): {body}")
    return body


def poll_for_token(base_url: str, device_code: str, interval: int, expires_in: int) -> str:
    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, body = _post_json(
            f"{base_url}/api/auth/device/token",
            {
                "grant_type": GRANT_TYPE,
                "client_id": CLIENT_ID,
                "device_code": device_code,
            },
        )
        if status == 200 and body.get("access_token"):
            return body["access_token"]
        if status == 400 and body.get("error") in {"authorization_pending", "slow_down"}:
            if body.get("error") == "slow_down":
                interval += 5
            continue
        # Kaneo doesn't follow RFC 8628's `slow_down` reply for rate
        # limiting — it returns a generic HTTP 429 instead. Treat that
        # as "back off, keep trying" rather than fatal.
        if status == 429:
            interval = min(interval + 10, 60)
            print(
                f"  rate-limited; backing off to {interval}s",
                file=sys.stderr,
            )
            continue
        # expired_token, access_denied, or anything else → abort
        sys.exit(f"device flow ended (HTTP {status}): {body}")
    sys.exit("device flow timed out before operator approved")


def upsert_env(env_file: Path, mcp_url: str, bearer: str) -> None:
    lines: list[str] = []
    if env_file.exists():
        lines = env_file.read_text().splitlines()
    new_lines: list[str] = []
    seen = {"KANEO_MCP_URL": False, "KANEO_MCP_BEARER": False}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("KANEO_MCP_URL="):
            new_lines.append(f"KANEO_MCP_URL={mcp_url}")
            seen["KANEO_MCP_URL"] = True
        elif stripped.startswith("KANEO_MCP_BEARER="):
            new_lines.append(f"KANEO_MCP_BEARER={bearer}")
            seen["KANEO_MCP_BEARER"] = True
        else:
            new_lines.append(line)
    if not seen["KANEO_MCP_URL"]:
        new_lines.append(f"KANEO_MCP_URL={mcp_url}")
    if not seen["KANEO_MCP_BEARER"]:
        new_lines.append(f"KANEO_MCP_BEARER={bearer}")
    env_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_file.with_suffix(env_file.suffix + ".tmp")
    tmp_path.write_text("\n".join(new_lines) + "\n")
    os.chmod(tmp_path, 0o600)
    # Preserve original ownership — pair-kaneo runs via sudo so a naive
    # write would land as root:root, then the bot service (uid op) can't
    # read its EnvironmentFile and systemd loops the unit forever.
    if env_file.exists():
        original_stat = env_file.stat()
        os.chown(tmp_path, original_stat.st_uid, original_stat.st_gid)
    os.replace(tmp_path, env_file)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Kaneo base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Where to persist the bearer (default: %(default)s)",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Skip writing to env file; print the bearer to stdout",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    mcp_url = f"{base_url}/mcp"

    print(f"Requesting device code from {base_url} ...", file=sys.stderr)
    code = request_device_code(base_url)

    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(
        f"Open {code['verification_uri_complete']} in a browser and",
        file=sys.stderr,
    )
    print(
        f"approve the request. Code: {code['user_code']}",
        file=sys.stderr,
    )
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print(
        f"Polling every {code['interval']}s "
        f"(expires in {code['expires_in']}s) ...",
        file=sys.stderr,
    )

    bearer = poll_for_token(
        base_url,
        code["device_code"],
        int(code["interval"]),
        int(code["expires_in"]),
    )

    if args.print_only:
        print(bearer)
        return 0

    upsert_env(args.env_file, mcp_url, bearer)
    print(
        f"Wrote KANEO_MCP_URL + KANEO_MCP_BEARER to {args.env_file}.",
        file=sys.stderr,
    )
    print(
        "Restart the bot so the new env is picked up:",
        file=sys.stderr,
    )
    print("  sudo systemctl restart telegram-ai-agent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
