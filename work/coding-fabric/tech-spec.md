---
feature: coding-fabric
work_type: feature
size: L
status: approved
created: 2026-05-12
last_updated: 2026-05-12
version: 0.2.0
branch: dev
source_user_spec: work/coding-fabric/user-spec.md
source_spec_md: spec.md (Mnemonic Protocol Agent-Driven Development Loop v0.1.1)
supersedes: work/coding-fabric/archive/v0.1.1/tech-spec.md
attestation: 9253b6c0-78ea-4127-9397-8b7875f791b3
solana_tx: 2bFxD4pWQZw87xnQcqVPqgTgfJ7VyLmTNiXbE8zCVpKPC61svZNcwYnTeNMiU3NtWheNdPV5fffHcF6iZkykBebf
arweave_tx: 7wd7o88htpgkubidTv5iYi4CDE8xQaY9cnX6Qwa6EfK1
related_features:
  - mnemonic-tg-bridge — upstream PR for `cwd:"DYNAMIC"` sentinel in pavel-molyanov/telegram-ai-agent (Python; size S; status approved)
---

# Tech Spec — Coding Fabric (v0.2.0, IaC + CI delivery)

## 0. Changelog vs v0.1.1

v0.1.1 prescribed manual operator execution of cloud-VM bootstrap (Tasks T01–T09, T20, T21).
v0.2.0 replaces that with **OpenTofu + Ansible + GitHub Actions**: the same final state
is reached, but every bootstrap step is code, version-controlled, replayable, and
deployable from CI on tag push. After first bootstrap, subsequent fabric updates flow
through the fabric itself (recursive self-hosting) — CI is only the cold-start path.

Two other changes:

- `telegram-ai-agent` (Python, `git@github.com:pavel-molyanov/telegram-ai-agent.git`)
  is **consumed as-is** from upstream. The only addition is a small generic feature
  (`cwd: "DYNAMIC"` sentinel with HTTP resolver) submitted to upstream as a PR —
  tracked as **separate feature `mnemonic-tg-bridge`**, size S. The Rust rewrite
  considered earlier is cancelled. coding-fabric T09 Ansible role installs the bot
  with `uv sync` from a pinned upstream commit that contains the feature (or from
  the `mnemonic-org/telegram-ai-agent` fork as fallback).
- Networking layer shifts from self-hosted WireGuard to **Tailscale** (operator
  ergonomics: phone-side onboarding is dramatically simpler; mesh model handles
  multi-device later without re-architecture).

## 1. Goal & Context

Unchanged from v0.1.1: stand up a single-VM, VPN-fronted autonomous coding fabric where
one operator drives all Mnemonic-protocol repositories from Telegram. Every meaningful
artefact attested via local Mnemonic MCP. Operational toil flows back through the same
fabric.

The fabric runs on a Hetzner Cloud CCX33 (4 vCPU dedicated, 16 GB RAM, with attached
200 GB volume) in `fsn1` (Falkenstein, EU). Tailnet membership replaces the WireGuard
peer model from v0.1.1.

## 2. Architecture

### 2.1 Three-plane model

```
+----------------------------------------------------------------+
|  CI plane (GitHub Actions in mnemonic-loop)                    |
|  - deploy-fabric.yml  (cold-start, on tag push / manual)       |
|  - e2e-smoke.yml      (post-deploy validation)                 |
|  - post-deploy-avp.yml (Agent Verification Plan)               |
|  - smoke-gate.yml      (existing — gates loop PR merges)       |
+----------------------------------------------------------------+
                            |
                            v (only first time, or recovery)
+----------------------------------------------------------------+
|  IaC plane (executed by CI or operator from laptop)            |
|  - infrastructure/tofu/      (Hetzner VM, volume, firewall)    |
|  - infrastructure/ansible/   (10 roles, deploy.yml)            |
|  - infrastructure/secrets/   (sops-encrypted secrets file)     |
+----------------------------------------------------------------+
                            |
                            v
+----------------------------------------------------------------+
|  Runtime plane (on Hetzner VM)                                  |
|  - Tailscale (replaces WireGuard) — only path to admin surfaces |
|  - Vaultwarden, Kaneo, Telegram bot, telegram-ai-agent          |
|  - ruflo, molyanov, mnemonic-mcp                                |
|  - workspace-manager (Python FastAPI, NEW)                      |
|  - fabric-watchdog (Python systemd-timer, NEW)                  |
|  - fabric-safe-mode (bash, NEW)                                 |
|  - Per-task worktrees, sccache, sanitized logs                  |
+----------------------------------------------------------------+
```

