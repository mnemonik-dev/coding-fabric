---
feature: coding-fabric
work_type: feature
size: L
status: approved
created: 2026-05-12
last_updated: 2026-05-12
branch: dev
source_user_spec: work/coding-fabric/user-spec.md
source_spec_md: spec.md (Mnemonic Protocol Agent-Driven Development Loop v0.1.1)
attestation: 9253b6c0-78ea-4127-9397-8b7875f791b3
solana_tx: 2bFxD4pWQZw87xnQcqVPqgTgfJ7VyLmTNiXbE8zCVpKPC61svZNcwYnTeNMiU3NtWheNdPV5fffHcF6iZkykBebf
arweave_tx: 7wd7o88htpgkubidTv5iYi4CDE8xQaY9cnX6Qwa6EfK1
---

# Tech Spec — Coding Fabric (Mnemonic Protocol Agent-Driven Development Loop)

## 1. Goal & Context

Stand up a single-VM, VPN-fronted autonomous coding fabric where one operator drives
all six Mnemonic-protocol repositories from Telegram. Every meaningful artefact
(user-spec, tech-spec, task, pre-deploy QA, deploy) is attested via the local
Mnemonic MCP server, forming a per-feature DAG (dog-food). Operational toil is
converted into Kaneo cards through the same fabric — no Slack-style side-channels.

This tech-spec elaborates `spec.md` (v0.1.1, finalized) and `work/coding-fabric/user-spec.md`.
The source spec is preserved verbatim as the design-of-record; this document re-states
it in implementation-ready form (component contracts, file layout, task waves,
validation plan).

## 2. Architecture

Four cooperating layers + one new glue component, all on a single cloud VM:

```
+--------------------------------------------------------------+
|                       Operator (Telegram)                     |
+--------------------------------------------------------------+
                           |
                           v
+--------------------------------------------------------------+
|  Transport: telegram-ai-agent                                 |
|  - 8 forum topics (core/mcp/wasm/demo-client/docs/loop/       |
|    protocol-qa/ops)                                           |
|  - cwd:"DYNAMIC" sentinel resolves via workspace-manager API  |
+--------------------------------------------------------------+
                           |
                           v
+--------------------------------------------------------------+
|  Glue (NEW): workspace-manager                                |
|  - HTTP API: POST /worktree, DELETE /worktree/{task_id},      |
|    GET /worktree/{task_id}, GET /worktrees                    |
|  - Creates ~/code/mnemonic-workspaces/<TASK-ID>               |
|  - Shared sccache; cap 10 active worktrees                    |
|  - Generates .env per worktree (from Vaultwarden); deletes    |
|    on cleanup                                                  |
+--------------------------------------------------------------+
                           |
          +----------------+----------------+
          v                v                v
+-------------------+ +----------+ +----------------------+
| Methodology:      | | Substrate| | Attestation:         |
| molyanov-ai-dev   | | ruflo    | | Local Mnemonic MCP   |
| - user-spec       | | (full)   | | - 5 trigger points   |
| - tech-spec       | | - AgentDB| | - DAG per feature    |
| - tasks           | | - SONA   | | - Sign at: user-spec |
| - TDD             | | - swarm  | |   tech-spec, task,   |
| - validators      | | - hooks  | |   pre-deploy-qa,     |
|   (delegated to   | | - plugins| |   deploy             |
|    ruflo plugins) | | - obs.   | +----------------------+
+-------------------+ +----------+
          |                |
          +-------+--------+
                  v
        +------------------+
        | CLI engine       |
        | (Claude Code     |
        |  default,        |
        |  Codex for       |
        |  demo-client)    |
        +------------------+
                  |
                  v
+--------------------------------------------------------------+
|  Operational plane                                            |
|  - Kaneo (self-hosted, task tracker)                          |
|  - Vaultwarden (secrets)                                      |
|  - fabric-watchdog (5-min cadence, 9 alert classes)           |
|  - last-known-good git tag (auto-updated)                     |
|  - fabric-safe-mode rollback script                           |
|  - smoke-gate (15-min docs task) for mnemonic-loop PRs        |
+--------------------------------------------------------------+
```

### 2.1 Components

