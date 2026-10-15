#!/usr/bin/env python3
"""Stub solver-canary.py for molecule (test-only).

Accepts exactly the D18 CLI surface and rejects unknown flags (argparse).
Logs each subcommand to /tmp/wc-calls.log and writes plausible state.json,
evm.json and result.json. Chain effects go to /tmp/wc-fake/chain for the fake cast.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, "/usr/local/lib/wc-fake")
from fakelib import addr_key, chain_get, chain_set, fail_if, log  # noqa: E402

ROLES = ("deployer", "buyer", "seller", "relayer")
SEND = ("deploy", "offer", "accept", "settle", "refund")


def parser():
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command", required=True)
    ps = {}
    for name in ("init", "check", "deploy", "quote", "offer", "accept", "deliver", "prove", "settle",
                 "evidence", "refund"):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True)
        if name in SEND:
            p.add_argument("--execute", action="store_true")
            p.add_argument("--confirm-chain", type=int)
            p.add_argument("--confirm-amount", type=int)
        ps[name] = p
    init = ps["init"]
    init.add_argument("--chain-id", type=int, default=5042)
    init.add_argument("--token", default="0x3600000000000000000000000000000000000000")
    init.add_argument("--amount", type=int, required=True)
    init.add_argument("--max-cost", type=int, required=True)
    init.add_argument("--instance", required=True)
    init.add_argument("--accept-window", type=int, default=3600)
    init.add_argument("--settle-window", type=int, default=14400)
    init.add_argument("--reuse-deployment")
    for role in ROLES:
        init.add_argument(f"--{role}", required=True)
    ps["deploy"].add_argument("--allow-local-image-id", action="store_true")
    ps["refund"].add_argument("--role", choices=("buyer", "seller", "relayer"), default="buyer")
    return top


def load(d):
    with open(os.path.join(d, "state.json")) as fh:
        return json.load(fh)


def save(d, st):
    with open(os.path.join(d, "state.json"), "w") as fh:
        json.dump(st, fh, indent=2)


def keystore_address(role):
    path = os.path.expanduser(f"~/.foundry/keystores/warrant-canary-{role}")
    with open(path) as fh:
        return json.load(fh)["address"]


def main():
    a = parser().parse_args()
    log("canary", sys.argv[1:])
    fail_if(a.command)
    d = a.dir
    if a.command == "init":
        if os.path.exists(d):
            sys.exit(f"init: {d} exists")
        st = {"version": 1, "chain_id": a.chain_id, "token": a.token, "amount": a.amount,
              "max_cost": a.max_cost, "accept_window": a.accept_window, "settle_window": a.settle_window,
              "roles": {r: getattr(a, r) for r in ROLES},
              "addresses": {r: keystore_address(r) for r in ROLES}, "tx": {}}
        if a.reuse_deployment:
            old = load(a.reuse_deployment)
            for key in ("escrow", "verifier", "image_id", "code_hashes", "image_builder"):
                if key in old:
                    st[key] = old[key]
            st["reused_from"] = os.path.basename(a.reuse_deployment.rstrip("/"))
        os.makedirs(d)
        save(d, st)
        return
    st = load(d)
    if a.command == "deploy" and a.execute:
        st.update(escrow="0x" + "e5" * 20, verifier="0x" + "f1" * 20, image_id="0x" + "ab" * 32)
        st["tx"]["deploy"] = {"hash": "0x" + "03" * 32}
        with open(os.path.join(d, "deployment.json"), "w") as fh:
            json.dump({"escrow": st["escrow"]}, fh)
    elif a.command == "quote":
        st["quote"] = {"task": "0x" + hashlib.sha256(os.path.abspath(d).encode()).hexdigest()}
    elif a.command in ("offer", "accept", "settle", "refund") and a.execute:
        task = st["quote"]["task"]
        buyer = "balance-" + addr_key(st["addresses"]["buyer"])
        if a.command == "offer":
            chain_set("task-" + task, 1)
            chain_set(buyer, int(chain_get(buyer, "0")) - st["amount"])
        elif a.command == "accept":
            chain_set("task-" + task, 2)
        elif a.command == "settle":
            chain_set("task-" + task, 3)
        else:
            chain_set("task-" + task, 4)
            chain_set(buyer, int(chain_get(buyer, "0")) + st["amount"])
        st["tx"][a.command] = {"hash": "0x" + "04" * 32}
    elif a.command == "prove":
        with open(os.path.join(d, "evm.json"), "w") as fh:
            json.dump({"seal": "0x", "journal": "0x"}, fh)
    elif a.command == "evidence":
        with open(os.path.join(d, "result.json"), "w") as fh:
            json.dump({"realProof": True, "chain_id": st["chain_id"]}, fh)
    save(d, st)


if __name__ == "__main__":
    main()
