"""Contract tests for the mnemonic-mcp Ansible role.

Tests fix the contract documented in
work/content-publish-pipeline/tasks/02-mnemonik-mcp-role-npm-rewrite.md
(TDD Anchor). Each test reads role files directly; no live VM, no molecule.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROLE_DIR = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = ROLE_DIR.parents[1]
REPO_ROOT = ANSIBLE_DIR.parents[1]
PLAYBOOK = ANSIBLE_DIR / "playbooks" / "deploy.yml"


def _load_yaml(path: Path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def _iter_tasks(blocks):
    """Yield every task dict from a list that may contain blocks (recursively)."""
    for item in blocks or []:
        if not isinstance(item, dict):
            continue
        if "block" in item:
            yield from _iter_tasks(item.get("block") or [])
            yield from _iter_tasks(item.get("rescue") or [])
            yield from _iter_tasks(item.get("always") or [])
        else:
            yield item


def test_defaults_pin_npm_package_and_version():
    defaults = _load_yaml(ROLE_DIR / "defaults" / "main.yml")
    assert defaults.get("mnemonik_mcp_npm_package") == "@mnemonik-xyz/mcp"
    version = defaults.get("mnemonik_mcp_npm_version")
    assert isinstance(version, str) and version, "version must be a non-empty string"
    assert defaults.get("mnemonic_mcp_enabled") is True
    assert "mnemonic_mcp_binary_url" not in defaults
    assert "mnemonic_mcp_binary_sha256" not in defaults


def test_tasks_set_fact_for_binary_discovery():
    tasks_root = _load_yaml(ROLE_DIR / "tasks" / "main.yml")
    tasks = list(_iter_tasks(tasks_root))

    # Find the set_fact task setting mnemonic_mcp_binary.
    set_fact_tasks = [
        t for t in tasks
        if isinstance(t.get("set_fact") or t.get("ansible.builtin.set_fact"), dict)
        and "mnemonic_mcp_binary" in (
            t.get("set_fact") or t.get("ansible.builtin.set_fact") or {}
        )
    ]
    assert set_fact_tasks, "expected a set_fact task defining mnemonic_mcp_binary"

    # Find the discovery command — must reference both binary spellings + use
    # failed_when on rc != 0.
    discovery_candidates = []
    for t in tasks:
        for key in ("command", "shell", "ansible.builtin.command",
                    "ansible.builtin.shell"):
            spec = t.get(key)
            if spec is None:
                continue
            text = spec if isinstance(spec, str) else json.dumps(spec)
            if "mnemonik-mcp" in text and "mnemonic-mcp" in text:
                discovery_candidates.append(t)
                break

    assert discovery_candidates, \
        "expected a discovery task referencing both mnemonik-mcp and mnemonic-mcp"
    discovery = discovery_candidates[0]
    failed_when = discovery.get("failed_when", "")
    assert "rc" in str(failed_when), \
        "discovery task must set failed_when on rc"


def test_no_sign_memory_anywhere():
    """sign-memory subcommand does not exist; defend against regression."""
    search_paths = [
        ROLE_DIR / "tasks",
        ROLE_DIR / "templates",
        ROLE_DIR / "defaults",
        ROLE_DIR / "handlers",
        ROLE_DIR / "README.md",
    ]
    hits: list[str] = []
    for path in search_paths:
        if not path.exists():
            continue
        if path.is_file():
            files = [path]
        else:
            files = [p for p in path.rglob("*") if p.is_file()]
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "sign-memory" in text:
                hits.append(str(f.relative_to(ROLE_DIR)))
    assert hits == [], f"`sign-memory` must not appear; found in: {hits}"


def test_no_systemd_unit_template_present():
    """MCP-stdio is a per-spawn subprocess pattern, not a daemon.

    A long-running systemd unit that runs `mnemonik-mcp mcp-stdio` immediately
    hits EOF on systemd's StandardInput=null and core-dumps (SIGTRAP). The
    binary is installed for spawn-per-attest use by content-publisher's
    mcp_client.py. Defense-in-depth: assert no service template is checked in.
    """
    leftover = ROLE_DIR / "templates" / "mnemonic-mcp.service.j2"
    assert not leftover.exists(), (
        f"{leftover.relative_to(ROLE_DIR)} must not exist — the role no longer "
        "registers a systemd daemon (per-spawn pattern, see tasks/main.yml header)"
    )


def test_tasks_remove_legacy_systemd_unit():
    """The role must idempotently delete any legacy mnemonic-mcp.service on the VM.

    Regression guard: a deploy onto an old VM where the previous role had
    enabled the daemon must clean it up; otherwise systemd keeps trying to
    start a failed unit on every reboot.
    """
    tasks_root = _load_yaml(ROLE_DIR / "tasks" / "main.yml")
    tasks = list(_iter_tasks(tasks_root))
    removers = []
    for t in tasks:
        spec = t.get("file") or t.get("ansible.builtin.file") or {}
        if (
            isinstance(spec, dict)
            and spec.get("path") == "/etc/systemd/system/mnemonic-mcp.service"
            and spec.get("state") == "absent"
        ):
            removers.append(t)
    assert removers, (
        "expected a `file: state=absent` task removing the legacy "
        "/etc/systemd/system/mnemonic-mcp.service"
    )


def test_package_lock_checked_in_and_valid_json():
    lock_path = ROLE_DIR / "files" / "package-lock.json"
    assert lock_path.exists(), "files/package-lock.json must be checked in"
    data = json.loads(lock_path.read_text())
    lock_version = data.get("lockfileVersion")
    assert isinstance(lock_version, int) and lock_version >= 2, \
        f"lockfileVersion must be >=2, got {lock_version!r}"

    blob = json.dumps(data)
    assert "@mnemonik-xyz/mcp" in blob, \
        "lockfile must reference @mnemonik-xyz/mcp"


def test_descoped_artifacts_removed():
    must_not_exist = [
        ROLE_DIR / "templates" / "config.yml.j2",
        ROLE_DIR / "templates" / "protocol-qa.env.j2",
        ROLE_DIR / "templates" / "molyanov-mnemonic-hooks.yml.j2",
        ROLE_DIR / "templates" / "hooks",
        # Daemon design retired post-deploy 2026-06-11: MCP-stdio is
        # per-spawn, not a long-running service. Unit must not return.
        ROLE_DIR / "templates" / "mnemonic-mcp.service.j2",
    ]
    leftovers = [str(p.relative_to(ROLE_DIR)) for p in must_not_exist if p.exists()]
    assert leftovers == [], f"descoped artifacts still present: {leftovers}"


@pytest.mark.skipif(
    subprocess.run(["which", "ansible-lint"], capture_output=True).returncode != 0,
    reason="ansible-lint not installed",
)
def test_ansible_lint_clean():
    # cwd=ROLE_DIR so the role-local `.ansible-lint` config is picked up
    # (it skips two pre-existing structural items documented in the file).
    result = subprocess.run(
        ["ansible-lint", "."],
        capture_output=True,
        text=True,
        cwd=ROLE_DIR,
    )
    assert result.returncode == 0, (
        f"ansible-lint failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


@pytest.mark.skipif(
    subprocess.run(["which", "ansible-playbook"], capture_output=True).returncode != 0,
    reason="ansible-playbook not installed",
)
def test_ansible_syntax_check():
    env = {
        **__import__("os").environ,
        "ANSIBLE_ROLES_PATH": str(ANSIBLE_DIR / "roles"),
    }
    result = subprocess.run(
        ["ansible-playbook", "--syntax-check", str(PLAYBOOK)],
        capture_output=True,
        text=True,
        cwd=ANSIBLE_DIR,
        env=env,
    )
    assert result.returncode == 0, (
        f"syntax-check failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
