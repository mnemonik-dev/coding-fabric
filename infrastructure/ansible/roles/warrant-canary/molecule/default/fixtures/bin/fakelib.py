"""Shared helpers for the molecule fakes (test-only)."""
import os
import sys

LOG = "/tmp/wc-calls.log"
CTL = "/tmp/wc-fake"
CHAIN = os.path.join(CTL, "chain")


def log(tool, argv):
    rpc = os.environ.get("WARRANT_CANARY_RPC", "-")
    pw = os.environ.get("ETH_PASSWORD", "-")
    with open(LOG, "a") as fh:
        fh.write(f"{tool} {' '.join(argv)} | RPC={rpc} | ETH_PASSWORD={pw}\n")


def ctl(name, default=""):
    try:
        with open(os.path.join(CTL, name)) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return default


def fail_if(name):
    if os.path.exists(os.path.join(CTL, "fail-" + name)):
        print(f"fake: forced failure ({name})", file=sys.stderr)
        sys.exit(1)


def chain_get(key, default="0"):
    try:
        with open(os.path.join(CHAIN, key)) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return default


def chain_set(key, value):
    os.makedirs(CHAIN, exist_ok=True)
    with open(os.path.join(CHAIN, key), "w") as fh:
        fh.write(str(value))


def addr_key(addr):
    return addr.lower().removeprefix("0x")
