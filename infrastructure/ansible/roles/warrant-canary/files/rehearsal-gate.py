#!/usr/bin/env python3
"""Green-rehearsal gate for deploy-solver and deploy-task.

Usage: rehearsal-gate.py LOCAL_DIR COMMIT IMAGE_ID INSTANCE_HASH

Picks the newest LOCAL_DIR/*/rehearsal.json and refuses (exit 1) unless it
parses, holds every field, and commit, image_id and instance_hash match while
real_proof and refund_ok are true. On success prints
{"run": <dir>, "prove_seconds": <int, rounded up>}.
"""
import json
import math
import os
import sys

FIELDS = ("commit", "image_id", "instance_hash", "build_seconds", "prove_seconds", "real_proof", "refund_ok")


def norm(value):
    return str(value).strip().lower().removeprefix("0x")


def refuse(msg):
    print(f"REFUSED (green rehearsal): {msg}", file=sys.stderr)
    return 1


def main(argv):
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    local_dir, commit, image_id, instance_hash = argv[1:]
    if not commit.strip():
        return refuse("warrant_canary_src_commit is empty")
    if not image_id.strip():
        return refuse("expected image ID is empty")
    if not instance_hash.strip():
        return refuse("instance hash of solver-bounty/instance-ionet.json is empty")
    dirs = sorted(d for d in (os.listdir(local_dir) if os.path.isdir(local_dir) else [])
                  if os.path.isfile(os.path.join(local_dir, d, "rehearsal.json")))
    if not dirs:
        return refuse(f"no rehearsal.json under {local_dir}; run the rehearse action first")
    run = os.path.join(local_dir, dirs[-1])
    try:
        with open(os.path.join(run, "rehearsal.json")) as fh:
            rec = json.load(fh)
    except ValueError as exc:
        return refuse(f"{run}/rehearsal.json does not parse: {exc}")
    if not isinstance(rec, dict):
        return refuse(f"{run}/rehearsal.json is not an object")
    missing = [f for f in FIELDS if f not in rec]
    if missing:
        return refuse(f"{run}/rehearsal.json lacks {', '.join(missing)}")
    if norm(rec["commit"]) != norm(commit):
        return refuse(f"rehearsal commit {rec['commit']} differs from the pinned commit {commit}")
    if norm(rec["image_id"]) != norm(image_id):
        return refuse(f"rehearsal image_id {rec['image_id']} differs from the expected {image_id}")
    if norm(rec["instance_hash"]) != norm(instance_hash):
        return refuse("rehearsal instance_hash differs from warrant-solver instance-hash of instance-ionet.json")
    if rec["real_proof"] is not True:
        return refuse("rehearsal real_proof is not true")
    if rec["refund_ok"] is not True:
        return refuse("rehearsal refund_ok is not true")
    try:
        prove = float(rec["prove_seconds"])
    except (TypeError, ValueError):
        return refuse("prove_seconds is not a number")
    if not math.isfinite(prove) or prove <= 0:
        return refuse("prove_seconds is not a positive number")
    print(json.dumps({"run": run, "prove_seconds": math.ceil(prove)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
