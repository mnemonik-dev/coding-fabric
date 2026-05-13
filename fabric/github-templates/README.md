# GitHub PR Conformance Templates

This directory contains canonical PR templates and workflows deployed to all six Mnemonic Protocol master repositories by the `fabric-services` Ansible role at deploy time.

## Structure

```
github-templates/
  pull_request_template.md          # PR template: 4 sections + placeholders
  config.example.yml                # Sample vendored-path exclusion config
  composite-action/
    action.yml                      # Reusable composite action for per-repo workflows
  workflows/
    pr-conformance.yml              # Main workflow: runs on PR open/sync
```

## Deployment

The `fabric-services` Ansible role copies these files into each master repo's `.github/` directory during fabric bootstrap:

- `pull_request_template.md` → `<repo>/.github/pull_request_template.md`
- `workflows/pr-conformance.yml` → `<repo>/.github/workflows/pr-conformance.yml`
- `composite-action/action.yml` → `<repo>/.github/composite-action/pr-conformance/action.yml`
- `config.example.yml` → `<repo>/.github/pr-conformance.config.yml` (or skipped if already exists)

Per-repo workflows can reference the composite action as:
```yaml
uses: ./.github/composite-action/pr-conformance@v1
```

## PR Template Requirements (AC24)

Every PR body MUST contain exactly four section headings:

1. `## What changed`
   - Description of changes, problem solved, files modified
   
2. `## Automated validation`
   - CI job status, test coverage, lint results, performance impact

3. `## Protocol conformance`
   - Task reference (task ID or feature name), task_type classification, upstream spec links, QA gates

4. `## Mnemonic attestation`
   - Solana tx, Arweave tx, attestation links, tag updates (if applicable)

### Example PR Body

```markdown
## What changed

Added new validator plugin for security audit delegation. Refactored
Ansible role `molyanov` to install security-audit runner via pip.

## Automated validation

- actionlint: PASS
- unit tests: 24 pass, 0 fail
- type check: PASS
- coverage: 87% → 91%

## Protocol conformance

Task 16 — PR conformance templates

task_type: mcp-compat

QA gates: Manual verification in scratch repo

## Mnemonic attestation

Pre-deploy attestation:
- solana_tx: 3SzJk9d2xKfHi8nRtQ5vP7mL2aB9cDeF4gHjK6lMnOp
```

## Task Type Classification

When a PR is part of a conformance-gated task (T17 harnesses), the `## Protocol conformance` section MUST include a YAML block specifying the task type:

```yaml
task_type: <type>
```

### Valid task_type Values

| Value | Harness | Purpose |
|-------|---------|---------|
| `byte-equivalence` | harness-byte-equivalence.yml | Schema/serialization verification |
| `mcp-compat` | harness-mcp-compat.yml | MCP tool compatibility check |
| `wasm-browser` | harness-wasm-browser.yml | WASM + browser integration |
| `full-mode` | harness-full-mode.yml | End-to-end full-mode test |

If task_type is not specified, conformance checks still run but no harness is dispatched.

### Task Type Extraction Regex

The workflow extracts task_type using this pattern:

```bash
grep -i "task_type:" | head -1 | sed -E 's/^[[:space:]]*task_type:[[:space:]]*([a-zA-Z0-9_-]+).*/\1/'
```

This allows flexibility in formatting:
- `task_type: byte-equivalence` ✅
- `task_type:byte-equivalence` ✅
- `  task_type: mcp-compat` ✅ (leading whitespace ignored)
- `TASK_TYPE: full-mode` ✅ (case-insensitive)

## Diff Size Cap (500 lines)

The workflow enforces a maximum of 500 changed + deleted lines per PR (across all files).

### Excluding Paths

Create `.github/pr-conformance.config.yml` in each repo with vendored paths to exclude:

```yaml
vendored_paths:
  - "Cargo.lock"
  - "package-lock.json"
  - "target/"
  - "node_modules/"
  - "*.pb.rs"
```

See `config.example.yml` for a comprehensive template. Copy and customize per repository.

### Excluding Line Patterns

Patterns like `Co-Authored-By:` (commit trailers) are automatically excluded via:

```yaml
line_patterns:
  - "^Co-Authored-By:"
```

## Composite Action (`pr-conformance`)

