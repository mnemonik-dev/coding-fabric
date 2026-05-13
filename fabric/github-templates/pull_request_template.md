## What changed

Description of the specific changes in this PR. Include:
- What problem does this solve?
- What files were modified?
- Any breaking changes?

**Example:**
```
Added new validator plugin for security audit delegation. Refactored
Ansible role `molyanov` to install security-audit runner via pip.
No breaking changes.
```

## Automated validation

Results of automated checks (linting, tests, type checking). Summarize:
- CI job status (passing/failing)
- Test coverage delta
- Lint warnings (if any)
- Performance impact (if any)

**Example:**
```
- actionlint: PASS
- unit tests: 24 pass, 0 fail
- type check (mypy): PASS
- coverage: 87% → 91% (+4%)
```

## Protocol conformance

Attestation and task-type classification. Include:
- Which task or feature does this advance? (reference task ID or feature name)
- Task metadata in YAML frontmatter (if task-type applicable):
  ```yaml
  task_type: byte-equivalence
  ```
- Links to upstream specs (user-spec AC, tech-spec D decision, task NN.md)
- Any QA gates or manual verification required?

**Example:**
```
Task 16 — PR conformance templates + workflows
References: tech-spec §4 Wave 5, user-spec AC24

task_type: mcp-compat

QA gates: None (this is template infrastructure itself)
```

## Mnemonic attestation

References to attestation DAG nodes (if applicable). Include:
- Solana tx signature(s) for pre-deploy or deploy phase
- Arweave tx ID(s) if off-chain storage used
- Link to attestation in `mnemonic_recall` memory
- Post-merge: tag updated? (last-known-good for non-loop PRs)

**Example:**
```
Pre-deploy attestation verified via mnemonic_recall:
- solana_tx: 3SzJk9d2xKfHi8nRtQ5vP7mL2aB9cDeF4gHjK6lMnOp
- arweave_tx: aR5wE8xQrT3yU1vSw2dFgHjKlMnOpQsT9uVwXyZaBcd1

(Post-merge: last-known-good tag auto-advances)
```
