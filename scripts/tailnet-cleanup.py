#!/usr/bin/env python3
"""Remove stale CI nodes from the tailnet.

Background: deploy-fabric.yml and e2e-smoke.yml register each CI runner with a
REUSABLE, NON-EPHEMERAL auth key under a unique hostname
(--hostname=gh-runner-<run_id>). The runner is destroyed minutes later but the
tailnet record persists forever. Same story for the VM: every destroy/recreate
left another mnemonic-fabric-N ghost behind.

Dry run by default. Nothing is deleted without --delete.

Auth (either one):
    export TAILSCALE_API_KEY=tskey-api-...          # admin console -> Settings -> Keys
    export TAILSCALE_OAUTH_CLIENT_ID=...            # or an OAuth client with
    export TAILSCALE_OAUTH_SECRET=...               # the devices:write scope

Usage:
    python3 scripts/tailnet-cleanup.py                      # dry run, CI nodes
    python3 scripts/tailnet-cleanup.py --include-vm-ghosts  # also offline mnemonic-fabric-N
    python3 scripts/tailnet-cleanup.py --min-age-days 7     # only nodes idle >= 7d
    python3 scripts/tailnet-cleanup.py --delete             # actually delete
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

API = "https://api.tailscale.com/api/v2"
TAILNET = os.environ.get("TAILSCALE_TAILNET", "-")  # "-" = the token's default tailnet

# Hostname prefixes created by CI. These are always disposable.
CI_PATTERNS = [
    re.compile(r"^gh-runner-"),
    re.compile(r"^gh-paywall-staging-"),
    re.compile(r"^github-runnervm"),
]
# VM ghosts: mnemonic-fabric / mnemonic-fabric-74 etc. Only with --include-vm-ghosts,
# and only when offline, so the live VM is never a candidate.
VM_GHOST = re.compile(r"^mnemonic-fabric(-\d+)?$")


def _request(method, url, token, data=None):
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
        return json.loads(body) if body.strip() else {}


def get_token():
    key = os.environ.get("TAILSCALE_API_KEY")
    if key:
        return key
    cid = os.environ.get("TAILSCALE_OAUTH_CLIENT_ID")
    secret = os.environ.get("TAILSCALE_OAUTH_SECRET")
    if not (cid and secret):
        sys.exit(
            "No credentials. Set TAILSCALE_API_KEY, or "
            "TAILSCALE_OAUTH_CLIENT_ID + TAILSCALE_OAUTH_SECRET."
        )
    payload = urllib.parse.urlencode(
        {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}
    ).encode()
    req = urllib.request.Request("https://api.tailscale.com/api/v2/oauth/token", data=payload)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["access_token"]


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--include-vm-ghosts", action="store_true",
                    help="also remove OFFLINE mnemonic-fabric-N duplicates")
    ap.add_argument("--min-age-days", type=float, default=1.0,
                    help="only nodes not seen for this many days (default 1)")
    args = ap.parse_args()

    token = get_token()
    devices = _request("GET", "%s/tailnet/%s/devices" % (API, TAILNET), token).get("devices", [])
    now = datetime.datetime.now(datetime.timezone.utc)

    doomed, kept_online, too_recent = [], [], []
    for d in devices:
        name = (d.get("hostname") or d.get("name") or "").split(".")[0]
        online = bool(d.get("online"))
        last_seen = parse_ts(d.get("lastSeen"))
        age_days = (now - last_seen).total_seconds() / 86400 if last_seen else 9999

        is_ci = any(p.search(name) for p in CI_PATTERNS)
        is_ghost = args.include_vm_ghosts and VM_GHOST.search(name)
        if not (is_ci or is_ghost):
            continue
        # Never touch anything currently connected.
        if online:
            kept_online.append(name)
            continue
        if age_days < args.min_age_days:
            too_recent.append(name)
            continue
        doomed.append((d.get("id"), name, round(age_days, 1), d.get("addresses", [None])[0]))

    print("Total devices in tailnet : %d" % len(devices))
    print("Matched for removal      : %d" % len(doomed))
    if kept_online:
        print("Skipped (currently online): %d  -> %s" % (len(kept_online), ", ".join(sorted(set(kept_online))[:5])))
    if too_recent:
        print("Skipped (seen recently)   : %d" % len(too_recent))
    print()

    if not doomed:
        print("Nothing to do.")
        return

    for _id, name, age, addr in sorted(doomed, key=lambda x: x[1]):
        print("   %-38s %-16s idle %sd" % (name, addr, age))
    print()

    if not args.delete:
        print("DRY RUN. Re-run with --delete to remove these %d nodes." % len(doomed))
        return

    ok = failed = 0
    for _id, name, _age, _addr in doomed:
        try:
            _request("DELETE", "%s/device/%s" % (API, _id), token)
            ok += 1
            print("   deleted %s" % name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("   FAILED  %s -> %s" % (name, exc))
    print("\nDeleted %d, failed %d." % (ok, failed))


if __name__ == "__main__":
    main()
