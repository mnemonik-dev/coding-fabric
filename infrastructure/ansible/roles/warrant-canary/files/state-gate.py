#!/usr/bin/env python3
"""Unstage state gate: pass only when the run's order is Paid (3) or Refunded (4).

Usage: state-gate.py RUN_DIR RPC_URL

Reads escrow and quote.task from RUN_DIR/state.json and calls
tasks(bytes32) on the escrow; the trailing uint8 is the TaskEscrow State
(0 Missing, 1 Offered, 2 Accepted, 3 Paid, 4 Refunded). Appends one line to
RUN_DIR/state-gate.log. Exit 0 = pass, 3 = refuse, 2 = usage or read error.
"""
import datetime
import json
import os
import subprocess
import sys

STATES = {0: "Missing", 1: "Offered", 2: "Accepted", 3: "Paid", 4: "Refunded"}
SIG = "tasks(bytes32)(address,address,bytes32,uint64,uint64,uint64,uint64,uint8)"


def log(run_dir, rpc, text):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(run_dir, "state-gate.log"), "a") as fh:
        fh.write(f"{stamp} run={run_dir} rpc={rpc} {text}\n")


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    run_dir, rpc = argv[1], argv[2]
    try:
        with open(os.path.join(run_dir, "state.json")) as fh:
            st = json.load(fh)
        escrow, task = st["escrow"], st["quote"]["task"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"state gate: cannot read escrow and quote.task from {run_dir}/state.json: {exc}", file=sys.stderr)
        return 2
    res = subprocess.run(["cast", "call", "--rpc-url", rpc, escrow, SIG, task],
                         capture_output=True, text=True)
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    if res.returncode != 0 or not lines or not lines[-1].split()[0].isdigit():
        msg = f"state gate: cast call failed (rc {res.returncode}): {res.stderr.strip()[:300]}"
        log(run_dir, rpc, "verdict=error")
        print(msg, file=sys.stderr)
        return 2
    state = int(lines[-1].split()[0])
    name = STATES.get(state, f"unknown({state})")
    verdict = "pass" if state in (3, 4) else "refuse"
    log(run_dir, rpc, f"task={task} state={state} ({name}) verdict={verdict}")
    print(f"state gate: run {run_dir} task {task} state {state} ({name}) -> {verdict}")
    if verdict == "refuse":
        print(f"state gate: order state is {name}; unstaging needs Paid or Refunded "
              "(or force_unstage with a reason)", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