### 2.2 IaC plane details

**OpenTofu** (Linux Foundation fork of Terraform, MPL-2.0):
- `infrastructure/tofu/hetzner/` — provider `hetznercloud/hcloud`
- Resources: `hcloud_server` (CCX33 fsn1, Ubuntu 24.04 LTS), `hcloud_volume` (200 GB),
  `hcloud_firewall` (allow Tailscale only; block public SSH), `hcloud_volume_attachment`
- Outputs: `vm_ipv4`, `vm_ipv6`, `volume_device`
- State backend: GitHub-stored encrypted state via `gha-terraform-state-action`, or
  Hetzner Object Storage (decision logged below)
- Run from CI by `deploy-fabric.yml` step `tofu apply -auto-approve` against
  pre-validated plan

**Ansible** (Red Hat, GPLv3):
- `infrastructure/ansible/inventory/hosts.yml` — single host, populated from OpenTofu outputs
- `infrastructure/ansible/roles/` — 10 roles, listed in §2.3
- `infrastructure/ansible/playbooks/deploy.yml` — orchestrates all roles in order
- `infrastructure/ansible/playbooks/safe-mode-rollback.yml` — invoked over SSH on incident
- Secrets pulled at runtime from `infrastructure/secrets/secrets.sops.yml` (age-encrypted)

**GitHub Actions** (in `mnemonic-loop` repo):
- Workflows under `.github/workflows/`
- Bootstrap secrets pulled from GH Actions secrets: `HCLOUD_TOKEN`,
  `TAILSCALE_AUTH_KEY`, `SOPS_AGE_KEY`, `VAULTWARDEN_INITIAL_ADMIN_PASSWORD`,
  `TELEGRAM_BOT_TOKEN`, `SOLANA_DEVNET_KEYPAIR`, `IRYS_TESTNET_PRIVATE_KEY`,
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_DEPLOY_PAT`

### 2.3 Ansible roles (10)

| # | Role | Installs / configures |
|---|------|----------------------|
| 1 | `base` | OS hardening, ufw, unattended-upgrades, operator OS user, base dirs |
| 2 | `tailscale` | Install tailscaled, `tailscale up --authkey` from Ansible-vault, advertise routes to admin services |
| 3 | `vaultwarden` | docker-compose, Caddy reverse-proxy (bound to tailnet IP only), initial admin password |
| 4 | `kaneo` | docker-compose, six default projects (`mnemonic-core`, …, `mnemonic-loop`) |
| 5 | `telegram-init` | Telegram-bot forum + 8 topics via Bot API (idempotent — skips if topics exist) |
| 6 | `restic-backups` | Restic + nightly cron + off-site repo + `backup-verify.sh` |
| 7 | `ruflo` | Install ruflo full CLI, write per-topic config matrix (§2.4) |
| 8 | `molyanov` | Install molyanov-ai-dev, install PK guard pre-write hook |
| 9 | `mnemonic-mcp` | **OPTIONAL, disabled by default (descoped 2026-05).** When `mnemonic_mcp_enabled=true`: install local Mnemonic MCP server, systemd unit, 5 trigger-point hook scripts. When disabled (default): install only 5 no-op stub hooks. Re-enablement tracked in backlog `mnemonic-attestation-integration`. |
| 10 | `fabric-services` | Deploy compiled artefacts from `fabric/`: workspace-manager, sanitizer, watchdog, safe-mode, swarm-bridge, validator wrappers; install systemd units and timers |
| 11 | `telegram-ai-agent` | `git clone` + `uv sync` upstream pavel-molyanov/telegram-ai-agent at a pinned commit (the one that contains the `cwd:"DYNAMIC"` PR — or `mnemonic-org/telegram-ai-agent` fork as fallback). Install systemd unit, render per-topic configs with `cwd: "DYNAMIC"`, set env var `TELEGRAM_AI_AGENT_CWD_RESOLVER_URL=http://{{ tailscale_ip }}:8080` (workspace-manager). |

