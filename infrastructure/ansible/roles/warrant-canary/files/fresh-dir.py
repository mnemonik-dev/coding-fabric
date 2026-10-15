#!/usr/bin/env python3
"""Print or create a fresh directory named after the current UTC time.

  fresh-dir.py name PARENT [--suffix S]    print PARENT/<UTC ts>S that does not exist
                                           (waits for the next second on a clash; does not create)
  fresh-dir.py create PARENT LABEL         create PARENT/<UTC ts>-LABEL[-N] and print it

The UTC ts format is %Y%m%dT%H%M%SZ, the format run_dir is validated against.
"""
import datetime
import os
import sys
import time


def ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv):
    if len(argv) >= 3 and argv[1] == "name":
        parent = argv[2]
        suffix = argv[4] if len(argv) >= 5 and argv[3] == "--suffix" else ""
        for _ in range(10):
            path = os.path.join(parent, ts() + suffix)
            if not os.path.exists(path):
                print(path)
                return 0
            time.sleep(1.05)
        print(f"no fresh name under {parent}", file=sys.stderr)
        return 1
    if len(argv) == 4 and argv[1] == "create":
        parent, label = argv[2], argv[3]
        base = os.path.join(parent, f"{ts()}-{label}")
        path, n = base, 1
        while True:
            try:
                os.makedirs(path, mode=0o755)
                print(path)
                return 0
            except FileExistsError:
                n += 1
                path = f"{base}-{n}"
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
