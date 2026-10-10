"""Isolation and persistence contracts for the staging Compose renderer."""

import copy
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "manifest.py"
spec = importlib.util.spec_from_file_location("staging_manifest", SOURCE)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


def candidate():
    def image(name):
        return f"ghcr.io/mnemonik-dev/{name}@sha256:" + "a" * 64

    return {
        "version": 1, "environment": "staging", "target_host": "staging.example.com",
        "candidate": {"mnemonic_commit": "a" * 40, "paywall_commit": "b" * 40,
                      "mcp_image": image("mnemonic-mcp"),
                      "facilitator_image": image("universal-paywall-facilitator"),
                      "approval_image": image("universal-paywall-approval-ui")},
        "predecessor": {"mcp_image": image("mnemonic-mcp"),
                        "facilitator_image": image("universal-paywall-facilitator")},
        "facilitator": {"env_file": manifest.STACK + "/secrets/facilitator.env",
                        "receipt_key_file": manifest.STACK + "/secrets/receipt-private-key.pem",
                        "data_volume": manifest.PROJECT + "_facilitator-payment-store"},
        "operators": [{"id": "o1", "public_url": "https://paywall-staging.example.com",
                       "env_file": manifest.STACK + "/secrets/mcp.env",
                       "identity_file": manifest.STACK + "/secrets/mnemonic-id.json",
                       "data_volume": manifest.PROJECT + "_mcp-staging-data"}],
    }


class ManifestTests(unittest.TestCase):
    def test_adopts_existing_volumes_never_creates_replacement_financial_state(self):
        rendered = manifest.render(candidate())
        for key in ("payment-data", "data-o1"):
            self.assertTrue(rendered["volumes"][key]["external"])
        self.assertEqual(rendered["name"], "universal-paywall-staging")
        self.assertNotIn("container_name", rendered["services"]["mcp"])

    def test_all_host_ports_are_loopback_and_facilitator_is_not_on_ingress(self):
        services = manifest.render(candidate())["services"]
        self.assertEqual(services["facilitator"]["networks"], ["default"])
        for service in services.values():
            for port in service.get("ports", []):
                self.assertTrue(port.startswith("127.0.0.1:"))

    def test_assets_are_versioned_and_mcp_waits_for_copy_completion(self):
        first = candidate()
        second = copy.deepcopy(first)
        second["candidate"]["approval_image"] = first["candidate"]["approval_image"].replace("a" * 64, "b" * 64)
        a, b = manifest.render(first), manifest.render(second)
        self.assertNotEqual(a["volumes"]["approval-assets"], b["volumes"]["approval-assets"])
        self.assertEqual(b["services"]["mcp"]["depends_on"]["approval-ui"]["condition"],
                         "service_completed_successfully")

    def test_identity_is_read_only_and_missing_file_is_not_created(self):
        service = manifest.render(candidate())["services"]["mcp"]
        identity = service["volumes"][-1]
        self.assertTrue(identity["read_only"])
        self.assertFalse(identity["bind"]["create_host_path"])
        self.assertEqual(identity["target"], "/keypair/identity.json")
        self.assertEqual(service["environment"]["MNEMONIC_CONFIG_DIR"], "/keypair")
        self.assertEqual(identity["target"],
                         service["environment"]["MNEMONIC_CONFIG_DIR"] + "/identity.json")
        # Newer images read this variable; it must not fall back to /keypair/id.json.
        self.assertEqual(service["environment"]["MNEMONIC_KEYPAIR_PATH"], identity["target"])

    def test_second_operator_has_distinct_identity_volume_and_alias(self):
        data = candidate()
        second = copy.deepcopy(data["operators"][0])
        second.update(id="o2", public_url="https://recovery-staging.example.com",
                      env_file=manifest.STACK + "/secrets/mcp-o2.env",
                      identity_file=manifest.STACK + "/secrets/o2.json",
                      data_volume=manifest.PROJECT + "_mcp-o2-data")
        data["operators"].append(second)
        rendered = manifest.render(data)
        self.assertNotEqual(rendered["services"]["mcp"]["networks"]["ingress"],
                            rendered["services"]["mcp-o2"]["networks"]["ingress"])
        second["identity_file"] = data["operators"][0]["identity_file"]
        with self.assertRaisesRegex(manifest.InvalidManifest, "identity"):
            manifest.validate(data)

    def test_rejects_production_tags_path_escape_credentials_and_unknown_secret_fields(self):
        mutations = [
            lambda d: d.update(environment="production"),
            lambda d: d["candidate"].update(mcp_image="ghcr.io/a/b:latest"),
            lambda d: d["operators"][0].update(data_volume="mnemonik_mcp-data"),
            lambda d: d["operators"][0].update(identity_file=manifest.STACK + "/secrets/../prod.key"),
            lambda d: d["operators"][0].update(public_url="https://mcp.mnemonik.xyz"),
            lambda d: d["operators"][0].update(public_url="https://key@paywall-staging.example.com"),
            lambda d: d["facilitator"].update(api_key="must-never-be-in-manifest"),
            lambda d: d.update(target_host="host; touch /tmp/injected"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = candidate()
                mutation(data)
                with self.assertRaises(manifest.InvalidManifest):
                    manifest.validate(data)


if __name__ == "__main__":
    unittest.main()