(11 roles total. Role `telegram-ai-agent` pins a specific upstream commit. Until the
upstream PR from feature `mnemonic-tg-bridge` is merged, role pins to the fork; once
merged, role pins to a tagged upstream release.)

### 2.4 Per-topic configuration matrix

Unchanged from v0.1.1:

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

### 2.5 workspace-manager HTTP API

Unchanged from v0.1.1 (FastAPI, 127.0.0.1:8080, OpenAPI-documented):

```
POST   /worktree              create
DELETE /worktree/{task_id}    cleanup
GET    /worktree/{task_id}    lookup
GET    /worktrees             list
GET    /health                introspection
```

Capacity cap = 10, shared sccache, per-worktree `.env` materialised from Vaultwarden,
deleted on DELETE. Service binds to tailnet IP only (Ansible-templated from OpenTofu
output). `telegram-ai-agent` (with the `cwd:"DYNAMIC"` patch from feature `mnemonic-tg-bridge`) resolves the sentinel against this API at engine spawn.

### 2.6 Mnemonic attestation DAG

**DEFERRED (2026-05 scope change):** moved to backlog feature
`mnemonic-attestation-integration` (see `work/mnemonic-attestation-integration/`).
The local Mnemonic MCP server is not yet production-ready, so the attestation
DAG cannot be produced on a real deploy. coding-fabric still installs 5 no-op
stub hooks under `/etc/mnemonic-mcp/hooks/` so callers do not break.

(Original intent, preserved for the backlog feature.) Per feature: 5 nodes
(user-spec → tech-spec → tasks → pre-deploy → deploy/post-deploy). Hooks
invoked at success of each molyanov phase.

### 2.7 Sanitized logging filter

Unchanged from v0.1.1. Python module under `fabric/logs/sanitizer/` with patterns for
base58 keys, JWK fragments, `sk-`/`api_`/`ANTHROPIC_` tokens, Telegram file URLs.

### 2.8 File layout

**In this repo** (artefacts that ship to VM):

```
fabric/
  logs/sanitizer/                   # Task 07
  workspace-manager/                # Task 08
  integrations/swarm-bridge.py      # Task 14
  molyanov/validators/              # Task 15
  watchdog/                         # Task 18
  safe-mode/                        # Task 19
  github-templates/                 # Task 16

infrastructure/
  tofu/hetzner/                     # Task 01
  ansible/
    inventory/                      # Task 24 (CI flow)
    playbooks/
      deploy.yml                    # Task 24
      safe-mode-rollback.yml        # Task 19
    roles/
      base/                         # Task 02
      tailscale/                    # Task 02
      vaultwarden/                  # Task 03
      kaneo/                        # Task 04
      telegram-init/                # Task 05
      restic-backups/               # Task 06
      ruflo/                        # Task 10
      molyanov/                     # Task 11
      mnemonic-mcp/                 # Task 12
      telegram-ai-agent/            # Task 09 (installs upstream pavel-molyanov + DYNAMIC patch)
      fabric-services/              # Tasks 08, 14, 15, 18, 19 deploy
  secrets/
    secrets.sops.yml                # age-encrypted, committed
    .sops.yaml                      # sops config
  scripts/
    bootstrap.sh                    # generate age key, seed initial secrets

.github/workflows/                  # In mnemonic-loop repo (Task 24, 25)
  deploy-fabric.yml
  e2e-smoke.yml
  post-deploy-avp.yml
  smoke-gate.yml                    # existing (extends Task 19)
  pr-conformance.yml                # Task 16
```