| Component | Owner | Type | Notes |
|-----------|-------|------|-------|
| `workspace-manager` | NEW (this tech-spec) | Go or Python HTTP service, systemd unit | Single source of truth for active worktrees; lifecycle hooks for .env materialization and sccache mount |
| `telegram-ai-agent` | Existing OSS, patched | Service | Patch: `cwd:"DYNAMIC"` sentinel → HTTP call to workspace-manager |
| `molyanov-ai-dev` | Existing OSS, installed | Slash-commands + skills | Project Knowledge is canonical; validators delegate via wrapper to ruflo plugins |
| `ruflo` | Existing OSS, installed | CLI + plugins + AgentDB | Per-topic toggles via config |
| `mnemonic-mcp` (local) | Existing (this protocol) | MCP server | Signs at 5 trigger points; mode `local` for most topics, `full` for `protocol-qa` |
| `fabric-watchdog` | NEW (this tech-spec) | Python script under systemd timer (5 min) | Posts to `ops` topic via Telegram bot API |
| `fabric-safe-mode` | NEW (this tech-spec) | Bash script invoked over SSH | Rollback to `last-known-good`, restart services, notify ops |
| `smoke-gate` | NEW (this tech-spec) | CI job invoked from mnemonic-loop PR pipeline | Trivial docs end-to-end task on candidate fabric, 15-min budget |
| Kaneo | Existing OSS | Self-hosted | Task tracker; `/turn-into-task` creates cards |
| Vaultwarden | Existing OSS | Self-hosted | Source of `.env` material; never read directly by agents |

### 2.2 Shared Resources

| Resource | Owner | Consumers | Instance count | Notes |
|----------|-------|-----------|----------------|-------|
| Shared sccache | workspace-manager | All worktrees | 1 (shared cache dir) | Avoid per-worktree duplication; mount read-write into each worktree |
| Master repo clones | Phase 0 admin | workspace-manager (for worktree base) | 1 per repo (6 total) | `~/code/mnemonic-masters/{repo}` |
| Vaultwarden vault | Phase 0 admin | workspace-manager (for .env), watchdog (for endpoints) | 1 | Agents never read directly |
| Telegram bot token | Phase 0 admin | telegram-ai-agent, fabric-watchdog | 1 | Single bot, 8 topics |
| Solana devnet wallet | Phase 0 admin | protocol-qa topic only | 1 | No mainnet key on VM |
| Irys testnet account | Phase 0 admin | protocol-qa topic only | 1 | Funded; balance checked by watchdog |
| Mnemonic MCP signing key | Phase 0 admin | mnemonic-mcp local server | 1 | On VM only |

### 2.3 File layout (VM)

```
~/code/
  mnemonic-masters/           # canonical clones (Phase 0)
    mnemonic-core/
    mnemonic-mcp/
    mnemonic-wasm/
    mnemonic-demo-client/
    mnemonic-docs/
    mnemonic-loop/
  mnemonic-workspaces/        # per-task worktrees (managed)
    <TASK-ID>/
      .env                    # generated per worktree, deleted on cleanup
      ...repo files...
~/.fabric/
  workspace-manager/          # service + state
    state.json
    config.yml
  watchdog/
    config.yml
    last-run.json
  safe-mode/
    rollback.sh
  sccache/                    # shared cache
  logs/
    sanitized/                # filter applied (base58/JWK/sk-/api_/ANTHROPIC_/tg-urls stripped)
```

### 2.4 Per-topic configuration matrix

| Topic | Engine | autopilot | aidefence | rag-memory | MNEMONIC_MODE | MEMORY_NAMESPACE |
|-------|--------|-----------|-----------|------------|---------------|------------------|
| core | Claude Code | off | off | on | local | `mnemonic-core` |
| mcp | Claude Code | off | off | on | local | `mnemonic-mcp` |
| wasm | Claude Code | off | on | on | local | `mnemonic-wasm` |
| demo-client | Codex | on | on | on | local | `mnemonic-demo-client` |
| docs | Claude Code | on | on | on | local | `mnemonic-docs` |
| loop | Claude Code | off | on | on | local | `mnemonic-loop` |
| protocol-qa | Claude Code | off | on | off | full | `mnemonic-qa` |
| ops | Claude Code | off | on | on | local | `mnemonic-ops` |

### 2.5 workspace-manager HTTP API contract

```
POST /worktree
  body: { task_id: str, repo: str, base_ref: str, topic: str }
  effect: create ~/code/mnemonic-workspaces/<task_id>, materialize .env from Vaultwarden,
          mount sccache, register in state.json, return cwd
  response: 200 { cwd: "/home/op/code/mnemonic-workspaces/<task_id>" }
  errors: 409 capacity (>=10 active), 404 repo unknown, 500 vault

DELETE /worktree/{task_id}
  effect: remove .env, run git worktree remove, deregister
  response: 204

GET /worktree/{task_id}
  response: 200 { cwd, created_at, repo, topic, state }

GET /worktrees
  response: 200 { active: [...], capacity: 10 }

GET /health
  response: 200 { ok: bool, sccache_mounted, vault_reachable, capacity_used }
```

