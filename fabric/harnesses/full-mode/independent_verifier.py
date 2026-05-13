"""
Independent verifier binary.

Performs actual independent verification by:
1. Reading attestation bytes from stdin or --attestation-file
2. Computing canonical hash (SHA256)
3. Comparing against --expected-hash
4. Reporting pass/fail via exit code

This verifier runs as a subprocess and maintains a separate trust boundary
from the main signing/submission harness.

Usage:
  python3 independent_verifier.py --attestation-file=<file> --expected-hash=<hex>
  OR
  cat attestation.cbor | python3 independent_verifier.py --expected-hash=<hex>
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional


def compute_canonical_hash(data: bytes) -> str:
    """
    Compute SHA256 hash of attestation bytes.
    Returns hex string.
    """
    return hashlib.sha256(data).hexdigest()


def verify_attestation(
    attestation_bytes: bytes, expected_hash: str
) -> bool:
    """
    Verify that attestation bytes hash to expected value.
    Returns True if hashes match.
    """
    computed = compute_canonical_hash(attestation_bytes)

    print(f"[Verifier] Attestation size: {len(attestation_bytes)} bytes")
    print(f"[Verifier] Computed hash:  {computed}")
    print(f"[Verifier] Expected hash:  {expected_hash}")

    if computed == expected_hash:
        print("[Verifier] PASS: Attestation hash matches expected")
        return True
    else:
        print("[Verifier] FAIL: Attestation hash mismatch")
        return False


def main() -> int:
    """
    Main verification routine.
    Parses command-line args, reads attestation, verifies hash.
    Returns 0 on success, 1 on failure.
    """
    attestation_file: Optional[str] = None
    expected_hash: Optional[str] = None

    # Parse command-line arguments
    for arg in sys.argv[1:]:
        if arg.startswith("--attestation-file="):
            attestation_file = arg.split("=", 1)[1]
        elif arg.startswith("--expected-hash="):
            expected_hash = arg.split("=", 1)[1]

    if not expected_hash:
        print("[Verifier] ERROR: --expected-hash=<hex> is required")
        return 1

    # Read attestation bytes
    try:
        if attestation_file:
            with open(attestation_file, "rb") as f:
                attestation_bytes = f.read()
            print(f"[Verifier] Read {len(attestation_bytes)} bytes from {attestation_file}")
        else:
            # Read from stdin
            attestation_bytes = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode()
            print(f"[Verifier] Read {len(attestation_bytes)} bytes from stdin")

        if not attestation_bytes:
            print("[Verifier] ERROR: No attestation data provided")
            return 1

        # Verify hash
        if verify_attestation(attestation_bytes, expected_hash):
            return 0
        else:
            return 1

    except Exception as e:
        print(f"[Verifier] ERROR: {type(e).__name__}: {str(e)[:100]}")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
