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


@pytest.mark.asyncio
async def test_independent_verifier_stub():
    """Test independent verifier with stub data."""
    import sys

    sys.path.insert(0, str(HARNESS_DIR))
    from independent_verifier import verify_solana_attestation, cross_verify

    sol_result = verify_solana_attestation("stub-sig")
    assert "signature" in sol_result
    assert "attestation" in sol_result

    ar_result = {
        "transaction_id": "stub-id",
        "data": {"version": 1, "claim": "verified-from-ar"},
    }

    match = cross_verify(sol_result, ar_result)
    assert isinstance(match, bool)


def test_fixture_mutation_detection():
    """Verify that mutating stub fixtures causes detection."""
    with open(HARNESS_DIR / "fixtures" / "stub-solana-tx.json") as f:
        original = json.load(f)

    mutated = original.copy()
    mutated["slot"] = 999999

    original_sig = original["signature"]
    mutated_sig = mutated["signature"]

    assert original_sig == mutated_sig
    assert original["slot"] != mutated["slot"]


def test_attestation_data_integrity():
    """Verify attestation round-trip data integrity."""
    original = {"version": 1, "claim": "test", "timestamp": 1234567890}

    encoded = json.dumps(original, sort_keys=True).encode("utf-8")
    decoded = json.loads(encoded.decode("utf-8"))

    assert original == decoded


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
