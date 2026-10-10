# coding-fabric — Agent Guide

This file is written for AI coding agents that have no prior context on the project. Read it before making changes. The project is primarily documented in English, and all code comments and docs are in English.

## Project Overview

`coding-fabric` is the autonomous, self-hosting development loop for the **Mnemonic Protocol**. A single operator issues intent from Telegram, and the system specifies, implements, reviews, QAs, and (with a veto window) merges changes across the Mnemonic repositories.

Key facts:

- Runtime is a single **Hetzner Cloud CCX33** VM running Ubuntu 24.04, fronted by **Tailscale**.
- Orchestration state lives in **Kaneo** (self-hosted ticket tracker + Postgres) running in Docker on the VM.
- Secrets are stored in **Vaultwarden** (Docker), also on the VM.
- The system is intentionally **direct-to-trunk**: changes land on the single active branch without PRs.
- **ruflo / claude-flow was dropped** from the runtime on 2026-05-20. It is now only a local coordination/intelligence tool; see `.mcp.json` and `docs/claude-flow-cli.md`.
- The root `spec.md` is the **historical** v0.1.1 spec and is now stale. Current architecture is in `docs/architecture-report.md` and `work/coding-fabric/tech-spec.md`.

### High-level flow

1. Operator posts intent in a Telegram forum topic.
2. `telegram-ai-agent` (a fork of `pavel-molyanov/telegram-ai-agent`) spawns a Claude/Codex engine.
3. The engine creates a Kaneo ticket.
4. `Symphony` inside `fabric/workspace-manager` polls Kaneo, leases a git worktree, materialises secrets from Vaultwarden, and dispatches stage-specific engines (`claude` for code/QA, `codex` for adversarial review).
5. The engine opens a PR, runs tests, and transitions the ticket through `review` → `qa` → `ready-to-merge`.
6. After a 24-hour veto window, Symphony auto-merges the PR unless the operator `/reject`s it.
7. `fabric-watchdog` runs every 5 minutes and alerts the ops topic if anything is unhealthy.

A second product surface, the **content-publish pipeline**, runs independently: Telegram brief → Claude-written article → score gate → operator preview → publish to `@mnemonik` → optional Mnemonic attestation. It lives in `fabric/content-publisher/`.

## Repository Layout

```
coding-fabric/
├── spec.md                         # historical v0.1.1 spec (stale; read docs/architecture-report.md instead)
├── docs/                           # architecture report, diagrams, claude-flow-cli reference
├── fabric/                         # services we own
│   ├── workspace-manager/          # FastAPI + Symphony orchestrator
│   ├── watchdog/                   # 9-check ops watchdog
│   ├── logs/sanitizer/             # mandatory secret-redaction library
│   ├── content-publisher/          # blog/queue pipeline
│   ├── safe-mode/                  # smoke gate, rollback, last-known-good tag
│   ├── harnesses/                  # conformance harnesses (byte-equivalence, mcp-compat, wasm-browser, full-mode)
│   ├── github-templates/           # PR templates + pr-conformance CI
│   └── molyanov/__init__.py        # placeholder only
├── infrastructure/
│   ├── tofu/hetzner/               # OpenTofu: VM, volume, firewall
│   ├── ansible/                    # deploy + rollback playbooks and roles
│   │   ├── playbooks/deploy.yml    # canonical deploy role order
│   │   ├── playbooks/safe-mode-rollback.yml
│   │   ├── roles/                  # base, ssh-hardening, tailscale, vaultwarden, kaneo,
│   │   │                           # mnemonik-server, telegram-init, restic-backups,
│   │   │                           # molyanov, mnemonic-mcp, fabric-services,
│   │   │                           # content-publisher, telegram-ai-agent
│   │   └── files/claude-skills/    # vendored Molyanov skill bundle
│   └── secrets/
│       └── secrets.sops.yml        # age-encrypted secrets (see .sops.yaml)
├── .symphony/                      # Symphony workflow prompts and merge policy
├── .github/workflows/              # deploy-fabric.yml, smoke-gate.yml, e2e-smoke.yml, post-deploy-avp.yml
├── scripts/                        # sync-skills.sh, pair-kaneo.py, e2e smoke, AVP steps
├── work/                           # specs, tasks, decision logs per feature
│   ├── coding-fabric/              # platform v0.2.0
│   ├── coding-fabric-autonomous/   # end-state draft
│   ├── content-publish-pipeline/   # blog pipeline feature
│   ├── mnemonic-tg-bridge/         # cwd:DYNAMIC fork strategy
│   └── mnemonic-attestation-integration/  # backlog: restore attestation DAG
└── mnemonik-bridge-workspace/
    └── telegram-ai-agent/          # forked Telegram bot (own pyproject.toml)
```

