"""O2 key initialization and isolation checks; no Docker or remote mutation."""
import importlib.util
import json
import os
import io
import shutil
import subprocess
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bootstrap_o2', Path(__file__).resolve().parents[1] / 'bootstrap-o2.py')
o2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o2)


class O2Tests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('age') and shutil.which('age-keygen'), 'age tools required')
    def test_off_host_key_backup_decrypts_with_owner_key(self):
        spec = importlib.util.spec_from_file_location('backup_o2', Path(__file__).resolve().parents[1] / 'backup-o2.py')
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        old_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'secrets').mkdir()
                names = ['secrets/id.json', 'secrets/mcp.env', 'operator.json', 'compose.json', 'Caddyfile']
                for name in names:
                    (root / name).write_text('synthetic-test-only')
                owner = root / 'owner.agekey'
                subprocess.run(['age-keygen', '-o', str(owner)], check=True, capture_output=True)
                recipient = subprocess.check_output(['age-keygen', '-y', str(owner)], text=True).strip()
                with patch.object(backup, 'ROOT', root), patch.object(backup.os, 'geteuid', return_value=0), \
                     patch('sys.argv', ['backup-o2.py', '--recipient', recipient]), patch('builtins.print'):
                    backup.main()
                encrypted = root / 'keys-backup.tar.gz.age'
                self.assertNotIn(b'synthetic-test-only', encrypted.read_bytes())
                plain = subprocess.check_output(['age', '--decrypt', '-i', str(owner), str(encrypted)])
                with tarfile.open(fileobj=io.BytesIO(plain), mode='r:gz') as tar:
                    self.assertEqual(tar.getnames(), names)
                    self.assertEqual(tar.extractfile('secrets/id.json').read(), b'synthetic-test-only')
        finally:
            os.umask(old_umask)

    def test_playbook_accepts_supported_ubuntu_releases(self):
        import yaml
        playbook = Path(__file__).resolve().parents[2] / 'ansible' / 'playbooks' / 'deploy-mcp-o2.yml'
        tasks = yaml.safe_load(playbook.read_text())[1]['tasks']
        check = next(task for task in tasks if task['name'] == 'Require the supported dedicated Ubuntu AMD64 host')
        self.assertIn("ansible_distribution_version in ['22.04', '24.04']", check['ansible.builtin.assert']['that'])

    def test_mnemonik_admin_keeps_its_privileges(self):
        import yaml
        playbook = Path(__file__).resolve().parents[2] / 'ansible' / 'playbooks' / 'deploy-mcp-o2.yml'
        plays = yaml.safe_load(playbook.read_text())
        validation = next(task for task in plays[0]['tasks'] if task['name'] == 'Validate required settings')
        self.assertNotIn("o2.admin_user != 'mnemonik'", validation['ansible.builtin.assert']['that'])
        prepare = next(task for task in plays[1]['tasks'] if task['name'] == 'Prepare O2 without starting containers')
        for name in ['Create restricted MCP deployment account without privileged groups',
                     'Replace the earlier unrestricted sudo rule with explicit MCP commands']:
            task = next(task for task in prepare['block'] if task['name'] == name)
            self.assertEqual(task['when'], 'not o2_mnemonik_is_admin | bool')

    def test_controller_tasks_run_locally(self):
        import yaml
        playbook = Path(__file__).resolve().parents[2] / 'ansible' / 'playbooks' / 'deploy-mcp-o2.yml'

        def tasks(items):
            for task in items:
                yield task
                yield from tasks(task.get('block', []))

        delegated = [task for play in yaml.safe_load(playbook.read_text())
                     for task in tasks(play.get('tasks', [])) if task.get('delegate_to') == 'localhost']
        self.assertTrue(delegated)
        for task in delegated:
            self.assertEqual(task.get('connection'), 'local', task['name'])

    def test_origin_cannot_inject_caddy_or_environment_directives(self):
        for value in ['127.0.0.1', 'mcp-staging.example.com\nBAD=1',
                      'mcp-staging.example.com {', 'https://mcp-staging.example.com',
                      'mcp-staging..com']:
            with self.assertRaises(ValueError):
                o2.hostname(value)
        self.assertEqual(o2.hostname('mcp-o2-staging.example.com'), 'mcp-o2-staging.example.com')
        self.assertEqual(o2.hostname('o2.example.com'), 'o2.example.com')

    def test_mcp_is_nonroot_and_has_no_docker_socket_or_public_port(self):
        data = o2.compose()
        self.assertEqual(data['name'], 'mnemonik-o2')
        mcp = data['services']['mcp']
        self.assertEqual(mcp['user'], '10001:10001')
        self.assertEqual(mcp['ports'], ['127.0.0.1:3000:3000'])
        self.assertTrue(mcp['volumes'][1]['read_only'])
        self.assertNotIn('docker.sock', json.dumps(data))
        self.assertIn('@sha256:', mcp['image'])

    def test_fresh_keys_are_valid_private_and_never_overwritten(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        old_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'o2'
                with patch.object(o2, 'ROOT', root), patch.object(o2.os, 'geteuid', return_value=0), \
                     patch.object(o2.os, 'uname') as uname, patch.object(o2.os, 'chown'), \
                     patch.object(o2.subprocess, 'run'), patch('builtins.print'), \
                     patch('sys.argv', ['bootstrap-o2.py', '--hostname', 'mcp-o2-staging.example.com']):
                    uname.return_value.machine = 'x86_64'
                    o2.main()
                    identity = root / 'secrets/id.json'
                    original = identity.read_bytes()
                    key = bytes(json.loads(original))
                    self.assertEqual(len(key), 64)
                    public = Ed25519PrivateKey.from_private_bytes(key[:32]).public_key()
                    self.assertEqual(public.public_bytes(Encoding.Raw, PublicFormat.Raw), key[32:])
                    self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
                    env = (root / 'secrets/mcp.env').read_text()
                    self.assertIn('PAYMENT_MODE=none\n', env)
                    self.assertIn('ANCHORING_NETWORK=mainnet\n', env)
                    self.assertIn('IRYS_GATEWAY_URL=https://gateway.irys.xyz\n', env)
                    self.assertEqual(json.loads((root / 'operator.json').read_text())['publicKey'], o2.public_key(key[32:]))
                    with self.assertRaises(SystemExit):
                        o2.main()
                    self.assertEqual(identity.read_bytes(), original)
        finally:
            os.umask(old_umask)


if __name__ == '__main__':
    unittest.main()
