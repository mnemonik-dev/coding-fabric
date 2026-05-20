# Ansible Role: telegram-ai-agent

Install and configure `pavel-molyanov/telegram-ai-agent` (Python-based AI-powered Telegram bot) with support for per-message worktree isolation via the `cwd:"DYNAMIC"` sentinel feature.

**Role ID:** T09 (coding-fabric tech-spec §2.3, role 11)

## Overview

This role:
1. Installs `uv` package manager (pinned version)
2. Clones `telegram-ai-agent` from upstream (or fork during PR-pending phase)
3. Runs `uv sync --frozen` to install Python dependencies into an isolated venv
4. Renders 8 per-topic configurations with `cwd:"DYNAMIC"` sentinel
5. Configures environment variables (tokens, resolver URL)
6. Deploys systemd unit with security hardening
7. Validates the pinned commit contains the DYNAMIC feature
8. Performs post-deployment healthchecks

## Upstream Integration

### PR-Pending Phase (Current)

**Upstream PR:** `mnemonic-tg-bridge` feature (pending merge)

**Repository:** `https://github.com/mnemonic-org/telegram-ai-agent.git` (fork, with DYNAMIC patch)

**Pin:** `main` (update to specific commit SHA as PR matures)

The fork is temporary. After the upstream PR merges, update:
```yaml
telegram_ai_agent_repo: "https://github.com/pavel-molyanov/telegram-ai-agent.git"
telegram_ai_agent_pin: "v1.2.3"  # or upstream commit SHA after merge
```

### Post-PR-Merge Phase

Once `pavel-molyanov/telegram-ai-agent` merges the DYNAMIC feature:

1. Pin role defaults to upstream tag:
   ```yaml
   telegram_ai_agent_repo: "https://github.com/pavel-molyanov/telegram-ai-agent.git"
   telegram_ai_agent_pin: "v1.2.3"  # real tag from upstream
   ```

2. Remove fork reference from defaults

3. Re-apply role; idempotent rollout handles the transition

### Fallback Fork (30+ Day Timeout)

If PR stalls > 30 days without movement:
1. Feature `mnemonic-tg-bridge` maintains fork at `mnemonic-org/telegram-ai-agent`
2. Role pins to fork with permanent fallback tag (`v<upstream-base>+mnemonic.1`)
3. On upstream PR merge: role pins back to upstream tag

See: `work/mnemonic-tg-bridge/user-spec.md` §4.2–4.4 (fallback policy)

## Requirements

### Ansible
- Ansible ≥ 2.15
- Python ≥ 3.10 (on target host)

### System
- OS: Ubuntu 24.04 LTS (Jammy)
- Requires: `base`, `telegram-init`, `tailscale` roles deployed first
- User: `op` user with home directory (`base` role provides this)
- Network: Tailscale network active (for resolver URL binding)

### Upstream Dependencies
- `pavel-molyanov/telegram-ai-agent` (MIT license)
- Feature: `cwd:"DYNAMIC"` sentinel with HTTP resolver (from `mnemonic-tg-bridge` PR)

## Role Dependencies

```yaml
dependencies:
  - role: base              # Creates op user, system dirs
  - role: telegram-init     # Creates Telegram forum and 8 topics
  - role: tailscale         # Sets tailscale_ip for resolver URL
```

## Variables

### Default Variables (defaults/main.yml)

#### Upstream Pinning
```yaml
# Fork URL (PR-pending phase); switch to upstream after PR merge
telegram_ai_agent_repo: "https://github.com/mnemonic-org/telegram-ai-agent.git"

# Commit SHA or tag to pin (reproducible deployments)
telegram_ai_agent_pin: "main"  # Update to specific commit as PR progresses
```

#### Installation Paths
```yaml
telegram_ai_agent_home: "/opt/telegram-ai-agent"        # Cloned repo
telegram_ai_agent_config_dir: "/etc/telegram-ai-agent"   # Config & secrets
telegram_ai_agent_owner: "op"                            # OS user
telegram_ai_agent_group: "op"                            # OS group
```

#### uv Configuration
```yaml
telegram_ai_agent_uv_version: "0.5.3"       # Pinned for reproducibility
telegram_ai_agent_uv_sync_timeout: 300      # Seconds (first-run pulls many deps)
```

#### Feature Gating
```yaml
telegram_ai_agent_require_dynamic_feature: true  # Fail if DYNAMIC not in pinned commit
```

#### Topic Matrix (8 topics from tech-spec §2.4)
Each topic has:
- `chat_id`, `thread_id`: populated from `inventory/telegram-topics.yml` (T05 output)
- `engine`: `claude` (default) or `codex` (demo-client)
- `cwd`: always `"DYNAMIC"` (resolved at engine spawn via workspace-manager)

Note: per-topic ruflo feature toggles (autopilot/aidefence/rag_memory) and
MNEMONIC_MODE/memory_namespace fields were removed 2026-05-20 alongside the
ruflo role drop (see work/coding-fabric/decisions.md Round 3).

