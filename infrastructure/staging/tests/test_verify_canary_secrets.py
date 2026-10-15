"""Regression checks for the value-free warrant canary secrets helper."""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import secrets
import subprocess
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "verify_canary_secrets.py"
spec = importlib.util.spec_from_file_location("staging_verify_canary_secrets", SOURCE)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

try:
    import yaml
except ImportError:
    HAVE_YAML = False
else:
    HAVE_YAML = True
try:
    from eth_account import Account
except ImportError:
    HAVE_DEPS = False
else:
    HAVE_DEPS = HAVE_YAML

ROLES = ("deployer", "buyer", "seller", "relayer")
IMAGE_ID = "0x56d9ff10f85f9f023a32c632dcd11ae95a9cd7024d34e4f489c1b392a299bff2"


def run(text):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err):
        code = verify.main(io.StringIO(text), out)
    return code, out.getvalue(), err.getvalue()


@unittest.skipUnless(HAVE_DEPS, "PyYAML and eth-account are required")
class VerifyCanarySecretsTests(unittest.TestCase):
    def setUp(self):
        self.seller_key = secrets.token_hex(32)
        self.document = {"unrelated_top_level_key": "synthetic-unrelated"}
        for role in ROLES:
            key = self.seller_key if role == "seller" else secrets.token_hex(32)
            address = Account.from_key(bytes.fromhex(key)).address
            self.document[f"warrant_canary_address_{role}"] = address
            self.document[f"warrant_canary_keystore_{role}"] = (
                f"synthetic-keystore-{role}-{secrets.token_hex(8)}")
            self.document[f"warrant_canary_keystore_password_{role}"] = (
                f"synthetic-password-{role}-{secrets.token_hex(8)}")
        self.document.update({
            "warrant_canary_ionet_api_key": f"synthetic-ionet-{secrets.token_hex(8)}",
            "warrant_canary_seller_key_hex": self.seller_key,
            "warrant_canary_registry_key": secrets.token_hex(32),
            "warrant_canary_mcp_auth_user": "warrant-canary",
            "warrant_canary_mcp_auth_pass": secrets.token_urlsafe(32),
            "warrant_canary_expected_image_id": IMAGE_ID,
        })
        # Every value a failure case may carry; none may reach the output.
        self.secret_values = {v for k, v in self.document.items()
                              if k != "warrant_canary_mcp_auth_user"}

    def check(self, document):
        # Track mutated values too; skip the empty and the public user name,
        # which cannot be meaningfully searched for in output.
        for value in document.values():
            if isinstance(value, str) and value and value != "warrant-canary":
                self.secret_values.add(value)
        return run(yaml.safe_dump(document))

    def mutated(self, **changes):
        document = dict(self.document)
        document.update(changes)
        return document

    def assert_fails_with(self, document, name):
        code, out, err = self.check(document)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, f"{name}\n")
        self.assert_value_free(out, err)

    def assert_value_free(self, out, err):
        for value in self.secret_values:
            self.assertNotIn(value, out)
            self.assertNotIn(value, err)
            if value.startswith("0x"):
                self.assertNotIn(value[2:].lower(), out.lower())
        self.assertNotIn("Traceback", out + err)

    def failure_cases(self):
        """Failure documents grouped by check family, each with its expected check name."""
        other = secrets.token_hex(32)
        seller_format = "warrant_canary_seller_key_hex: not 64 lowercase hex characters"
        registry_format = "warrant_canary_registry_key: not 64 lowercase hex characters"
        pass_unsafe = "warrant_canary_mcp_auth_pass: contains ':' or whitespace"
        image_format = "warrant_canary_expected_image_id: not 0x and 64 lowercase hex characters"
        address = self.document["warrant_canary_address_buyer"]
        return {
            "seller_derivation": [
                (self.mutated(warrant_canary_seller_key_hex=other),
                 "warrant_canary_seller_key_hex: does not derive warrant_canary_address_seller"),
            ],
            "hex_key_format": [
                (self.mutated(warrant_canary_seller_key_hex="0x" + self.seller_key), seller_format),
                (self.mutated(warrant_canary_seller_key_hex=self.seller_key.upper()), seller_format),
                (self.mutated(warrant_canary_seller_key_hex=self.seller_key[:63]), seller_format),
                (self.mutated(warrant_canary_registry_key="0x" + other), registry_format),
                (self.mutated(warrant_canary_registry_key=other.upper()), registry_format),
                (self.mutated(warrant_canary_registry_key=other[:63]), registry_format),
            ],
            "registry_equals_seller": [
                (self.mutated(warrant_canary_registry_key=self.seller_key),
                 "warrant_canary_registry_key: equals warrant_canary_seller_key_hex"),
            ],
            "auth": [
                (self.mutated(warrant_canary_mcp_auth_user=""),
                 "warrant_canary_mcp_auth_user: empty"),
                (self.mutated(warrant_canary_mcp_auth_pass="a" * 31),
                 "warrant_canary_mcp_auth_pass: shorter than 32 characters"),
                (self.mutated(warrant_canary_mcp_auth_pass=secrets.token_urlsafe(32) + ":x"),
                 pass_unsafe),
                (self.mutated(warrant_canary_mcp_auth_pass=secrets.token_urlsafe(32) + " x"),
                 pass_unsafe),
            ],
            "image_and_address_format": [
                (self.mutated(warrant_canary_expected_image_id=IMAGE_ID[2:]), image_format),
                (self.mutated(warrant_canary_expected_image_id="0x" + IMAGE_ID[2:].upper()),
                 image_format),
                (self.mutated(warrant_canary_expected_image_id=IMAGE_ID[:-1]), image_format),
                (self.mutated(warrant_canary_address_buyer=address[:-1]),
                 "warrant_canary_address_buyer: not a 0x 20-byte hex address"),
                (self.mutated(warrant_canary_address_relayer=self.document[
                    "warrant_canary_address_relayer"][2:] + "00"),
                 "warrant_canary_address_relayer: not a 0x 20-byte hex address"),
                (self.mutated(warrant_canary_address_deployer="0x" + "g" * 40),
                 "warrant_canary_address_deployer: not a 0x 20-byte hex address"),
            ],
        }

    def assert_family_fails(self, family):
        for document, name in self.failure_cases()[family]:
            with self.subTest(name=name):
                self.assert_fails_with(document, name)

    def test_valid_document_prints_ok(self):
        code, out, err = self.check(self.document)
        self.assertEqual((code, out, err), (0, "ok\n", ""))

    def test_checksum_case_address_accepted(self):
        lower = self.mutated(warrant_canary_address_seller=self.document[
            "warrant_canary_address_seller"].lower())
        self.assertEqual(self.check(lower)[:2], (0, "ok\n"))

    def test_seller_key_address_mismatch(self):
        document = self.mutated(warrant_canary_seller_key_hex=secrets.token_hex(32))
        code, out, err = self.check(document)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "warrant_canary_seller_key_hex: does not derive "
                              "warrant_canary_address_seller\n")
        self.assert_value_free(out, err)

    def required_keys(self):
        return set(self.document) - {"unrelated_top_level_key"}

    def test_required_keys_are_the_eighteen_canary_keys(self):
        self.assertEqual(set(verify.REQUIRED_KEYS), self.required_keys())
        self.assertEqual(len(self.required_keys()), 18)
        self.assertNotIn("warrant_canary_src_commit", verify.REQUIRED_KEYS)

    def test_missing_key_reported_by_name(self):
        for name in sorted(self.required_keys()):
            with self.subTest(name=name):
                document = dict(self.document)
                del document[name]
                self.assert_fails_with(document, f"{name}: missing")

    def test_hex_key_format(self):
        self.assert_family_fails("hex_key_format")

    def test_registry_equals_seller_fails(self):
        self.assert_family_fails("registry_equals_seller")

    def test_auth_pass_rules(self):
        self.assert_family_fails("auth")
        boundary = self.mutated(warrant_canary_mcp_auth_pass="a" * 32)
        self.assertEqual(self.check(boundary)[:2], (0, "ok\n"))
        code, out, err = self.check(self.mutated(warrant_canary_mcp_auth_pass="a" * 30 + ":"))
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "warrant_canary_mcp_auth_pass: shorter than 32 characters\n"
                              "warrant_canary_mcp_auth_pass: contains ':' or whitespace\n")

    def test_underivable_seller_key_reports_derivation_failure(self):
        # Zero is outside the secp256k1 key range, so eth_account raises.
        self.assert_fails_with(self.mutated(warrant_canary_seller_key_hex="0" * 64),
                               "warrant_canary_seller_key_hex: derivation failed")

    def test_unexpected_error_prints_fixed_message(self):
        secret = secrets.token_hex(32)
        with patch.object(verify, "check", side_effect=RuntimeError(secret)):
            code, out, err = run(yaml.safe_dump(self.document))
        self.assertEqual((code, out), (1, "verify: failed\n"))
        self.assertNotIn(secret, out + err)
        self.assertNotIn("Traceback", out + err)

    def test_image_id_and_address_format(self):
        self.assert_family_fails("image_and_address_format")

    def test_empty_required_value_reported_by_name(self):
        for name in sorted(self.required_keys()):
            with self.subTest(name=name):
                self.assert_fails_with(self.mutated(**{name: ""}), f"{name}: empty")

    def test_malformed_seller_address_reported_once(self):
        document = self.mutated(warrant_canary_address_seller="0x" + "g" * 40)
        code, out, err = self.check(document)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "warrant_canary_address_seller: not a 0x 20-byte hex address\n")
        self.assert_value_free(out, err)

    def test_unquoted_hex_scalars_read_as_strings(self):
        # sops -d prints these values without quotes; they must not become ints.
        text = "".join(f"{key}: {value}\n" for key, value in self.document.items())
        self.assertIn(f"warrant_canary_expected_image_id: {IMAGE_ID}\n", text)
        self.assertEqual(run(text)[:2], (0, "ok\n"))

    def test_non_string_value_fails_without_value(self):
        document = self.mutated(warrant_canary_mcp_auth_pass=["synthetic-list-item"])
        self.assert_fails_with(document, "warrant_canary_mcp_auth_pass: not a string")

    def test_output_is_value_free(self):
        cases = [case for family in self.failure_cases().values() for case in family]
        for document, name in cases:
            with self.subTest(name=name):
                code, out, err = self.check(document)
                self.assertNotEqual(code, 0)
                self.assert_value_free(out, err)

    def test_invalid_yaml_no_traceback(self):
        secret = secrets.token_hex(32)
        for text in (f"key: [unclosed {secret}\n", f"- {secret}\n- b\n", f"{secret}\n", ""):
            with self.subTest(text=text[:6]):
                code, out, err = run(text)
                self.assertNotEqual(code, 0)
                self.assertEqual(out, "input: not a YAML mapping\n")
                self.assertNotIn(secret, out + err)
                self.assertNotIn("Traceback", out + err)

    def test_script_entry_point(self):
        result = subprocess.run(
            [sys.executable, "-I", str(SOURCE)], input=yaml.safe_dump(self.document),
            capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "ok\n", ""))


class MissingDependencyTests(unittest.TestCase):
    def test_missing_yaml_reported_without_traceback(self):
        with patch.dict(sys.modules, {"yaml": None}):
            code, out, err = run("warrant_canary_seller_key_hex: x\n")
        self.assertEqual((code, out), (1, "missing dependency: yaml\n"))
        self.assertNotIn("Traceback", err)

    @unittest.skipUnless(HAVE_YAML, "PyYAML is required to reach the derivation")
    def test_missing_eth_account_reported_without_traceback(self):
        document = {"warrant_canary_seller_key_hex": secrets.token_hex(32),
                    "warrant_canary_address_seller": "0x" + "1" * 40}
        with patch.dict(sys.modules, {"eth_account": None}):
            code, out, err = run(yaml.safe_dump(document))
        self.assertNotEqual(code, 0)
        self.assertIn("missing dependency: eth_account", out.splitlines())
        self.assertNotIn(document["warrant_canary_seller_key_hex"], out + err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
