---
created: 2026-06-06
status: draft
branch: dev
size: L
---

# Tech Spec: content-publish-pipeline

## Solution

Add a new long-running systemd service `content-publisher.service` that owns a JSONL job queue on the persistent Hetzner volume, spawns `claude` subprocesses with the `claude-blog` skill suite to write articles, and invokes the `mnemonik-blogger` CLI to publish them to the @mnemonik Telegram channel. The Telegram bot gains a `/publish_content` slash command (and matching MCP tool `publish_content_enqueue`) that enqueues jobs; preview + approval/timeout flow uses aiogram inline keyboards (`publish:approve/reject/retry`). Attestation goes through the local Mnemonik MCP role (toggled from descoped to enabled). The pipeline is **fully independent** of Symphony / Kaneo / workspace-manager.

## Architecture

### What we're building/modifying

- **`infrastructure/ansible/roles/content-publisher/`** *(new role)* — clones `mnemonik-dev/blogger` + `mnemonik-dev/claude-blog` to `/opt/`, creates Python venv, renders `/etc/blogger.env` from sops, installs the systemd unit, ensures persistent-volume mount.
- **`fabric/content-publisher/`** *(new Python package)* — long-running worker process. Mirrors `fabric/workspace-manager/`'s shape (`main.py` lifespan, `poll_loop.py`-style queue runner, `state.py`-style atomic-write persistence) but with a JSONL queue instead of in-memory state. Spawns `claude` as a standalone subprocess (NOT via `fabric.workspace_manager.agent_runner`).
- **`mnemonik-bridge-workspace/telegram-ai-agent/`** *(extend existing)*:
  - `src/telegram_bot/core/services/bot_commands.py:122` — append `publish_content` to `MOLYANOV_BOT_COMMANDS`.
  - `src/telegram_bot/core/handlers/publish.py` *(new)* — slash handler + 3 callback handlers (approve/reject/retry).
  - `src/telegram_bot/__main__.py:279-286` — register `publish_router` via `dp.include_router(publish_router)`.
  - `mcp-servers/bot/server.py` — add 5th `@mcp.tool()`: `publish_content_enqueue(prompt, fire_at=None, mode=None) -> dict`.
- **`infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml`** *(toggle)* — `mnemonic_mcp_enabled: true` (was `false`).
- **`infrastructure/secrets/secrets.sops.yml`** *(extend)* — add 6 keys: `blogger_telegram_bot_token`, `blogger_telegram_channel`, `publish_min_score`, `publish_approval_timeout_min`, `publish_mode`, `blogger_repo_ref`, `claude_blog_repo_ref`.
- **`infrastructure/ansible/playbooks/deploy.yml`** *(extend)* — insert `- role: content-publisher` between `fabric-services` and `restic-backups` roles.

### How it works

End-to-end flow for `/publish_content <prompt> [--at <ISO>] [--auto]`:

```
TG /publish_content      ┌──────────────────────────────────────┐
        │                │ bot process (telegram-ai-agent)       │
        ▼                │  - slash handler validates args       │
1. Bot validates ──────► │  - calls publish_content_enqueue MCP  │
   args, calls MCP tool  │    tool which writes to queue.jsonl   │
                         └──────────────────────────────────────┘
                              │ atomic append (POSIX O_APPEND)
                              ▼
   /mnt/HC_Volume_*/content-publisher/queue.jsonl
   {id, prompt, fire_at|null, mode, status=queued, ...}
                              ▲
                              │ poll every 5s
                         ┌──────────────────────────────────────┐
2. Worker picks job ───► │ content-publisher.service             │
                         │  - read+sort queue                    │
                         │  - select first job where fire_at<=now│
                         │  - CAS status: queued -> writing      │
                         │  - mkdir /var/lib/content-publisher/  │
                         │       work/<id>/                      │
                         │  - subprocess: claude -p <prompt>     │
                         │     --mcp-config /etc/blogger.mcp.json│
                         │     --strict-mcp-config               │
                         │     --skill claude-blog/blog-writer   │
                         │  - claude writes article.md           │
                         │  - CAS status: writing -> scoring     │
                         │  - subprocess: python                 │
                         │     /opt/claude-blog/scripts/         │
                         │     analyze_blog.py article.md        │
                         │  - parse score + issues               │
                         │  - render preview via blogger CLI:    │
                         │     mnemonik-blogger render           │
                         │     --platform telegram --from-file   │
                         │     article.md --stdout               │
                         │  - CAS status: scoring -> preview-sent│
                         │     stores approval_deadline=now+5min │
                         │  - send preview to operator (DM)      │
                         │    via Bot API sendMessage + inline kb│
                         └──────────────────────────────────────┘
                              │              │
        ┌─────────────────────┘              └──────────────────┐
        ▼ operator clicks                                       ▼ 5 min silence
3a. Bot callback         3b. Worker timer poll
    @router.callback_query(F.data.startswith("publish:"))    every 30s:
    Reads queue: CAS preview-sent -> publishing               for jobs where
    Invokes mnemonik-blogger post --from-file article.md       status=preview-sent
       --platforms telegram --live                              AND now>=approval_deadline:
    blogger -> Telegram Bot API (publisher bot token)            CAS preview-sent -> publishing
    Captures post_url, post_id                                   (same publish flow)
    CAS publishing -> published

4. Attestation
   mnemonic_sign_memory CLI: writes content + metadata to MCP server
   Returns receipt_hash
   CAS published -> done (with attestation hash recorded)

5. If attestation fails:
   CAS published -> attest-pending
   Worker retries every 5min for 24h
   If still failing: alert operator
```