`telegram-ai-agent` resolves `cwd:"DYNAMIC"` by calling `POST /worktree` with task_id derived
from the Kaneo card or the Telegram message thread.

### 2.6 Mnemonic attestation DAG

Per feature, 5 nodes:

```
user-spec (memory)
   |
   v
tech-spec (memory, parent=user-spec)
   |
   v
task-N (receipt, parent=tech-spec)  [one per task]
   |
   v
pre-deploy-qa (receipt, parent=task receipts list)
   |
   v
deploy + post-deploy (agent.state, parent=pre-deploy)
```

`mnemonic-mcp` local server is called at five hook points:
- `user-spec-planning` Phase 9 (approve) → `mnemonic_sign_memory`
- `tech-spec-planning` Phase 6 (approve) → `mnemonic_sign_memory`
- `do-task` completion → `mnemonic_sign_memory` (receipt type)
- `pre-deploy-qa` pass → `mnemonic_sign_memory` (receipt type)
- `post-deploy-qa` pass → `mnemonic_sign_memory` (agent.state type)

### 2.7 Sanitized logging filter

Single Python middleware applied to all log streams before writing to disk and before
posting to Telegram:

```
patterns_to_strip = [
  r"[1-9A-HJ-NP-Za-km-z]{32,44}",  # base58 keys (Solana sig/pubkey)
  r"\"(kty|crv|x|y|d)\":",          # JWK fragment markers
  r"\b(sk-|api_|ANTHROPIC_)[A-Za-z0-9_-]+\b",
  r"https://api\.telegram\.org/file/[^\s]+",
]
```

Replacement: `***REDACTED***`. Each log line is filtered exactly once. Unit tests assert
zero leakage for known secret formats (Phase 0).

## 3. Decisions

Each decision references a user-spec AC unless marked `[TECHNICAL]`.

| # | Decision | Justification | Anchors |
|---|----------|---------------|---------|
| D1 | Single cloud VM, VPN-fronted | Single-operator scope; HA out of scope v0.1 | AC1–AC3 (Telegram everywhere), §6 Manual setup |
| D2 | `workspace-manager` is NEW Python (FastAPI) service under systemd | Stack alignment with other Python tooling; FastAPI gives OpenAPI for telegram-ai-agent patch | AC4–AC7 (worktree lifecycle) |
| D3 | Capacity cap = 10 active worktrees, 50 GB disk budget | Matches spec.md "heavy" disk decision | AC5, AC32 |
| D4 | Disk-pressure thresholds 75/85/90% on 50 GB budget | spec.md §8 | AC32 |
| D5 | Engine choice per topic: Claude Code default, Codex for `demo-client` | spec.md §12 finalized | AC8 |
| D6 | `/do-feature` dispatches through ruflo swarm MCP (`swarm_init`, `agent_spawn`) with per-task cwd injection | spec.md §4 | AC9 |
| D7 | Validators delegated: skeptic→jujutsu, security-auditor→security-audit, post-deploy-qa→browser | spec.md §4 | AC10 |
| D8 | Five-point attestation lineage via local Mnemonic MCP | spec.md §5; dog-food protocol | AC11–AC16 |
| D9 | QA gates by task_type with byte-equivalence/third-party-verify/MCP-compat/WASM-browser/full-mode | spec.md §6 | AC17–AC22 |
| D10 | PR template enforces 4 sections, 500-line cap | spec.md §6 | AC24 |
| D11 | Per-topic feature toggles (autopilot/aidefence/rag-memory) per matrix in §2.4 | spec.md §7 | AC25–AC27 |
| D12 | Molyanov Project Knowledge is canonical; ruflo substrate must not overwrite | spec.md §7 | AC28 |
| D13 | `fabric-watchdog` Python systemd-timer service, 5-min cadence, 9 alert classes | spec.md §8 | AC29–AC32 |
| D14 | `/turn-into-task` is a Telegram bot command that POSTs to Kaneo HTTP API | spec.md §8 | AC31 |
| D15 | `last-known-good` git tag auto-updated after every successful non-loop task | spec.md §10 | AC34 |
| D16 | `mnemonic-loop` PRs gated by 15-min smoke gate (trivial docs task end-to-end on candidate fabric) | spec.md §10 | AC33, AC35 |
| D17 | `fabric-safe-mode` rollback script over SSH stops services, checkout last-known-good, rebuild, restart, notify ops | spec.md §10 | AC36 |
| D18 | Vaultwarden is sole secret store; per-worktree `.env` materialized by workspace-manager, deleted on cleanup | spec.md §9 | AC38 |
| D19 | Solana devnet only; no mainnet keypair on VM | spec.md §9 | AC39 |
| D20 | Sanitized logging filter strips base58/JWK/sk-/api_/ANTHROPIC_/tg-file-URLs | spec.md §9 | AC40 |
| D21 | Agent trust boundary: broad rights inside worktree; no write outside; no direct Vaultwarden; no push to main; no production deploy | spec.md §9 | AC41 |
| D22 | Nightly VM backup with at least one verified test restore during Phase 0 | spec.md §11 Phase 0 | AC42 |
| D23 | `ops` topic process tree is independent of workspace-manager/ruflo/watchdog | spec.md §10 | AC3 |
| D24 | `[TECHNICAL]` `workspace-manager` exposes `GET /health` for fabric-watchdog drift detection | Operational visibility — no AC mandates this directly | none |
| D25 | `[TECHNICAL]` `fabric-watchdog` uses systemd-timer (not cron) for journalctl traceability | Better debugging on VM | none |

