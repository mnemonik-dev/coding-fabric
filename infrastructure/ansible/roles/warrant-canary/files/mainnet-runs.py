#!/usr/bin/env python3
"""Scan solver-bounty/canary/mainnet run directories.

  mainnet-runs.py active MAINNET_DIR
      Print the active run: the newest non -simulate directory whose state.json
      holds escrow and quote.task. Exit 1 when there is none.
  mainnet-runs.py escrow-check MAINNET_DIR RUN_DIR_INPUT
      RUN_DIR_INPUT is "" or mainnet/<UTC ts>. Refuse (exit 1) when a deployed
      run exists (state.json holds escrow, no abandoned marker) unless the input
      names exactly that one directory, which must exist, must not end in
      -simulate and must hold state.json. Prints {"retry_of": name|null}.
"""
import json
import os
import re
import sys

NAME = re.compile(r"\A[0-9]{8}T[0-9]{6}Z\Z")


def runs(mainnet_dir):
    if not os.path.isdir(mainnet_dir):
        return []
    return sorted(n for n in os.listdir(mainnet_dir) if NAME.match(n))


def state(mainnet_dir, name):
    try:
        with open(os.path.join(mainnet_dir, name, "state.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def active(mainnet_dir):
    for name in reversed(runs(mainnet_dir)):
        st = state(mainnet_dir, name)
        if st and st.get("escrow") and isinstance(st.get("quote"), dict) and st["quote"].get("task"):
            print(os.path.join(mainnet_dir, name))
            return 0
    print(f"no active mainnet run under {mainnet_dir} (state.json with escrow and quote.task)", file=sys.stderr)
    return 1


def escrow_check(mainnet_dir, run_dir_input):
    deployed = [n for n in runs(mainnet_dir)
                if (state(mainnet_dir, n) or {}).get("escrow")
                and not os.path.exists(os.path.join(mainnet_dir, n, "abandoned"))]
    retry = None
    if run_dir_input:
        if not re.fullmatch(r"mainnet/[0-9]{8}T[0-9]{6}Z", run_dir_input):
            print(f"run_dir {run_dir_input!r} is not mainnet/<UTC ts>", file=sys.stderr)
            return 1
        retry = run_dir_input.split("/", 1)[1]
        if not os.path.isfile(os.path.join(mainnet_dir, retry, "state.json")):
            print(f"run_dir {run_dir_input} does not exist or holds no state.json", file=sys.stderr)
            return 1
    if deployed and deployed != ([retry] if retry else None):
        print("REFUSED: a deployed run already exists (state.json holds escrow): "
              + ", ".join("mainnet/" + n for n in deployed)
              + ". Pass run_dir naming it only to retry after a partial deployment.", file=sys.stderr)
        return 1
    print(json.dumps({"retry_of": retry}))
    return 0


def main(argv):
    if len(argv) == 3 and argv[1] == "active":
        return active(argv[2])
    if len(argv) == 4 and argv[1] == "escrow-check":
        return escrow_check(argv[2], argv[3])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
