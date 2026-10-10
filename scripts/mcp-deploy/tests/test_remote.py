"""Local tests for scripts/mcp-deploy/remote.sh with a fake docker on PATH."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "remote.sh"

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Fake docker: state lives in $FAKE_STATE; compose calls are logged.
S="$FAKE_STATE"
if [ "$1" = info ]; then exit 0; fi
if [ "$1" = compose ]; then
  echo "compose $*" >> "$S/calls"
  for a in "$@"; do last="$a"; prev="$a"; done
  case " $* " in
    *" up "*)
      net=$(grep -m1 '^CADDY_NETWORK_NAME=' mcp.env | cut -d= -f2-)
      echo "mnemonik_mnemonic $net " > "$S/mcp_nets"
      cp "$S/health_after_up" "$S/health" ;;
  esac
  exit 0
fi
if [ "$1" = inspect ]; then
  c="$2"; fmt="$4"
  case "$fmt" in
    *compose.project*) echo mnemonik ;;
    *Mounts*) cat "$S/keydir" ;;
    *Health*) cat "$S/health" ;;
    *Aliases*) echo "[mnemonik-mcp-1 mcp mnemonik-mcp]" ;;
    *Networks*) if [ "$c" = vaultwarden-caddy-1 ]; then cat "$S/caddy_nets"; else cat "$S/mcp_nets"; fi ;;
  esac
  exit 0
fi
echo "unexpected docker $*" >&2; exit 2
"""

ENV = (
    "MCP_PUBLIC_BASE_URL=https://mcp.example.com\n"
    "MCP_JWT_SECRET=super-secret-value\n"
    "MCP_IMAGE_TAG=latest\n"
    "CADDY_NETWORK_NAME=vaultwarden_default\n"
    "RUST_LOG=info\n"
)
SECRET = list(range(64))


class RemoteDeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo, self.state, bin_dir, keydir = (root / d for d in ("repo", "state", "bin", "keypair"))
        for d in (self.repo, self.state, bin_dir, keydir):
            d.mkdir()
        (self.repo / "docker-compose.yml").write_text(
            "services:\n  mcp:\n    image: ${MCP_IMAGE:-ghcr.io/mnemonik-xyz/mnemonic-mcp}:${MCP_IMAGE_TAG:-latest}\n")
        (self.repo / "mcp.env").write_text(ENV)
        os.chmod(self.repo / "mcp.env", 0o600)
        (keydir / "identity.json").write_text(json.dumps({"secret": SECRET}))
        (self.state / "keydir").write_text(str(keydir) + "\n")
        (self.state / "caddy_nets").write_text("vaultwarden_vaultwarden \n")
        (self.state / "mcp_nets").write_text("mnemonik_mnemonic vaultwarden_default \n")
        (self.state / "health").write_text("healthy\n")
        (self.state / "health_after_up").write_text("healthy\n")
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(self.state),
                    "REPO": str(self.repo), "DOCKER": "docker", "SUDO": "", "SLEEP": "0", "HEALTH_TRIES": "2",
                    "IMAGE_TAG": "sha-faf7f17", "FORCE_RESEED": "false"}

    def tearDown(self):
        self.tmp.cleanup()

    def run_mode(self, mode, **extra):
        return subprocess.run(["bash", str(SCRIPT)], env={**self.env, "MODE": mode, **extra},
                              capture_output=True, text=True)

    def calls(self):
        path = self.state / "calls"
        return path.read_text() if path.exists() else ""

    def env_file(self):
        return (self.repo / "mcp.env").read_text()

    def test_plan_reports_changes_and_changes_nothing(self):
        result = self.run_mode("plan")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("image tag: latest -> sha-faf7f17", result.stdout)
        self.assertIn("CADDY_NETWORK_NAME: vaultwarden_default -> vaultwarden_vaultwarden", result.stdout)
        self.assertIn("EXPECTED_PUBKEY=", result.stdout)
        self.assertEqual(self.env_file(), ENV)
        self.assertEqual(self.calls(), "")
        self.assertNotIn("super-secret-value", result.stdout + result.stderr)

    def test_apply_sets_keys_uses_env_file_and_keeps_secrets(self):
        result = self.run_mode("apply", FORCE_RESEED="true")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        env = self.env_file()
        for line in ("MCP_IMAGE_TAG=sha-faf7f17", "MNEMONIC_KEYPAIR_PATH=/keypair/identity.json",
                     "CADDY_NETWORK_NAME=vaultwarden_vaultwarden", "MCP_JWT_SECRET=super-secret-value"):
            self.assertIn(line + "\n", env)
        self.assertNotIn("MNEMONIC_FORCE_RESEED", env)
        self.assertEqual(env.count("MCP_IMAGE_TAG="), 1)
        self.assertEqual(oct((self.repo / "mcp.env").stat().st_mode & 0o777), "0o600")
        calls = self.calls()
        self.assertIn("--env-file mcp.env pull mcp", calls)
        self.assertIn("--env-file mcp.env up -d mcp", calls)
        self.assertIn("mcp healthy on vaultwarden_vaultwarden", result.stdout)
        self.assertNotIn("super-secret-value", result.stdout + result.stderr)

    def test_failed_apply_rolls_back_and_recreates(self):
        (self.state / "health_after_up").write_text("unhealthy\n")
        self.assertNotEqual(self.run_mode("apply").returncode, 0)
        result = self.run_mode("rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file(), ENV)
        self.assertIn("recreated mcp", result.stdout)
        self.assertEqual(self.calls().count("up -d mcp"), 2)

    def test_rollback_without_apply_touches_nothing(self):
        result = self.run_mode("rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing was changed", result.stdout)
        self.assertEqual(self.calls(), "")

    def test_failure_before_recreate_does_not_recreate_on_rollback(self):
        (self.state / "keydir").write_text(str(self.state / "missing") + "\n")
        self.assertNotEqual(self.run_mode("apply").returncode, 0)
        self.assertEqual(self.env_file(), ENV)
        result = self.run_mode("rollback")
        self.assertIn("nothing was changed", result.stdout)
        self.assertEqual(self.calls(), "")

    def test_refuses_when_caddy_network_unknown(self):
        (self.state / "caddy_nets").write_text("some_other_net \n")
        result = self.run_mode("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Caddy networks", result.stdout)
        self.assertEqual(self.env_file(), ENV)
        self.assertEqual(self.calls(), "")

    def test_refuses_unexpected_compose_layout(self):
        (self.repo / "docker-compose.yml").write_text("services:\n  mcp:\n    image: ghcr.io/x/mnemonic-mcp:sha-1\n")
        result = self.run_mode("plan")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("layout changed", result.stdout)


if __name__ == "__main__":
    unittest.main()