See defaults/main.yml for full per-topic config.

### Runtime Variables (Required)

These must be passed at playbook invocation (typically from sops decryption):

```yaml
# Telegram Bot Token (from infrastructure/secrets/secrets.sops.yml)
telegram_bot_token: ""  # SOPS-encrypted, must be decrypted before role execution

# Workspace-manager resolver URL (T08 dependency)
tailscale_ip: ""  # Set by tailscale role; resolver is http://{{ tailscale_ip }}:8080

# API Keys (from sops)
anthropic_api_key: ""         # Required (Claude engines)
openai_api_key: ""            # Optional (fallback)
deepgram_api_key: ""          # Optional (voice transcription)
```

### Inventory Dependency

The role requires `infrastructure/ansible/inventory/telegram-topics.yml` (generated by T05):

```yaml
telegram_ai_agent_topics:
  core:
    chat_id: "-1001234567890"
    thread_id: 1
  # ... 7 more topics
```

The role loads this at runtime and merges `chat_id`/`thread_id` into per-topic configs.

## Usage

### Basic Playbook

```yaml
---
- hosts: fabric_vms
  roles:
    - role: telegram-ai-agent
      vars:
        telegram_bot_token: "{{ vault_telegram_bot_token }}"
        anthropic_api_key: "{{ vault_anthropic_api_key }}"
        tailscale_ip: "{{ groups['fabric_vms'][0] | ansible_var }}"
```

### With SOPS Integration

```yaml
---
- hosts: fabric_vms
  pre_tasks:
    - name: Load sops secrets
      community.sops.load_vars:
        file: infrastructure/secrets/secrets.sops.yml
        name: sops_secrets

  roles:
    - role: telegram-ai-agent
      vars:
        telegram_bot_token: "{{ sops_secrets.telegram_bot_token }}"
        anthropic_api_key: "{{ sops_secrets.anthropic_api_key }}"
        deepgram_api_key: "{{ sops_secrets.get('deepgram_api_key', '') }}"
```

### Switching Between Fork and Upstream

**During PR (fork):**
```yaml
telegram_ai_agent_repo: "https://github.com/mnemonic-org/telegram-ai-agent.git"
telegram_ai_agent_pin: "cwd-dynamic-v1"
```

**After PR merge (upstream):**
```yaml
telegram_ai_agent_repo: "https://github.com/pavel-molyanov/telegram-ai-agent.git"
telegram_ai_agent_pin: "v1.2.3"
```

Role is idempotent; re-applying automatically handles git repo transitions.

## Files Generated

### Configuration
- `/etc/telegram-ai-agent/config.yml` — 8 per-topic configs with DYNAMIC cwd
- `/etc/telegram-ai-agent/.env` — environment variables (mode 0600)

### Installation
- `/opt/telegram-ai-agent/.venv/` — Python virtual environment
- `/opt/telegram-ai-agent/` — Cloned upstream repository

### Systemd
- `/etc/systemd/system/telegram-ai-agent.service` — Service unit

## Validation & Testing

### Pre-flight Checks

The role performs these checks before starting the service:

1. **DYNAMIC Feature Validation** (AC6 from coding-fabric)
   - Checks for `tests/test_dynamic_cwd.py` in pinned commit
   - Falls back to grepping for `"DYNAMIC"` in source
   - **FAILS LOUDLY** if DYNAMIC not found → points to `work/mnemonic-tg-bridge/user-spec.md`

2. **Config Validation**
   ```bash
   uv run --project /opt/telegram-ai-agent --check-config /etc/telegram-ai-agent/config.yml
   ```

3. **Environment Setup**
   - Verifies `.env` file (mode 0600)
   - Confirms resolver URL is set
   - Validates all 8 topics have chat_id/thread_id

### Smoke Tests

Manual verification after deployment:

```bash
# Service status
systemctl is-active telegram-ai-agent

# Service logs
journalctl -u telegram-ai-agent -f

# Config validation
/opt/telegram-ai-agent/.venv/bin/python -m telegram_bot \
  --config /etc/telegram-ai-agent/config.yml --check-config

# Verify DYNAMIC sentinels
grep 'cwd: "DYNAMIC"' /etc/telegram-ai-agent/config.yml | wc -l
# Should output: 8
```

### Integration Tests

From operator's phone (post-deployment on real VM):

1. Send `/ping` in `ops` topic → bot responds within 30s
2. Send message with code snippet in `docs` topic → workspace-manager creates worktree, engine spawns in DYNAMIC cwd
3. Verify: `curl http://{{ tailscale_ip }}:8080/worktrees` shows active worktree
4. After task completes → `DELETE /worktree/{task_id}` cleanup fires, worktree removed

See: coding-fabric tech-spec §3 D8, acceptance criteria AC6 (per-message worktree isolation)

## Idempotency

**Second run:** 0 changed (idempotent)