## 4. Implementation Tasks

Total: 19 implementation tasks (3 audit + 3 final = 25 with mandatory waves). Exceeds the
soft cap of 15 — this is intentional for an L-sized 21-day infrastructure project covering
spec.md Phases 0–5. Splitting MVP (Waves 1–4) vs Extension (Waves 5–6) is possible if the
operator prefers; default is full-scope delivery as spec.md §11 prescribes.

### Wave 1 — Infrastructure foundation (spec.md Phase 0, d1–d3)

**T1.1 — Provision VM, VPN, and access controls**
- Description: Provision the cloud VM (≥ 8 vCPU, 32 GB RAM, 200 GB disk). Configure VPN gateway,
  add operator account, restrict SSH to VPN-only. Establish base OS hardening (firewall, auto-updates).
- Skill: `infrastructure-setup`
- Reviewers: `security-auditor`, `reliability-engineer`
- Verify-smoke: `ssh op@vm 'systemctl is-system-running'` returns `running`; `nmap -p 22 vm.public-ip`
  shows port closed from outside VPN.
- Verify-user: confirm Telegram phone reaches operator only through VPN tunnel; admin SSH works.
- Files to modify: `~/.fabric/infra/{cloud-init,wireguard,sshd_config}`
- Files to read: `spec.md` §9, §11 Phase 0; `user-spec.md` §6, AC41

**T1.2 — Vaultwarden + Kaneo + Telegram forum/bot + 8 topics + nightly backups**
- Description: Install Vaultwarden, Kaneo, Telegram bot, create forum with 8 topics. Seed
  Vaultwarden with required credentials. Configure nightly backup with test-restore.
- Skill: `infrastructure-setup`
- Reviewers: `security-auditor`, `reliability-engineer`
- Verify-smoke: `curl https://vault.vm/api/health` returns 200; `curl https://kaneo.vm/api/health` returns 200;
  Telegram `/start` from operator returns greeting; `backup-verify.sh` restores last snapshot to staging dir successfully.
- Verify-user: log into Vaultwarden, Kaneo via VPN; receive bot reply in each topic.
- Files to modify: `~/.fabric/infra/docker-compose.yml`, `~/.fabric/infra/backups/{cron,restore-test.sh}`
- Files to read: `user-spec.md` §4.1, §6, AC42; `spec.md` §11 Phase 0

**T1.3 — Master repo clones + sanitized logging filter**
- Description: Clone all six master repos to `~/code/mnemonic-masters/`. Implement the sanitized
  logging middleware (base58 / JWK / sk-/api_/ANTHROPIC_ / Telegram file URLs) and apply to all
  log streams. Unit-test against known secret samples.
- Skill: `python-alchemist`
- Reviewers: `security-auditor`, `code-reviewer`
- Verify-smoke: Run `pytest tests/test_sanitizer.py` — all known-secret samples replaced with
  `***REDACTED***`. `cat ~/.fabric/logs/sanitized/*.log | grep -E "(sk-|ANTHROPIC_|[1-9A-HJ-NP-Za-km-z]{32,})"` returns nothing.
- Files to modify: `~/.fabric/logs/sanitizer/{filter.py,tests/test_filter.py}`
- Files to read: `user-spec.md` AC40; `spec.md` §9

