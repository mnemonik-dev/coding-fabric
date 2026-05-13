# Ansible Role: molyanov

Install molyanov-ai-dev slash commands and Project Knowledge guard hook for coding-fabric.

## Responsibilities

1. **molyanov-ai-dev skills bundle**: Clone `pavel-molyanov/molyanov-ai-dev` (MIT) at pinned tag (v0.3.0) to `/opt/molyanov-ai-dev`, then symlink `skills/` to `~/.claude/skills/molyanov-ai-dev` so Claude Code discovers the bundle.
2. **Global configuration**: Render `~/.fabric/molyanov/global.yml` with defaults and references
3. **PK guard pre-write hook**: Install `~/.fabric/molyanov/hooks/pk-guard.sh` (mode 0755)
4. **Ops-notify wrapper**: Install `~/.fabric/molyanov/hooks/ops-notify.sh` (mode 0755)
5. **ruflo integration**: Configure `~/.fabric/ruflo/hooks.d/molyanov-pk-guard.yml` for hook loading

## Project Knowledge Guard Mechanism

### Scope (fail-closed)

The `pk-guard.sh` hook **always rejects** writes that target paths matching
`.claude/skills/project-knowledge/references/`, regardless of whether
`RUFLO_SESSION` is set. This is fail-closed by design (audit Finding F-005 in
T21 security-audit): the previous version gated enforcement on
`RUFLO_SESSION`, which an agent could trivially bypass by unsetting the env
var before invoking the hook.

The guard returns immediately (exit 0) for any path that does NOT touch the PK
references tree, so it is safe to install as a generic pre-write hook.

### Enforcement

When a write targets any path matching `.claude/skills/project-knowledge/references/`:

1. **Reject**: Exit code 1, blocking the write.
2. **Log**: Message to systemd journal via `logger -t molyanov-pk-guard` (sanitized by Task 07 filter).
3. **Alert**: POST to Telegram ops-topic via bot token (plain text — no `parse_mode=HTML` per audit F-006).

### Operator Bypass Procedure

The only way to allow a PK-references write is to set `PK_GUARD_BYPASS=1` in
the invoking shell. This bypass is intended for deliberate operator-interactive
sessions only (never agents, never CI). Every bypass is logged to
`auth.notice` and an ops-topic Telegram message records the bypass for
post-hoc audit.

```bash
# Operator-interactive bypass (deliberate write to PK references).
PK_GUARD_BYPASS=1 vim ~/code/<feature>/.claude/skills/project-knowledge/references/architecture.md
```

Do NOT export `PK_GUARD_BYPASS` for an entire shell session; scope it to a
single command so the guard re-engages immediately after.

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
# From test worktree (no RUFLO_SESSION needed — guard is fail-closed)
cd ~/code/mnemonic-workspaces/test-task-1
ruflo memory write .claude/skills/project-knowledge/references/architecture.md "test" 2>&1
# Should exit non-zero; ops-topic receives alert.

# Operator bypass (deliberate write):
PK_GUARD_BYPASS=1 ruflo memory write .claude/skills/project-knowledge/references/test.md "test"
# Should succeed; operator-bypass log emitted to journal + ops-topic.
```

## Cross-Role Coupling

This role creates a configuration file that ruflo loads:

- **Producer** (molyanov): `~/.fabric/ruflo/hooks.d/molyanov-pk-guard.yml`
- **Consumer** (ruflo): Loads all YAML in `hooks.d/` at startup

This coupling is documented in both roles and minimizes inter-role assumptions.
