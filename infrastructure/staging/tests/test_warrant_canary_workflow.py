"""Checks of .github/workflows/warrant-canary.yml: parsed with yaml.safe_load; inline
scripts run with bash/python under controlled env (no network, sops or VM)."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / '.github' / 'workflows' / 'warrant-canary.yml'
ROLE = ROOT / 'infrastructure' / 'ansible' / 'roles' / 'warrant-canary'
DEFAULTS = ROLE / 'defaults' / 'main.yml'

ACTIONS = ['preflight', 'configure', 'rehearse', 'stage-keys', 'unstage-keys',
           'deploy-solver', 'deploy-task', 'deploy-seller']
INPUTS = {'action', 'deploy_contracts', 'execute', 'render_only', 'roles', 'run_dir', 'settle_window',
          'force_unstage', 'force_unstage_reason', 'expected_image_id_override', 'rpc_override'}
D1_KEYS = {'warrant_canary_action', 'warrant_canary_deploy_contracts', 'warrant_canary_execute',
           'warrant_canary_render_only', 'warrant_canary_roles', 'warrant_canary_run_dir',
           'warrant_canary_settle_window', 'warrant_canary_force_unstage',
           'warrant_canary_force_unstage_reason', 'warrant_canary_expected_image_id_override',
           'warrant_canary_rpc_override', 'warrant_canary_output_dir'}
PLACEHOLDERS = ['<SOLVER_ESCROW_ADDRESS>', '<GROTH16_VERIFIER_ADDRESS>', '<TASK_ESCROW_ADDRESS>',
                '<JOURNAL_VERIFIER_ADDRESS>', '<SELLER_ADDRESS>', '<REGISTRY_PUBLIC_KEY>']
IMAGE_ID = '0x' + 'ab' * 32
ADDR_A, ADDR_B = '0x' + '1' * 40, '0x' + '2' * 40
PUBKEY = '0x03' + 'cd' * 32
RPC = 'https://rpc.testnet.arc.network'
DEVELOP = 'refs/heads/develop'
FALLBACK = 'Print the laptop fallback'
BROADCAST_IF = ("inputs.deploy_contracts && inputs.execute && github.ref == 'refs/heads/develop' && "
                "(inputs.action == 'deploy-solver' || inputs.action == 'deploy-task')")
BASE_ENV = {'WC_ACTION': 'preflight', 'WC_DEPLOY_CONTRACTS': 'false', 'WC_EXECUTE': 'false',
            'WC_RENDER_ONLY': 'false', 'WC_ROLES': 'buyer,seller,relayer', 'WC_RUN_DIR': '',
            'WC_SETTLE_WINDOW': '', 'WC_FORCE_UNSTAGE': 'false', 'WC_FORCE_UNSTAGE_REASON': '',
            'WC_EXPECTED_IMAGE_ID_OVERRIDE': '', 'WC_RPC_OVERRIDE': '', 'GITHUB_REF': DEVELOP}
SOLVER = {'WC_ACTION': 'deploy-solver', 'WC_SETTLE_WINDOW': '9800'}
SWITCHED = {'WC_DEPLOY_CONTRACTS': 'true', 'WC_EXECUTE': 'true'}
CHECK_FLAGS = ('escrow_has_code', 'verifier_has_code', 'token_ok', 'image_id_ok', 'verifier_ok',
               'total_reserved_zero')


def heredoc(run, opener):
    match = re.search(re.escape(opener) + r"\n(.*?)\nPY\n", run, re.S)
    assert match, f'no heredoc after {opener!r}'
    return match.group(1)


class WarrantCanaryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = yaml.safe_load(WORKFLOW.read_text())
        cls.jobs = cls.wf['jobs']
        cls.static, cls.run_job = cls.jobs['static'], cls.jobs['run']

    def inputs(self):
        return self.wf.get('on', self.wf.get(True))['workflow_dispatch']['inputs']

    def all_steps(self):
        return [(name, step) for name, job in self.jobs.items() for step in job['steps']]

    def names(self):
        return [s.get('name') for s in self.run_job['steps']]

    def step(self, job, name):
        matches = [s for s in self.jobs[job]['steps'] if s.get('name') == name]
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def step_with(self, needle):
        matches = [s for _, s in self.all_steps() if needle in s.get('run', '')]
        self.assertEqual(len(matches), 1, needle)
        return matches[0]

    def bash(self, script, env):
        base = {'PATH': os.environ['PATH'], 'HOME': os.environ.get('HOME', '/tmp')}
        return subprocess.run(['bash', '-c', script], env={**base, **env}, capture_output=True, text=True,
                              timeout=60)

    def validate(self, **overrides):
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / 'summary.md'
            env = {**BASE_ENV, **overrides, 'GITHUB_STEP_SUMMARY': str(summary)}
            res = self.bash(self.step('static', 'Validate inputs')['run'], env)
            return res, summary.read_text() if summary.exists() else ''

    def assertRefused(self, **overrides):
        res, summary = self.validate(**overrides)
        self.assertNotEqual(res.returncode, 0, overrides)
        self.assertIn('::error::', res.stdout, (overrides, res.stderr))
        self.assertIn('refused', summary, overrides)

    def assertAccepted(self, **overrides):
        res, _ = self.validate(**overrides)
        self.assertEqual(res.returncode, 0, (overrides, res.stdout, res.stderr))

    def build_extra_vars(self, broadcast='false', expect_ok=True, **overrides):
        env = {**BASE_ENV, **SOLVER, **SWITCHED, 'WC_ROLES': 'buyer,seller',
               'WARRANT_CANARY_BROADCAST': broadcast, **overrides}
        with tempfile.TemporaryDirectory() as d:
            res = self.bash(self.step('run', 'Build extra-vars (no broadcast)')['run'], {**env, 'RUNNER_TEMP': d})
            if not expect_ok:
                self.assertNotEqual(res.returncode, 0, overrides)
                return res.stderr
            self.assertEqual(res.returncode, 0, res.stderr)
            path = Path(d) / 'wc' / 'extra-vars.json'
            return json.loads(path.read_text()), stat.S_IMODE(path.stat().st_mode), d

    def pattern_step(self):
        return self.step('run', 'Build the secret-pattern file and mask secret values')

    def pr_step(self):
        return self.step('run', 'Open the deployment PR in policy-execution')

    def test_dispatch_only_with_concurrency_and_minimal_permissions(self):
        self.assertEqual(list(self.wf.get('on', self.wf.get(True))), ['workflow_dispatch'])
        self.assertEqual(self.wf['concurrency'], {'group': 'warrant-canary', 'cancel-in-progress': False})
        self.assertEqual(self.wf['permissions'], {'contents': 'read'})
        self.assertFalse([j for j in self.jobs.values() if 'permissions' in j])

    def test_input_set_matches_data_models(self):
        self.assertEqual(set(self.inputs()), INPUTS)
        self.assertEqual(self.inputs()['action']['options'], ACTIONS)

    def test_switches_default_false(self):
        for name in ('deploy_contracts', 'execute', 'render_only', 'force_unstage'):
            self.assertEqual(self.inputs()[name]['type'], 'boolean', name)
            self.assertIs(self.inputs()[name]['default'], False, name)

    def test_settle_window_has_no_default(self):
        self.assertEqual(self.inputs()['settle_window'], {'description': self.inputs()['settle_window']['description'],
                                                          'type': 'string', 'required': False})

    def test_no_accept_window_input(self):
        self.assertFalse([n for n in self.inputs() if 'accept_window' in n])  # the role fixes it at 1800

    def test_every_job_has_timeout(self):
        self.assertTrue(all(isinstance(job.get('timeout-minutes'), int) for job in self.jobs.values()))
        self.assertIsInstance(self.step('run', 'Run the playbook').get('timeout-minutes'), int)

    def test_static_checks_precede_remote_steps(self):
        self.assertIn('static', self.run_job['needs'])
        runs = '\n'.join(s.get('run', '') for s in self.static['steps'])
        self.assertTrue(all(n in runs for n in ('actionlint', '--syntax-check', 'unittest discover', 'refuse ')))
        self.assertNotIn('secrets.', json.dumps(self.static))
        self.assertFalse([s for s in self.static['steps'] if 'tailscale' in s.get('uses', '')])
        self.assertNotRegex(runs, r'(^|\s)(ssh|tailscale|sops)\s')

    def test_inputs_scoped_to_steps(self):
        self.assertNotIn('env', self.wf)
        self.assertFalse([k for job in self.jobs.values() for k in job.get('env') or {} if k.startswith('WC_')])
        tailscale = self.step('run', 'Tailscale up (ephemeral)')
        self.assertNotIn('env', tailscale)
        self.assertNotIn('inputs.', json.dumps(tailscale.get('with')).replace('github.run_id', ''))

    def test_broadcast_steps_gated_on_both_switches(self):
        steps = [s for _, s in self.all_steps() if (s.get('env') or {}).get('WARRANT_CANARY_BROADCAST') == 'true']
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['if'], '${{ ' + BROADCAST_IF + ' }}')
        negated = self.step('run', 'Build extra-vars (no broadcast)')
        self.assertEqual(negated['if'], '${{ !(' + BROADCAST_IF + ') }}')
        self.assertEqual(negated['run'], steps[0]['run'])
        self.assertEqual(negated['env'], {**steps[0]['env'], 'WARRANT_CANARY_BROADCAST': 'false'})

    def test_builder_reasserts_broadcast_conditions(self):
        self.assertIn('broadcast needs deploy_contracts and execute',
                      self.build_extra_vars('true', False, WC_EXECUTE='false'))
        self.assertIn('refs/heads/develop', self.build_extra_vars('true', False, GITHUB_REF='refs/heads/x'))
        self.assertIn('deploy-solver', self.build_extra_vars('true', False, WC_ACTION='configure'))

    def test_inputs_and_secrets_not_interpolated_in_run(self):
        for _, step in self.all_steps():
            self.assertNotIn('${{', step.get('run', ''), step.get('name'))

    def test_key_material_cleanup_always(self):
        last = self.run_job['steps'][-1]
        self.assertEqual(last['if'], 'always()')
        for needle in ('wc/ssh/key', 'wc/ssh/known_hosts', 'age/keys.txt', 'wc/patterns.txt',
                       'wc/inventory.yml', 'wc/extra-vars.json', 'wc/policy-execution'):
            self.assertIn(needle, last['run'])

    def test_vm_tmpfs_check_runs_after_failure(self):
        step = self.step_with('test ! -e /run/warrant-canary')
        self.assertIn('always()', step['if'])
        self.assertIn("inputs.action != 'stage-keys'", step['if'])

    def test_secret_grep_precedes_upload_artifact(self):
        for job in self.jobs.values():
            for i, step in enumerate(job['steps']):
                if step.get('uses', '').startswith('actions/upload-artifact'):
                    self.assertTrue(any('grep -rFf' in s.get('run', '') for s in job['steps'][:i]))
                    self.assertNotIn('always()', step['if'])
                    self.assertIn("steps.grep.outcome == 'success'", step['if'])
        grep = self.step('run', 'Grep the fetched records for secret values')['run']
        self.assertTrue(grep.startswith('set +x'))
        self.assertLess(grep.index("grep -qx ''"), grep.index('grep -rFf'))
        self.assertIn('-type l', grep)

    def test_pattern_file_step_disables_xtrace(self):
        run = self.pattern_step()['run']
        self.assertTrue(run.startswith('set +x'))
        self.assertNotRegex(run, r'\bcat\b|wc -l')

    def test_pattern_file_only_secret_values(self):
        run = self.pattern_step()['run']
        for needle in ('keystore_password', 'seller_key_hex', 'registry_key', 'ionet_api_key',
                       'mcp_auth_pass', 'ciphertext', 'mac', 'dev-mnemonik'):
            self.assertIn(needle, run)
        for needle in ('warrant_canary_address_', 'warrant_canary_expected_image_id',
                       'warrant_canary_src_commit', 'warrant_canary_mcp_auth_user'):
            self.assertNotIn(needle, run)
        self.assertIn('test -s', run)
        self.assertIn("grep -qx ''", run)
        self.assertIn('print(f"::add-mask::{value}")', run)  # masked before the playbook runs
        names = self.names()
        self.assertLess(names.index(self.pattern_step()['name']), names.index('Run the playbook'))

    def run_placement(self, action, readme_text, out_files, run_name='20261015T120000Z'):
        script = heredoc(self.pr_step()['run'], "python3 - <<'PY'")
        with tempfile.TemporaryDirectory() as d:
            out, clone = Path(d) / 'out', Path(d) / 'clone'
            out.mkdir()
            for name, content in out_files.items():
                (out / name).write_text(content)
            readme = clone / ('solver-bounty/canary' if action == 'deploy-solver'
                              else 'showcases/ionet_arc') / 'README.md'
            readme.parent.mkdir(parents=True)
            readme.write_text(readme_text)
            env = {'PATH': os.environ['PATH'], 'WC_ACTION': action, 'WC_OUT': str(out), 'WC_CLONE': str(clone),
                   'RUN_NAME': run_name}
            res = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True)
            files = {str(p.relative_to(clone)): p.read_text() for p in clone.rglob('*') if p.is_file()}
        return res, files

    def assertPlacementFails(self, action, readme_text, out_files, message):
        self.assertIn(message, self.run_placement(action, readme_text, out_files)[0].stderr)

    def seller_out(self, keys=f"# header\nregistry_key: '{PUBKEY}'\n"):
        return {'seller-public-manifest.json': json.dumps({'seller_address': ADDR_A}),
                'mainnet-public_keys.yaml': keys}

    def test_pr_step_replaces_fixed_placeholders(self):
        run = self.pr_step()['run']
        for needle in PLACEHOLDERS + ['showcases/ionet_arc/config/mainnet-public_keys.yaml', 'git diff --cached --text',
                                      '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}']:
            self.assertIn(needle, run)
        self.assertNotRegex(run, r'\bsed\b|Co-Authored-By')
        res, files = self.run_placement('deploy-seller', '<SELLER_ADDRESS> <REGISTRY_PUBLIC_KEY>', self.seller_out())
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(files['showcases/ionet_arc/README.md'], f'{ADDR_A} {PUBKEY}')
        self.assertIn('registry_key', files['showcases/ionet_arc/config/mainnet-public_keys.yaml'])
        self.assertIn('solver-bounty/canary/mainnet/seller-public-manifest.json', files)
        self.assertPlacementFails('deploy-seller', '| k | `<REGISTRY_PUBLIC_KEY>` |\n', self.seller_out(),
                                  '<SELLER_ADDRESS> not found')
        self.assertPlacementFails('deploy-seller', '<SELLER_ADDRESS> <REGISTRY_PUBLIC_KEY>', self.seller_out(
            f"registry_key: '{PUBKEY}'\nbuyer_acceptance_key: '{PUBKEY}'\n"), 'buyer_acceptance_key')
        solver_out = {'state.json': json.dumps({'escrow': ADDR_A, 'verifier': ADDR_B}), 'deployment.json': '{}',
                      'arc-before.json': '{}', 'arc-after.json': '{}'}
        res, files = self.run_placement('deploy-solver', '<SOLVER_ESCROW_ADDRESS> <GROTH16_VERIFIER_ADDRESS>',
                                        solver_out)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(files['solver-bounty/canary/README.md'], f'{ADDR_A} {ADDR_B}')
        self.assertIn('solver-bounty/canary/mainnet/20261015T120000Z/state.json', files)
        self.assertPlacementFails('deploy-solver', '<SOLVER_ESCROW_ADDRESS> only', solver_out,
                                  '<GROTH16_VERIFIER_ADDRESS> not found')
        task = json.dumps({'checks': {**{f: True for f in CHECK_FLAGS}, 'escrow': ADDR_A, 'verifier': ''}})
        self.assertPlacementFails('deploy-task', '<TASK_ESCROW_ADDRESS> <JOURNAL_VERIFIER_ADDRESS>',
                                  {'task-escrow.json': task}, 'empty or malformed')

    def test_health_files_compared(self):
        run = self.step('run', 'Compare health records')['run']
        for needle in ('health-before.json', 'health-after.json', 'warrant-canary-out',
                       '.services | map_values(.status)', 'stopped', 'exit 1'):
            self.assertIn(needle, run)

    def test_pr_opens_only_after_health_and_grep_pass(self):
        names, pr = self.names(), self.pr_step()
        self.assertLess(names.index('Compare health records'), names.index(pr['name']))
        self.assertLess(names.index('Upload records'), names.index(pr['name']))
        for needle in ('success()', "steps.health.outcome == 'success'", "steps.grep.outcome == 'success'"):
            self.assertIn(needle, pr['if'])
        self.assertNotIn('always()', pr['if'])

    def test_pr_token_masked_setup_and_logout(self):
        run = self.pr_step()['run']
        self.assertLess(run.index('::add-mask::'), run.index('export GH_TOKEN'))
        self.assertLess(run.index('export GH_TOKEN'), run.index('gh auth setup-git'))
        self.assertNotIn('gh auth login', run)
        logout = self.step_with('gh auth logout')
        self.assertEqual(logout['if'], 'always()')
        self.assertGreater(self.names().index(logout['name']), self.names().index(self.pr_step()['name']))
        token_users = sorted(s['name'] for _, s in self.all_steps() if '["dev-mnemonik"]' in s.get('run', ''))
        self.assertEqual(token_users, sorted(['Check the PR token', self.pr_step()['name']]))
        self.assertFalse([s for s in [self.wf, *self.jobs.values()] if 'GH_TOKEN' in (s.get('env') or {})])

    def test_no_environment_and_repository_secrets(self):
        for job in self.jobs.values():
            self.assertNotIn('environment', job)
        for name in ('secrets.SOPS_AGE_KEY', 'secrets.CI_SSH_PRIVATE_KEY', 'secrets.TAILSCALE_AUTH_KEY'):
            self.assertIn(name, json.dumps(self.run_job))
        for _, step in self.all_steps():
            uses = step.get('uses', '')
            if uses.startswith('actions/checkout'):
                self.assertIs(step['with']['persist-credentials'], False)
            elif uses and not uses.startswith('actions/'):
                self.assertRegex(uses, r'@[0-9a-f]{40}$', uses)

    def test_inventory_does_not_enable_become(self):
        run = self.step('run', 'Render the inventory')['run']
        self.assertIn('ansible_user: op', run)
        self.assertNotIn('ansible_become', run)
        for _, step in self.all_steps():
            if 'ansible-playbook' in step.get('run', ''):
                self.assertNotIn('--become', step['run'])
                self.assertNotRegex(step['run'], r'\s-b(\s|$)')

    def test_sops_age_key_file_exported(self):
        for job_name, step in self.all_steps():
            run = step.get('run', '')
            if ('--decrypt' in run or 'ansible-playbook' in run) and step.get('name') != FALLBACK:
                value = (step.get('env') or {}).get('SOPS_AGE_KEY_FILE', '')
                self.assertIn('runner.temp', value, (job_name, step.get('name')))
                self.assertTrue(value.endswith('/age/keys.txt'))

    def test_run_dir_read_from_run_json(self):
        step = self.step('run', 'Read the deployed run directory')
        self.assertNotRegex(step['run'], r'\bssh\b')
        for value, ok in (('mainnet/20261015T120000Z', True), ('mainnet/20261015T120000Z-simulate', False),
                          ('../etc', False), ('', False)):
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / 'warrant-canary-out').mkdir()
                (Path(d) / 'warrant-canary-out' / 'run.json').write_text(json.dumps({'run_dir': value}))
                out = Path(d) / 'out'
                res = self.bash(step['run'], {'RUNNER_TEMP': d, 'GITHUB_OUTPUT': str(out)})
                self.assertEqual(res.returncode == 0, ok, value)
                if ok:
                    self.assertEqual(out.read_text(), 'name=20261015T120000Z\n')

    def test_role_fetches_run_json_for_deploy_solver(self):
        text = (ROLE / 'tasks' / 'deploy_solver.yml').read_text()
        lists = re.findall(r'_wc_fetch_list:\n((?:[ \t]+- .*\n)+)', text)
        self.assertTrue(any(re.search(r'/run\.json"?\s*$', block, re.M) for block in lists), lists)

    def test_settle_window_validated_before_key_staging(self):
        for bad in ('', '7200;id', '$(id)', ' 7200', 'abc', '7200\nid'):
            self.assertRefused(**{**SOLVER, 'WC_SETTLE_WINDOW': bad})
        self.assertAccepted(**SOLVER)
        for job_name, step in self.all_steps():
            if 'ansible-playbook' in step.get('run', '') and '--syntax-check' not in step['run']:
                self.assertEqual(job_name, 'run')

    def test_develop_ref_check_present(self):
        self.assertRefused(WC_ACTION='deploy-task', GITHUB_REF='refs/heads/feature/x', **SWITCHED)
        self.assertAccepted(WC_ACTION='deploy-task', **SWITCHED)
        for action in ('preflight', 'configure', 'stage-keys'):
            self.assertRefused(WC_ACTION=action, GITHUB_REF='refs/heads/feature/x')
        self.assertEqual(self.run_job['if'], "github.ref == 'refs/heads/develop'")

    def test_overrides_refused_with_execute(self):
        self.assertRefused(WC_ACTION='configure', WC_EXECUTE='true', WC_EXPECTED_IMAGE_ID_OVERRIDE=IMAGE_ID)
        self.assertRefused(**SOLVER, **SWITCHED, WC_RPC_OVERRIDE=RPC)
        self.assertAccepted(WC_ACTION='configure', WC_EXPECTED_IMAGE_ID_OVERRIDE=IMAGE_ID)
        self.assertAccepted(**SOLVER, WC_RPC_OVERRIDE=RPC)

    def test_overrides_refused_on_other_actions(self):
        for action in ACTIONS:
            extra = {'WC_SETTLE_WINDOW': '9800'} if action == 'deploy-solver' else {}
            if action != 'configure':
                self.assertRefused(WC_ACTION=action, WC_EXPECTED_IMAGE_ID_OVERRIDE=IMAGE_ID, **extra)
            if action != 'deploy-solver':
                self.assertRefused(WC_ACTION=action, WC_RPC_OVERRIDE=RPC)

    def test_force_unstage_requires_reason(self):
        self.assertRefused(WC_ACTION='unstage-keys', WC_FORCE_UNSTAGE='true', WC_FORCE_UNSTAGE_REASON='')

    def test_force_unstage_refused_on_other_action(self):
        self.assertRefused(WC_ACTION='deploy-task', WC_FORCE_UNSTAGE='true', WC_FORCE_UNSTAGE_REASON='order refunded')
        self.assertAccepted(WC_ACTION='unstage-keys', WC_FORCE_UNSTAGE='true',
                            WC_FORCE_UNSTAGE_REASON='order 0x12 refunded (tx 0xab), see task-14: ok')

    def test_force_unstage_reason_plain_text_only(self):
        for bad in ("{{ lookup('file', '/etc/passwd') }}", '{% raw %}x', 'a`id`', 'a$(id)', 'two\nlines',
                    'quote "x"', "it's", 'a\\b', '50%', 'a;b', 'a/b', '#x', 'x' * 201):
            for force in ('true', 'false'):
                self.assertRefused(WC_ACTION='unstage-keys', WC_FORCE_UNSTAGE=force, WC_FORCE_UNSTAGE_REASON=bad)

    def test_template_syntax_refused_in_every_string_input(self):
        names = ['WC_' + n.upper() for n, v in self.inputs().items() if v['type'] == 'string']
        self.assertEqual(len(names), 6)
        for name in names:
            for bad in ('{{ 7 * 7 }}', '{% if 1 %}x{% endif %}', 'https://rpc.example/{{x}}'):
                self.assertRefused(**{**SOLVER, 'WC_ACTION': 'deploy-solver', name: bad})

    def test_run_dir_traversal_refused(self):
        for bad in ('../mainnet/20261015T120000Z', 'mainnet/../../etc', '/opt/warrant-canary/src',
                    'mainnet/20261015T120000Z/..'):
            self.assertRefused(WC_RUN_DIR=bad)

    def test_run_dir_pattern_enforced(self):
        for bad in ('mainnet/20261015T120000Z-simulate', 'local/20261015T120000Z', 'mainnet/2026-10-15',
                    'mainnet/20261015T120000Z;id', 'mainnet/20261015T120000Z\n'):
            self.assertRefused(WC_RUN_DIR=bad)
        self.assertAccepted(WC_RUN_DIR='mainnet/20261015T120000Z')
        self.assertAccepted(WC_RUN_DIR='')

    def test_roles_outside_allowed_set_refused(self):
        for bad in ('buyer,admin', 'buyer,,seller', 'buyer, seller', 'buyer;id', 'buyer,buyer', ''):
            self.assertRefused(WC_ACTION='stage-keys', WC_ROLES=bad)
        for good in ('buyer,seller,relayer', 'deployer'):
            self.assertAccepted(WC_ACTION='stage-keys', WC_ROLES=good)

    def test_rpc_override_requires_https(self):
        for bad in ('http://rpc.example', 'file:///etc/passwd', 'https://rpc.example/$(id)', 'https://rpc.example a',
                    'https://169.254.169.254/latest', 'https://localhost:8545', 'https://LocalHost',
                    'https://a.localhost/x', 'https://0x7f000001', 'https://0x7f.1', 'https://2130706433'):
            self.assertRefused(**SOLVER, WC_RPC_OVERRIDE=bad)
        self.assertAccepted(**SOLVER, WC_RPC_OVERRIDE=RPC)

    def test_image_id_override_format_enforced(self):
        for bad in ('ab' * 32, '0x' + 'a' * 63, '0x' + 'a' * 65, '0x' + 'AB' * 32, IMAGE_ID + ';id'):
            self.assertRefused(WC_ACTION='configure', WC_EXPECTED_IMAGE_ID_OVERRIDE=bad)
        self.assertAccepted(WC_ACTION='configure', WC_EXPECTED_IMAGE_ID_OVERRIDE=IMAGE_ID)

    def test_switches_refused_on_non_deploy_action(self):
        for action in ('preflight', 'configure', 'rehearse', 'stage-keys', 'unstage-keys', 'deploy-seller'):
            self.assertRefused(WC_ACTION=action, WC_DEPLOY_CONTRACTS='true')
            self.assertRefused(WC_ACTION=action, WC_EXECUTE='true')
        for action in ('deploy-solver', 'deploy-task'):
            self.assertAccepted(**{**SOLVER, **SWITCHED, 'WC_ACTION': action})

    def test_extra_vars_keys_match_contract(self):
        data, mode, tmp = self.build_extra_vars()
        self.assertEqual(set(data), D1_KEYS)
        self.assertEqual(mode, 0o600)
        self.assertEqual(data['warrant_canary_roles'], ['buyer', 'seller'])
        self.assertTrue(all(isinstance(data[f'warrant_canary_{k}'], bool)
                            for k in ('deploy_contracts', 'execute', 'render_only', 'force_unstage')))
        self.assertEqual(data['warrant_canary_output_dir'], os.path.join(tmp, 'warrant-canary-out'))
        self.assertEqual(data['warrant_canary_settle_window'], '9800')

    def test_extra_vars_keys_match_role_defaults(self):
        text = DEFAULTS.read_text()  # a missing file (Task 7 owns it) errors, it never skips
        begin, end = '# --- BEGIN workflow inputs (D1) ---', '# --- END workflow inputs (D1) ---'
        self.assertTrue(begin in text and end in text)
        block = yaml.safe_load(text.split(begin, 1)[1].split(end, 1)[0])
        data, _, _ = self.build_extra_vars()
        self.assertEqual(set(block), set(data))
        self.assertEqual({k: type(v) for k, v in block.items()}, {k: type(v) for k, v in data.items()})
        self.assertIsInstance(block['warrant_canary_roles'], list)

    def test_extra_vars_switches_false_outside_broadcast_step(self):
        for broadcast, expected in (('false', False), ('true', True)):
            data = self.build_extra_vars(broadcast)[0]
            self.assertIs(data['warrant_canary_deploy_contracts'], expected)
            self.assertIs(data['warrant_canary_execute'], expected)

    def test_extra_vars_free_text_not_on_command_line(self):
        reason = 'it\'s "stuck"\nsecond line `uname` $(id) ;rm -rf /'
        data, _, _ = self.build_extra_vars(WC_ACTION='unstage-keys', WC_FORCE_UNSTAGE='true',
                                           WC_FORCE_UNSTAGE_REASON=reason, **{k: 'false' for k in SWITCHED})
        self.assertEqual(data['warrant_canary_force_unstage_reason'], reason)
        for action, force in (('deploy-solver', 'false'), ('unstage-keys', 'false'), ('deploy-task', 'true')):
            data = self.build_extra_vars(WC_ACTION=action, WC_FORCE_UNSTAGE=force, WC_FORCE_UNSTAGE_REASON='x')[0]
            self.assertEqual(data['warrant_canary_force_unstage_reason'], '', action)
        for run in [s.get('run', '') for _, s in self.all_steps() if s.get('name') != FALLBACK]:
            if 'ansible-playbook' in run:
                self.assertNotIn('force_unstage_reason', run)
                self.assertNotIn('WC_', run)
            self.assertNotRegex(run, r'WC_FORCE_UNSTAGE_REASON[^\n]*GITHUB_(ENV|OUTPUT)')

    def summarize(self, **env):
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / 'summary.md'
            env = {'GITHUB_STEP_SUMMARY': str(summary), 'WC_MODE': 'n/a', 'PLAY_OUTCOME': 'success', **env}
            res = self.bash(self.step('run', 'Job summary')['run'], env)
            self.assertEqual(res.returncode, 0, res.stderr)
            return summary.read_text() + res.stdout

    def test_summary_fences_force_unstage_reason(self):
        reason = 'stuck ```\n# heading\n$(id) and ````` more'
        text = self.summarize(WC_ACTION='unstage-keys', WC_FORCE_UNSTAGE='true', WC_FORCE_UNSTAGE_REASON=reason)
        self.assertIn(f'{"`" * 6}text\n{reason}\n{"`" * 6}\n', text)
        self.assertNotIn('uid=', text)
        self.assertIn('`unstage-keys`', text)
        text = self.summarize(WC_ACTION='stage-keys', WC_FORCE_UNSTAGE='false')
        self.assertIn('keys staged; run `unstage-keys` after the deal', text)
        mode = self.step('run', 'Job summary')['env']['WC_MODE']
        self.assertIn("'simulate'", mode)
        self.assertIn("'broadcast'", mode)


if __name__ == '__main__':
    unittest.main()
