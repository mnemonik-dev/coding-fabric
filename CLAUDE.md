# coding-fabric — Claude Code Configuration

## Project: coding-fabric

Autonomous coding fabric. Operator types intent in Telegram → bot's claude creates
a GitHub repo + Kaneo project + ticket → Symphony's poll loop picks up the ticket
→ spawns a coding agent (claude or codex) in an isolated worktree following the
Molyanov methodology (user-spec → tech-spec → tasks → TDD → PR) → opens a PR →
reviewer agent (cross-engine) → QA → auto-merge with veto window. No human in
the loop after the intent.

### Development commands

There is **no repo-wide build**. Each Python service under `fabric/*/` is an
independent package with its own `pyproject.toml`, virtualenv, and test suite —
`cd` into the service directory and work there. There is no monorepo task runner;
`npx @claude-flow/cli` in the global CLAUDE.md refers to a coordination /
code-intelligence tool, not this project's build — see `docs/claude-flow-cli.md`
for how (and when) to use it here.

Python services (each identical shape — swap the directory):

```bash
cd fabric/workspace-manager        # or content-publisher, watchdog, logs/sanitizer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                             # full suite (testpaths + asyncio_mode preconfigured)
pytest tests/test_dispatch.py      # one file
pytest tests/test_dispatch.py::test_name   # one test
pytest -k worktree                 # by keyword
pytest --cov                       # coverage (config in pyproject)

ruff check .                       # lint  (content-publisher only ships ruff/mypy config)
ruff format .                      # format
mypy                               # type-check (content-publisher: strict mode)
```

Requires-python differs per package: `workspace-manager`/`watchdog` need **3.12+**,
`content-publisher` needs **3.11+**. `pythonpath` is set in each `pyproject.toml`
(`src` or `.`), so tests import modules directly — no `PYTHONPATH` export needed.

Run the workspace-manager locally (smoke):

```bash
cd fabric/workspace-manager
BIND_IP=127.0.0.1 uvicorn main:app --port 8080
curl -s http://127.0.0.1:8080/health | python -m json.tool
```

Rust byte-equivalence harness (conformance, not part of the runtime loop):

```bash
cd fabric/harnesses/byte-equivalence && make      # or: cargo build --release (in rust/)
```

### CI gates

- **`smoke-gate.yml`** — runs on PRs to `mnemonic-loop` touching `fabric/**` or ansible.
  Builds `fabric/safe-mode/smoke-gate` Docker image and asserts services come up. 15-min budget.
- **`deploy-fabric.yml`** — the canonical deploy. Triggers on `fabric-v*` tags (or manual
  `workflow_dispatch` with `apply`/`plan`/`rollback`). OpenTofu (Hetzner) + Ansible. Tofu
  state restored from a prior run's artifact. Persistent-VM mode (no destroy-recreate) — see Key constraints #1.
- **`e2e-smoke.yml`** / **`post-deploy-avp.yml`** — post-deploy verification over the tailnet
  (see `scripts/e2e/smoke.sh` and `scripts/avp/`).

### Pipeline (end-to-end)

```
Telegram intent
   │
   ▼  (bot's claude creates: GH repo + Kaneo project + ticket + TG topic)
Kaneo ticket (bus — every transition is a state-change + comment)
   │
   ▼  (Symphony poll loop, ~5s)
Symphony (workspace-manager)  ─►  claude / codex  in isolated worktree
                                       │
                                       ▼  commit → open PR  →  ticket: in-progress → review
                                       │
                                       ▼  Reviewer engine (different one) → ticket: review → qa
                                       │
                                       ▼  QA agent (tests) → ticket: qa → ready-to-merge
                                       │
                                       ▼  Auto-merge w/ veto window  (operator can /reject)
```

### Components map