## Technology Stack

| Layer | Tech |
|-------|------|
| Primary language | Python 3.10–3.12 (per-service; see pyproject.toml) |
| Web framework | FastAPI + uvicorn (`workspace-manager`) |
| HTTP client | httpx |
| Validation | Pydantic v2 + pydantic-settings |
| Async | asyncio; pytest-asyncio for tests |
| Config/serialization | YAML (PyYAML), JSON, JSONL |
| Telegram bot | aiogram 3.x (`telegram-ai-agent`) |
| Infrastructure | OpenTofu 1.8.x, Ansible, Docker Compose |
| Secrets | sops + age |
| CI | GitHub Actions |
| VPN | Tailscale |
| Harnesses | Rust (`cargo`), TypeScript (`npm`), WASM (`wasm-pack`), Python |
| Optional coordination tool | `npx @claude-flow/cli@latest` (ruflo) — local only, not runtime |

## Key Configuration Files

- `fabric/workspace-manager/pyproject.toml` — FastAPI/Symphony service, Python ≥3.12.
- `fabric/watchdog/pyproject.toml` — watchdog, Python ≥3.12, stdlib-only runtime deps.
- `fabric/content-publisher/pyproject.toml` — content pipeline, Python ≥3.11, includes ruff/mypy config.
- `fabric/logs/sanitizer/pyproject.toml` — shared log-redaction library, Python ≥3.10.
- `mnemonik-bridge-workspace/telegram-ai-agent/pyproject.toml` — Telegram bot, Python ≥3.12, hatchling build.
- `.mcp.json` — local `ruflo` MCP server config (autoStart: false).
- `.sops.yaml` — sops/age recipient for `infrastructure/secrets/secrets.sops.yml`.
- `.symphony/policy.yml` — merge policy (`auto-with-veto`, 24h window) and workspace cleanup rules.
- `.symphony/workflows/{code,review,qa}.md` — stage prompts with YAML front matter.
- `infrastructure/tofu/hetzner/*.tf` — VM, volume, firewall.
- `infrastructure/ansible/playbooks/deploy.yml` — canonical deploy role order.
- `.github/workflows/*.yml` — CI/CD.

## Build, Test and Run Commands

**There is no repo-wide build or task runner.** Each Python service is an independent package.

### Python services

Replace `<service>` with `workspace-manager`, `watchdog`, `content-publisher`, or `logs/sanitizer`:

```bash
cd fabric/<service>
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                             # full suite
pytest tests/test_<file>.py        # one file
pytest tests/test_<file>.py::test_name  # one test
pytest -k keyword                  # by keyword
pytest --cov                       # coverage (where configured)
```

Notes:

- `pytest` config is inside each `pyproject.toml` (`testpaths`, `asyncio_mode=auto`, `pythonpath`). No `PYTHONPATH` export is needed.
- `workspace-manager` and `watchdog` require Python ≥3.12.
- `content-publisher` requires Python ≥3.11.
- `logs/sanitizer` requires Python ≥3.10.

### Lint / format / type-check

Only `content-publisher` and `telegram-ai-agent` ship `ruff`/`mypy` config:

```bash
cd fabric/content-publisher
ruff check .
ruff format .
mypy
```

`workspace-manager` and `watchdog` do not currently include lint/type config.

### Running `workspace-manager` locally

```bash
cd fabric/workspace-manager
BIND_IP=127.0.0.1 uvicorn main:app --port 8080
curl -s http://127.0.0.1:8080/health | python -m json.tool
```

### Rust / TypeScript harness

```bash
cd fabric/harnesses/byte-equivalence
make              # build + test
# or manually:
cd rust && cargo build --release
cd ts && npm ci && npm run build
python3 -m pytest tests/ -v
```

