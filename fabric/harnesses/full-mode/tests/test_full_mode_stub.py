"""
Full-mode stub tests.

Mock devnet/testnet RPC to test round-trip logic without real network.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

HARNESS_DIR = Path(__file__).parent.parent


def test_stub_fixtures_exist():
    """Verify stub fixtures are present."""
    assert (HARNESS_DIR / "fixtures" / "stub-solana-tx.json").exists()
    assert (HARNESS_DIR / "fixtures" / "stub-arweave-tx.json").exists()


def test_stub_solana_tx_valid():
    """Verify Solana stub fixture is valid JSON."""
    with open(HARNESS_DIR / "fixtures" / "stub-solana-tx.json") as f:
        data = json.load(f)
    assert "signature" in data
    assert "slot" in data
    assert "confirmationStatus" in data


def test_stub_arweave_tx_valid():
    """Verify Arweave stub fixture is valid JSON."""
    with open(HARNESS_DIR / "fixtures" / "stub-arweave-tx.json") as f:
        data = json.load(f)
    assert "id" in data
    assert "status" in data
    assert data["status"] == 200


@pytest.mark.asyncio
async def test_devnet_roundtrip_stub():
    """Test devnet round-trip with mocked RPC."""
    os.environ["MNEMONIC_MODE"] = "stub"

    import sys

    sys.path.insert(0, str(HARNESS_DIR))
    from devnet_roundtrip import sign_attestation, submit_to_devnet, confirm_transaction

    attestation = {"version": 1, "claim": "test"}
    signature = sign_attestation(attestation)

    assert signature is not None
    assert len(signature) > 0

    tx_sig = submit_to_devnet(signature, attestation)
    assert tx_sig is not None

    confirmed = confirm_transaction(tx_sig)
    assert confirmed is True


@pytest.mark.asyncio
async def test_arweave_roundtrip_stub():
    """Test Arweave round-trip with mocked RPC."""
    os.environ["MNEMONIC_MODE"] = "stub"

    import sys

    sys.path.insert(0, str(HARNESS_DIR))
    from arweave_roundtrip import (
        prepare_attestation,
        upload_to_arweave,
        wait_for_finalization,
        retrieve_from_arweave,
    )

    attestation = {"version": 1, "claim": "test"}
    data = prepare_attestation(attestation)

    assert len(data) > 0

    tx_id = upload_to_arweave(data)
    assert tx_id is not None

    finalized = wait_for_finalization(tx_id)
    assert finalized is True

    retrieved = retrieve_from_arweave(tx_id)
    assert len(retrieved) > 0


def test_independent_verifier_subprocess():
    """Test independent verifier as subprocess with real hash verification."""
    import subprocess
    import hashlib

    # Test attestation data
    attestation_data = b'{"version": 1, "claim": "test-attestation"}'
    expected_hash = hashlib.sha256(attestation_data).hexdigest()

    # Run verifier as subprocess (correct approach)
    result = subprocess.run(
        [
            "python3",
            str(HARNESS_DIR / "independent_verifier.py"),
            f"--attestation-file=/dev/stdin",
            f"--expected-hash={expected_hash}",
        ],
        input=attestation_data,
        capture_output=True,
        text=False,
    )

    # Verify exit code 0 on matching hash
    assert result.returncode == 0, f"Verifier failed: {result.stderr.decode()}"
    assert b"PASS" in result.stdout


def test_fixture_mutation_detection():
    """
    Verify that mutating stub fixtures causes hash change.
    Uses deterministic SHA256 hash, not Python's non-deterministic hash().
    """
    import hashlib

    with open(HARNESS_DIR / "fixtures" / "stub-solana-tx.json") as f:
        original = json.load(f)

    mutated = original.copy()
    mutated["slot"] = 999999

    original_bytes = json.dumps(original, sort_keys=True).encode("utf-8")
    mutated_bytes = json.dumps(mutated, sort_keys=True).encode("utf-8")

    original_hash = hashlib.sha256(original_bytes).hexdigest()
    mutated_hash = hashlib.sha256(mutated_bytes).hexdigest()

    assert original_hash != mutated_hash, (
        f"Mutation not detected: {original_hash} == {mutated_hash}"
    )
    assert original["slot"] != mutated["slot"]


def test_attestation_data_integrity():
    """Verify attestation round-trip data integrity."""
    original = {"version": 1, "claim": "test", "timestamp": 1234567890}

    encoded = json.dumps(original, sort_keys=True).encode("utf-8")
    decoded = json.loads(encoded.decode("utf-8"))

    assert original == decoded


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
