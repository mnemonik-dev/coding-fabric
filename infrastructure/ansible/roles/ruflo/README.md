# Ansible Role: ruflo

Install ruflo full CLI and render per-topic configuration matrix implementing the cohabitation toggles (autopilot, aidefence, rag-memory) and Mnemonic namespace bindings from tech-spec §2.4.

## Purpose

- Install ruflo at a pinned upstream version via the official installer
- Render global config to `~/.fabric/ruflo/global.yml`
- Generate 8 per-topic configs in `~/.fabric/ruflo/topics/{topic}.yml` from the canonical matrix variable
- Install and invoke `validate_matrix.py` to assert on-disk state matches expected configuration
- Enforce idempotency: re-render only on variable change; validation runs every apply

## Variables

### Main variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ruflo_version` | string | `0.20.0` | Pinned upstream version of ruflo CLI |
| `ruflo_topic_matrix` | list | See defaults/main.yml | Canonical per-topic configuration matrix (8 topics × 7 fields) |
| `ruflo_global_defaults` | dict | See defaults/main.yml | Global defaults applied to all topics |
| `ansible_user` | string | Required | OS user for installation (typically 'mnemonic') |

### Matrix structure (per topic)

Each entry in `ruflo_topic_matrix` is a dict with these fields:

| Field | Type | Values | Purpose |
|-------|------|--------|---------|
| `topic` | string | core, mcp, wasm, demo-client, docs, loop, protocol-qa, ops | Topic identifier |
| `engine` | string | claude-code, codex | Which CLI engine to invoke (AC8, AC11 from user-spec) |
| `autopilot` | string | "on", "off" | Enable ruflo autopilot (AC25 from user-spec) |
| `aidefence` | string | "on", "off" | Enable ruflo aidefence (AC27 from user-spec) |
| `rag_memory` | string | "on", "off" | Enable ruflo-rag-memory (AC26 from user-spec) |
| `mnemonic_mode` | string | "local", "full" | Mnemonic attestation mode |
| `memory_namespace` | string | `mnemonic-{topic}` | Namespace for Mnemonic memory store |

## Behavior

### Idempotency

- Global and per-topic templates are re-rendered on every run (via Jinja2)
- Change detection is automatic: if `ruflo_topic_matrix` changes, only affected files are updated
- Handlers are notified to re-run validation on any config file change
- Validation script (`validate_matrix.py`) runs on every apply regardless

### Installation

1. Downloads the official ruflo installer script
2. Sets `RUFLO_VERSION` environment variable to the pinned version
3. Executes installer (no logging of installer output if it contains secrets)
4. Verifies installation via `ruflo --version` (must match pinned version)

### Validation

The role installs `validate_matrix.py` as a healthcheck script. This script:

- Reads all 8 per-topic config files from `~/.fabric/ruflo/topics/`
- Compares each against the canonical matrix in its `CANONICAL_MATRIX` constant
- Asserts all 7 fields match exactly for each topic
- Exits 0 on success; non-zero and prints details on mismatch

## Files Created

```
~/.fabric/ruflo/
  global.yml                 # Global defaults
  topics/
    core.yml                 # Per-topic configs (8 total)
    mcp.yml
    wasm.yml
    demo-client.yml
    docs.yml
    loop.yml
    protocol-qa.yml
    ops.yml
  validate_matrix.py         # Matrix validation script (healthcheck)
```

## Specifications

- Idempotent: yes
- Ansible-lint clean: yes (FQCN modules, no deprecated syntax)
- Variable prefix: `ruflo_*`
- Handlers: validation on config change
- Matrix source: tech-spec §2.4, anchors user-spec AC25–AC28

## Acceptance Criteria (Task 10)

- [x] Role idempotent (templates re-render on var change, validator runs every run)
- [x] All 8 per-topic config files present and matching §2.4 exactly
- [x] `ruflo config show --topic <each>` reflects expected flags (verified by validator)
- [x] `validate_matrix.py` exits 0
- [x] `ansible-lint` passes (no deprecated modules, FQCN only)

## Verification Steps

### Automated (in molecule)

```bash
molecule test -s ruflo
```

### Smoke (after apply to real host)

```bash
# Verify all 8 topics render correctly
for t in core mcp wasm demo-client docs loop protocol-qa ops; do
  echo "=== Topic: $t ==="
  cat ~/.fabric/ruflo/topics/$t.yml
done

# Run validator
python ~/.fabric/ruflo/validate_matrix.py

# Check installed ruflo version
ruflo --version
```

## References

- tech-spec §2.3 (role 7), §2.4 (matrix), §3 (D17, D18)
- user-spec AC25–AC28 (cohabitation toggles, namespace binding)
- Task 10 acceptance criteria
