#!/usr/bin/env python3
"""Health records and the health rule (D5, D15, D16).

  health.py snapshot --targets JSON --stopped-file PATH --out PATH
      Probe every target and write {"services": {name: {"status": int}}, "stopped": [...]}.
      A target is {"url": U, "container": C}; with C the probe runs curl inside
      that container. An unreachable target is status 0 (curl prints 000).
  health.py compare --before PATH --after PATH --stopped-file PATH
      Exit 0 when the rule holds, 1 otherwise; the report goes to stdout.

The operator record (stopped-services.json) is {"stopped": [...], "stopped_at": ...,
"restarted_at": null|ts}. While restarted_at is null the listed services are excluded
from the comparison and reported as stopped; once it is set they are compared again.
A missing record means nothing is stopped.
"""
import argparse
import json
import subprocess
import sys

SERVICES = ("mcp", "kaneo", "vaultwarden", "caddy", "ollama")


def operator_record(path):
    try:
        with open(path) as fh:
            rec = json.load(fh)
    except FileNotFoundError:
        return [], None
    stopped = rec.get("stopped") or []
    if not isinstance(stopped, list) or not all(isinstance(s, str) for s in stopped):
        raise SystemExit(f"{path}: 'stopped' must be a list of service names")
    return stopped, rec.get("restarted_at")


def excluded(path):
    stopped, restarted_at = operator_record(path)
    return sorted(stopped) if restarted_at is None else []


def probe(target):
    curl = ["curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10", target["url"]]
    cmd = ["docker", "exec", target["container"]] + curl if target.get("container") else curl
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0
    return int(out) if out.isdigit() else 0


def snapshot(args):
    targets = json.loads(args.targets)
    missing = [s for s in SERVICES if s not in targets]
    if missing:
        raise SystemExit(f"health targets missing: {missing}")
    record = {"services": {name: {"status": probe(targets[name])} for name in SERVICES},
              "stopped": excluded(args.stopped_file)}
    with open(args.out, "w") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(record, sort_keys=True))
    return 0


def codes(path):
    with open(path) as fh:
        rec = json.load(fh)
    return {name: int(v["status"]) for name, v in rec["services"].items()}


def compare(args):
    before, after = codes(args.before), codes(args.after)
    skip = set(excluded(args.stopped_file))
    failures, lines = [], []
    for name in SERVICES:
        b, a = before.get(name), after.get(name)
        if name in skip:
            lines.append(f"{name}: stopped by the operator (before {b}, after {a}), excluded")
        elif a == b:
            lines.append(f"{name}: {b} -> {a} ok")
        else:
            lines.append(f"{name}: {b} -> {a} CHANGED")
            failures.append(name)
    print("\n".join(lines))
    if failures:
        print(f"HEALTH RULE FAILED: {', '.join(failures)} changed and is not recorded as stopped "
              "in stopped-services.json (or was not restored after restarted_at).")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--targets", required=True)
    s.add_argument("--stopped-file", required=True)
    s.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--before", required=True)
    c.add_argument("--after", required=True)
    c.add_argument("--stopped-file", required=True)
    a = ap.parse_args()
    return snapshot(a) if a.cmd == "snapshot" else compare(a)


if __name__ == "__main__":
    sys.exit(main())
