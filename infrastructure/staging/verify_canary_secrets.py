#!/usr/bin/env python3
"""Check the decrypted warrant canary keys of secrets.sops.yml, read from stdin.

Usage: sops -d infrastructure/secrets/secrets.sops.yml | python3 -I verify_canary_secrets.py

Prints exactly `ok` on success, otherwise only the names of failed checks.
Never prints, logs or raises a secret value: parser and library errors can echo
their input, so their messages are discarded and replaced by fixed text.
"""

from __future__ import annotations

import re
import sys

ROLES = ("deployer", "buyer", "seller", "relayer")
REQUIRED_KEYS = (
    "warrant_canary_ionet_api_key",
    *(f"warrant_canary_keystore_{role}" for role in ROLES),
    *(f"warrant_canary_keystore_password_{role}" for role in ROLES),
    *(f"warrant_canary_address_{role}" for role in ROLES),
    "warrant_canary_seller_key_hex",
    "warrant_canary_registry_key",
    "warrant_canary_mcp_auth_user",
    "warrant_canary_mcp_auth_pass",
    "warrant_canary_expected_image_id",
)
SELLER_KEY = "warrant_canary_seller_key_hex"
REGISTRY_KEY = "warrant_canary_registry_key"
AUTH_PASS = "warrant_canary_mcp_auth_pass"
IMAGE_ID = "warrant_canary_expected_image_id"
SELLER_ADDRESS = "warrant_canary_address_seller"
AUTH_PASS_MIN = 32

BARE_HEX_KEY = re.compile(r"[0-9a-f]{64}")
PREFIXED_HEX_32 = re.compile(r"0x[0-9a-f]{64}")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
HEADER_UNSAFE = re.compile(r"[:\s]")


class MissingDependency(Exception):
    """A required library is not importable; carries only the module name."""


def derived_address(hex_key: str) -> str:
    try:
        from eth_account import Account
    except ImportError:
        raise MissingDependency("eth_account") from None
    return Account.from_key(bytes.fromhex(hex_key)).address


def check(document: dict) -> list[str]:
    """Return the names of failed checks. Messages name keys, never values."""
    failures = []
    values = {}
    for name in REQUIRED_KEYS:
        if name not in document:
            failures.append(f"{name}: missing")
        elif not isinstance(document[name], str):
            failures.append(f"{name}: not a string")
        elif document[name] == "":
            failures.append(f"{name}: empty")
        else:
            values[name] = document[name]

    seller = values.get(SELLER_KEY)
    if seller is not None:
        if not BARE_HEX_KEY.fullmatch(seller):
            failures.append(f"{SELLER_KEY}: not 64 lowercase hex characters")
        elif ADDRESS.fullmatch(values.get(SELLER_ADDRESS, "")):
            # A malformed address is reported once by the address check below.
            try:
                matches = derived_address(seller).lower() == values[SELLER_ADDRESS].lower()
            except MissingDependency as missing:
                failures.append(f"missing dependency: {missing.args[0]}")
            except Exception:
                failures.append(f"{SELLER_KEY}: derivation failed")
            else:
                if not matches:
                    failures.append(f"{SELLER_KEY}: does not derive {SELLER_ADDRESS}")

    registry = values.get(REGISTRY_KEY)
    if registry is not None:
        if not BARE_HEX_KEY.fullmatch(registry):
            failures.append(f"{REGISTRY_KEY}: not 64 lowercase hex characters")
        elif registry == seller:
            failures.append(f"{REGISTRY_KEY}: equals {SELLER_KEY}")

    password = values.get(AUTH_PASS)
    if password is not None:
        if len(password) < AUTH_PASS_MIN:
            failures.append(f"{AUTH_PASS}: shorter than {AUTH_PASS_MIN} characters")
        if HEADER_UNSAFE.search(password):
            failures.append(f"{AUTH_PASS}: contains ':' or whitespace")

    image_id = values.get(IMAGE_ID)
    if image_id is not None and not PREFIXED_HEX_32.fullmatch(image_id):
        failures.append(f"{IMAGE_ID}: not 0x and 64 lowercase hex characters")

    for role in ROLES:
        name = f"warrant_canary_address_{role}"
        if name in values and not ADDRESS.fullmatch(values[name]):
            failures.append(f"{name}: not a 0x 20-byte hex address")
    return failures


def main(stdin=None, stdout=None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        try:
            import yaml
        except ImportError:
            stdout.write("missing dependency: yaml\n")
            return 1
        try:
            # sops -d emits hex strings unquoted, and the YAML 1.1 resolver of
            # safe_load turns `0x...` into an int. BaseLoader keeps every scalar
            # a string, which is what sops stores, and constructs no objects.
            document = yaml.load(stdin.read(), Loader=yaml.BaseLoader)
        except Exception:
            document = None
        if not isinstance(document, dict):
            stdout.write("input: not a YAML mapping\n")
            return 1
        failures = check(document)
    except BaseException:
        stdout.write("verify: failed\n")
        return 1
    stdout.write("".join(f"{name}\n" for name in failures) if failures else "ok\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
