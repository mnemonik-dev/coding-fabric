#!/usr/bin/env python3
"""Stub check-arc-solver.py for molecule (test-only)."""
import argparse
import json
import os
import sys

sys.path.insert(0, "/usr/local/lib/wc-fake")
from fakelib import fail_if, log  # noqa: E402

top = argparse.ArgumentParser()
sub = top.add_subparsers(dest="phase", required=True)
for name, positional in (("before", "evm"), ("after", "manifest")):
    p = sub.add_parser(name)
    p.add_argument(positional)
    p.add_argument("--out", required=True)
    p.add_argument("--token", default="0x3600000000000000000000000000000000000000")
    p.add_argument("--allow-chain", type=int, default=0)
    if name == "before":
        p.add_argument("--image-id")
a = top.parse_args()
log("probe", sys.argv[1:])
fail_if(a.phase)
if os.path.exists(a.out):
    sys.exit(f"--out {a.out} exists")
with open(a.out, "w") as fh:
    json.dump({"phase": a.phase, "ok": True}, fh)