| Name | Role | Code location | Runtime location on VM |
|------|------|---------------|------------------------|
| **Telegram bot** | Entry point. Spawns claude with Kaneo MCP + bot's MCP server. | `mnemonik-bridge-workspace/telegram-ai-agent/` | `/opt/telegram-ai-agent/` (systemd: `telegram-ai-agent.service`) |
| **Bot's MCP server** | Tools the bot's claude calls (e.g. `create_topic`). | `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py` | inside bot process |
| **Kaneo** | Ticket tracker + MCP server. Postgres backed. | (docker image, upstream) | `/opt/kaneo/` (docker compose). Public: https://kaneo.mnemonik.xyz |
| **Symphony / workspace-manager** | Poll loop + FastAPI. Dispatches tickets to engines in worktrees. | `fabric/workspace-manager/` (~1600 LOC) | `/opt/fabric/workspace-manager/` (systemd: `workspace-manager.service`) |
| **Vaultwarden** | Secret store. Used by Symphony when a task needs a secret. | (docker image, upstream) | `/opt/vaultwarden/` (docker compose). Public: vault.mnemonic-fabric:8443 (tailnet only) |
| **Caddy** | Reverse proxy for kaneo + vaultwarden. | (in vaultwarden compose) | `/opt/vaultwarden/caddy_conf_d/` |
| **Tailscale** | Tailnet for safe inter-service comms. | cloud-init injects | `100.x.x.x` on VM |
| **(planned) blogger** | Content publisher CLI. Wraps `mnemonik-blogger`. Stateless. | mnemonik-dev/blogger (external) | `/opt/blogger/` (CLI on PATH, no systemd) |
| **(planned) claude-blog** | Blog skill suite + scorer (`analyze_blog.py`). Used both as skill content and as quality gate. | mnemonik-dev/claude-blog (fork, external) | `/opt/claude-blog/` |

### Infrastructure

- **Cloud**: Hetzner Cloud (NOT Robot/dedicated). Server type `ccx33` = 8 dedicated vCPU / 32 GB RAM, location `fsn1`. Defined in `infrastructure/tofu/hetzner/variables.tf`.
- **Persistent volume** `fabric-data` (200 GB, ID 105783873) attached as `/dev/sdb` → `/mnt/HC_Volume_105783873/`. Kaneo Postgres data lives here so it survives VM destroy.
- **State on volume**: only Kaneo (48 MB). Vaultwarden data is on root disk (304 KB, would be lost on destroy — known issue).
- **Tofu state**: GitHub Actions artifact `tofu-state`, restored at the start of each deploy.
- **Secrets**: `infrastructure/secrets/secrets.sops.yml`, age-encrypted, decrypted at deploy time.

### Bus / handoff protocol

Agents do NOT talk directly. **Kaneo is the bus.** Every stage handoff is
`kaneo_client.set_status(ticket_id, new_state) + kaneo_client.add_comment(...)`.
Stage names = Kaneo column IDs (`in-progress` → `review` → `qa` → `ready-to-merge`).
Telegram topic is the human-facing mirror — `_notify_topic` in `dispatch.py` posts
progress alongside Kaneo comments.

Per-ticket "methodology" lives in the TARGET REPO at `.claude/skills/<stage>/WORKFLOW.md`.
Symphony loads the right one based on ticket state and points the engine at it.
Symphony stays dumb; methodology is version-controlled per project.

### Multi-PAT, repo binding

Kaneo project description carries directives parsed by `dispatch.py`:

```
repo: mnemonik-dev/<reponame>
pat: <pat-name>              ← key into GITHUB_PATS JSON map
topic: <tg-topic-id>         ← optional, for progress mirroring
```

`GITHUB_PATS` is a JSON map `{name: token}` in `/etc/symphony.env`. Fail-loud if
`pat:` references an unknown name.

### Key constraints (operational truths to respect)

