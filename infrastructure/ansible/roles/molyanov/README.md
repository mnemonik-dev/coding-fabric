# Ansible Role: molyanov

Install molyanov-ai-dev slash commands and Project Knowledge guard hook for coding-fabric.

## Responsibilities

1. **molyanov-ai-dev skills bundle**: Clone to `~/.claude/skills/` at pinned version (v0.1.0)
2. **Global configuration**: Render `~/.fabric/molyanov/global.yml` with defaults and references
3. **PK guard pre-write hook**: Install `~/.fabric/molyanov/hooks/pk-guard.sh` (mode 0755)
4. **Ops-notify wrapper**: Install `~/.fabric/molyanov/hooks/ops-notify.sh` (mode 0755)
5. **ruflo integration**: Configure `~/.fabric/ruflo/hooks.d/molyanov-pk-guard.yml` for hook loading

## Project Knowledge Guard Mechanism

### Scope

The `pk-guard.sh` hook **only fires** when the `RUFLO_SESSION` environment variable is set (non-empty). This ensures:

- Does not fire on operator's manual edits (`git add`, `vim`)
- Does not fire on read-only operations (`git diff`, `cat`)
- Fires only within ruflo-invoked write contexts (workspace tasks)

### Enforcement

When `RUFLO_SESSION=<task-id>` is set and a write targets any path matching `.claude/skills/project-knowledge/references/`:

1. **Reject**: Exit code 1, blocking the write
2. **Log**: Message to systemd journal via `logger -t molyanov-pk-guard` (sanitized by Task 07 filter)
3. **Alert**: POST to Telegram ops-topic via bot token (if available)

### Bypass Procedure

**Emergency only** (requires operator verification):

```bash
env -i RUFLO_SESSION= <command that writes to PK refs>
```

Setting `RUFLO_SESSION=""` disables the guard for that invocation. All such bypasses are logged.

## Dependencies

- **base**: OS user, directories
- **tailscale**: Telegram bot connectivity (ops alerts)
- **ruflo**: Pre-write hook configuration loading (Task 10)
- **Task 07 sanitizer**: Log path filtering

## File Structure

```
roles/molyanov/
  tasks/main.yml              - Install bundle, render configs, install hooks, verify
  defaults/main.yml           - Operator user, repo URLs, patterns, sops keys
  handlers/main.yml           - Notification handlers
  files/
    pk-guard.sh              - Pre-write hook (bash, ~30 lines)
    ops-notify.sh            - Ops alert wrapper
  templates/
    global.yml.j2            - molyanov global config
    ruflo-hooks.d-molyanov-pk-guard.yml.j2  - Cross-role hook integration
  molecule/
    molecule.yml             - Docker test platform
    converge.yml             - Role application
    verify.yml               - Assertions + idempotency check
```

## Idempotency

- Skills bundle: `git clone` with `update: yes` and `force: yes`; same commit on each run
- Hooks: `copy` module; no changes if already installed
- Configs: `template` module; re-rendered only on variable changes
- Second run makes zero changes (verified by molecule)

## Acceptance Criteria

- [x] Role idempotent (molecule verify shows no changes on second run)
- [x] molyanov slash commands available under operator's CLI (skills bundle cloned)
- [x] `pk-guard.sh` is executable (mode 0755) and rejects writes to PK paths (verified by molecule verify.yml)
- [x] `ansible-lint` passes (no warnings, standard role layout)

## Testing

Run full test suite:

```bash
molecule test -s molyanov
```

Smoke test (operator verification):

```bash
# From test worktree
cd ~/code/mnemonic-workspaces/test-task-1
RUFLO_SESSION=test-task-1 ruflo memory write .claude/skills/project-knowledge/references/architecture.md "test" 2>&1
# Should exit non-zero; ops-topic receives alert

# Bypass (emergency):
env -i RUFLO_SESSION= bash -c 'echo "test" > .claude/skills/project-knowledge/references/test.md'
# Should succeed (emergency override logged)
```

## Cross-Role Coupling

This role creates a configuration file that ruflo loads:

- **Producer** (molyanov): `~/.fabric/ruflo/hooks.d/molyanov-pk-guard.yml`
- **Consumer** (ruflo): Loads all YAML in `hooks.d/` at startup

This coupling is documented in both roles and minimizes inter-role assumptions.