**On the VM** (after `make deploy`):

```
~/code/mnemonic-masters/            # 6 protocol repos (Ansible-cloned)
~/code/mnemonic-workspaces/         # per-task worktrees
~/.fabric/                          # mirror of fabric/ from this repo
  logs/sanitized/
  workspace-manager/
  watchdog/
  safe-mode/
  …
/etc/systemd/system/                # Ansible-deployed units
  workspace-manager.service
  mnemonic-mcp.service
  fabric-watchdog.timer
  fabric-watchdog.service
  telegram-ai-agent.service
```

## 3. Decisions

Each decision references a user-spec AC unless marked `[TECHNICAL]`. Decisions changed
or added in v0.2.0 marked `[v0.2.0]`.

| # | Decision | Justification | Anchors |
|---|----------|---------------|---------|
| D1 | Hetzner Cloud CCX33, fsn1, 200 GB attached volume `[v0.2.0]` | Cost-effective EU dedicated vCPU; matches spec.md "heavy" baseline (32 GB target met via burst + swap; volume covers disk budget) | AC4, AC5, §6 |
| D2 | Tailscale replaces self-hosted WireGuard `[v0.2.0]` | Phone-side onboarding trivial; mesh model survives future multi-device without re-architecture; one less self-hosted thing | AC1–AC3 |
| D3 | OpenTofu (MPL-2.0) for cloud provisioning `[v0.2.0]` | Drop-in Terraform replacement, full OSS; cleaner than BSL Terraform | AC1, §7 |
| D4 | Ansible (GPLv3) for VM configuration `[v0.2.0]` | Agentless (SSH-only), mature, idempotent; matches operator's mental model | §6 |
| D5 | GitHub Actions for CI/cold-start deploy `[v0.2.0]` | Already where the source lives; secrets store native; manual `workflow_dispatch` covers ad-hoc redeploy | AC1, AC33 |
| D6 | secrets via sops + age, committed to repo `[v0.2.0]` | Encrypted-at-rest in git; only the age private key is in GH Actions secret; operator can decrypt locally for ops | AC38 |
| D7 | First deploy via CI; subsequent via fabric `[v0.2.0]` | Bootstrap-then-self-host. CI is cold-start path only. Recursive self-hosting (T19 smoke-gate) handles steady state | AC33–AC37 |
| D8 | `telegram-ai-agent` consumed as-is from upstream `pavel-molyanov/telegram-ai-agent`; only delta is a small upstream PR adding optional `cwd:"DYNAMIC"` HTTP resolver. Feature tracked as separate user-spec `mnemonic-tg-bridge` (size S). Fallback path: fork to `mnemonic-org/telegram-ai-agent` if PR stalls > 30 days. `[v0.2.0, revised]` | Reuse > rewrite when upstream is active (43 stars, maintained, MIT, recent push); generic feature is upstream-acceptable; preserves AC6 (per-message worktree isolation) without Rust greenfield cost. | AC6, AC8 |
| D9 | workspace-manager unchanged (FastAPI, Python, 127.0.0.1:8080 bound to tailnet) `[refined]` | API contract stable; binding changes from public-VPN-only to tailnet-IP-only | AC4–AC7 |
| D10 | Capacity cap 10, disk thresholds 75/85/90% on 50 GB budget | spec.md "heavy" | AC5, AC32 |
| D11 | Engine choice per topic: Claude Code default, Codex for demo-client | spec.md §12 | AC8 |
| D12 | `/do-feature` via ruflo swarm MCP with per-task cwd injection | spec.md §4 | AC9 |
| D13 | Validator delegation: skeptic→jujutsu, security-auditor→security-audit, post-deploy-qa→browser | spec.md §4 | AC10 |
| D14 | Five-point attestation lineage via local Mnemonic MCP `[DEFERRED to backlog feature mnemonic-attestation-integration — mnemonic-mcp server not yet production-ready as of 2026-05]` | spec.md §5 | AC11–AC16 |
| D15 | QA gates by task_type (byte-equivalence / third-party-verify / MCP-compat / WASM-browser / full-mode) | spec.md §6 | AC17–AC22 |
| D16 | PR template enforces 4 sections, 500-line cap | spec.md §6 | AC24 |
| D17 | Per-topic feature toggles per §2.4 matrix | spec.md §7 | AC25–AC27 |
| D18 | Molyanov Project Knowledge canonical; ruflo cannot overwrite | spec.md §7 | AC28 |
| D19 | `fabric-watchdog` Python systemd-timer, 5-min cadence, 9 alert classes | spec.md §8 | AC29–AC32 |
| D20 | `/turn-into-task` is Telegram bot command POSTing to Kaneo HTTP API | spec.md §8 | AC31 |
| D21 | `last-known-good` git tag auto-updates after every non-loop task success | spec.md §10 | AC34 |
| D22 | Smoke-gate before mnemonic-loop merges (15-min docs task on candidate fabric) | spec.md §10 | AC33, AC35 |
| D23 | `fabric-safe-mode` invoked via Ansible playbook over SSH `[refined]` | Same goal as v0.1.1, but plays back through Ansible idempotency rather than bespoke bash | AC36 |
| D24 | Vaultwarden sole secret store on VM; per-worktree `.env` materialised + deleted | spec.md §9 | AC38 |
| D25 | Solana devnet only; no mainnet keypair on VM | spec.md §9 | AC39 |
| D26 | Sanitizer strips base58/JWK/sk-/api_/ANTHROPIC_/tg-file-URLs at every log site | spec.md §9 | AC40 |
| D27 | Agent trust boundary: in-worktree write, no outward write, no Vaultwarden read, no main push, no production deploy | spec.md §9 | AC41 |
| D28 | Nightly off-site Restic backups with verified test-restore in bootstrap | spec.md §11 Phase 0 | AC42 |
| D29 | `ops` topic process tree independent of workspace-manager/ruflo/watchdog | spec.md §10 | AC3 |
| D30 | `[TECHNICAL]` Ansible roles versioned via git tags; CI pins to tag, not branch | Repeatable rollouts; one less drift source | none |
| D31 | `[TECHNICAL]` OpenTofu state in GH Actions artifact + sops encryption (no Hetzner Object Storage) | Avoid an extra service; state is small and rarely concurrent | none |

