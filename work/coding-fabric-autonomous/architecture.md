---
feature: coding-fabric-autonomous
work_type: architecture
status: draft
created: 2026-05-24
parent: work/coding-fabric/user-spec.md
---

# Coding Fabric — Autonomous End-State

Operator-grade autonomous development loop. Telegram is the only human surface;
every other piece is an agent or a daemon. Operator can go away for a week,
the system keeps shipping.

## 1. Components

| Piece | Role | Source |
|---|---|---|
| **Telegram bot** (`telegram-ai-agent`) | Human gateway. Captures intent, streams progress, exposes `/reject` and other operator commands. | own fork of pavel-molyanov/telegram-ai-agent |
| **Kaneo** | Source of truth for tickets, projects, comments, PR links. State machine. Ships its own MCP server (18 tools — `create_task`, `update_task_status`, `move_task`, `create_task_comment`, etc.). | usekaneo/kaneo (MIT, self-hosted) |
| **Symphony** | Orchestrator daemon. Polls Kaneo for active tickets, leases per-task workspace, spawns the configured engine with the per-stage `WORKFLOW.md` prompt, tracks the run, retries on transient failures. | **Extension of the existing `fabric/workspace-manager/` FastAPI service** (already deployed, has workspace lifecycle / state.json / capacity caps / tests). Adds: poll loop, dispatch logic, agent runner, workflow loader — per [openai/symphony SPEC.md](https://github.com/openai/symphony/blob/main/SPEC.md). Kaneo adapter replaces the spec's Linear adapter; multi-engine execution layer replaces the Codex-only one. |
| **Engines** | Pure code execution. Claude, Codex, Qwen, Hermes, … plugged in via `ProviderAdapter` registry. Each registered engine answers an `OpenAI-compatible` (or native) protocol; selection is per-stage in `WORKFLOW.md`. | claude/codex CLIs already installed; qwen/hermes via OpenAI-compatible API endpoints |
| **Vaultwarden** | Long-term secret store. Symphony materialises a per-task `.env` into the workspace from `mnemonic/topic/<topic>` items. | self-hosted, already in role |
| **Workspace tree** | `~/code/symphony-workspaces/<TASK-ID>/` per task, isolated git clone of the task's repo, on-demand `.env`, on-demand skills symlink. Persisted across runs; cleaned on terminal state. | filesystem; managed by Symphony's Workspace Manager (spec §3.1.5) |
| **Molyanov skill bundle** | The methodology surface — `tech-spec-planning`, `do-task`, `code-writing`, `code-reviewing`, `test-master`, `security-auditor`, `pre-deploy-qa`, `post-deploy-qa`, etc. Available globally (`$HOME/.claude/skills/`) so every engine session can call them via `Skill`. | vendored at `infrastructure/ansible/files/claude-skills/`; refresh via `scripts/sync-skills.sh` |

Out: OpenClaw (only needed for Telegram-gateway role; we have that natively),
ruflo / claude-flow (dropped 2026-05-20).

In (reused, not replaced): `fabric/workspace-manager/` becomes Symphony. Its
existing FastAPI endpoints get demoted to internal helpers; new modules
(poll loop, dispatch, agent runner, workflow loader) wrap them. No
throwaway code.

## 2. Task Lifecycle

```
┌──────────────────┐                 ┌────────────────┐
│ Telegram (you)   │                 │   Kaneo        │
│ "do X in core"   │                 │   (tickets)    │
└────────┬─────────┘                 └────┬───────────┘
         │                                ▲ │
         │ /new-task or natural intent    │ │ MCP create_task
         ▼                                │ │
┌──────────────────┐  bot's claude calls  │ │
│ Bot              │  Kaneo MCP tools  ───┘ │
│  (claude session)│                        │
└──────────────────┘                        │
                                            │
                                            │ Symphony polls (tick)
                                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Symphony                                                    │
│  1. picks active ticket                                     │
│  2. resolves stage (code | review | qa) from ticket label   │
│  3. loads .symphony/workflows/<stage>.md from repo          │
│  4. leases workspace ~/code/symphony-workspaces/<TASK-ID>/  │
│  5. materialises .env from Vaultwarden                      │
│  6. spawns engine specified in WORKFLOW.md frontmatter      │
│  7. streams events; on completion updates ticket            │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│ Engine subprocess (claude / codex / qwen / hermes / …)   │
│  - reads WORKFLOW.md prompt as system prompt             │
│  - has Skill tool → Molyanov methodology                 │
│  - has Kaneo MCP tools → ticket writes                   │
│  - has bot MCP tools → Telegram comms                    │
│  - has Bash/Read/Write/Grep/Edit                         │
│  - cwd = workspace_path                                  │
└──────────────────────────────────────────────────────────┘
                             │
                             ▼
                         opens PR
                             │
                             ▼
              create_task: "Review TASK-ABC PR #N"
                             │
                             ▼
              Symphony picks up review ticket
              (different WORKFLOW.md, possibly different engine)
                             │
                             ▼
              review passes → create_task: "QA TASK-ABC"
                             │
                             ▼
              QA passes → ticket: "TASK-ABC ready to merge"
                             │
                             ▼
              veto window starts; bot posts to ops topic
                             │
              ─── no /reject in 24h ───
                             ▼
              Symphony calls `gh pr merge` → ticket → done
```

## 3. Cross-engine review

By design — `code.md` says `engine: claude`, `review.md` says `engine: codex`,
`qa.md` says `engine: claude` (with playwright MCP). All three workflows live
in the same repo. Symphony picks based on ticket label, never hard-codes the
engine choice in its own state.

Why: adversarial diversity. The model that wrote the code shouldn't be the
only one approving it. Plus, codex is stronger at certain languages
(JS/TS), claude at others (Python, infra) — pick per repo, per stage.

## 4. Auto-merge with veto window

Per-repo policy in `.symphony/policy.yml`:

```yaml
merge_policy: auto-with-veto   # | always-human-merge | full-auto
veto_window_hours: 24
veto_topic: ops                # Telegram topic to post the hold to
```

After review + QA tickets transition to `done`, Symphony:
1. Posts in the configured veto topic: `"PR #N ready to auto-merge in 24h.
   Reply /reject TASK-ABC to cancel."`
2. Schedules a Kaneo ticket transition for `<now> + 24h`
3. On tick at deadline: if no `/reject` came in (which would have transitioned
   the ticket to `cancelled`), calls `gh pr merge`. Otherwise leaves the
   ticket in `human-review`.

`full-auto` skips the wait. `always-human-merge` requires an explicit
`/approve` from the operator.

## 5. What's already in place

- [x] Telegram bot serving messages, claude/codex engines, allowlist auth (commit `6afb3b8`)
- [x] Molyanov skill bundle deployed to bot's HOME (commit `900801b`)
- [x] Vaultwarden running on VM (deployed 2026-05-22)
- [x] Skills + commands picked up by every claude session — verified live
- [x] Topic_config empty by default; topics are operator-created on demand

## 6. What's missing (Phase 2)

| # | Task | Blocker for |
|---|---|---|
| 11 | Kaneo running on VM | everything below |
| 12 | Kaneo MCP wired into bot's claude/codex sessions | bot creating tickets from chat |
| 13 | Engine registry (replace claude/codex if-elif with adapter map) | qwen/hermes addition; Symphony multi-engine |
| 14 | Symphony-Python (fork from SPEC.md with Kaneo + multi-engine) | autonomous loop |
| 15 | Per-repo `.symphony/workflows/{code,review,qa}.md` | what each stage actually does |
| 16 | Auto-merge with veto window | hands-off operation |

## 7. Decommission list (after Symphony lands)

- `vault.py` in `fabric/workspace-manager/` — Symphony's workflow can opt
  into vault, but the lease path no longer requires it. Drop the hard
  coupling; keep the function for stages that ask for it.
- The hard-coupled `materialise_env` call in `main.py` create-worktree flow.

## 8. Fallbacks (operator-side)

- **Telegram down → Kaneo web UI.** Already deployed at
  `kaneo.mnemonic-fabric.ts` (tailnet TLS). Phone or laptop. Symphony
  keeps running — it polls Kaneo, not the bot.
- **Hard recovery → Tailscale SSH** to the VM.
- **Email gateway** — backlog. Adds SMTP, inbound parsing, anti-spam.
  Not v0.1. Reconsider if Telegram + Kaneo prove insufficient.

## 9. Open questions / risks

- **Kaneo MCP auth** — tools need a Bearer token. How does the bot acquire it
  on behalf of the operator? Options: shared service token in sops, OAuth
  flow during first deploy. Resolve during task #12.
- **Per-engine MCP support** — claude has first-class MCP. Codex too. Qwen
  and Hermes via OpenAI-compatible endpoint don't natively speak MCP; we'd
  need a shim or use them only for stages that don't need MCP (less likely —
  Symphony agents need Kaneo MCP at minimum).
- **Symphony state durability** — spec says "tracker/filesystem-driven
  restart recovery without requiring a persistent database". Our Kaneo IS
  the persistent state. Symphony's in-memory queue is rebuilt on restart
  from Kaneo's active tickets. Should work.
- **Veto window across restarts** — if Symphony restarts during the 24h
  hold, the timer is lost. Need to persist the deadline as a ticket field
  (`scheduled_merge_at`) so any Symphony instance can pick it up.
- **Workspace cleanup** — when does the per-task workspace get nuked? After
  N days post-merge? On disk-pressure (fabric-watchdog already monitors)?
  Configurable per repo via `.symphony/policy.yml`.
