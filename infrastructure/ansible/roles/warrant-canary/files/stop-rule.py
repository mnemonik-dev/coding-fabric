#!/usr/bin/env python3
"""Stop rule (Decision 12): refuse when free disk on / or MemAvailable is too low.

Usage: stop-rule.py MIN_DISK_GB MIN_MEM_GB [--disk-kb N] [--mem-kb N]
The --disk-kb/--mem-kb overrides exist for molecule only.
"""
import argparse
import os
import sys


def mem_available_kb():
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    raise SystemExit("stop rule: MemAvailable missing from /proc/meminfo")


def disk_free_kb(path="/"):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize // 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("min_disk_gb", type=int)
    ap.add_argument("min_mem_gb", type=int)
    ap.add_argument("--disk-kb", default="")
    ap.add_argument("--mem-kb", default="")
    a = ap.parse_args()
    disk = int(a.disk_kb) if a.disk_kb else disk_free_kb()
    mem = int(a.mem_kb) if a.mem_kb else mem_available_kb()
    need_disk = a.min_disk_gb * 1024 * 1024
    need_mem = a.min_mem_gb * 1024 * 1024
    print(f"stop rule: disk free {disk} kB (need {need_disk}), MemAvailable {mem} kB (need {need_mem})")
    problems = []
    if disk < need_disk:
        problems.append(f"free disk on / is under {a.min_disk_gb} GB")
    if mem < need_mem:
        problems.append(f"MemAvailable is under {a.min_mem_gb} GB")
    if problems:
        print("STOP RULE: " + "; ".join(problems) + ". Nothing was staged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