### Wave 2 — Workspace + Transport (spec.md Phase 1, d4–d6)

**T2.1 — workspace-manager HTTP service (worktree lifecycle, sccache, per-worktree .env)**
- Description: Build FastAPI service exposing the API contract in §2.5. Worktree create/destroy,
  capacity cap = 10, shared sccache mount, `.env` materialization from Vaultwarden and deletion
  on cleanup, state.json persistence. Systemd unit + healthcheck endpoint.
- Skill: `fastapi-expert`
- Reviewers: `security-auditor`, `reliability-engineer`, `code-reviewer`
- Verify-smoke: `curl -X POST http://localhost:8080/worktree -d '{"task_id":"TEST-1","repo":"mnemonic-docs","base_ref":"main","topic":"docs"}'` returns 200 with a `cwd` path; the path exists and contains the repo working tree and a `.env` file; `curl -X DELETE http://localhost:8080/worktree/TEST-1` returns 204 and removes the worktree and `.env`.
- Files to modify: `~/.fabric/workspace-manager/{main.py,worktree.py,vault.py,state.py,systemd/workspace-manager.service,tests/}`
- Files to read: tech-spec §2.5; `user-spec.md` AC4–AC7, AC38

**T2.2 — telegram-ai-agent DYNAMIC sentinel patch**
- Description: Patch telegram-ai-agent so that any topic config with `cwd: "DYNAMIC"` resolves
  the actual path via `POST /worktree` to workspace-manager. Cache the resolution per task lifecycle;
  release on task end.
- Skill: `python-alchemist`
- Reviewers: `code-reviewer`, `reliability-engineer`
- Verify-smoke: With workspace-manager running, send a Telegram message to a configured `DYNAMIC`-cwd
  topic; agent logs show the resolved path; engine is spawned with the correct cwd.
- Verify-user: send `pwd` request through the topic; the engine echoes the worktree path.
- Files to modify: `~/code/mnemonic-masters/telegram-ai-agent/agents/{config_loader.py,topic_runtime.py}`
- Files to read: tech-spec §2.5; `user-spec.md` AC6

### Wave 3 — Methodology, substrate, attestation (spec.md Phase 2, d7–d9)

**T3.1 — ruflo install + per-topic cohabitation toggles + MEMORY_NAMESPACE pinning**
- Description: Install ruflo (full CLI). Generate per-topic config files implementing the matrix
  in §2.4: autopilot/aidefence/rag-memory toggles and `MEMORY_NAMESPACE` per topic. Ensure
  ruflo-aidefence is off in `core`/`mcp` and ruflo-rag-memory is off in `protocol-qa`.
- Skill: `infrastructure-setup`
- Reviewers: `code-reviewer`, `security-auditor`
- Verify-smoke: `ruflo config show --topic core` reflects matrix; `ruflo plugin list --topic protocol-qa`
  shows rag-memory disabled.
- Files to modify: `~/.fabric/ruflo/{topics/*.yml,global.yml}`
- Files to read: tech-spec §2.4; `user-spec.md` AC25–AC28

**T3.2 — molyanov-ai-dev install + Project Knowledge canonical**
- Description: Install molyanov-ai-dev for all 8 topics. Pin Project Knowledge as the canonical
  source — add a pre-hook that aborts any ruflo write attempt to PK paths and posts to ops.
- Skill: `infrastructure-setup`
- Reviewers: `code-reviewer`
- Verify-smoke: `claude /init-project-knowledge` runs in a docs worktree; attempting `ruflo memory write`
  to PK path is rejected with non-zero exit and an ops-topic alert.
- Files to modify: `~/.fabric/molyanov/{global.yml,hooks/pk-guard.sh}`
- Files to read: `user-spec.md` AC28

**T3.3 — Local Mnemonic MCP server + 5 trigger-point wiring**
- Description: Install and configure local Mnemonic MCP server on the VM. Wire signing hooks at
  the five trigger points (user-spec approve, tech-spec approve, task complete, pre-deploy-qa pass,
  deploy/post-deploy verify). DAG parents follow §2.6. `protocol-qa` runs in `MNEMONIC_MODE=full`.
- Skill: `infrastructure-setup`
- Reviewers: `security-auditor`, `code-reviewer`
- Verify-smoke: Trigger each hook manually with a stub artefact; confirm a memory/receipt/agent.state
  is created with the expected parent chain via `mnemonic_recall` and `mnemonic_verify`.