## 4. Implementation Tasks

Total: 25 tasks across 8 waves. Audits + Final Wave per standard pattern. Task brevity:
descriptions are 2–3 sentence scope statements — detailed AC and TDD anchors live in
each individual `tasks/NN.md` file.

### Wave 1 — IaC foundation (parallel)

**T01 — OpenTofu module for Hetzner VM + volume + firewall**
- Skill: `terraform-master`; Reviewers: `security-auditor`, `reliability-engineer`
- Files: `infrastructure/tofu/hetzner/{main,variables,outputs}.tf`

**T02 — Ansible roles `base` + `tailscale` (OS hardening + tailnet membership)**
- Skill: `ansible-automation`; Reviewers: `security-auditor`, `reliability-engineer`
- Files: `infrastructure/ansible/roles/{base,tailscale}/`

**T03 — Ansible role `vaultwarden`**
- Skill: `ansible-automation`; Reviewers: `security-auditor`, `code-reviewer`
- Files: `infrastructure/ansible/roles/vaultwarden/`

**T04 — Ansible role `kaneo`**
- Skill: `ansible-automation`; Reviewers: `reliability-engineer`, `code-reviewer`
- Files: `infrastructure/ansible/roles/kaneo/`

**T05 — Ansible role `telegram-init` (forum + 8 topics via Bot API)**
- Skill: `ansible-automation`; Reviewers: `reliability-engineer`, `code-reviewer`
- Files: `infrastructure/ansible/roles/telegram-init/`