1. **Persistent VM — no destroy-recreate.** DONE (tasks #24 + #25, committed `9c6c7ea` 2026-06-11). `deploy-fabric.yml` now applies in persistent-VM mode with the stable CI key (`CI_SSH_PRIVATE_KEY` secret → `TF_VAR_ci_ssh_pubkey`); there is no `tofu destroy` left. State off the persistent volume still survives normal deploys, but a manual VM rebuild would lose anything not on `/mnt/HC_Volume_105783873/` (e.g. Vaultwarden data — see below).
2. **Sops file appears as "Generic Password" in GitGuardian.** False positive — encrypted-at-rest payloads match the regex. Mark false-positive in dashboard.
3. **Co-Authored-By trailer:** never add it to user commits (see Rules section below).
4. **CI deploy is the canonical path** — don't edit `.env` files or systemd units directly on the VM (drop-ins as emergency-fix only, then mirror into the Ansible template + commit).
5. **Direct-to-trunk** development: this repo has one branch `claude/review-coding-fabric-spec-uUvMK` that functions as trunk. No PRs. Commits land directly. If a PR-based flow is wanted, set up `main` separately.

### Where to find more

- **`docs/vm-runbook.md`** — connection commands, paths, secrets map, common ops. Internal doc, NOT committed (gitignored).
- **`fabric/workspace-manager/dispatch.py`** — heart of the system (start at `_dispatch_ticket`).
- **`fabric/workspace-manager/poll_loop.py`** — 171 lines, short, read for the polling cadence.
- **`fabric/workspace-manager/kaneo_client.py`** — Kaneo REST adapter (endpoint shapes verified against live Kaneo).
- **`infrastructure/ansible/roles/`** — one role per service. Each role's `tasks/main.yml` is the canonical "how to install this on the VM".

### Known issues / open tasks

| # | Description |
|---|-------------|
| #13 | Bot engine selection is hardcoded `Literal["claude","codex"]` — should be a registry to add qwen/hermes |
| #19 | Bot streams tool-use noise (🌐/⏳) to Telegram as separate messages — filter |
| ~~#24~~ | ~~Remove destroy-recreate from CI deploy~~ — DONE (`9c6c7ea`, persistent-VM mode) |
| ~~#25~~ | ~~Switch CI from ephemeral to stable SSH key~~ — DONE (stable `CI_SSH_PRIVATE_KEY`, workflow committed) |
| —  | Vaultwarden data not on persistent volume |
| —  | Symphony `/health` reports `vault_reachable=false` — unconfirmed if it actually breaks anything |
| —  | Bot's `topic_config.json` resets to 0 bytes on VM recreate (state not on volume) → new TG topics get created redundantly after each redeploy |

---

## Rules

- Do what has been asked; nothing more, nothing less
- NEVER create files unless absolutely necessary — prefer editing existing files
- NEVER create documentation files unless explicitly requested
- NEVER save working files or tests to root — use `/src`, `/tests`, `/docs`, `/config`, `/scripts`
- ALWAYS read a file before editing it
- NEVER commit secrets, credentials, or .env files
- NEVER add a `Co-Authored-By` trailer to user commits unless this project's `.claude/settings.json` has `attribution.commit` set (#2078). The Claude Code Bash tool may suggest one in its default commit-message template — ignore it. `Co-Authored-By` is semantic authorship attribution under git/GitHub convention; the tool is the facilitator, not a co-author.
- Keep files under 500 lines
- Validate input at system boundaries

## Agent Comms (SendMessage-First Coordination)

Named agents coordinate via `SendMessage`, not polling or shared state.

```
Lead (you) ←→ architect ←→ developer ←→ tester ←→ reviewer
              (named agents message each other directly)
```

### Spawning a Coordinated Team

```javascript
// ALL agents in ONE message, each knows WHO to message next
Agent({ prompt: "Research the codebase. SendMessage findings to 'architect'.",
  subagent_type: "researcher", name: "researcher", run_in_background: true })
Agent({ prompt: "Wait for 'researcher'. Design solution. SendMessage to 'coder'.",
  subagent_type: "system-architect", name: "architect", run_in_background: true })
Agent({ prompt: "Wait for 'architect'. Implement it. SendMessage to 'tester'.",
  subagent_type: "coder", name: "coder", run_in_background: true })
Agent({ prompt: "Wait for 'coder'. Write tests. SendMessage results to 'reviewer'.",
  subagent_type: "tester", name: "tester", run_in_background: true })
Agent({ prompt: "Wait for 'tester'. Review code quality and security.",
  subagent_type: "reviewer", name: "reviewer", run_in_background: true })

// Kick off the pipeline
SendMessage({ to: "researcher", summary: "Start", message: "[task context]" })
```

### Patterns

| Pattern | Flow | Use When |
|---------|------|----------|
| **Pipeline** | A → B → C → D | Sequential dependencies (feature dev) |
| **Fan-out** | Lead → A, B, C → Lead | Independent parallel work (research) |
| **Supervisor** | Lead ↔ workers | Ongoing coordination (complex refactor) |

### Rules

- ALWAYS name agents — `name: "role"` makes them addressable
- ALWAYS include comms instructions in prompts — who to message, what to send
- Spawn ALL agents in ONE message with `run_in_background: true`
- After spawning: STOP, tell user what's running, wait for results
- NEVER poll status — agents message back or complete automatically

## Swarm & Routing

### Config
- **Topology**: hierarchical-mesh (anti-drift)
- **Max Agents**: 15
- **Memory**: hybrid
- **HNSW**: Enabled
- **Neural**: Enabled

```bash
npx @claude-flow/cli@latest swarm init --topology hierarchical --max-agents 8 --strategy specialized
```

### Agent Routing

| Task | Agents | Topology |
|------|--------|----------|
| Bug Fix | researcher, coder, tester | hierarchical |
| Feature | architect, coder, tester, reviewer | hierarchical |
| Refactor | architect, coder, reviewer | hierarchical |
| Performance | perf-engineer, coder | hierarchical |
| Security | security-architect, auditor | hierarchical |

### When to Swarm
- **YES**: 3+ files, new features, cross-module refactoring, API changes, security, performance
- **NO**: single file edits, 1-2 line fixes, docs updates, config changes, questions

### 3-Tier Model Routing

| Tier | Handler | Use Cases |
|------|---------|-----------|
| 1 | Agent Booster (WASM) | Simple transforms — skip LLM, use Edit directly |
| 2 | Haiku | Simple tasks, low complexity |
| 3 | Sonnet/Opus | Architecture, security, complex reasoning |

## Memory & Learning

### Before Any Task
```bash
npx @claude-flow/cli@latest memory search --query "[task keywords]" --namespace patterns
npx @claude-flow/cli@latest hooks route --task "[task description]"
```

### After Success
```bash
npx @claude-flow/cli@latest memory store --namespace patterns --key "[name]" --value "[what worked]"
npx @claude-flow/cli@latest hooks post-task --task-id "[id]" --success true --store-results true
```

### MCP Tools (use `ToolSearch("keyword")` to discover)

| Category | Key Tools |
|----------|-----------|
| **Memory** | `memory_store`, `memory_search`, `memory_search_unified` |
| **Bridge** | `memory_import_claude`, `memory_bridge_status` |
| **Swarm** | `swarm_init`, `swarm_status`, `swarm_health` |
| **Agents** | `agent_spawn`, `agent_list`, `agent_status` |
| **Hooks** | `hooks_route`, `hooks_post-task`, `hooks_worker-dispatch` |
| **Security** | `aidefence_scan`, `aidefence_is_safe`, `aidefence_has_pii` |
| **Hive-Mind** | `hive-mind_init`, `hive-mind_consensus`, `hive-mind_spawn` |

### Background Workers

| Worker | When |
|--------|------|
| `audit` | After security changes |
| `optimize` | After performance work |
| `testgaps` | After adding features |
| `map` | Every 5+ file changes |
| `document` | After API changes |

```bash
npx @claude-flow/cli@latest hooks worker dispatch --trigger audit
```

## Agents

**Core**: `coder`, `reviewer`, `tester`, `planner`, `researcher`
**Architecture**: `system-architect`, `backend-dev`, `mobile-dev`
**Security**: `security-architect`, `security-auditor`
**Performance**: `performance-engineer`, `perf-analyzer`
**Coordination**: `hierarchical-coordinator`, `mesh-coordinator`, `adaptive-coordinator`
**GitHub**: `pr-manager`, `code-review-swarm`, `issue-tracker`, `release-manager`

Any string works as a custom agent type.

## Build & Test

- ALWAYS run tests after code changes
- ALWAYS verify build succeeds before committing

```bash
npm run build && npm test
```

## CLI Quick Reference

```bash
npx @claude-flow/cli@latest init --wizard           # Setup
npx @claude-flow/cli@latest swarm init --v3-mode     # Start swarm
npx @claude-flow/cli@latest memory search --query "" # Vector search
npx @claude-flow/cli@latest hooks route --task ""    # Route to agent
npx @claude-flow/cli@latest doctor --fix             # Diagnostics
npx @claude-flow/cli@latest security scan            # Security scan
npx @claude-flow/cli@latest performance benchmark    # Benchmarks
```

26 commands, 140+ subcommands. Use `--help` on any command for details.

## Setup

```bash
claude mcp add claude-flow -- npx -y @claude-flow/cli@latest
npx @claude-flow/cli@latest daemon start
npx @claude-flow/cli@latest doctor --fix
```

**Agent tool** handles execution (agents, files, code, git). **MCP tools** handle coordination (swarm, memory, hooks). **CLI** is the same via Bash.