- Git clone with `update: yes` — skips if already at pinned commit
- uv sync — only runs if `uv.lock` changed
- Config templates — only re-rendered if vars changed; handlers reload only on change
- systemd daemon-reload — only fires if unit file changed

Example re-apply:
```bash
ansible-playbook playbooks/deploy.yml -i inventory/hosts.yml --tags telegram-ai-agent
# Should show: "changed=0"
```

## Security Considerations

### Secrets Management
- `.env` file: mode `0600`, owner `op` (readable only by service user)
- No secrets logged (tasks with `no_log: true`)
- Tokens not echoed in task names

### systemd Hardening
- `NoNewPrivileges=true` — prevent privilege escalation
- `ProtectSystem=strict` — filesystem read-only except /etc, /opt
- `PrivateTmp=true` — isolated /tmp
- User `op` (non-root)

### Network
- Resolver URL is tailnet-only (`http://tailscale_ip:8080`)
- No public network binding
- Assumes trust-by-network (loopback/tailnet)

### DYNAMIC Feature Gate
- Pre-flight check confirms pinned commit has DYNAMIC support
- Prevents accidental deployment of upstream-without-patch
- Fails loudly with actionable error message

## Cross-Role Dependencies

### Upstream T05 (telegram-init)
- Generates `inventory/telegram-topics.yml` with chat_id/thread_id for each topic
- Role loads this at runtime to configure per-topic chat/thread binding

### Upstream T08 (workspace-manager)
- Runs on port 8080 (tailnet-bound)
- Implements `/worktree` POST/DELETE API for DYNAMIC cwd resolver
- Role points to it via `TELEGRAM_AI_AGENT_CWD_RESOLVER_URL=http://{{ tailscale_ip }}:8080`

### Upstream T02 (base, tailscale)
- `op` user and home dir
- `tailscale_ip` variable
- Tailnet active and accessible

## Troubleshooting

### Service fails to start
```bash
journalctl -u telegram-ai-agent -n 50 -e
systemctl status telegram-ai-agent --full
```

### DYNAMIC feature not found in pinned commit
```bash
# Check current pin
grep "telegram_ai_agent_pin" infrastructure/ansible/roles/telegram-ai-agent/defaults/main.yml

# If on fork: ensure mnemonic-org/telegram-ai-agent is the repo
# If PR merged: update to upstream and retag

# See: work/mnemonic-tg-bridge/user-spec.md for fallback policy
```

### Resolver URL not set
```bash
# Ensure tailscale role ran first
systemctl status tailscaled
tailscale status

# Check config
grep "TELEGRAM_AI_AGENT_CWD_RESOLVER_URL" /etc/telegram-ai-agent/.env
```

### Config validation fails
```bash
# Check config syntax
/opt/telegram-ai-agent/.venv/bin/python -m yaml /etc/telegram-ai-agent/config.yml

# Check topic matrix matches schema
grep "chat_id" /etc/telegram-ai-agent/config.yml | wc -l
# Should output: 8
```

## Maintenance

### Updating Pinned Version

When upstream releases a new tag:

```yaml
# In defaults/main.yml
telegram_ai_agent_pin: "v1.2.4"
```

Re-apply role:
```bash
ansible-playbook playbooks/deploy.yml -i inventory/hosts.yml --tags telegram-ai-agent
```

Idempotency ensures smooth rollout.

### Updating Fork Repo URL

If fallback fork is created during PR stall:

```yaml
telegram_ai_agent_repo: "https://github.com/mnemonic-org/telegram-ai-agent.git"
telegram_ai_agent_pin: "v0.8.0+mnemonic.1"
```

### Reverting to Upstream

Once PR merges:

```yaml
telegram_ai_agent_repo: "https://github.com/pavel-molyanov/telegram-ai-agent.git"
telegram_ai_agent_pin: "v1.2.3"  # Upstream tag
```

## Testing

### Molecule Tests
```bash
cd infrastructure/ansible/roles/telegram-ai-agent
molecule test
```

### Ansible Lint
```bash
ansible-lint infrastructure/ansible/roles/telegram-ai-agent
```

### Manual Smoke
```bash
systemctl is-active telegram-ai-agent
curl -s http://localhost:8080/health  # workspace-manager health
```

## References

- **Feature Specification:** `work/mnemonic-tg-bridge/user-spec.md`
- **Tech Spec:** `work/coding-fabric/tech-spec.md` §2.3 (role 11), §2.4 (matrix)
- **User Spec:** `work/coding-fabric/user-spec.md` AC6, AC8
- **Task Spec:** `work/coding-fabric/tasks/09.md`
- **Decision Log:** `work/coding-fabric/tech-spec.md` D8

## License

This role deploys `pavel-molyanov/telegram-ai-agent` (MIT license).
Role code: GPLv3 (via Ansible).

## Author

- **Created:** 2026-05-12
- **Maintained by:** Mnemonic Protocol team
- **Status:** Approved (coding-fabric v0.2.0)
