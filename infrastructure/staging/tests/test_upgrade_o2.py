"""O2 image upgrade edits only the MCP image and keeps a rollback copy."""
import importlib.util
import json
import os
import unittest.mock
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

    def test_replacements_fsync_the_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed(root)
            with unittest.mock.patch.object(upgrade.os, 'fsync', wraps=os.fsync) as synced, \
                 unittest.mock.patch.object(upgrade.os, 'open', wraps=os.open) as opened:
                upgrade.upgrade(root, NEW)
            directory_opens = [c for c in opened.call_args_list if c.args[1] & os.O_DIRECTORY]
            # Two replacements (compose.json.prev, compose.json), each followed
            # by a directory fsync; plus one fsync per file.
            self.assertEqual(len(directory_opens), 2)
            self.assertTrue(all(Path(c.args[0]) == root for c in directory_opens))
            self.assertEqual(synced.call_count, 4)

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


LIVE_ENV = (
    'MCP_PUBLIC_BASE_URL=https://mcp2.example.com\n'
    'MCP_JWT_SECRET=secret-value\n'
    'MNEMONIC_KEYPAIR_PATH=/keypair/id.json\n'
    '# operator note\n'
    'ANCHORING_NETWORK=devnet\n'
    'SOLANA_RPC_URL=https://api.devnet.solana.com\n'
    'LEGACY_STORE_GATEWAY_URL=https://old.example.com\n'
    'ARWEAVE_URL=https://old-alias.example.com\n'
    'RUST_LOG=info\n'
)


class ReconcileEnvTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'secrets').mkdir()
        (self.root / 'secrets/mcp.env').write_text(LIVE_ENV)
        self.desired = upgrade.reviewed_storage_env()

    def tearDown(self):
        self.directory.cleanup()

    def env(self):
        return (self.root / 'secrets/mcp.env').read_text()

    def test_sets_storage_keys_drops_superseded_and_keeps_secrets(self):
        changed = upgrade.reconcile_env(self.root, self.desired)
        text = self.env()
        for key, value in self.desired.items():
            self.assertIn(f'{key}={value}\n', text)
            self.assertEqual(text.count(f'{key}='), 1)
        self.assertNotIn('LEGACY_STORE_GATEWAY_URL', text)
        self.assertNotIn('ARWEAVE_URL=', text)
        for kept in ('MCP_JWT_SECRET=secret-value', 'MNEMONIC_KEYPAIR_PATH=/keypair/id.json',
                     '# operator note', 'RUST_LOG=info'):
            self.assertIn(kept, text)
        self.assertIn('LEGACY_STORE_GATEWAY_URL', changed)
        self.assertNotIn('MCP_JWT_SECRET', changed)
        self.assertEqual((self.root / 'secrets/mcp.env.prev').read_text(), LIVE_ENV)
        # Second run: nothing to change, no new write.
        (self.root / 'secrets/mcp.env.prev').unlink()
        self.assertEqual(upgrade.reconcile_env(self.root, self.desired), [])
        self.assertFalse((self.root / 'secrets/mcp.env.prev').exists())

    def test_rollback_restores_only_what_this_run_changed(self):
        installed(self.root)
        (self.root / 'compose.json.prev').write_text('stale from an earlier run')
        upgrade.clear_previous(self.root)
        self.assertFalse((self.root / 'compose.json.prev').exists())
        upgrade.reconcile_env(self.root, self.desired)
        self.assertEqual(upgrade.rollback(self.root), ['secrets/mcp.env'])
        self.assertEqual(self.env(), LIVE_ENV)
        self.assertTrue(json.loads((self.root / 'compose.json').read_text()))

    def test_bootstrap_and_upgrade_share_storage_settings(self):
        self.assertEqual(self.desired['ARWEAVE_GATEWAY_URL'], 'https://arweave.net')
        self.assertEqual(self.desired['ANCHORING_NETWORK'], 'mainnet')


if __name__ == '__main__':
    unittest.main()