### Ansible / OpenTofu

```bash
# Deploy from a laptop (after tailnet access + sops key)
ansible-playbook -i infrastructure/ansible/inventory/hosts.yml infrastructure/ansible/playbooks/deploy.yml

# OpenTofu
cd infrastructure/tofu/hetzner
tofu init
tofu plan
tofu apply
```

## Code Organization

### `fabric/workspace-manager/`

- `main.py` — FastAPI app + lifespan.
- `dispatch.py` — core Symphony dispatch logic; maps Kaneo ticket labels to workflows.
- `poll_loop.py` — Kaneo polling loop (~30 s).
- `kaneo_client.py` — Kaneo REST adapter.
- `workflow_loader.py` — parses `.symphony/workflows/*.md` YAML front matter.
- `agent_runner.py` — spawns `claude`/`codex` engines in worktrees.
- `auto_merge.py` — veto window + `gh pr merge` logic.
- `vault.py` — Vaultwarden `bw` CLI wrapper.
- `main.py` exposes `/worktree`, `/worktrees`, `/health`.

### `fabric/watchdog/`

- `__main__.py` — single-shot entry point called by systemd timer.
- `scheduler.py` — runs 9 checks in a thread pool.
- `checks/` — one module per alert class.
- `turn_into_task.py` — `/turn-into-task <alert_id>` command handler.
- Hard dependency on `fabric.logs.sanitizer`; refuses to start without it.

### `fabric/content-publisher/`

- `src/content_publisher/main.py` — FastAPI app + worker task startup.
- `worker.py` — queue poll loop.
- `queue.py` / `state.py` — JSONL queue + CAS state-machine persistence.
- `models.py` — Pydantic `Job` model and 13-state transition graph.
- `spawn.py` — spawns Claude with the blog-writer skill.
- `score.py` / `render.py` / `publish.py` / `attest.py` / `cleanup.py` / `deadline.py` / `recovery.py`.
- `mcp_client.py` — per-spawn stdio Mnemonic MCP client.

### `fabric/logs/sanitizer/`

Single redaction chokepoint used by watchdog and workspace-manager. Strips base58 keys, JWK fragments, `sk-`/`api_`/`ANTHROPIC_*` tokens, and Telegram file URLs.

### `mnemonik-bridge-workspace/telegram-ai-agent/`

Forked Telegram bot. Own `pyproject.toml`, systemd service, and MCP server for bot tooling. Communicates with `workspace-manager` to resolve `cwd: "DYNAMIC"`.

## Development Conventions

- **No monorepo build.** Work inside the service directory and use its own venv.
- **Keep files under 500 lines.** Split large modules.
- **Atomic persistence pattern** (duplicated deliberately per service): `flock` → write tempfile → `os.replace` → fsync parent dir.
- **Fail-closed.** Input is validated at system boundaries (e.g., `_TASK_ID_RE = r"^[A-Z0-9\-]{3,40}$"`).
- **Log hygiene.** All logs and outbound Telegram messages must pass the sanitizer.
- **Secret handling.** Runtime secrets come from Vaultwarden or systemd `LoadCredential`, not committed `.env` files. Per-worktree `.env` files are mode `0600` and deleted on cleanup.
- **Restricted child env.** Spawned engines receive an allowlisted environment; parent service secrets are not inherited.
- **Read before editing.** Always read a file before changing it.
- **Never create files unless necessary.** Prefer editing existing files.
- **Never commit secrets, credentials, or `.env` files.**
- **No `Co-Authored-By` trailer** unless explicitly configured.
- **Direct-to-trunk.** Changes are committed directly to the active branch; there are no PRs for this repo. CI smoke-gate is the required check before merge.

## Testing Strategy

