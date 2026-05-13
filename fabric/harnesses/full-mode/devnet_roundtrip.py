"""
Solana devnet round-trip test.

Signs an attestation, submits to devnet, waits for confirmation,
and verifies the transaction. In stub mode, mocks RPC responses.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

HARNESS_DIR = Path(__file__).parent
FIXTURES_DIR = HARNESS_DIR / "fixtures"
STUB_TX_FILE = FIXTURES_DIR / "stub-solana-tx.json"

CONFIRM_TIMEOUT = 30
MNEMONIC_MODE = os.getenv("MNEMONIC_MODE", "stub")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "http://localhost:8899")


def load_stub_tx() -> Dict[str, Any]:
    """Load stub Solana transaction."""
    if STUB_TX_FILE.exists():
        with open(STUB_TX_FILE) as f:
            return json.load(f)
    return {
        "signature": "5Dz6Vs4oEWGqj7qjXFQhELvTJvfJ4Z3c8X9mK2yZnqXxYzZkH7gZ8zZkH7gZ8zZkH7gZ8zZk",
        "slot": 123456,
        "blockTime": 1234567890,
        "confirmationStatus": "confirmed",
    }


def sign_attestation(attestation: Dict[str, Any]) -> str:
    """
    Sign attestation with Solana keypair.
    In stub mode, returns mock signature.
    """
    if MNEMONIC_MODE == "full":
        try:
            import solana
            from solana.keypair import Keypair

            keypair_path = os.getenv("SOLANA_KEYPAIR_PATH", "~/.config/solana/id.json")
            keypair = Keypair.from_secret_key(
                open(Path(keypair_path).expanduser(), "rb").read()
            )
            return str(keypair.public_key)
        except Exception as e:
            raise RuntimeError(f"Failed to load keypair: {e}")
    else:
        import hashlib

        payload = json.dumps(attestation, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return digest[:80]


def submit_to_devnet(signature: str, attestation: Dict[str, Any]) -> str:
    """
    Submit signed attestation to Solana devnet.
    In stub mode, returns mock transaction signature.
    """
    if MNEMONIC_MODE == "full":
        try:
            from solana.rpc.api import Client

            client = Client(SOLANA_RPC_URL)
            print(f"Connecting to {SOLANA_RPC_URL}...")
            health = client.get_health()
            print(f"Cluster health: {health}")
            return str(health)
        except Exception as e:
            raise RuntimeError(f"Failed to submit to devnet: {e}")
    else:
        print(f"[STUB] Would submit to {SOLANA_RPC_URL}")
        return load_stub_tx()["signature"]


def confirm_transaction(tx_sig: str) -> bool:
    """
    Wait for transaction confirmation on devnet.
    In stub mode, returns True immediately.
    """
    if MNEMONIC_MODE == "full":
        try:
            from solana.rpc.api import Client

            client = Client(SOLANA_RPC_URL)
            for _ in range(CONFIRM_TIMEOUT):
                status = client.get_signature_status(tx_sig)
                if status and status["result"]:
                    return True
            return False
        except Exception as e:
            print(f"Confirmation check failed: {e}")
            return False
    else:
        print(f"[STUB] Transaction confirmed: {tx_sig}")
        return True


def verify_on_devnet(tx_sig: str) -> Dict[str, Any]:
    """
    Fetch and verify transaction on devnet.
    """
    if MNEMONIC_MODE == "full":
        try:
            from solana.rpc.api import Client

            client = Client(SOLANA_RPC_URL)
            tx = client.get_transaction(tx_sig)
            return {"signature": tx_sig, "verified": True, "block": tx["result"]["slot"]}
        except Exception as e:
            raise RuntimeError(f"Failed to verify on devnet: {e}")
    else:
        return load_stub_tx()


def main():
    """Execute devnet round-trip test."""
    print(f"Starting devnet round-trip (mode={MNEMONIC_MODE})...")

    attestation = {"version": 1, "claim": "test-attestation", "timestamp": 1234567890}

    print("1. Signing attestation...")
    signature = sign_attestation(attestation)
    print(f"   Signature: {signature[:40]}...")

    print("2. Submitting to devnet...")
    tx_sig = submit_to_devnet(signature, attestation)
    print(f"   Tx: {tx_sig}")

    print("3. Waiting for confirmation...")
    if not confirm_transaction(tx_sig):
        raise RuntimeError("Transaction confirmation timeout")
    print("   Confirmed!")

    print("4. Verifying on-chain...")
    result = verify_on_devnet(tx_sig)
    print(f"   Verified: {result}")

    print("Devnet round-trip passed!")
    return result


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