**T06 — Ansible role `restic-backups` (nightly + verify-restore)**
- Skill: `ansible-automation`; Reviewers: `reliability-engineer`, `security-auditor`
- Files: `infrastructure/ansible/roles/restic-backups/`

### Wave 2 — Core fabric services (Python)

**T07 — `fabric/logs/sanitizer/` Python module + tests**
- Skill: `python-alchemist`; Reviewers: `security-auditor`, `code-reviewer`
- Files: `fabric/logs/sanitizer/`

**T08 — `fabric/workspace-manager/` FastAPI service + tests**
- Skill: `fastapi-expert`; Reviewers: `security-auditor`, `reliability-engineer`, `code-reviewer`
- Files: `fabric/workspace-manager/`

**T09 — Ansible role `telegram-ai-agent` (installs upstream pavel-molyanov bot with `cwd:"DYNAMIC"` patch)**
- Skill: `ansible-automation`; Reviewers: `code-reviewer`, `reliability-engineer`
- Files: `infrastructure/ansible/roles/telegram-ai-agent/`
- Note: role pins to upstream commit/tag containing the `cwd:"DYNAMIC"` feature (from feature `mnemonic-tg-bridge`). Until that PR merges upstream, role pins to fork `mnemonic-org/telegram-ai-agent`. Pin is a variable; switch is one-line.

### Wave 3 — Substrate + methodology + attestation (Ansible)

**T10 — Ansible role `ruflo` (full CLI install + per-topic cohabitation toggles)**
- Skill: `ansible-automation`; Reviewers: `code-reviewer`, `security-auditor`
- Files: `infrastructure/ansible/roles/ruflo/`

**T11 — Ansible role `molyanov` (skills install + PK guard hook)**
- Skill: `ansible-automation`; Reviewers: `code-reviewer`
- Files: `infrastructure/ansible/roles/molyanov/`

**T12 — Ansible role `mnemonic-mcp` (server install + 5 trigger-point hooks)**
- Skill: `ansible-automation`; Reviewers: `security-auditor`, `code-reviewer`
- Files: `infrastructure/ansible/roles/mnemonic-mcp/`

**T13 — CI job `e2e-smoke.yml` (post-deploy end-to-end through full pipeline)**
- Skill: `github-actions-pro`; Reviewers: `reliability-engineer`
- Files: `.github/workflows/e2e-smoke.yml`

### Wave 4 — Swarm bridge + delegation

**T14 — `fabric/integrations/swarm-bridge.py` (`/do-feature` → ruflo swarm with per-task cwd)**
- Skill: `python-alchemist`; Reviewers: `code-reviewer`, `reliability-engineer`
- Files: `fabric/integrations/swarm-bridge.py`, tests

**T15 — `fabric/molyanov/validators/` (skeptic/security-auditor/post-deploy-qa → ruflo plugin wrappers)**
- Skill: `python-alchemist`; Reviewers: `code-reviewer`, `security-auditor`
- Files: `fabric/molyanov/validators/`, tests

### Wave 5 — PR conformance + harnesses

**T16 — PR template + `pr-conformance.yml` (4 sections + 500-line cap)**
- Skill: `github-actions-pro`; Reviewers: `code-reviewer`
- Files: `fabric/github-templates/`, per-repo `.github/` (delivered by `fabric-services` role at deploy time, not committed to six master repos here)