- **Unit / integration tests:** `pytest` in each Python service. Async tests use `pytest-asyncio` with `asyncio_mode=auto`.
- **Coverage:** configured in `workspace-manager`, `watchdog`, and `content-publisher` pyproject files.
- **Ansible roles:** some roles have Molecule tests under `molecule/default/`.
- **Smoke gate:** `smoke-gate.yml` builds a candidate Docker image from `fabric/safe-mode/smoke-gate/` and runs a trivial docs task end-to-end within a 15-minute budget. Required before fabric changes merge.
- **E2E smoke:** `e2e-smoke.yml` joins the tailnet and sends a synthetic `/do-feature` to the live bot, then verifies worktree cleanup.
- **Post-deploy AVP:** `post-deploy-avp.yml` runs 9 validation steps plus a deliberate-break rollback drill.
- **Harnesses:** `byte-equivalence`, `mcp-compat`, `full-mode`, and a planned `wasm-browser` harness. Real integration is opt-in via env; default mode runs stub tests.

## Deployment & CI

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `deploy-fabric.yml` | tag `fabric-v*` or manual `workflow_dispatch` | OpenTofu plan/apply → Ansible deploy → best-effort e2e smoke |
| `smoke-gate.yml` | PR touching `fabric/**`, ansible, Docker files | 15-min candidate-fabric smoke test; required status check |
| `e2e-smoke.yml` | reusable / dispatch | Tailnet SSH smoke against live VM |
| `post-deploy-avp.yml` | dispatch | Post-deploy validation + rollback drill |

Runtime deployment topology:

- Hetzner Cloud `CCX33` in `fsn1`.
- 200 GB persistent volume attached as `/dev/sdb`; Kaneo Postgres and content-publisher queue live here.
- Default-deny firewall; Tailscale is the admin plane.
- Docker Compose stacks: `vaultwarden` + Caddy, `kaneo` + Postgres, optional `mnemonik-server` + Ollama.
- systemd units: `workspace-manager`, `telegram-ai-agent`, `content-publisher`, `fabric-watchdog.timer`.
- Secrets decrypted controller-side via `sops` + `age` (`SOPS_AGE_KEY` in GitHub Actions).

### Deploy role order (Ansible)

1. `base`
2. `ssh-hardening`
3. `tailscale`
4. `vaultwarden`
5. `kaneo`
6. `mnemonik-server` (optional, disabled by default)
7. `telegram-init`
8. `restic-backups` (disabled by default)
9. `molyanov`
10. `mnemonic-mcp` (client binary; gated)
11. `fabric-services`
12. `content-publisher`
13. `telegram-ai-agent`

## Security Considerations

- **Network:** default-deny firewall; admin access is Tailnet-only. Public surfaces are Caddy `:443`/`:80` for Kaneo and outbound Telegram Bot API long-poll.
- **Secrets at rest:** `infrastructure/secrets/secrets.sops.yml` is age-encrypted. Decryption key is on the operator laptop, in the `SOPS_AGE_KEY` GitHub secret, and on paper.
- **Secrets in memory:** Vaultwarden session token is loaded via systemd `LoadCredential`, never written to an env var on disk.
- **Worktree isolation:** per-task `.env` files are `0600`, deleted on cleanup, and built from Vaultwarden items scoped to the ticket's Telegram topic.
- **Log redaction:** the sanitizer strips key material and tokens before disk or Telegram.
- **SSRF guards:** watchdog uses an allowlist for public endpoints; mainnet endpoints are rejected.
- **Agent containment:** engines run inside per-task worktrees with no Vaultwarden access, no push-to-main, and merges only through the veto-window path.
- **Mnemonic attestation** is disabled by default. It is only re-enabled for the content-publish pipeline as a per-spawn stdio process.

## Common Entry Points

- Start reading the loop: `fabric/workspace-manager/dispatch.py` → `poll_loop.py` → `kaneo_client.py`.
- Worktree lifecycle: `fabric/workspace-manager/main.py` and `vault.py`.
- Content pipeline state machine: `fabric/content-publisher/src/content_publisher/models.py`.
- Log redaction: `fabric/logs/sanitizer/filter.py`.
- Watchdog checks: `fabric/watchdog/checks/`.
- Orchestration prompts: `.symphony/workflows/`.
- Decision history: `work/coding-fabric/decisions.md`.

## See Also

- `CLAUDE.md` — detailed agent rules, swarm coordination, and claude-flow usage.
- `docs/architecture-report.md` — current architecture with Mermaid/PlantUML diagrams.
- `docs/claude-flow-cli.md` — how to use `npx @claude-flow/cli@latest` locally.
- `work/coding-fabric/tech-spec.md` — approved v0.2.0 tech spec.