- Files to modify: `~/.fabric/mnemonic/{config.yml,hooks/{user-spec.sh,tech-spec.sh,task-complete.sh,pre-deploy.sh,post-deploy.sh}}`
- Files to read: tech-spec §2.6; `user-spec.md` AC11–AC16

**T3.4 — End-to-end smoke test through full pipeline**
- Description: Drive one representative feature (e.g., a docs typo fix) through the entire
  pipeline from Telegram intent to merge. Confirm all five attestations are created and linked.
- Skill: `pre-deploy-qa`
- Reviewers: `reliability-engineer`
- Verify-smoke: After completion, `mnemonic_recall --feature=<id>` returns a 5-node DAG; the PR
  is merged in `mnemonic-docs`; the worktree is cleaned.
- Verify-user: confirm topic timeline shows phase transitions; cleanup happened.
- Files to modify: `~/.fabric/tests/e2e/smoke-feature.md`
- Files to read: tech-spec §2.6, §2.5; `user-spec.md` §8.3, AC1–AC16

### Wave 4 — Swarm bridge + delegation (spec.md Phase 3, d10–d13)

**T4.1 — /do-feature dispatches to ruflo swarm with per-task cwd injection**
- Description: Modify the `/do-feature` slash command so that it invokes ruflo swarm MCP tools
  (`swarm_init`, `agent_spawn`) with `cwd` resolved per task via workspace-manager. Each spawned
  agent receives an isolated worktree.
- Skill: `python-alchemist`
- Reviewers: `code-reviewer`, `reliability-engineer`
- Verify-smoke: `claude /do-feature smoke-test` from a configured topic spawns N agents whose
  process listings show distinct `--cwd=...mnemonic-workspaces/<TASK-ID>` arguments.
- Files to modify: `~/.fabric/molyanov/skills/feature-execution/SKILL.md` (or equivalent), `~/.fabric/integrations/swarm-bridge.py`
- Files to read: tech-spec §2.5; `user-spec.md` AC9

**T4.2 — Validator delegation to ruflo plugins**
- Description: Wire molyanov validators so that `skeptic` → ruflo `jujutsu`,
  `security-auditor` → ruflo `security-audit`, `post-deploy-qa` → ruflo `browser`. Validator
  reports remain in molyanov format; delegation is internal.
- Skill: `python-alchemist`
- Reviewers: `code-reviewer`, `security-auditor`
- Verify-smoke: Run each validator on a known-bad fixture; confirm the underlying ruflo plugin
  was invoked (stderr trace) and the validator report cites it.
- Files to modify: `~/.fabric/molyanov/validators/{skeptic_wrapper.py,security_auditor_wrapper.py,post_deploy_qa_wrapper.py}`
- Files to read: `user-spec.md` AC10

### Wave 5 — Conformance harnesses (spec.md Phase 4, d14–d16)

**T5.1 — task_type field + PR template enforcement**
- Description: Add `task_type` to task frontmatter (schema/cbor, crypto-primitive, mcp-surface,
  wasm-bindings, full-mode-integration, crypto-crate-bump, loop-fabric, other). Enforce four-section
  PR template (what changed, automated validation, protocol conformance, mnemonic attestation) and
  500-line cap via a CI check.
- Skill: `github-actions-pro`
- Reviewers: `code-reviewer`
- Verify-smoke: Open a PR exceeding 500 lines — CI fails with the right error. Open a PR missing
  one of the four sections — CI fails.
- Files to modify: `~/code/mnemonic-masters/.github/{pull_request_template.md,workflows/pr-conformance.yml}` (in each of the six repos)
- Files to read: `user-spec.md` AC24

**T5.2 — Byte-equivalence harness (schema/CBOR + WASM) + MCP compatibility + full-mode integration**
- Description: Implement four conformance harnesses runnable as gate jobs:
  byte-equivalence for schema/CBOR changes (Rust ↔ WASM ↔ TypeScript serializers produce identical
  bytes for a fixture corpus); WASM browser test that runs the WASM bindings in a headless browser
  and asserts byte-equivalence with the Rust reference; MCP client-compatibility test that runs a
  reference client against the server; full-mode integration test that runs a Solana devnet round-trip
  followed by independent-verifier recovery from Arweave testnet.
- Skill: `playwright-pro`
- Reviewers: `solidity-sage`, `cpp-master`, `security-auditor`, `code-reviewer`
- Verify-smoke: All four harnesses pass against `main` of each relevant repo. `harness-byte-equivalence`
  flags a deliberately mutated test fixture.