**Crash recovery on worker startup:**

For each job in queue with in-flight status:
- `writing` / `scoring` → restart from start (worktree path persisted in job record; idempotent)
- `preview-sent` → check `approval_deadline`; if expired, publish; else leave alone (next callback or timer will handle)
- `publishing` → critical case: query Telegram channel via `getChat`+`getChatHistory`-equivalent for the tentative_post_id; if found → mark `published` and continue attestation; if not → publish

**Race: concurrent click + timeout fire:**

Atomic CAS on `status` field. Implementation: `state.py`-style flock + rewrite-then-rename pattern (existing in `fabric/workspace-manager/state.py:atomic_write`). Whichever process gets `status=preview-sent` and writes `status=publishing` first wins. Loser reads `status=publishing` and returns "too late".

### Shared resources

| Resource | Owner (creates) | Consumers | Instance count |
|----------|----------------|-----------|----------------|
| `/mnt/HC_Volume_*/content-publisher/queue.jsonl` | content-publisher.service (lifespan) | content-publisher worker (RW), bot callback handlers (RW via MCP tool) | 1 (file) |
| `/etc/blogger.env` | Ansible role (template) | content-publisher.service (EnvironmentFile=) | 1 (file) |
| `/etc/blogger.mcp.json` | Ansible role (template) | claude subprocesses spawned by worker | 1 (file) |
| Mnemonik MCP server | `mnemonic-mcp.service` (already deployed via existing role) | content-publisher worker (CLI calls), bot's claude (subprocess MCP) | 1 (process) |
| Telegram Bot API client (publisher bot) | mnemonik-blogger CLI (via env token) | publish step | per-invocation (CLI) |

## Decisions

### Decision 1: Standalone subprocess for claude spawn (not reuse `fabric.agent_runner`)
**Decision:** content-publisher worker spawns `claude` directly via `asyncio.create_subprocess_exec(["claude", "-p", prompt, "--mcp-config", "/etc/blogger.mcp.json", "--strict-mcp-config", ...])`. Does NOT import `fabric.workspace_manager.agent_runner`.
**Rationale:** Supports user-spec constraint "content-publisher.service отдельный сервис, НЕ интегрируется в Symphony". Avoids cross-service Python dependency: if Symphony's `agent_runner` changes signature, publisher silently breaks. Self-contained worker is easier to test and reason about.
**Alternatives considered:** Import `fabric.agent_runner.run_agent` as a library (DRY, but couples deploy of publisher to fabric/workspace-manager packaging).
**User-spec anchor:** Decision #1 in user-spec Технические решения.

### Decision 2: JSONL queue with atomic CAS (not SQLite)
**Decision:** Queue persisted as one JSON object per line in `/mnt/HC_Volume_*/content-publisher/queue.jsonl`. State transitions via read-modify-write of the whole file + `os.replace()` (POSIX atomic). Reuses pattern from `fabric/workspace-manager/state.py`.
**Rationale:** Existing codebase has flock+atomic-write helpers for the same concern. JSONL is grep-friendly for debugging. Queue size is bounded (jobs hours-to-days, not millions of rows). Supports user-spec Decision #3 ("очередь хранить как JSONL на персистентном volume").
**Alternatives considered:** SQLite on volume — overkill for <1000 jobs lifetime. Postgres — already used by Kaneo, but adds a network dependency.
**User-spec anchor:** Decision #3 + AC8 (queue survives VM restart).