### Inputs

| Input | Required | Description |
|-------|----------|-------------|
| `pr_body` | yes | Pull request body text |
| `base_ref` | yes | Base branch for git diff |
| `head_ref` | yes | Head branch for git diff |
| `config_path` | no | Path to pr-conformance.config.yml (default: `.github/pr-conformance.config.yml`) |

### Outputs

| Output | Type | Description |
|--------|------|-------------|
| `conforms` | boolean | Whether all template sections are present |
| `sections_missing` | string | Comma-separated missing sections (empty if all present) |
| `shortstat` | string | git diff --shortstat output |
| `diff_exceeds_cap` | boolean | Whether diff exceeds 500-line cap |
| `task_type` | string | Extracted task_type (empty if not specified) |

### Example Usage in Per-Repo Workflow

```yaml
name: "Conformance Check"
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: ./.github/composite-action/pr-conformance@v1
        id: check
        with:
          pr_body: ${{ github.event.pull_request.body }}
          base_ref: ${{ github.base_ref }}
          head_ref: ${{ github.head_ref }}

      - run: |
          echo "Conforms: ${{ steps.check.outputs.conforms }}"
          echo "Task type: ${{ steps.check.outputs.task_type }}"
```

## Workflow Behavior (`pr-conformance.yml`)

Triggered on PR `opened` and `synchronize` events.

### Job 1: `conformance`

1. Checks all four template sections are present
2. Computes `git diff --shortstat` excluding vendored paths
3. Asserts diff ≤ 500 lines (changed + deleted)
4. Extracts task_type via regex
5. Posts a single status comment (via `peter-evans/find-comment`)
6. **Fails** if any section missing or diff exceeds cap

Outputs: `conforms`, `task_type`

### Job 2: `dispatch-harness` (conditional)

Only runs if:
- `conformance` job passes
- `task_type` is not empty

Steps:
1. Validates task_type against allowed values
2. **Fails** with actionable error if task_type is invalid
3. Dispatches to `harness-<task_type>.yml` workflow with PR number and task_type

## Permissions

```yaml
permissions:
  contents: read
  pull-requests: write
  statuses: write
```

Minimal: read-only access to repo content, comment/status on PRs.

## Testing Strategy

### Manual Smoke Test (per-repo)

In a scratch repository, create test PRs:

1. **Missing section test**: Submit PR body missing `## Protocol conformance`
   - Expected: workflow fails, comment lists missing section
   
2. **Oversized PR test**: Add 600+ lines (e.g., add a data file)
   - Expected: workflow fails with "exceeds 500-line cap"

3. **Compliant PR test**: All four sections present, ≤500 lines
   - Expected: workflow passes, comment confirms

4. **Task type dispatch test**: Add `task_type: mcp-compat`
   - Expected: harness-mcp-compat.yml is dispatched

5. **Invalid task type test**: Add `task_type: unknown-harness`
   - Expected: dispatch job fails with clear error

## Diagnostics

### Check Composite Action

```bash
actionlint fabric/github-templates/composite-action/action.yml
```

### Validate Workflow

```bash
actionlint fabric/github-templates/workflows/pr-conformance.yml
```

### Local Diff Test (Simulate PR)

```bash
# Test with base=main, head=HEAD
git diff --shortstat main..HEAD

# Test excluding a path
git diff --shortstat main..HEAD -- ':!Cargo.lock'
```

## Molyanov Task-Creator Template Update

The `molyanov` task-creator command-line template (T11, T24) must be updated to emit the `task_type` frontmatter field. After this PR:

1. New tasks created via `/do-feature` will have a `task_type:` field in the frontmatter
2. That field is populated by the operator (or harness) based on the feature scope
3. When PR is opened, the conformance workflow extracts it and dispatches appropriately

**Note:** T16 itself does not create tasks with harness type; T17 harnesses define their own task creation. This update ensures future tasks can be automatically gated.

## Related Tasks

- **T17** — Conformance harnesses (byte-equivalence, mcp-compat, wasm-browser, full-mode)
- **T11** — Update molyanov task-creator template with task_type field
- **AC24** — User-spec requirement: 4-section PR template + 500-line cap
- **D16** — Tech-spec decision: PR template enforcement in per-repo workflows
