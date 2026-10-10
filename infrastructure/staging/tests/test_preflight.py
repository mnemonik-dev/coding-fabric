"""Regression checks for staging environment and identity adoption."""

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from test_manifest import manifest

SOURCE = Path(__file__).resolve().parents[1] / "preflight.py"
spec = importlib.util.spec_from_file_location("staging_preflight", SOURCE)
preflight = importlib.util.module_from_spec(spec)
with patch.dict("sys.modules", {"manifest": manifest}):
    spec.loader.exec_module(preflight)


def facilitator_environment():
    return {
        "CHAIN_ID": "5042002", "NETWORK": "arc-testnet",
        "EXACT_PAYMENTS_ENABLED": "1", "PAYMENT_STORE_PATH": "/data/payments.json",
        "USDC_ADDRESS": "0x3600000000000000000000000000000000000000",
        "USDC_EIP712_NAME": "USDC", "USDC_EIP712_VERSION": "2",
        "ARC_RPC_URL": "https://rpc.testnet.arc.network",
        "FACILITATOR_KEY": "synthetic-key", "SERVICE_API_KEYS": "synthetic-api-key",
        "SERVICE_ID": "mnemonic-anchor-staging", "SERVICE_PAY_TO": "synthetic-payee",
        "RECEIPT_KEY_ID": "staging-2026-07", "SESSION_STAKE_VAULT_FACTORY": "synthetic-factory",
    }


def predecessor():
    return {
        "Config": {"Image": "reviewed-predecessor", "Labels": {
            "com.docker.compose.project": manifest.PROJECT,
            "com.docker.compose.service": "mcp"}},
        "State": {"Running": True},
        "Mounts": [
            {"Destination": "/data", "Type": "volume", "Name": "reviewed-data"},
            {"Destination": "/keypair/id.json", "Type": "bind", "Source": "reviewed-identity"},
        ],
    }


def check_predecessor(container):
    preflight.check_container(container, "reviewed-predecessor", "mcp", "reviewed-data",
                              "reviewed-identity", "/keypair/id.json")


class PreflightTests(unittest.TestCase):
    def test_accepts_provisioned_arc_testnet_configuration(self):
        env = facilitator_environment()
        preflight.check_effective("facilitator", env, copy.deepcopy(env))

    def test_rejects_other_networks_and_mainnet_chain(self):
        for overrides in ({"NETWORK": "eip155:5042002"}, {"NETWORK": "mainnet"},
                          {"CHAIN_ID": "1"}):
            with self.subTest(overrides=overrides):
                env = facilitator_environment()
                env.update(overrides)
                with self.assertRaises(manifest.InvalidManifest):
                    preflight.check_effective("facilitator", env)

    def test_mcp_requires_persisted_config_directory(self):
        env = {
            "DATABASE_PATH": "/data/attestations.db", "ANCHORING_NETWORK": "devnet",
            "MNEMONIC_CONFIG_DIR": "/keypair", "MNEMONIC_KEYPAIR_PATH": "/keypair/identity.json",
            "STORAGE_MODE": "full", "PAYMENT_MODE": "none",
            "MCP_JWT_SECRET": "synthetic-jwt", "MCP_PUBLIC_BASE_URL": "https://staging.example.com",
            "EMBED_PROVIDER": "fastembed", "ARWEAVE_GATEWAY_URL": "https://arweave.net",
        }
        preflight.check_effective("mcp", env)
        # Images since monorepo e20ccea read MNEMONIC_KEYPAIR_PATH; the env-file
        # and image default /keypair/id.json is not mounted in the candidate.
        legacy = dict(env, MNEMONIC_KEYPAIR_PATH="/keypair/id.json")
        with self.assertRaisesRegex(manifest.InvalidManifest, "MNEMONIC_KEYPAIR_PATH"):
            preflight.check_effective("mcp", legacy)
        del env["MNEMONIC_CONFIG_DIR"]
        env["MNEMONIC_KEYPAIR_PATH"] = "/keypair/id.json"
        with self.assertRaisesRegex(manifest.InvalidManifest, "MNEMONIC_CONFIG_DIR"):
            preflight.check_effective("mcp", env)

    def test_mcp_requires_arweave_gateway(self):
        env = {
            "DATABASE_PATH": "/data/attestations.db", "ANCHORING_NETWORK": "devnet",
            "MNEMONIC_CONFIG_DIR": "/keypair", "MNEMONIC_KEYPAIR_PATH": "/keypair/identity.json",
            "STORAGE_MODE": "full", "PAYMENT_MODE": "none",
            "MCP_JWT_SECRET": "synthetic-jwt", "MCP_PUBLIC_BASE_URL": "https://staging.example.com",
            "EMBED_PROVIDER": "fastembed",
        }
        with self.assertRaisesRegex(manifest.InvalidManifest, "Arweave gateway"):
            preflight.check_effective("mcp", env)
        # The server still accepts the older variable name.
        preflight.check_effective("mcp", dict(env, ARWEAVE_URL="https://arweave.net"))

    def test_adopts_legacy_and_current_identity_mounts(self):
        for target in ("/keypair/id.json", "/keypair/identity.json"):
            with self.subTest(target=target):
                container = predecessor()
                container["Mounts"][1]["Destination"] = target
                check_predecessor(container)

    def test_legacy_mount_cannot_hide_current_identity_drift(self):
        container = predecessor()
        container["Mounts"].append({"Destination": "/keypair/identity.json", "Type": "bind",
                                    "Source": "different-identity"})
        with self.assertRaisesRegex(manifest.InvalidManifest, "existing key mount differs"):
            check_predecessor(container)

    def test_rejects_missing_or_replaced_identity_mount(self):
        for mounts in ([], [{"Destination": "/keypair/id.json", "Type": "bind",
                            "Source": "different-identity"}]):
            with self.subTest(mounts=mounts):
                container = predecessor()
                container["Mounts"] = container["Mounts"][:1] + mounts
                with self.assertRaisesRegex(manifest.InvalidManifest, "existing key mount differs"):
                    check_predecessor(container)