### Decision 3: Mnemonik MCP role enabled and invoked as CLI
**Decision:** Toggle `mnemonic_mcp_enabled: true` in role defaults. Worker invokes `mnemonic_sign_memory` as a bare CLI command (per code-research §G — that's the existing pattern in 5 hook scripts). Bypasses JSON-RPC/HTTP.
**Rationale:** Existing callers use it as CLI. Avoids inventing a new invocation pattern. The `mnemonic-mcp` role already provides the binary on PATH.
**Alternatives considered:** Spawn a sidecar MCP client process with stdio JSON-RPC (matches MCP protocol, but no existing caller does this).
**User-spec anchor:** Decision #5 (attestation via local Mnemonik MCP) + AC7.

### Decision 4: Single renderer = `mnemonik-blogger render --stdout` for both preview and publish
**Decision:** Bot's preview and final publish both go through `mnemonik-blogger render --platform telegram --from-file article.md --stdout`. Worker captures stdout, sends bytes to bot, bot relays bytes via `sendMessage`. Then publish step calls `mnemonik-blogger post --from-file article.md --platforms telegram --live` (same input file). pytest verifies the render output bytes equal what blogger would actually send.
**Rationale:** Satisfies user-spec AC5 (byte-equality). Avoids duplicating Telegram-specific length/chunking logic in our code.
**Alternatives considered:** Import `mnemonik_blogger.render` Python module directly (faster: no subprocess); but binds our package to blogger's internals — fragile across upstream changes. CLI is the documented contract.
**User-spec anchor:** AC5.

### Decision 5: Separate publisher bot via sops `blogger_telegram_bot_token`
**Decision:** Operator creates `@mnemonik_publisher_bot` (or similar) via @BotFather, gets it admin of @mnemonik channel, puts token in sops as `blogger_telegram_bot_token`. blogger CLI reads this env var, not the operator-bot's token.
**Rationale:** User-spec Decision #6 (Q17=B). Privilege isolation: operator-bot lives in internal chats, publisher-bot only writes to public channel. Different threat models.
**User-spec anchor:** AC12, Decision #6.

### Decision 6: No auto-rewrite on low score; `[🔄 Retry]` button in preview
**Decision:** Worker computes score+feedback once. Always sends preview to operator. If `score < min_score`, preview text includes `Score: X/100 ⚠️ Issues: ...` and inline keyboard adds a third button `[🔄 Перегенерировать]`. Click creates a NEW job with same prompt + feedback as a hint.
**Rationale:** User-spec Decision #8 — operator's eye is the safety net; auto-rewrite drains credits. Preview must show why the score is low so operator can decide.
**Alternatives considered:** Self-rewrite loop with hard cap 3 (initial interview answer; reversed during validation round 1 per user adequacy review).
**User-spec anchor:** AC4 + Decision #8.

### Decision 7: PUBLISH_MODE=auto retained in MVP (env-flag branch)
**Decision:** Env var `PUBLISH_MODE` ∈ {`approval`, `auto`}. `approval` = default, sends preview. `auto` = skips preview, publishes directly after rendering. Operator gets post-fact DM.
**Rationale:** User-spec Decision #11 — supports long-term autonomous vision; env-flag adds ~10 LOC; testable as AC11.
**User-spec anchor:** AC11.

### Decision 8: Mnemonic MCP role enabled but feature does NOT depend on it being healthy for publishing
**Decision:** Publishing to Telegram and attestation are sequential but decoupled in state machine. Status `published` is set when blogger CLI returns success. If `mnemonic_sign_memory` then fails or times out, status moves to `attest-pending` and retries on a 5-min cadence for 24h. After 24h: operator notified, job moves to `attest-failed` (manual re-sign possible by re-running with stored content).
**Rationale:** Mnemonik MCP unreachable mid-publish must NOT block a successful TG post (can't roll back). User-spec Риск 1.
**User-spec anchor:** Риск 1.

### Decision 9: Single inline-keyboard prefix `publish:` with sub-action and short job_id
**Decision:** Callback data format: `publish:<action>:<job_id[:8]>` where action ∈ {`approve`,`reject`,`retry`}. Job_id is the first 8 chars of UUID. Handler resolves full job_id by prefix lookup in queue.
**Rationale:** Telegram callback_data limit is 64 bytes. Format fits in ~20. Mirrors existing pattern in `core/handlers/tail.py` and `cancel.py`.
**User-spec anchor:** Ограничения (callback_data 64 байт).

### Decision 10: Persistent volume mount enforced via systemd `RequiresMountsFor=` + `ConditionPathIsMountPoint=`
**Decision:** content-publisher.service unit declares `RequiresMountsFor=/mnt/HC_Volume_<id>/content-publisher` (`<id>` resolved at install-time by Ansible) AND `ConditionPathIsMountPoint=/mnt/HC_Volume_<id>`. If mount missing on boot, service refuses to start (loud fail in `journalctl`).
**Rationale:** Prevents silent fallback to root disk (data loss on next VM destroy). User-spec Риск 9 + AC15.
**User-spec anchor:** Риск 9, AC15.

### Decision 11: Pinned upstream refs (blogger + claude-blog), tech-spec verifies CLI surface BEFORE Wave 2 implementation
**Decision:** Ansible role defaults pin `blogger_repo_ref` and `claude_blog_repo_ref` to specific commit SHAs (not `main`). Tech-spec assumes specific CLI surface (`mnemonik-blogger render --platform telegram --from-file ... --stdout`, `mnemonik-blogger post --from-file ... --platforms telegram --live`, `python analyze_blog.py <file>`). Wave 1 includes a verification subtask that clones both repos to a CI runner and confirms these CLI signatures exist before implementation Waves 2-3 proceed.
**Rationale:** User-spec Ограничения mandates this gate.
**User-spec anchor:** Ограничения "обязательная верификация CLI surface".

### Decision 12: Bot's MCP server gains `publish_content_enqueue` tool; bot's claude is the slash command's parser
**Decision:** `/publish_content` is registered in `MOLYANOV_BOT_COMMANDS`; the corresponding aiogram handler in `publish.py` extracts the raw text and lets bot's claude call the new `publish_content_enqueue(prompt, fire_at, mode)` MCP tool. The tool writes to queue.jsonl directly (the bot already has read+write access to the volume path through its existing read/write paths configuration; alternative path: the bot forwards over HTTP to the worker, but adds a network hop).
**Rationale:** Reuses the existing bot's claude+MCP path (already wired in `bot_mcp_runtime.py`). Parsing `--at <ISO>` flags through bot's claude is more robust than regex (handles natural language too).
**Alternatives considered:** Pure aiogram handler with argparse-style flags (no LLM parse). Simpler in isolation, but loses NLU benefit. We can fall back to this if MCP path proves flaky.
**User-spec anchor:** Decision #2 user-spec ("отдельный systemd-сервис... ownership очереди").

## Data Models

### Queue JSONL schema (`queue.jsonl`)

One JSON object per line. Order = creation order. Worker selects by `min(fire_at)` for jobs with `status in {queued, writing→retry}`.

```json
{
  "id": "abc12345-...",                       // UUID v4
  "created_at": "2026-06-06T14:00:00Z",       // ISO 8601 UTC
  "fire_at": "2026-06-10T10:00:00Z",          // null for immediate
  "approval_deadline": "2026-06-10T10:05:00Z",// set when status=preview-sent
  "prompt": "Explain why Mnemonik...",         // operator's original text
  "feedback_for_retry": null,                  // analyze_blog feedback if this is a retry
  "mode": "approval",                          // "approval" | "auto"
  "status": "queued",                          // see state machine below
  "score": null,                               // analyze_blog score 0-100
  "issues": [],                                // analyze_blog feedback items
  "worktree_path": null,                       // /var/lib/content-publisher/work/<id>/
  "article_path": null,                        // <worktree>/article.md
  "preview_message_id": null,                  // Telegram message_id for in-place edits
  "tentative_post_id": null,                   // set just before blogger CLI call (crash recovery)
  "post_url": null,                            // set after publish
  "attestation_hash": null,                    // set after mnemonic_sign_memory
  "attest_attempts": 0,                        // retry counter for attestation
  "retries_of": null                           // job_id this was created by [🔄 Retry] from
}
```

### Job state machine

```
queued → writing → scoring → preview-sent → publishing → published → done
                                ↓ [🔄 click]
                                rejected (terminal)
                            ↓ rejected (terminal)
                       → publishing → published → attest-pending → done
                                                       ↓ 24h fail
                                                  attest-failed (terminal)
   (anywhere on hard error) → failed (terminal)
```

### `/etc/blogger.env` (rendered from sops)

```
# Bot tokens
TELEGRAM_BOT_TOKEN={{ blogger_telegram_bot_token }}
TELEGRAM_CHANNEL={{ blogger_telegram_channel | default('@mnemonik') }}

# Quality gate
MNEMONIK_CLAUDE_BLOG_PATH=/opt/claude-blog
MNEMONIK_MIN_SCORE={{ publish_min_score | default(80) }}

# Workflow
PUBLISH_MODE={{ publish_mode | default('approval') }}
PUBLISH_APPROVAL_TIMEOUT_MIN={{ publish_approval_timeout_min | default(5) }}

# Paths
CONTENT_PUBLISHER_QUEUE=/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher/queue.jsonl
CONTENT_PUBLISHER_WORKTREE_ROOT=/var/lib/content-publisher/work

# Safety
MNEMONIK_DRY_RUN=false
```

### `/etc/blogger.mcp.json` (claude MCP config for worker-spawned claude)

```json
{
  "mcpServers": {
    "mnemonik": {
      "command": "mnemonic-mcp",
      "args": []
    }
  }
}
```

## Dependencies

### New packages
- `mnemonik-dev/blogger` — Python package via `pip install -e .[twitter]` in venv at `/opt/blogger/venv/`. Pinned to commit SHA in role defaults.
- `mnemonik-dev/claude-blog` — Cloned as a skill directory at `/opt/claude-blog/`; not pip-installed (skill files + scripts only). Pinned to commit SHA.

### Using existing (from project)
- `fabric/workspace-manager/state.py` — atomic-write+flock pattern for queue.jsonl persistence (study, NOT import; we duplicate ~30 LOC to keep services decoupled per Decision #1).
- `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py` — `@mcp.tool()` pattern for `publish_content_enqueue`.
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/cancel.py` — aiogram `@router.callback_query(F.data == "cancel_cc")` pattern for `publish:approve/reject/retry`.
- `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2` — systemd unit template shape for `content-publisher.service.j2`.
- `infrastructure/ansible/roles/mnemonic-mcp/` — already provides Mnemonik MCP binary on PATH; toggle `mnemonic_mcp_enabled: true`.
- `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2` — sops→env-file render pattern for `blogger.env.j2`.

## Testing Strategy

**Feature size:** L

### Unit tests

- `fabric/content-publisher/tests/test_queue.py` — queue.jsonl round-trip, atomic CAS under simulated concurrent writers (threading), sort by fire_at, idempotent state transitions.
- `fabric/content-publisher/tests/test_state_machine.py` — every valid transition, every illegal transition rejected, crash recovery decisions for each in-flight status.
- `fabric/content-publisher/tests/test_preview_render.py` — byte-equality between `mnemonik-blogger render` stdout and what bot's `sendMessage` would forward. Mock article.md fixtures with edge cases (links, code blocks, >4096 char thread split).
- `fabric/content-publisher/tests/test_analyze_gate.py` — analyze_blog scorer invocation: high score → preview without retry button, low score → preview with retry button + issues in text.
- `fabric/content-publisher/tests/test_at_parse.py` — `--at <ISO>` parsing: future OK, past rejected with explicit error.
- `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_publish_handlers.py` — aiogram callback handler routing (`publish:approve`, `publish:reject`, `publish:retry`); short-id lookup in queue.

### Integration tests

- `fabric/content-publisher/tests/integration/test_queue_persist.py` — write job, kill+restart worker, assert job still in queue and resumes from correct status.
- `fabric/content-publisher/tests/integration/test_concurrent_publish.py` — simulate concurrent operator-click + timeout-timer with threading; assert exactly one publish call to mocked blogger CLI.
- `fabric/content-publisher/tests/integration/test_crash_recovery.py` — kill worker at each in-flight status; restart; assert correct resumption (writing → restart claude; scoring → restart scorer; preview-sent → re-check deadline; publishing → verify post via mocked TG API, no double-post).
- `fabric/content-publisher/tests/integration/test_attestation_retry.py` — mock Mnemonik MCP failure; assert job moves to attest-pending, retries on schedule, escalates after 24h.

### E2E tests

- `tests/e2e/test_publish_smoke.sh` — on the live VM (post-deploy hook only): operator runs `/publish_content TEST_FIXTURE_PROMPT` against the bot; script asserts a preview message arrives within 90s; clicks approve via Bot API; asserts a real post in @mnemonik within 30s; asserts `mnemonic_recall <hash>` returns the article body. Tear-down deletes the test post from @mnemonik manually (operator step).

## Agent Verification Plan

**Source:** user-spec "Как проверить" section (10 agent steps + 3 user steps).

### Verification approach

Per-task `Verify-smoke:` lives in each Implementation Task below. The Final Wave's post-deploy step runs the full live-environment E2E from user-spec "Агент проверяет" table. Tools required for that:

### Tools required

- `bash` + `curl` — Telegram Bot API for `getMyCommands`, `getUpdates`, `answerCallbackQuery` simulation, `getChat`.
- `ssh` — into op@VM for `journalctl -u content-publisher`, `journalctl -u telegram-ai-agent`, `cat /mnt/HC_Volume_*/content-publisher/queue.jsonl`.
- `mnemonic-mcp` CLI — `mnemonic_recall <hash>` for AC7.
- `Telegram MCP` (optional, if available in CI) — for simulating operator clicks on inline buttons in a scripted way.

## Risks

| Risk | Mitigation |
|------|-----------|
| Mnemonik MCP CLI surface differs from what hook scripts use | Wave 1 verification subtask reads upstream `mnemonic-mcp` defaults/main.yml:16 binary; confirms `mnemonic_sign_memory` signature; tech-spec amended before Wave 2 if surface differs |
| Blogger upstream renames `render --stdout` between pin and use | Wave 1 verification subtask clones repo at pinned SHA, runs `mnemonik-blogger render --help` to confirm flag exists |
| Bot's claude MCP tool call for `publish_content_enqueue` flaky (LLM may hallucinate args) | Fallback aiogram handler parses `--at <ISO> [--auto]` flags from raw slash text without going through claude. Lands behind feature flag in Wave 3 |
| Concurrent queue writes from bot (MCP tool) and worker conflict | Both use flock+atomic-write helper. fcntl.flock blocks; release on file-handle close. Worst case: bot waits ~10ms. Acceptable |
| Hetzner volume mount path varies (`/mnt/HC_Volume_<id>/` numeric ID) | Ansible role discovers ID via `ansible_facts.mounts | selectattr('device', 'equalto', '/dev/sdb')`, templates concrete path into env file + systemd unit |
| `RequiresMountsFor=` units exist before mount → systemd marks them `failed` permanently | systemd timer (10s) re-attempts start after fabric-services role completes. Alternative: use `After=mnt-HC_Volume_<id>.mount` — preferred but mount unit name is also derived from path |
| Mnemonik MCP role descoped → enabling it breaks existing services | `mnemonic_mcp_enabled: true` toggle confined to its own role; doesn't change existing services. Validation: `systemctl status mnemonic-mcp.service` post-deploy |
| Pinned blogger ref goes stale after upstream merges fixes | Manual bump: edit `roles/content-publisher/defaults/main.yml` + redeploy. No auto-update |

## User-Spec Deviations

None.

All decisions trace to either an explicit user-spec requirement (anchored above) or a `[TECHNICAL]` decision marked as such. The Mnemonik MCP role toggle (enable) was explicitly approved by user during Phase 3 clarification of this tech-spec.

## Acceptance Criteria

User-spec ACs 1-15 inherited as-is. Additional tech-level ACs:

- [ ] **AC-T1 — Pinned refs verified**: Before Wave 2 starts, both `mnemonik-dev/blogger` and `mnemonik-dev/claude-blog` cloned at their pinned SHAs, CLI surface (3 commands) checked, results recorded in `work/content-publish-pipeline/decisions.md` as "Verified at SHA X on date Y".
- [ ] **AC-T2 — No regression in existing services**: Post-deploy `systemctl status` of `telegram-ai-agent`, `workspace-manager`, `kaneo-*`, `vaultwarden-*`, `mnemonic-mcp` — all `active (running)`.
- [ ] **AC-T3 — Queue file mode + ownership**: `/mnt/HC_Volume_*/content-publisher/queue.jsonl` is `op:op 0600`; `/etc/blogger.env` is `root:op 0640` (sops contents not world-readable).
- [ ] **AC-T4 — RequiresMountsFor enforces fail-loud**: `umount` the volume in a test → `systemctl restart content-publisher` exits non-zero with explicit "mount missing" message.
- [ ] **AC-T5 — Pre-existing tests pass**: All workspace-manager + telegram-ai-agent test suites still green after our changes (proves the bot-side additions are non-breaking).

## Implementation Tasks

### Wave 1 (independent — foundation)

#### Task 1: Sops + secrets template extension
- **Description:** Add 7 new keys to `infrastructure/secrets/secrets.sops.yml.template` (and document expected types). Add empty placeholders to the encrypted `secrets.sops.yml` via `sops edit` (operator fills real values). Update `infrastructure/ansible/roles/fabric-services/tasks/main.yml` or new sops vars loader to expose them as Ansible facts.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Files to modify:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/secrets/secrets.sops.yml` (sops-edited), `infrastructure/ansible/playbooks/deploy.yml`
- **Files to read:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml` (sops load pattern)

#### Task 2: Enable Mnemonik MCP role
- **Description:** Toggle `mnemonic_mcp_enabled: true` in `infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml`. Verify role plays cleanly on existing VM (existing systemd unit, doesn't conflict). No code changes besides the flag.
- **Skill:** code-writing
- **Reviewers:** code-reviewer
- **Verify-smoke:** After deploy: `ssh op@VM 'systemctl is-active mnemonic-mcp'` → `active`; `mnemonic_sign_memory --help` runs on VM PATH.
- **Files to modify:** `infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml`
- **Files to read:** `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml`, `infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2`

#### Task 3: Ansible role `content-publisher` — install scaffold + upstream CLI verification
- **Description:** Create new role with `tasks/main.yml` (clones blogger + claude-blog at pinned SHAs, creates Python venv, pip-installs blogger, ensures `/var/lib/content-publisher/work/` and volume queue dir), `defaults/main.yml` (pinned SHAs, default paths), `templates/blogger.env.j2`, `templates/blogger.mcp.json.j2`, `templates/content-publisher.service.j2` (NOT yet started — Python service binary doesn't exist yet, just systemd unit ready). Add `- role: content-publisher` to `infrastructure/ansible/playbooks/deploy.yml`. **Includes upstream CLI verification step (Decision #11):** after cloning, role asserts `mnemonik-blogger render --help | grep -- --stdout` and `python /opt/claude-blog/scripts/analyze_blog.py --help` exit 0; fails the deploy with a clear error if not. Records the verified SHAs + verification outcome in `work/content-publish-pipeline/decisions.md` (committed manually after first green deploy).
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** `ansible-playbook --syntax-check`; on test deploy, `/opt/blogger/`, `/opt/claude-blog/`, `/etc/blogger.env`, `/etc/systemd/system/content-publisher.service` all present; systemd unit `enabled` but `inactive` (no binary yet); ansible task "Verify upstream CLI surface" passes.
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/tasks/main.yml`, `infrastructure/ansible/roles/content-publisher/defaults/main.yml`, `infrastructure/ansible/roles/content-publisher/templates/blogger.env.j2`, `infrastructure/ansible/roles/content-publisher/templates/blogger.mcp.json.j2`, `infrastructure/ansible/roles/content-publisher/templates/content-publisher.service.j2`, `infrastructure/ansible/playbooks/deploy.yml`, `work/content-publish-pipeline/decisions.md`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/tasks/main.yml`, `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml`, `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2`, `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2`

### Wave 2 (depends on Wave 1)

#### Task 4: `fabric/content-publisher/` package — queue + state machine + CAS
- **Description:** Create new Python package `fabric/content-publisher/` (pyproject.toml + src/content_publisher/). Implement `state.py` (atomic-write+flock helpers, mirrored from workspace-manager pattern), `queue.py` (JSONL load/append/CAS), `models.py` (Pydantic Job + status enum). NOT YET claude spawn or publish logic.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `fabric/content-publisher/pyproject.toml`, `fabric/content-publisher/src/content_publisher/state.py`, `fabric/content-publisher/src/content_publisher/queue.py`, `fabric/content-publisher/src/content_publisher/models.py`, `fabric/content-publisher/tests/test_queue.py`, `fabric/content-publisher/tests/test_state_machine.py`
- **Files to read:** `fabric/workspace-manager/state.py`, `fabric/workspace-manager/models.py`, `fabric/workspace-manager/tests/conftest.py`

#### Task 5: `fabric/content-publisher/` — claude spawn + analyze_blog gate + renderer
- **Description:** Implement worker step that takes a `writing` job: creates worktree, spawns claude as standalone subprocess with `--mcp-config /etc/blogger.mcp.json --strict-mcp-config -p <prompt>` and waits for completion (claude writes article.md via filesystem). Then calls `analyze_blog.py` as subprocess for score+issues. Then calls `mnemonik-blogger render --platform telegram --from-file --stdout` to produce preview bytes. Updates job status accordingly. Mock-friendly (env-injectable command paths for tests).
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-smoke:** unit-test fixture article.md → run worker → assert article.md created, score captured, preview bytes match `mnemonik-blogger render` output.
- **Files to modify:** `fabric/content-publisher/src/content_publisher/worker.py`, `fabric/content-publisher/src/content_publisher/spawn.py`, `fabric/content-publisher/tests/test_preview_render.py`, `fabric/content-publisher/tests/test_analyze_gate.py`
- **Files to read:** `fabric/workspace-manager/dispatch.py` (subprocess.exec pattern), `fabric/workspace-manager/agent_runner.py` (reference only — we don't import)

#### Task 6: `fabric/content-publisher/` — publish + attestation + retry queue
- **Description:** Implement publish step (invoke `mnemonik-blogger post --from-file --platforms telegram --live`, capture URL), attestation step (invoke `mnemonic_sign_memory` CLI, capture receipt hash). On attestation failure, move job to `attest-pending` with retry schedule. Implement crash-recovery scan on `main.py` lifespan startup.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/publish.py`, `fabric/content-publisher/src/content_publisher/attest.py`, `fabric/content-publisher/src/content_publisher/main.py`, `fabric/content-publisher/tests/test_attestation_retry.py`, `fabric/content-publisher/tests/integration/test_crash_recovery.py`
- **Files to read:** `fabric/workspace-manager/main.py` (lifespan pattern), `infrastructure/ansible/roles/mnemonic-mcp/templates/hooks/user-spec.sh.j2` (mnemonic_sign_memory usage example)

#### Task 7: Wire `content-publisher.service` to start on deploy + smoke check
- **Description:** Update Ansible role: after installing `fabric/content-publisher/` package into venv (similar to fabric-services pattern), enable and start `content-publisher.service`. Add Ansible `wait_for` task to verify the worker started and is polling. Update handlers for systemd unit changes.
- **Skill:** code-writing
- **Reviewers:** code-reviewer
- **Verify-smoke:** After deploy: `ssh op@VM 'systemctl is-active content-publisher'` → `active`; `journalctl -u content-publisher --since "5 minutes ago" | grep "polling queue"` matches at least one line.
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/tasks/main.yml`, `infrastructure/ansible/roles/content-publisher/handlers/main.yml`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/handlers/main.yml`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

### Wave 3 (depends on Wave 2 — bot integration)

#### Task 8: Bot's MCP server — `publish_content_enqueue` tool
- **Description:** Add `@mcp.tool()` `publish_content_enqueue(prompt: str, fire_at: str | None = None, mode: str | None = None) -> dict` in `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`. Validates fire_at parses + is future. Atomically appends a new Job to `CONTENT_PUBLISHER_QUEUE` env-var path. Returns `{job_id, status}`. Tests cover happy path + past-date rejection.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`, `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/tests/test_publish_tool.py`
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py` (other `@mcp.tool()` patterns), `fabric/content-publisher/src/content_publisher/queue.py` (Task 5)

#### Task 9: Bot — slash command + callback handlers (`publish.py` router)
- **Description:** Add `LocalizedBotCommand("publish_content", ...)` to `MOLYANOV_BOT_COMMANDS` in `bot_commands.py`. Create new file `core/handlers/publish.py` with: aiogram message handler for `/publish_content` (parses raw text, instructs bot's claude to call `publish_content_enqueue` MCP tool); 3 callback handlers for `publish:approve|reject|retry:<job_id[:8]>`. Register router in `__main__.py:279-286`. Sends preview message with inline keyboard when worker signals preview-ready (via a tiny internal HTTP callback exposed by worker — or by polling — TBD between dev + reviewer). For MVP: bot polls queue.jsonl directly for jobs with `status=preview-sent` and `preview_message_id=null`, sends preview + sets the field.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-smoke:** unit test: simulate callback `publish:approve:abc12345` → handler reads queue, CAS preview-sent→publishing, invokes mocked publish.
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py`, `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_publish_handlers.py`
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/cancel.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py:122`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py:202,279-286`

### Audit Wave

#### Task 10: Code Audit
- **Description:** Full-feature code quality audit. Read all source files created/modified across Tasks 1-9. Review holistically for cross-component issues: queue ownership boundary (worker writes vs. bot MCP-tool writes), state-machine correctness across files, error-handling consistency, shared-resources compliance with Architecture decisions. Write audit report.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 11: Security Audit
- **Description:** Full-feature security audit. Read all source files created/modified. Analyze for OWASP Top 10 across all components: sops secret handling, command injection in subprocess calls (claude, mnemonik-blogger, analyze_blog, mnemonic_sign_memory), Telegram callback_data tampering, queue.jsonl access control, blogger token leakage in logs. Write audit report.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 12: Test Audit
- **Description:** Full-feature test quality audit. Read all test files created. Verify coverage of every state transition + every callback path + crash recovery + attestation retry, meaningful assertions, test pyramid balance (unit-heavy, ≤4 integration, 1 E2E smoke). Write audit report.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 13: Pre-deploy QA
- **Description:** Acceptance testing: run all unit + integration tests on dev machine. Verify all acceptance criteria from user-spec (15 ACs) and tech-spec (5 AC-T). Generate AVP report.
- **Skill:** pre-deploy-qa
- **Reviewers:** none

#### Task 14: Deploy
- **Description:** Push branch, dispatch `deploy-fabric.yml` workflow, monitor for green (Tofu apply, Ansible playbook). Verify VM uptime preserved (#24/#25 already landed by this point, no destroy expected). Verify new role `content-publisher` applied, new service active.
- **Skill:** deploy-pipeline
- **Reviewers:** none

#### Task 15: Post-deploy verification
- **Description:** Live environment verification (operator + agent):
  - `/publish_content TEST: explain Mnemonik in one tweet` via operator's Telegram → expect preview within 90s — tool: Telegram MCP or operator's eyes
  - Click `[✓ Опубликовать]` → expect post in @mnemonik within 30s — tool: Bot API getChatHistory or operator's eyes
  - `mnemonic_recall <hash>` → returns article body — tool: ssh + mnemonic-mcp CLI
  - `systemctl is-active content-publisher mnemonic-mcp` → both `active` — tool: ssh + systemctl
  - Inspect `/mnt/HC_Volume_*/content-publisher/queue.jsonl` → job status=done — tool: ssh + cat
  - Delete the test post from @mnemonik manually after verification.
  Tools: Telegram MCP (or Bot API curl), ssh, bash, mnemonic-mcp CLI.
- **Skill:** post-deploy-qa
- **Reviewers:** none
