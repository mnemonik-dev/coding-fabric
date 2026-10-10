"""O2 image upgrade edits only the MCP image and keeps a rollback copy."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('upgrade_o2', HERE / 'upgrade-o2.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)

OLD = 'ghcr.io/mnemonik-xyz/mnemonic-mcp@sha256:' + 'a' * 64
NEW = 'ghcr.io/mnemonik-xyz/mnemonic-mcp@sha256:' + 'b' * 64


def installed(root, image=OLD):
    data = {'name': 'mnemonik-o2', 'services': {
        'mcp': {'image': image, 'env_file': ['./secrets/mcp.env'],
                'volumes': [{'type': 'bind', 'target': '/keypair/id.json'}]},
        'caddy': {'image': 'caddy@sha256:' + 'c' * 64}}}
    (root / 'compose.json').write_text(json.dumps(data, indent=2) + '\n')
    return data


class UpgradeTests(unittest.TestCase):
    def test_reviewed_image_is_the_bootstrap_pin(self):
        self.assertRegex(upgrade.reviewed_image(), upgrade.IMAGE_RE)

    def test_changes_only_the_mcp_image_and_keeps_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = installed(root)
            original = (root / 'compose.json').read_text()
            self.assertEqual(upgrade.upgrade(root, NEW), (OLD, NEW))
            after = json.loads((root / 'compose.json').read_text())
            self.assertEqual(after['services']['mcp']['image'], NEW)
            before['services']['mcp']['image'] = NEW
            self.assertEqual(after, before)
            self.assertEqual((root / 'compose.json.prev').read_text(), original)
            self.assertEqual((root / 'compose.json').stat().st_mode & 0o777, 0o600)
            upgrade.rollback(root)
            self.assertEqual((root / 'compose.json').read_text(), original)

    def test_same_image_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed(root, NEW)
            self.assertEqual(upgrade.upgrade(root, NEW), (NEW, NEW))
            self.assertFalse((root / 'compose.json.prev').exists())

    def test_refuses_unpinned_or_missing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SystemExit):
                upgrade.upgrade(root, NEW)
            installed(root, 'ghcr.io/mnemonik-xyz/mnemonic-mcp:latest')
            with self.assertRaises(SystemExit):
                upgrade.upgrade(root, NEW)
            installed(root)
            with self.assertRaises(SystemExit):
                upgrade.upgrade(root, 'ghcr.io/other/image@sha256:' + 'b' * 64)
            with self.assertRaises(SystemExit):
                upgrade.rollback(root)


if __name__ == '__main__':
    unittest.main()
