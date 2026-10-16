#!/usr/bin/env python3
"""Rehearsal driver for the warrant-canary `rehearse` action (D7, D19).

Runs the explicit solver-canary.py chain on loopback Anvil from the pinned
checkout, then the refund scenario, and writes rehearsal.md and
rehearsal.json into the first run directory. Anvil is started and stopped by
the role; keystores and password files are staged by the role.

Environment (set by the role): WARRANT_CANARY_RPC (the Anvil URL),
RISC0_USE_DOCKER=1, RAYON_NUM_THREADS, PATH. ETH_PASSWORD is set here per
signing step to PW_DIR/<role>; it is a path, never a password.
"""
import argparse
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

ROLES = ("deployer", "buyer", "seller", "relayer")


class Fail(Exception):
    pass


class Driver:
    def __init__(self, a):
        self.a = a
        self.src = a.src
        self.rpc = os.environ["WARRANT_CANARY_RPC"]
        self.addresses = json.loads(a.addresses)

    # --- process helpers -------------------------------------------------
    def run(self, argv, role=None, cwd=None, check=True, quiet=False):
        env = dict(os.environ)
        env.pop("ETH_PASSWORD", None)
        if role:
            env["ETH_PASSWORD"] = os.path.join(self.a.pw_dir, role)
        if not quiet:
            print("+ " + " ".join(argv), flush=True)
        res = subprocess.run(argv, cwd=cwd or self.src, env=env, capture_output=True, text=True)
        if not quiet and res.stdout.strip():
            print(res.stdout.rstrip()[-4000:], flush=True)
        if res.stderr.strip():
            print(res.stderr.rstrip()[-4000:], file=sys.stderr, flush=True)
        if check and res.returncode != 0:
            raise Fail(f"{' '.join(argv[:3])} exited {res.returncode}")
        return res

    def canary(self, sub, run_dir, *args, role=None, send=False):
        argv = ["python3", "scripts/solver-canary.py", sub, "--dir", run_dir, *args]
        if send:
            argv += ["--execute", "--confirm-chain", str(self.a.chain_id), "--confirm-amount", str(self.a.amount)]
        return self.run(argv, role=role)

    def probe(self, phase, target, *args):
        if os.path.exists(args[args.index("--out") + 1]):
            raise Fail(f"check-arc-solver --out path already exists for {phase}")
        return self.run(["python3", "scripts/check-arc-solver.py", phase, target, *args])

    def cast(self, *args):
        return self.run(["cast", *args, "--rpc-url", self.rpc], quiet=True).stdout.strip()

    def gate(self, run_dir):
        return self.run(["python3", os.path.join(self.a.bin_dir, "state-gate.py"), run_dir, self.rpc],
                        check=False).returncode

    def fresh(self):
        return self.run(["python3", os.path.join(self.a.bin_dir, "fresh-dir.py"), "name", self.a.local_dir],
                        quiet=True).stdout.strip()

    def init_args(self, token, accept, settle):
        args = ["--chain-id", str(self.a.chain_id), "--token", token, "--amount", str(self.a.amount),
                "--max-cost", str(self.a.max_cost), "--instance", self.a.instance,
                "--accept-window", str(accept), "--settle-window", str(settle)]
        for role in ROLES:
            args += [f"--{role}", f"account:warrant-canary-{role}"]
        return args

    def balance(self, token, who):
        out = self.cast("call", token, "balanceOf(address)(uint256)", who)
        return int(out.split()[0])

    # --- steps -----------------------------------------------------------
    def setup_chain(self):
        for role in ROLES:
            self.cast("rpc", "anvil_setBalance", self.addresses[role], "0x56BC75E2D63100000")
        res = self.run(["forge", "create", "script/ShowcaseToken.sol:ShowcaseToken", "--broadcast", "--json",
                        "--rpc-url", self.rpc, "--private-key", self.a.anvil_key],
                       cwd=os.path.join(self.src, "contracts"), quiet=True)
        # forge create --json prints one indented JSON object over several lines.
        token = None
        out = res.stdout
        if "{" in out and "}" in out:
            try:
                token = json.loads(out[out.index("{"):out.rindex("}") + 1]).get("deployedTo")
            except ValueError:
                pass
        if not token or not re.fullmatch(r"0x[0-9a-fA-F]{40}", token):
            raise Fail("forge create ShowcaseToken printed no deployedTo address")
        print(f"ShowcaseToken deployed at {token}", flush=True)
        self.cast("send", token, "mint(address,uint256)", self.addresses["buyer"],
                  str(2 * self.a.amount), "--private-key", self.a.anvil_key)
        return token

    def first_order(self, token, run):
        self.canary("init", run, *self.init_args(token, self.a.accept_window, self.a.settle_window))
        self.canary("check", run)
        self.canary("deploy", run, role="deployer", send=True)
        self.probe("after", os.path.join(run, "state.json"), "--allow-chain", str(self.a.chain_id),
                   "--out", os.path.join(run, "arc-after.json"))
        self.canary("quote", run)
        self.canary("offer", run, role="buyer", send=True)
        self.canary("accept", run, role="seller", send=True)
        self.canary("deliver", run, role="seller")
        prove_seconds = self.prove(run)
        self.probe("before", os.path.join(run, "evm.json"), "--allow-chain", str(self.a.chain_id),
                   "--token", token, "--image-id", self.a.expected_image_id,
                   "--out", os.path.join(run, "arc-before.json"))
        self.canary("settle", run, role="relayer", send=True)
        self.canary("evidence", run)
        if self.gate(run) != 0:
            raise Fail("state gate did not accept the settled first order (expected Paid)")
        return prove_seconds

    def prove(self, run):
        sampler = subprocess.Popen(["bash", os.path.join(self.a.bin_dir, "wrap-sampler.sh"), run],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            start = time.monotonic()
            self.run(["/usr/bin/time", "-v", "-o", os.path.join(run, "prove.time"),
                      "python3", "scripts/solver-canary.py", "prove", "--dir", run])
            return math.ceil(time.monotonic() - start)
        finally:
            sampler.terminate()
            try:
                sampler.wait(timeout=10)
            except subprocess.TimeoutExpired:
                sampler.kill()

    def refund_scenario(self, token, first):
        """D19: second order past settleBy -> refund -> init --reuse-deployment -> one quote."""
        refund_run = self.fresh()
        args = self.init_args(token, self.a.refund_accept_window, self.a.refund_settle_window)
        self.canary("init", refund_run, "--reuse-deployment", first, *args)
        self.canary("quote", refund_run)
        before = self.balance(token, self.addresses["buyer"])
        self.canary("offer", refund_run, role="buyer", send=True)
        self.canary("accept", refund_run, role="seller", send=True)
        if self.gate(refund_run) != 3:
            raise Fail("state gate did not reject the Accepted refund order")
        self.cast("rpc", "evm_increaseTime", str(self.a.refund_settle_window + 120))
        self.cast("rpc", "evm_mine")
        self.canary("refund", refund_run, role="buyer", send=True)
        after = self.balance(token, self.addresses["buyer"])
        if after != before:
            raise Fail(f"buyer balance not restored after refund ({before} -> {after})")
        if self.gate(refund_run) != 0:
            raise Fail("state gate did not accept the Refunded order")
        third = self.fresh()
        self.canary("init", third, "--reuse-deployment", refund_run, *args)
        self.canary("quote", third)
        return refund_run, third

    # --- records ---------------------------------------------------------
    def health_delta(self, run):
        out = os.path.join(run, "health-rehearsal-end.json")
        health = os.path.join(self.a.bin_dir, "health.py")
        self.run(["python3", health, "snapshot", "--targets", self.a.health_targets,
                  "--stopped-file", self.a.stopped_file, "--out", out], quiet=True)
        res = self.run(["python3", health, "compare", "--before", self.a.health_before, "--after", out,
                        "--stopped-file", self.a.stopped_file], check=False, quiet=True)
        return res.stdout.strip()


def peak_rss_kb(path):
    try:
        with open(path) as fh:
            for line in fh:
                if "Maximum resident set size" in line:
                    return int(line.rsplit(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return None


def wrap_stats(path):
    units = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "kB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3}
    peak, stamps = 0, []
    try:
        with open(path) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 4:
                    continue
                stamps.append(parts[0])
                m = re.match(r"([0-9.]+)([A-Za-z]+)", parts[3])
                if m and m.group(2) in units:
                    peak = max(peak, int(float(m.group(1)) * units[m.group(2)]))
    except OSError:
        pass
    seconds = None
    if len(stamps) >= 2:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        seconds = int((datetime.datetime.strptime(stamps[-1], fmt)
                       - datetime.datetime.strptime(stamps[0], fmt)).total_seconds())
    return peak, seconds


def main():
    ap = argparse.ArgumentParser()
    for name in ("src", "build-stamp", "run-note", "local-dir", "anvil-key", "instance", "commit", "expected-image-id", "pw-dir",
                 "bin-dir", "addresses", "health-targets", "health-before", "stopped-file"):
        ap.add_argument(f"--{name}", required=True)
    for name in ("chain-id", "amount", "max-cost", "accept-window", "settle-window",
                 "refund-accept-window", "refund-settle-window"):
        ap.add_argument(f"--{name}", type=int, required=True)
    a = ap.parse_args()
    d = Driver(a)

    head = d.run(["git", "rev-parse", "HEAD"], quiet=True).stdout.strip()
    if head != a.commit:
        raise Fail(f"checkout HEAD {head} differs from the pinned commit {a.commit}")
    if d.run(["git", "status", "--porcelain"], quiet=True).stdout.strip():
        raise Fail("checkout has local changes (source_dirty would be true)")
    chain = d.cast("chain-id")
    if chain != str(a.chain_id):
        raise Fail(f"Anvil reports chain id {chain}, expected {a.chain_id}")

    build_seconds = None
    try:
        with open(a.build_stamp) as fh:
            build_seconds = int(fh.read().strip())
    except (OSError, ValueError):
        pass
    start = time.monotonic()
    image_out = d.run(["warrant-host", "solver-image-id"]).stdout.split()
    image_check_seconds = math.ceil(time.monotonic() - start)
    if build_seconds is None:
        build_seconds = image_check_seconds
    image_id = image_out[-1] if image_out else ""
    hash_out = d.run(["warrant-solver", "instance-hash", a.instance]).stdout.split()
    instance_hash = hash_out[-1] if hash_out else ""

    token = d.setup_chain()
    first = d.fresh()
    with open(a.run_note, "w") as fh:
        fh.write(first + "\n")
    prove_seconds = d.first_order(token, first)
    refund_ok, refund_note = True, "refund past settleBy restored the buyer balance; retry quote succeeded"
    try:
        refund_run, third = d.refund_scenario(token, first)
        refund_note += f" (refund run {refund_run}, retry {third})"
    except Fail as exc:
        refund_ok, refund_note = False, f"refund scenario failed: {exc}"

    try:
        with open(os.path.join(first, "result.json")) as fh:
            real_proof = json.load(fh).get("realProof") is True
    except (OSError, ValueError):
        real_proof = False
    rss = peak_rss_kb(os.path.join(first, "prove.time"))
    wrap_peak, wrap_seconds = wrap_stats(os.path.join(first, "wrap-stats.log"))
    free_gb = shutil.disk_usage("/").free / 1024 ** 3
    delta = d.health_delta(first)

    record = {"commit": head, "image_id": image_id, "instance_hash": instance_hash,
              "build_seconds": build_seconds, "prove_seconds": prove_seconds,
              "real_proof": real_proof, "refund_ok": refund_ok}
    with open(os.path.join(first, "rehearsal.json"), "w") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    md = [
        "# Rehearsal on loopback Anvil",
        "",
        f"- Commit: {head}",
        f"- Image ID: {image_id}",
        f"- Instance hash: {instance_hash}",
        f"- Build seconds (configure cargo build of this commit; else the image-ID check): {build_seconds}",
        f"- Image-ID check seconds (warrant-host solver-image-id, warm cache): {image_check_seconds}",
        f"- Prove seconds (whole prove step): {prove_seconds}",
        f"- Wrap seconds (docker stats samples): {wrap_seconds if wrap_seconds is not None else 'no samples'}",
        f"- Peak RSS of prove (prove.time): {rss if rss is not None else 'unknown'} kB",
        f"- Wrap container peak memory: {wrap_peak} bytes",
        f"- Free disk on / after the run: {free_gb:.1f} GB",
        f"- Refund: {refund_note}",
        "",
        "Health delta against health-before.json:",
        "",
        *[f"    {line}" for line in delta.splitlines()],
        "",
    ]
    if real_proof:
        md.append("realProof: true")
    with open(os.path.join(first, "rehearsal.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(json.dumps({"run": first, **record}), flush=True)
    if not (real_proof and refund_ok):
        print("rehearsal is not green: real_proof and refund_ok must both be true", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as exc:
        print(f"REHEARSAL FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
