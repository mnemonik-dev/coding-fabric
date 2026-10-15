#!/usr/bin/env python3
"""Check a TaskEscrow deployment with cast and record the result.

Usage: task-escrow-checks.py RECORD USDC
RPC from WARRANT_CANARY_RPC. RECORD is the Foundry broadcast of DeployMock
(copied to task-escrow.json). Writes a `checks` object into RECORD with
escrow_code_hash, verifier_code_hash, token_ok, image_id_ok, verifier_ok and
total_reserved_zero. Exit 1 when any check is false (after writing).
"""
import json
import os
import subprocess
import sys

ONE = "0x" + "0" * 63 + "1"


def cast(*args):
    res = subprocess.run(["cast", *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"cast {args[0]} failed: {res.stderr.strip()[:300]}")
    return res.stdout.strip()


def first(text):
    return text.split()[0] if text.split() else ""


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    record_path, usdc = argv[1], argv[2].lower()
    rpc = os.environ.get("WARRANT_CANARY_RPC", "")
    if not rpc:
        raise SystemExit("WARRANT_CANARY_RPC is not set")
    with open(record_path) as fh:
        record = json.load(fh)
    created = {tx.get("contractName"): tx.get("contractAddress")
               for tx in record.get("transactions", []) if tx.get("transactionType") == "CREATE"}
    escrow, verifier = created.get("TaskEscrow"), created.get("JournalVerifier")
    if not escrow or not verifier:
        raise SystemExit(f"{record_path}: no CREATE of TaskEscrow and JournalVerifier in transactions")

    def code_hash(addr):
        code = cast("code", "--rpc-url", rpc, addr)
        return cast("keccak", code) if code not in ("", "0x") else ""

    escrow_hash, verifier_hash = code_hash(escrow), code_hash(verifier)
    token = first(cast("call", "--rpc-url", rpc, escrow, "token()(address)")).lower()
    image_id = first(cast("call", "--rpc-url", rpc, escrow, "imageId()(bytes32)")).lower()
    onchain_verifier = first(cast("call", "--rpc-url", rpc, escrow, "verifier()(address)")).lower()
    reserved = first(cast("call", "--rpc-url", rpc, escrow, "totalReserved()(uint256)"))
    checks = {
        "escrow": escrow,
        "verifier": verifier,
        "escrow_code_hash": escrow_hash,
        "verifier_code_hash": verifier_hash,
        "escrow_has_code": bool(escrow_hash),
        "verifier_has_code": bool(verifier_hash),
        "token_ok": token == usdc,
        "image_id_ok": image_id == ONE,
        "verifier_ok": onchain_verifier == verifier.lower(),
        "total_reserved_zero": reserved == "0",
    }
    record["checks"] = checks
    tmp = record_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, record_path)
    flags = ("escrow_has_code", "verifier_has_code", "token_ok", "image_id_ok", "verifier_ok", "total_reserved_zero")
    failed = [k for k in flags if not checks[k]]
    print(json.dumps(checks, indent=2))
    if failed:
        print(f"TASK ESCROW CHECKS FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
