"""
Arweave testnet round-trip test.

Uploads attestation to Arweave testnet, waits for finalization,
and verifies retrieval. In stub mode, mocks responses.

CRITICAL: In full mode, RPC URL is validated to prevent mainnet submission.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse

HARNESS_DIR = Path(__file__).parent
FIXTURES_DIR = HARNESS_DIR / "fixtures"
STUB_AR_TX = FIXTURES_DIR / "stub-arweave-tx.json"

MNEMONIC_MODE = os.getenv("MNEMONIC_MODE", "stub")
ARWEAVE_RPC_URL = os.getenv("ARWEAVE_RPC_URL", "http://localhost:1984")
FINALIZATION_TIMEOUT = 60
FINALIZATION_SLEEP_SECONDS = 1

# Arweave testnet allowlist — prevents accidental mainnet submission
ALLOWED_ARWEAVE_HOSTS = {
    "testnet.arweave.net",
    "localhost",
    "127.0.0.1",
}


def validate_rpc_url(url: str, mode: str) -> None:
    """
    Validate RPC URL against testnet allowlist in full mode.
    Raises RuntimeError if URL points to mainnet or unknown host.
    """
    if mode != "full":
        return

    parsed = urlparse(url)
    host = parsed.hostname or ""

    if host not in ALLOWED_ARWEAVE_HOSTS:
        raise RuntimeError(
            f"SECURITY: Refusing non-testnet Arweave RPC URL: {host}. "
            f"Allowed hosts: {ALLOWED_ARWEAVE_HOSTS}"
        )


def load_stub_ar_tx() -> Dict[str, Any]:
    """Load stub Arweave transaction."""
    if STUB_AR_TX.exists():
        with open(STUB_AR_TX) as f:
            return json.load(f)
    return {
        "id": "Ym3qBb9JVCZgj9TQL14FVjXZXiYi3n4YbB2ykZqQVh8",
        "owner": {"address": "iNEBRQeZksQd3Y1Fb-4OYvFWX-AEo4p7-L-MhVW1jWc"},
        "reward": "8",
        "last_tx": "xxxxx",
        "quantity": "0",
        "status": 200,
        "data_root": "root_hash",
    }


def prepare_attestation(data: Dict[str, Any]) -> bytes:
    """Prepare attestation as CBOR/JSON for upload."""
    return json.dumps(data, sort_keys=True).encode("utf-8")


def upload_to_arweave(attestation_data: bytes) -> str:
    """
    Upload attestation to Arweave testnet.
    In stub mode, returns mock transaction ID.
    """
    if MNEMONIC_MODE == "full":
        try:
            import requests

            response = requests.post(
                f"{ARWEAVE_RPC_URL}/tx",
                data=attestation_data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()["id"]
            else:
                raise RuntimeError(f"Upload failed: {response.status_code}")
        except Exception as e:
            raise RuntimeError(f"Failed to upload to Arweave: {e}")
    else:
        print(f"[STUB] Would upload {len(attestation_data)} bytes to {ARWEAVE_RPC_URL}")
        return load_stub_ar_tx()["id"]


def wait_for_finalization(tx_id: str) -> bool:
    """
    Poll Arweave for transaction finalization with timeout.
    Sleeps between attempts to avoid rate-limiting.
    In stub mode, returns True immediately.
    """
    if MNEMONIC_MODE == "full":
        try:
            import requests

            deadline = time.monotonic() + (FINALIZATION_TIMEOUT * FINALIZATION_SLEEP_SECONDS)

            for attempt in range(FINALIZATION_TIMEOUT):
                if time.monotonic() > deadline:
                    return False

                response = requests.get(
                    f"{ARWEAVE_RPC_URL}/tx/{tx_id}", timeout=30
                )
                if response.status_code == 200:
                    status = response.json()
                    if status.get("status") == 200:
                        return True
                    print(f"  Attempt {attempt+1}: status {status.get('status')}")

                # Sleep before next attempt, unless this is the last iteration
                if attempt < FINALIZATION_TIMEOUT - 1:
                    time.sleep(FINALIZATION_SLEEP_SECONDS)

            return False
        except Exception as e:
            print(f"Finalization check failed: {type(e).__name__}")
            return False
    else:
        print(f"[STUB] Transaction finalized: {tx_id}")
        return True


def retrieve_from_arweave(tx_id: str) -> bytes:
    """
    Retrieve attestation from Arweave with timeout.
    """
    if MNEMONIC_MODE == "full":
        try:
            import requests

            response = requests.get(
                f"{ARWEAVE_RPC_URL}/tx/{tx_id}/data", timeout=30
            )
            if response.status_code == 200:
                return response.content
            else:
                raise RuntimeError(f"Retrieval failed: {response.status_code}")
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve from Arweave: {type(e).__name__}")
    else:
        return json.dumps(
            {"version": 1, "claim": "test-attestation", "timestamp": 1234567890}
        ).encode("utf-8")


def main():
    """Execute Arweave round-trip test."""
    print(f"Starting Arweave round-trip (mode={MNEMONIC_MODE})...")

    # Validate RPC URL before proceeding
    try:
        validate_rpc_url(ARWEAVE_RPC_URL, MNEMONIC_MODE)
    except RuntimeError as e:
        print(f"RPC URL validation failed: {e}")
        raise

    attestation = {"version": 1, "claim": "test-attestation", "timestamp": 1234567890}

    print("1. Preparing attestation...")
    attestation_data = prepare_attestation(attestation)
    print(f"   Size: {len(attestation_data)} bytes")

    print("2. Uploading to Arweave...")
    tx_id = upload_to_arweave(attestation_data)
    print(f"   Tx ID: {tx_id}")

    print("3. Waiting for finalization...")
    if not wait_for_finalization(tx_id):
        raise RuntimeError("Finalization timeout")
    print("   Finalized!")

    print("4. Retrieving from Arweave...")
    retrieved = retrieve_from_arweave(tx_id)
    print(f"   Retrieved {len(retrieved)} bytes")

    print("5. Verifying round-trip...")
    assert retrieved == attestation_data, "Retrieved data mismatch"
    print("   Verified!")

    print("Arweave round-trip passed!")
    return {"transaction_id": tx_id, "size": len(attestation_data)}


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