**T17 — Conformance harnesses scaffolding (byte-equivalence / MCP-compat / WASM-browser / full-mode)**
- Skill: `playwright-pro`; Reviewers: `security-auditor`, `code-reviewer`, `reliability-engineer`
- Files: `fabric/harnesses/{byte-equivalence,mcp-compat,wasm-browser,full-mode}/`
- Note: fixtures depend on real upstream code (mnemonic-core/wasm) — harness scaffolds the structure, real fixture sets follow when those repos are available

### Wave 6 — Watchdog + safe-mode

**T18 — `fabric/watchdog/` (9 alert classes + `/turn-into-task` + systemd timer)**
- Skill: `python-alchemist`; Reviewers: `reliability-engineer`, `security-auditor`, `code-reviewer`
- Files: `fabric/watchdog/`, tests

**T19 — `fabric/safe-mode/` (last-known-good hook + rollback playbook + smoke-gate workflow)**
- Skill: `github-actions-pro`; Reviewers: `reliability-engineer`, `security-auditor`, `code-reviewer`
- Files: `fabric/safe-mode/`, `infrastructure/ansible/playbooks/safe-mode-rollback.yml`, `.github/workflows/smoke-gate.yml`

### Wave 7 — Audits (parallel, no reviewers)

**T20 — Code audit**
- Skill: `code-reviewing`; Reviewers: none
- Reads all artefacts from Tasks 01–19; writes `logs/tasks/code-audit.json`

**T21 — Security audit**
- Skill: `security-auditor`; Reviewers: none
- Reads all artefacts; OWASP + secret-handling + trust-boundary deep dive; writes `logs/tasks/security-audit.json`

**T22 — Test audit**
- Skill: `test-master`; Reviewers: none
- Reads all tests + AC traceability; writes `logs/tasks/test-audit.json`

### Wave 8 — Final

**T23 — Pre-deploy QA**
- Skill: `pre-deploy-qa`; Reviewers: none
- Runs full local test suite + AC verification; triggers Mnemonic pre-deploy attestation

**T24 — CI workflow `deploy-fabric.yml` (cold-start bootstrap)**
- Skill: `github-actions-pro`; Reviewers: `reliability-engineer`, `security-auditor`
- Files: `.github/workflows/deploy-fabric.yml`, `infrastructure/ansible/playbooks/deploy.yml`
- Effect: `tofu apply` → `ansible-playbook deploy.yml` → calls `e2e-smoke.yml`

**T25 — CI workflow `post-deploy-avp.yml` (8 AVP steps via MCP)**
- Skill: `post-deploy-qa`; Reviewers: none
- Files: `.github/workflows/post-deploy-avp.yml`
- Effect: deliberate-break drill + first recursive self-merge; signs post-deploy attestation

### One-time operator checklist (not a task)

See `work/coding-fabric/bootstrap-checklist.md`. Estimate 30 minutes the very first time
the fabric is deployed; never repeated.

## 5. Testing Strategy (size L)

Unchanged conceptually from v0.1.1. Pyramid balance:

- **Unit:** sanitizer (T07), workspace-manager internals (T08), validator wrappers (T15),
  watchdog checks (T18), swarm-bridge (T14), safe-mode (T19)
- **Integration:** workspace-manager API lifecycle; Ansible role idempotency (run each
  role twice on a docker container, second run no changes); OpenTofu `tofu validate`
  + `tofu plan -detailed-exitcode`; Mnemonic MCP signing at each trigger point
- **End-to-end:** CI job `e2e-smoke.yml` (T13) — drives one feature top-to-bottom
  through the deployed fabric; CI job `post-deploy-avp.yml` (T25) — full AVP including
  deliberate-break + recursive self-merge
- **Conformance:** four harnesses (T17), each tied to a `task_type` and dispatched
  by `pr-conformance.yml` (T16)
- **Smoke gate:** 15-min docs-task on candidate fabric for every mnemonic-loop PR (T19)