- Files to modify: `~/.fabric/harnesses/{byte-equivalence/,mcp-compat/,wasm-browser/,full-mode/}`
- Files to read: `user-spec.md` AC17–AC22; `spec.md` §6

### Wave 6 — Watchdog + safe-mode (spec.md Phase 5, d17–d21)

**T6.1 — fabric-watchdog (9 alert classes, 5-min cadence) + /turn-into-task**
- Description: Implement Python systemd-timer service running every 5 minutes. Checks all 9
  alert classes (orphaned worktrees, hung tmux, expired Solana RPC, exhausted Irys, stale ruflo
  swarms, disk pressure 75/85/90%, master clone drift, stale PRs, failed Mnemonic attestation,
  stale last-known-good tag). Posts to `ops` topic. Also implement `/turn-into-task` Telegram bot
  command that POSTs to Kaneo API to create a card, attaching alert context.
- Skill: `python-alchemist`
- Reviewers: `reliability-engineer`, `security-auditor`, `code-reviewer`
- Verify-smoke: For each alert class, inject a synthetic condition; watchdog posts an alert in
  ops within 5 minutes; `/turn-into-task <alert-id>` creates a Kaneo card.
- Files to modify: `~/.fabric/watchdog/{checks/*.py,scheduler.py,telegram.py,kaneo.py,systemd/}`
- Files to read: `user-spec.md` AC29–AC32, AC31

**T6.2 — last-known-good auto-update + fabric-safe-mode rollback + smoke gate**
- Description: After every successful non-loop task, advance the `last-known-good` git tag to
  current `mnemonic-loop` HEAD. Implement `fabric-safe-mode rollback` script (SSH-invoked) that
  stops services, checks out `last-known-good`, rebuilds, restarts, and posts to ops. Implement
  `smoke-gate` CI job for `mnemonic-loop` PRs: provisions a candidate fabric, runs a trivial docs
  task end-to-end with a 15-minute budget, blocks merge on failure.
- Skill: `github-actions-pro`
- Reviewers: `reliability-engineer`, `code-reviewer`
- Verify-smoke: Run `fabric-safe-mode rollback` against a deliberately broken state; services
  return; ops topic receives notification within 60 seconds. Open a `mnemonic-loop` PR that
  breaks fabric init — smoke-gate fails within 15 min and blocks merge.
- Files to modify: `~/.fabric/safe-mode/{rollback.sh,last-known-good.sh}`, `~/code/mnemonic-masters/mnemonic-loop/.github/workflows/smoke-gate.yml`
- Files to read: `user-spec.md` AC33–AC37

### Wave 7 — Audit (parallel; reviewers: none)

**T7.1 — Code audit**
- Description: Holistic code-quality review across workspace-manager, sanitizer, watchdog,
  swarm-bridge, validator wrappers, harnesses, safe-mode. Output: `logs/techspec/code-audit.json`.
- Skill: `code-reviewing`
- Reviewers: none

**T7.2 — Security audit**
- Description: OWASP Top 10 plus secret-handling specific checks across all new components,
  with emphasis on Vaultwarden interaction, .env materialization, sanitized logging, and
  agent trust boundaries. Output: `logs/techspec/security-audit.json`.
- Skill: `security-auditor`
- Reviewers: none

**T7.3 — Test audit**
- Description: Test quality and coverage analysis: unit tests for sanitizer; integration tests
  for workspace-manager API; end-to-end smoke; harness invocation tests; watchdog alert tests;
  safe-mode rollback dry-run. Output: `logs/techspec/test-audit.json`.
- Skill: `test-master`
- Reviewers: none

### Wave 8 — Final

**T8.1 — Pre-deploy QA**
- Description: Run full test suite. Verify every AC in `user-spec.md` (42 ACs) is covered by
  automation or documented manual verification. Produce JSON report.
- Skill: `pre-deploy-qa`
- Reviewers: none

**T8.2 — Deploy**
- Description: Apply the finalized fabric configuration to the VM via the same fabric (recursive
  self-hosting). Confirms `mnemonic-loop` can deploy itself. Triggers an attestation
  (agent.state, parent=pre-deploy).
- Skill: `deploy-pipeline`
- Reviewers: `reliability-engineer`, `security-auditor`

**T8.3 — Post-deploy verification (deliberate-break + first recursive self-merge)**
- Description: On the live VM: (a) perform a deliberate-break of a fabric subsystem and confirm
  `fabric-safe-mode` rollback succeeds and ops notification fires; (b) drive a real, non-trivial
  PR in `mnemonic-loop` through the smoke-gate and self-merge. Sign post-deploy attestation.
