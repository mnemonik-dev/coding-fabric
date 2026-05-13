# fabric/safe-mode — Incident Recovery & Smoke Testing

Safe-mode is the fabric's resilience layer: automatic stability checkpoint tagging, incident playbooks, and pre-merge smoke testing.

## Components

### 1. last-known-good tag (last-known-good.sh)

Post-success hook invoked by `do-task` after a non-loop task completes.

- Tags `mnemonic-loop` HEAD as annotated `last-known-good` (signed by Mnemonic MCP if available)
- Pushes tag to remote
- Skips if task branch is `mnemonic-loop` itself (loop progress is not a stable checkpoint)
- Idempotent: if HEAD already tagged, silently skips
- Lock-protected to prevent concurrent tagging

**Usage:**
```bash
MNEMONIC_TASK_BRANCH=some-feature MNEMONIC_REPO_PATH=/path/to/mnemonic-loop \
  ./fabric/safe-mode/last-known-good.sh
```

### 2. safe-mode-rollback Ansible playbook

Invoked over SSH when operators detect fabric degradation or need to recover from a failed loop iteration.

**Actions:**
1. Stop all fabric services (workspace-manager, mnemonic-mcp, fabric-watchdog, telegram-ai-agent)
2. Acquire file lock at `/var/lock/fabric-safe-mode` (prevent concurrent rollbacks)
3. Checkout `last-known-good` tag in the `mnemonic-loop` master clone on VM
4. Re-run only `deploy.yml` for `fabric-services` role (skip base, tailscale, vaultwarden, kaneo, OpenTofu)
5. Post ops banner to Telegram

On error: abort immediately, issue high-severity ops alert, leave nothing half-stopped.

**Usage:**
```bash
ansible-playbook -i infrastructure/ansible/inventory/hosts.yml \
  infrastructure/ansible/playbooks/safe-mode-rollback.yml
```

### 3. smoke-gate CI workflow

GitHub Actions workflow (`.github/workflows/smoke-gate.yml`) required check for PRs touching `mnemonic-loop`.

**Flow:**
1. Triggered on: push to `mnemonic-loop` branches or PR with `mnemonic-loop` label
2. Spins up ephemeral docker-compose-based candidate fabric
3. Runs trivial docs-task end-to-end: "Write a test plan for X"
4. Hard timeout: 15 min
5. Blocks merge on failure

Reuses same docs-task as T13 e2e-smoke (single source of truth).

**Candidate fabric layout:**
```
fabric/safe-mode/smoke-gate/
  Dockerfile          — minimal fabric image
  docker-compose.yml  — ephemeral services (workspace-manager, mcp, etc.)
  run.sh              — orchestrates candidate smoke test
```

## Testing

```bash
# Unit tests for last-known-good.sh
bash fabric/safe-mode/tests/test_lkg.sh

# Lint workflow syntax
actionlint .github/workflows/smoke-gate.yml

# Lint Ansible playbook (if ansible-lint available)
ansible-lint infrastructure/ansible/playbooks/safe-mode-rollback.yml
```

## Edge Cases

**Concurrent rollback:** File lock at `/var/lock/fabric-safe-mode` ensures only one rollback proceeds at a time.

**Rollback failure:** Abort; high-severity ops alert via Telegram; don't leave services half-stopped.

**Smoke-gate CI cache stale:** Invalidate on changes to `fabric/init/`, `fabric/Dockerfile`, docker-compose templates.

**Telegram sanitizer:** All POSTs through curl + sed pipeline to escape special characters.

## Integration Points

- `do-task` hook: calls `last-known-good.sh` on success
- Ops runbooks: invoke `safe-mode-rollback.yml` on incident
- mnemonic-loop PRs: gated by `smoke-gate` workflow
- Mnemonic MCP: signs `last-known-good` tags (if available)