## 6. Agent Verification Plan (post-deploy QA, run by T25 in CI)

Tools required: Telegram MCP, Playwright MCP, `curl`, `tailscale ssh`,
`mnemonic_recall`, `mnemonic_verify`, `git`, journalctl/systemctl, `restic verify`.

Steps:
1. Send test intent into each of 8 topics; confirm bot reply + worktree creation
2. Drive a docs PR end-to-end. Verify 5-node DAG via `mnemonic_recall`
3. Inject each of the 9 watchdog alert classes; assert ops-topic message
4. Run `/turn-into-task` against an alert; assert Kaneo card created
5. Deliberate-break: corrupt workspace-manager state.json; run safe-mode-rollback playbook; assert services restored + ops notification fired
6. Open mnemonic-loop PR with intentional smoke-gate break; assert merge blocked
7. Verify `last-known-good` tag advanced after successful non-loop task
8. Sanitizer corpus check: `grep` against secret patterns over fresh logs returns zero matches
9. Restic verify: `restic check` against off-site repo returns ok

## 7. User-Spec Deviations

None functional. Delivery mechanism changed from "operator runs commands" to
"operator runs `make deploy` or CI runs on tag push", but every user-spec AC remains
satisfied. Two infrastructure-layer choices may warrant your sign-off:

- **D2 Tailscale instead of WireGuard.** user-spec AC1 says "только через VPN" without
  prescribing protocol. Tailscale is still a VPN; the tailnet is operator-only.
  `[PENDING USER APPROVAL — confirmed implicitly during planning conversation]`
- **D1 Hetzner CCX33 vs spec.md "8+ vCPU, 32 GB"**: CCX33 is 4 dedicated vCPU + 16 GB
  RAM. Burst CPU + swap covers occasional spikes; cost is ~1/3 of CCX43. Recommend
  starting CCX33 and upsizing if watchdog disk/CPU alerts fire frequently in the first
  month. `[PENDING USER APPROVAL — confirmed during planning]`

## 8. Risks (delta over v0.1.1)

| # | Risk | Mitigation |
|---|------|------------|
| TR1 | OpenTofu state corruption blocks redeploy | State file is small + age-encrypted; restore from previous GH Actions artifact; idempotent re-apply |
| TR2 | Tailscale auth-key leak grants tailnet access | Key is single-use, short-TTL; rotated each redeploy; alerted via tailnet admin events |
| TR3 | sops age-key in GH secret is the master key | One-key-to-rule-them-all risk acknowledged; rotation playbook documented; protected by GitHub MFA |
| TR4 | mnemonic-tg-bridge upstream PR stalls or is rejected | 30-day timeout triggers fork to `mnemonic-org/telegram-ai-agent`; coding-fabric T09 Ansible role pins to fork until PR merges upstream; functional equivalence preserved either way |
| TR5 | Ansible role version drift on long-running cluster | Roles pinned to git tags per D30; CI pins to tag |
| TR6 | Hetzner CCX33 undersized for parallel workloads | Watchdog disk/CPU alerts fire; upsize is a one-line OpenTofu change + `tofu apply` |
| TR7 | Recursive self-hosting bricks fabric | T19 smoke-gate + last-known-good + safe-mode playbook (Ansible-idempotent rollback) |
| TR8 | Restic backup destination unavailable | Restic supports multiple repos; configure secondary; backup job exits non-zero, watchdog alert |

## 9. Out of Scope (v0.1)

Inherited from spec.md §13 plus:
- `ruflo-mnemonic` plugin (separate future)
- federation / multi-operator
- Solana mainnet
- public demo of the loop
- OpenClaw-equivalent PM agent
- VM HA / multi-region
- mnemonic-tg-bridge implementation (upstream PR for `cwd:"DYNAMIC"` sentinel) — separate feature, this tech-spec consumes the merged upstream commit / fork commit only