- Skill: `post-deploy-qa`
- Reviewers: none
- Verify-user: confirm Telegram topic shows merge and attestation; confirm `mnemonic_recall`
  shows the 5-node DAG and parent chain for this very feature.

## 5. Testing Strategy (size L)

**Unit tests** — sanitizer (T1.3), workspace-manager internals (T2.1), Vaultwarden client mocking,
validator wrappers (T4.2), watchdog checks (T6.1), safe-mode dry-run (T6.2).

**Integration tests** — workspace-manager API full lifecycle; telegram-ai-agent DYNAMIC
resolution; ruflo swarm dispatch with cwd injection; molyanov validator → ruflo plugin delegation;
Mnemonic MCP signing at each of the 5 trigger points with parent verification; sanitized logging
across whole pipeline.

**End-to-end** — Phase 2 smoke (T3.4) drives one feature top-to-bottom; Phase 5 deliberate-break
+ first recursive self-merge (T8.3). Both produce a complete 5-node attestation DAG.

**Conformance harnesses** (T5.2) act as gating tests for the relevant `task_type`s on every PR
in the six repos:
- schema/cbor → byte-equivalence
- crypto-primitive → third-party verification
- mcp-surface → MCP client compatibility
- wasm-bindings → browser + byte-equivalence
- full-mode-integration → devnet round-trip + independent verifier recovery

**Smoke gate** (T6.2) — 15-min docs-task end-to-end against candidate fabric for every
`mnemonic-loop` PR.

**Test data:** synthetic secret samples (sanitizer), fixture worktrees, fixture PRs, frozen
Solana devnet block height for full-mode integration corpus, frozen CBOR fixtures for
byte-equivalence corpus.

## 6. Agent Verification Plan (post-deploy QA)

Tools required: Telegram MCP, Playwright MCP, `curl`, `ssh` (over VPN), `mnemonic_recall`,
`mnemonic_verify`, `git`, journalctl/systemctl.

Steps:
1. Send a test intent into each of 8 topics; confirm bot reply and worktree creation
   (`curl http://workspace-manager:8080/worktrees`).
2. Drive a docs PR end-to-end. Verify 5-node DAG via `mnemonic_recall`.
3. Inject each of the 9 watchdog alert classes; assert ops-topic message.
4. Run `/turn-into-task` against one alert; assert Kaneo card created (REST GET).
5. Deliberate-break: corrupt workspace-manager state.json; run `fabric-safe-mode rollback`;
   assert services back up and ops notification fired.
6. Open a `mnemonic-loop` PR that intentionally breaks smoke-gate; assert merge blocked.
7. Verify `last-known-good` tag advanced after a successful non-loop task.
8. Verify sanitized-logging filter on a freshly produced log: `grep` against secret
   regex patterns returns zero matches.

## 7. User-Spec Deviations

None. All decisions trace to acceptance criteria in `user-spec.md`. Two purely operational
decisions (D24 `GET /health` endpoint, D25 systemd-timer over cron) are marked `[TECHNICAL]`
because they do not derive from any user-spec AC but support implementation hygiene.

## 8. Risks (delta over user-spec)

| # | Risk | Mitigation |
|---|------|------------|
| TR1 | workspace-manager becomes single point of failure for routing | `ops` topic + Telegram bot are independent (D23, AC3); fabric-safe-mode bypasses workspace-manager |
| TR2 | Vaultwarden outage blocks all worktree creation | workspace-manager exposes `409 vault_unreachable`; ops alert; manual restart via SSH |
| TR3 | ruflo swarm spawn cwd injection regression breaks isolation | T4.1 verify-smoke asserts distinct `--cwd` per agent; integration tests in CI |
| TR4 | Sanitizer regex over-redacts and hides real signal | Sanitizer is filter-only; raw logs retained inside VM under root-only path for emergency review |
| TR5 | last-known-good tag points to a state that fails to rebuild | Smoke-gate (T6.2) re-runs before every loop merge; safe-mode script aborts and notifies if rebuild fails |
| TR6 | Recursive self-merge corrupts in-flight DAG | Mnemonic attestation parents are immutable; corrupted child does not break parent chain — manual re-attest possible |

## 9. Out of Scope (mirrors spec.md §13)

- `ruflo-mnemonic` plugin (separate future)
- federation / multi-operator
- Solana mainnet
- public demo of the loop
- OpenClaw-equivalent PM agent
- VM HA
