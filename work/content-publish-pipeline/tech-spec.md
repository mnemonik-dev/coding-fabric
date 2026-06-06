---
created: 2026-06-06
status: draft
branch: dev
size: L
---

# Tech Spec: content-publish-pipeline

## Solution

Add a new long-running systemd service `content-publisher.service` that owns a JSONL job queue on the persistent Hetzner volume, spawns `claude` subprocesses with the `claude-blog` skill suite to write articles, then publishes them to the @mnemonik Telegram channel via the `mnemonik-blogger` Python API. The Telegram bot gains a `/publish_content` slash command (and matching MCP tool `publish_content_enqueue`) that enqueues jobs; preview + approval/timeout flow uses aiogram inline keyboards (`publish:approve/reject/retry`). Attestation goes through the Mnemonik MCP server (installed via `npm install mnemonik-xyz/mcp` from `github.com/mnemonik-xyz/monorepo`, role flipped from descoped to enabled). The pipeline is fully independent of Symphony / Kaneo / workspace-manager.

## Architecture

### What we're building/modifying

- **`infrastructure/ansible/roles/content-publisher/`** *(new role)* — clones `mnemonik-dev/blogger` + `mnemonik-dev/claude-blog` to `/opt/`, creates Python venv with `--require-hashes`, renders `/etc/blogger.env` from sops, installs the systemd unit, ensures persistent-volume mount via `RequiresMountsFor=`.
- **`fabric/content-publisher/`** *(new Python package)* — long-running worker process. Mirrors `fabric/workspace-manager/`'s shape (FastAPI `main.py` lifespan, queue-poll loop, deadline-checker sub-loop, `StateStore`-style atomic-write persistence per `fabric/workspace-manager/state.py:_write_raw`+`_acquire`/`_release` pattern) but with a JSONL queue instead of in-memory state. Spawns `claude` as a standalone subprocess (NOT via `agent_runner` from workspace-manager).
- **`mnemonik-bridge-workspace/telegram-ai-agent/`** *(extend existing)*:
  - `src/telegram_bot/core/services/bot_commands.py:122` — append `publish_content` to `MOLYANOV_BOT_COMMANDS`.
  - `src/telegram_bot/core/handlers/publish.py` *(new)* — slash handler + 3 callback handlers (approve/reject/retry).
  - `src/telegram_bot/__main__.py` — register `publish_router` via `dp.include_router(publish_router)`.
  - `mcp-servers/bot/server.py` — add 5th `@mcp.tool()`: `publish_content_enqueue(prompt, fire_at=None, mode=None) -> dict`.
- **`infrastructure/ansible/roles/mnemonic-mcp/`** *(rewrite)* — replace binary-download flow with `npm install -g mnemonik-xyz/mcp`. Toggle `mnemonic_mcp_enabled: true`.
- **`infrastructure/secrets/secrets.sops.yml`** *(extend)* — add 7 keys: `blogger_telegram_bot_token`, `blogger_telegram_channel`, `publish_min_score`, `publish_approval_timeout_min`, `publish_mode`, `blogger_repo_ref`, `claude_blog_repo_ref`.
- **`infrastructure/ansible/playbooks/deploy.yml`** *(extend)* — insert `- role: content-publisher` between `fabric-services` and `telegram-ai-agent` roles (verified via `grep -n` of current order).

### How it works

End-to-end flow for `/publish_content <prompt> [--at <ISO>] [--auto]`:

```
TG /publish_content      ┌──────────────────────────────────────┐
        │                │ bot process (telegram-ai-agent)       │
        ▼                │  - aiogram handler validates from_user│
1. Bot validates ──────► │    against OPERATOR_USER_ID allowlist │
   args (LLM-parse;      │  - calls publish_content_enqueue MCP  │
   see Deviation #1)     │    via bot's claude                   │
                         │  - MCP tool imports shared CAS module │
                         │    (content_publisher.queue) for write│
                         └──────────────────────────────────────┘
                              │ atomic append (POSIX O_APPEND
                              │ + flock via shared lib)
                              ▼
   /mnt/HC_Volume_<id>/content-publisher/queue.jsonl
   {id, prompt, fire_at|null, mode, status=queued,
    cleanup_at, regenerated_to|null, ...}
                              ▲
                              │
                         ┌──────────────────────────────────────┐
2. Worker — TWO sub-loops:│ content-publisher.service             │
                         │  (a) queue-poll (every 5s):           │
                         │      pick first job where status=     │
                         │      queued AND fire_at<=now OR null  │
                         │  (b) deadline-checker (every 30s):    │
                         │      for jobs status=preview-sent     │
                         │      AND now>=approval_deadline:      │
                         │      CAS to publishing (timeout fire) │
                         │                                       │
                         │ Queue picks:                          │
                         │  - CAS status: queued -> writing      │
                         │  - mkdir /var/lib/content-publisher/  │
                         │       work/<sanitized_id>/            │
                         │  - subprocess (stdin for prompt):     │
                         │     claude --print --mcp-config       │
                         │       /etc/blogger.mcp.json           │
                         │       --strict-mcp-config             │
                         │       --skill claude-blog/blog-writer │
                         │     (prompt piped via stdin, NOT argv)│
                         │  - claude writes article.md           │
                         │  - CAS status: writing -> scoring     │
                         │  - subprocess: python                 │
                         │     /opt/claude-blog/scripts/         │
                         │     analyze_blog.py article.md        │
                         │     (path arg validated against       │
                         │      worktree containment)            │
                         │  - parse score + issues; truncate     │
                         │     issues array to first 3 for       │
                         │     preview                           │
                         │  - render preview via DIRECT Python   │
                         │     import (see Decision 4):          │
                         │     from mnemonik_blogger.content     │
                         │       .ingest import ingest_article   │
                         │     from mnemonik_blogger.content     │
                         │       .formatters import render       │
                         │     post=ingest_article(article_text) │
                         │     preview_bytes=render('telegram',  │
                         │       post)                           │
                         │  - CAS status: scoring -> preview-sent│
                         │     stores approval_deadline=now+5min │
                         │  - signals bot via DM path (worker    │
                         │     writes `preview_pending=true` and │
                         │     bot's 5s queue-poll sends preview │
                         │     msg with HMAC-tagged inline kb)   │
                         └──────────────────────────────────────┘
                              │              │
        ┌─────────────────────┘              └──────────────────┐
        ▼ operator clicks                                       ▼ deadline-checker fires
3a. Bot callback (authorized only)               3b. Worker deadline-checker:
    @router.callback_query(F.data.startswith     for jobs where status=preview-sent
       ("publish:"))                              AND now>=approval_deadline:
    Validates from_user==OPERATOR_USER_ID         CAS preview-sent -> publishing
    Validates HMAC on callback_data               (same publish flow as 3a)
    Reads queue: CAS preview-sent -> publishing
    BEFORE blogger call: write tentative_post_id
       (timestamp-derived) to queue.jsonl
    Invokes (DIRECT IMPORT, not CLI):
       from mnemonik_blogger.agent import publish_post
       result = publish_post(article_md, platforms=['telegram'],
                             dry_run=False)
       (uses same render() under the hood as preview)
    Captures result.post_url, result.post_id
    CAS publishing -> published

4. Attestation (decoupled from publish success)
   subprocess (content via stdin, not argv):
     mnemonic-mcp sign_memory --post-url <url>
       --score <N> --hash sha256(article_md)
   Returns receipt_hash
   CAS published -> done (with attestation_hash + content_sha256 recorded)

5. If attestation fails:
   CAS published -> attest-pending
   deadline-checker also retries attest-pending jobs every 5min for 24h
   If still failing: alert operator, status -> attest-failed
```

**Crash recovery on worker startup** — iterates queue.jsonl, for each in-flight job:
- `writing` / `scoring` → restart from start (worktree path persisted, idempotent)
- `preview-sent` → re-check `approval_deadline`; if expired, publish; else leave (deadline-checker or click will handle)
- `publishing` → CRITICAL CASE: look up `tentative_post_id` in queue; query `mnemonik_blogger.agent.find_post_by_id(tentative_post_id, channel='@mnemonik')` (direct Python import, returns Optional[PostMeta]). If found → mark `published` and continue attestation. If not → re-publish.
- `attest-pending` → resume attestation retry schedule.

**Why `tentative_post_id` MUST be written before blogger call**: a crash between blogger.post returning success and the queue write of `published` status WOULD cause a double-post on next worker start. By writing `tentative_post_id` BEFORE the blogger call, recovery can query the channel for that ID and detect the in-flight post.

**Race: concurrent click + timeout fire** — handled by atomic CAS on `status` field. Implementation: shared `content_publisher.queue.cas_status(job_id, expected, target)` helper that uses `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()`. BOTH bot callback handler AND worker deadline-checker MUST go through this shared module (no ad-hoc writes from either process). Whichever process writes `status=publishing` first wins; loser reads current state and returns "too late, status: <X>".

### Shared resources

| Resource | Owner (creates) | Consumers | Instance count |
|----------|----------------|-----------|----------------|
| `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` | Ansible role (dir+empty file at deploy) | content-publisher worker (RW via cas_status), bot callback handlers (RW via cas_status), bot MCP tool (W via cas_status) | 1 (file) |
| `content_publisher.queue.cas_status()` shared CAS helper | `fabric/content-publisher/src/content_publisher/queue.py` | Worker + bot (both processes import it) | 1 (singleton func) |
| `/etc/blogger.env` | Ansible role (template, mode 0640 root:op) | content-publisher.service (EnvironmentFile=) | 1 (file) |
| `/etc/blogger.mcp.json` | Ansible role (template) | claude subprocesses spawned by worker | 1 (file) |
| Mnemonik MCP server (`mnemonic-mcp.service`) | `mnemonic-mcp` role (npm install -g) | content-publisher worker (CLI calls), bot's claude (subprocess MCP) | 1 (process) |
| Telegram Bot API client (publisher bot) | `mnemonik_blogger.agent.publish_post` (env-loaded token) | publish step | per-invocation (in-process) |

## Decisions

### Decision 1: Standalone subprocess for claude spawn (not reuse `agent_runner`)
**Decision:** content-publisher worker spawns `claude` directly via `asyncio.create_subprocess_exec("claude", "--print", "--mcp-config", "/etc/blogger.mcp.json", "--strict-mcp-config", stdin=PIPE, env=restricted_env)`. Prompt sent via stdin (NOT argv). Does NOT import workspace-manager's `agent_runner` (which is a flat module import `from agent_runner import run_agent`, scoped to that service's package only).
**Rationale:** Supports user-spec "content-publisher.service отдельный сервис, НЕ интегрируется в Symphony". Self-contained worker. stdin avoids argv injection + process-listing leak.
**Alternatives considered:** Refactor `agent_runner` into a shared `fabric/common/` library. Rejected — invasive change to a working service for a marginal DRY win.
**User-spec anchor:** Decision #1 in user-spec Технические решения.

### Decision 2: JSONL queue with atomic CAS (not SQLite)
**Decision:** Queue persisted as one JSON object per line in `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl`. State transitions via `content_publisher.queue.cas_status()` helper using `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()` (POSIX atomic). Mirrors pattern of `fabric/workspace-manager/state.py` (`StateStore._write_raw` + `_acquire`/`_release` helpers).
**Rationale:** Existing codebase has the same concern solved similarly. JSONL is grep-friendly. Queue size bounded (<1000 jobs lifetime). Shared CAS primitive used by BOTH bot and worker processes (Decision 9 architectural).
**Alternatives considered:** SQLite — overkill for <1000 rows + WAL adds locking quirks. Redis — adds a network dependency. Postgres — used by Kaneo, but cross-service coupling.
**User-spec anchor:** Decision #3 + AC8.

### Decision 3: Mnemonik MCP installed via `npm install -g mnemonik-xyz/mcp`
**Decision:** Rewrite `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml` to replace the bogus binary-download (`mnemonic_mcp_binary_url`/`mnemonic_mcp_binary_sha256` were placeholders pointing at a non-existent `github.com/mnemonic-ai/mnemonic-mcp` repo) with `npm install -g mnemonik-xyz/mcp` from the real monorepo at `github.com/mnemonik-xyz/monorepo`. Pin to a specific commit ref via `npm install mnemonik-xyz/mcp#<sha>`. Toggle `mnemonic_mcp_enabled: true`. The binary lands at a standard npm-global path (`/usr/local/bin/mnemonic-mcp` or similar); systemd unit invokes it from PATH.
**Rationale:** Real upstream URL provided by operator. npm install gives reproducible install + node_modules lock. Existing role file structure stays; only the install task body changes.
**Alternatives considered:** Local stub Python wrapper that writes attestations to JSONL (deferred attestation). Rejected because attestation is the product differentiator per user-spec ("sweetest part").
**User-spec anchor:** Decision #5 (attestation via local Mnemonik MCP) + AC7.

### Decision 4: Direct Python import for renderer (not CLI)
**Decision:** Bot's preview and final publish both use the same in-process function call:
```python
from mnemonik_blogger.content.ingest import ingest_article
from mnemonik_blogger.content.formatters import render
post = ingest_article(article_md_text)
preview_bytes = render('telegram', post)  # used for preview
# Later (publish path):
from mnemonik_blogger.agent import publish_post
result = publish_post(article_md_text, platforms=['telegram'], dry_run=False)
# publish_post internally calls the SAME render() under the hood, guaranteeing byte-equality
```
The earlier draft of this spec referenced `mnemonik-blogger render --stdout` — this CLI subcommand **does not exist** in upstream blogger (only `platforms`, `preview`, `post`, `score`). The `preview` subcommand emits multi-segment decorated output (`===== telegram =====` headers) that is NOT byte-identical to publish output.
**Rationale:** Direct import is the only way to satisfy user-spec AC5 (byte-equality). Pinning the blogger ref in the role mitigates upstream API drift.
**Alternatives considered:** Parse `mnemonik-blogger preview` output and strip telegram block. Rejected — preview output is decorated and not byte-identical to `post` output; AC5 would have to be downgraded to "visually similar".
**User-spec anchor:** AC5.

### Decision 5: Separate publisher bot via sops `blogger_telegram_bot_token`
**Decision:** Operator creates `@mnemonik_publisher_bot` via @BotFather, gets it admin of @mnemonik channel, puts token in sops as `blogger_telegram_bot_token`. `mnemonik_blogger.agent.publish_post` reads `TELEGRAM_BOT_TOKEN` from env (rendered from this sops key into `/etc/blogger.env`), distinct from operator-bot's token in `/etc/telegram-ai-agent/.env`.
**Rationale:** User-spec Decision #6 (Q17=B). Privilege isolation: operator-bot's token leaking gives chat access; publisher-bot's leaking only allows channel posts (smaller blast radius).
**Alternatives considered:** Reuse operator-bot token + restrict via Telegram per-bot channel-admin permissions. Rejected — same token in two contexts means any compromise is total.
**User-spec anchor:** AC12, Decision #6.

### Decision 6: No auto-rewrite on low score; `[🔄 Retry]` button in preview
**Decision:** Worker computes score+feedback once. Always sends preview to operator. If `score < min_score`, preview text includes `Score: X/100 ⚠️ Issues: <first 3 issues>` and inline keyboard adds `[🔄 Перегенерировать]`. Click creates a NEW job with same prompt + feedback as a hint; old job records `regenerated_to=<new_id>`.
**Rationale:** User-spec Decision #8 — operator's eye is the safety net. Truncation to 3 issues per user-spec AC4.
**Alternatives considered:** Self-rewrite loop with hard cap 3 (initial interview answer; reversed during user-spec validation round 1).
**User-spec anchor:** AC4 + Decision #8.

### Decision 7: PUBLISH_MODE=auto retained in MVP (env-flag branch)
**Decision:** Env var `PUBLISH_MODE` ∈ {`approval`, `auto`}. `approval` = default. `auto` = skip preview step, publish directly after render. Operator gets post-fact DM in the exact format: `Auto-published: <link>. Receipt: <hash>. Score: <N>.`
**Rationale:** User-spec Decision #11.
**Alternatives considered:** Defer to a separate feature. Rejected — env-flag branch is ~10 LOC, testable as AC11.
**User-spec anchor:** AC11.

### Decision 8: Mnemonik MCP role enabled but feature does NOT block publish on attestation failure
**Decision:** Publishing to Telegram and attestation are sequential but decoupled. Status `published` is set when blogger.publish_post returns success. If `mnemonic-mcp sign_memory` then fails, status moves to `attest-pending`; deadline-checker retries every 5min for 24h. After 24h: status `attest-failed`, operator notified.
**Rationale:** Mnemonik MCP unreachable mid-publish must NOT block a successful TG post (can't roll back). User-spec Риск 1.
**Alternatives considered:** Atomic publish+attest (try-attest-before-publish). Rejected — attesting before publish risks recording an attestation for content that never gets posted.
**User-spec anchor:** Риск 1.

### Decision 9: HMAC-tagged callback_data + 12-char job_id prefix + operator authorization
**Decision:** Callback data format: `publish:<action>:<job_id[:12]>:<hmac>` where `hmac` = first 8 bytes of `HMAC-SHA256(server_secret, "<action>:<job_id[:12]>")`. Total size <40 bytes (fits Telegram's 64-byte limit). Handler validates HMAC before any action. Additionally: handler checks `callback.from_user.id == OPERATOR_USER_ID` (hardcoded constant loaded from `/etc/blogger.env`).
Pattern mirrors `core/handlers/tail.py:227-228` (real prefix-based handler — earlier draft cited cancel.py which uses exact match `F.data == "cancel_cc"`, not prefix).
**Rationale:** Telegram callback_data limit + 8-char UUID prefix is birthday-bound (collision at ~1k jobs); 12 chars + HMAC removes both collision and tamper risk.
**Alternatives considered:** 8-char id + status-oracle response. Rejected — enables enumeration via "too late" replies.
**User-spec anchor:** Ограничения (callback_data 64 байт) + AC6.

### Decision 10: Persistent volume mount enforced via systemd `RequiresMountsFor=` with concrete volume ID
**Decision:** content-publisher.service unit declares `RequiresMountsFor=/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher`. The `hetzner_volume_id` is resolved at install time by Ansible (`ansible_facts.mounts | selectattr('device', 'equalto', '/dev/sdb')`) into a concrete integer ID and templated. NO literal `*` ever reaches the unit file.
**Rationale:** Prevents silent fallback to root disk + prevents Ansible templating a useless glob.
**Alternatives considered:** `After=mnt-HC_Volume_<id>.mount` — preferred per systemd best practice but the mount unit name is also derived from path. RequiresMountsFor= is shorter and equivalent.
**User-spec anchor:** Риск 9, AC15.

### Decision 11: Pinned upstream refs (blogger + claude-blog), Ansible role verifies CLI surface
**Decision:** Role defaults pin `blogger_repo_ref` and `claude_blog_repo_ref` to specific commit SHAs (not `main`). Ansible role asserts as part of install: `python -c "from mnemonik_blogger.content.formatters import render; from mnemonik_blogger.content.ingest import ingest_article; from mnemonik_blogger.agent import publish_post"` exits 0. `python /opt/claude-blog/scripts/analyze_blog.py --help` exits 0. If either fails: deploy fails loud with module path mismatch error.
**Rationale:** User-spec mandates this gate. Module import test is more reliable than CLI flag grep (which would have caught `--stdout` non-existence anyway).
**User-spec anchor:** Ограничения "обязательная верификация CLI surface".

### Decision 12: pip install with `--require-hashes` for blogger venv
**Decision:** Ansible role generates a hashes-locked requirements file (`pip-compile --generate-hashes` from a base requirements file checked into the role) and installs blogger via `pip install --require-hashes -r requirements.locked.txt`. Pinned blogger ref is the source of truth; lock file pins all transitive deps.
**Rationale:** Supply chain. User-spec is silent on this but security-audit findings flagged it as a real gap.
**Alternatives considered:** Trust transitive deps (no `--require-hashes`). Rejected — public repo + popular dep names = realistic typosquatting risk.
**User-spec anchor:** [TECHNICAL] — extends user-spec implicit constraint of pinned blogger ref.

## Data Models

### Queue JSONL schema (`queue.jsonl`)

One JSON object per line. Order = creation order. Worker queue-poll selects by `min(fire_at)` (or null=immediate) for jobs with `status in {queued}`.

```json
{
  "id": "abc12345-...",                       // UUID v4, validated as strict UUID
  "created_at": "2026-06-06T14:00:00Z",       // ISO 8601 UTC
  "fire_at": "2026-06-10T10:00:00Z",          // null for immediate
  "approval_deadline": "2026-06-10T10:05:00Z",// set when status=preview-sent
  "cleanup_at": "2026-06-11T10:05:00Z",       // 24h after preview-sent; worktree GC
  "prompt": "Explain why Mnemonik...",         // operator's original text
  "feedback_for_retry": null,                  // analyze_blog issues if this is a retry
  "mode": "approval",                          // "approval" | "auto"
  "status": "queued",                          // see state machine below
  "score": null,                               // analyze_blog score 0-100
  "issues": [],                                // analyze_blog feedback; first 3 shown in preview
  "worktree_path": null,                       // /var/lib/content-publisher/work/<id>/
  "article_path": null,                        // <worktree>/article.md
  "preview_message_id": null,                  // Telegram message_id for in-place edits
  "preview_pending": false,                    // bot's queue-poll signal flag
  "tentative_post_id": null,                   // WRITTEN BEFORE blogger call for crash recovery
  "post_url": null,                            // set after publish
  "content_sha256": null,                      // sha256(article_md_bytes), recorded with attestation
  "attestation_hash": null,                    // set after mnemonic_sign_memory
  "attest_attempts": 0,                        // retry counter for attestation
  "regenerated_to": null,                      // job_id created by [🔄 Retry] from this job
  "retries_of": null                           // inverse link: job this was created by [🔄 Retry] from
}
```

### Job state machine

```
queued ──► writing ──► scoring ──► preview-sent ─┬─► publishing ──► published ──► attest-pending ──► done
   │          │           │              │       ├─► rejected (terminal — preview-sent + [✗] click)
   │          │           │              │       └─► (regenerated; new job created, this stays at
   │          │           │              │            preview-sent until cleanup_at, then archived)
   │          │           │              │
   └──────────┴───────────┴──────────────┴─► failed (terminal — any hard error, operator notified)
                                                                              │
                                                                              ▼ 24h fail
                                                                       attest-failed (terminal)
```

**Spawn failure path** (user-spec AC10): worker catches subprocess exit code != 0 OR import error during direct-import render → CAS to `failed` → sends Telegram DM to operator via bot's existing `send_message` MCP tool path (worker writes a `notify_pending` flag; bot's queue-poll handler picks it up and sends the DM with traceback in a `<pre>` block, truncated to 4000 chars).

### `/etc/blogger.env` (rendered from sops)

```
# Bot tokens
TELEGRAM_BOT_TOKEN={{ blogger_telegram_bot_token }}
TELEGRAM_CHANNEL={{ blogger_telegram_channel | default('@mnemonik') }}

# Authorization
OPERATOR_USER_ID={{ telegram_ai_agent_allowed_user_ids[0] }}
CALLBACK_HMAC_SECRET={{ publish_callback_hmac_secret }}

# Quality gate
MNEMONIK_CLAUDE_BLOG_PATH=/opt/claude-blog
MNEMONIK_MIN_SCORE={{ publish_min_score | default(80) }}

# Workflow
PUBLISH_MODE={{ publish_mode | default('approval') }}
PUBLISH_APPROVAL_TIMEOUT_MIN={{ publish_approval_timeout_min | default(5) }}

# Paths (concrete volume ID, no literal *)
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
- `mnemonik-dev/blogger` — Python package via `pip install --require-hashes -r requirements.locked.txt` in venv at `/opt/blogger/venv/`. Pinned to commit SHA in role defaults.
- `mnemonik-dev/claude-blog` — Cloned as a skill directory at `/opt/claude-blog/`; not pip-installed. Pinned to commit SHA.
- `mnemonik-xyz/mcp` (from `github.com/mnemonik-xyz/monorepo`) — installed via `npm install -g mnemonik-xyz/mcp#<sha>`. Provides `mnemonic-mcp` binary on PATH.

### Using existing (from project)
- `fabric/workspace-manager/state.py:_write_raw`, `_acquire`, `_release` — atomic-write+flock pattern, READ for reference; we copy ~30 LOC into `fabric/content-publisher/src/content_publisher/state.py` (decoupled services per Decision 1).
- `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py` — `@mcp.tool()` pattern.
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py:227-228` — aiogram `@router.callback_query(F.data.startswith("ttui:"))` pattern (REAL prefix-based handler).
- `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2` — systemd unit template shape.
- `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2` — sops→env-file render pattern.

## Testing Strategy

**Feature size:** L

Mock points centralized in `fabric/content-publisher/tests/conftest.py` (single source of truth): `mock_analyze_blog`, `mock_blogger_publish_post`, `mock_mnemonic_mcp`, `mock_claude_subprocess`. Same fixtures imported by bot-side tests where needed.

### Unit tests

- `test_queue.py` — JSONL round-trip; `cas_status` atomic under simulated concurrent writers (threading); sort by fire_at; idempotent state transitions.
- `test_state_machine.py` — every valid + every illegal transition; crash recovery decisions per in-flight status.
- `test_preview_render.py` — **byte-equality cross-test**: render `article.md` fixture via `mnemonik_blogger.content.formatters.render('telegram', post)` AND capture what `publish_post(..., dry_run=True)` would have sent (it uses the same render internally); assert byte-identical. Tests several edge cases (links, code blocks, >4096-char split into thread).
- `test_analyze_gate.py` — high score (90): preview without retry button; low score (50): preview with `[🔄]` button + first 3 issues in text (truncate the 4th+ issues).
- `test_at_parse.py` — future ISO accepted; past ISO rejected with explicit error.
- `test_uuid_validation.py` — accepts only valid UUID-v4 strings; rejects `../`, control chars, leading dash, too-long, empty.
- `test_publish_handlers.py` (bot side) — aiogram callback routing for `publish:approve|reject|retry`; HMAC validation; `from_user.id != OPERATOR_USER_ID` rejected with `answerCallbackQuery` "Not authorized".
- `test_deadline_checker.py` — 30s sub-loop math: job with `approval_deadline = now-1s` fires; `approval_deadline = now+10s` doesn't.
- `test_attest_retry.py` — retry cadence: 5min intervals; escalation at 24h.
- `test_spawn_failure_path.py` — claude subprocess returns non-zero → CAS `failed` + notify_pending=true (AC10).
- `test_auto_mode.py` — `PUBLISH_MODE=auto`: enqueue + run → no preview step, post-fact notify in the exact format (AC11).
- `test_separate_bot_token.py` — `mnemonik_blogger.agent.publish_post` reads `TELEGRAM_BOT_TOKEN` from `/etc/blogger.env`-style env (NOT operator-bot's `.env`) (AC12).
- `test_required_mounts_for.py` — render systemd unit template, assert `RequiresMountsFor=` line has concrete numeric `<id>` (no literal `*`); rendering with unresolved `hetzner_volume_id` fact MUST fail loud (AC15).

### Integration tests

- `test_queue_persist.py` — write job, kill+restart worker, assert resumes from correct status.
- `test_concurrent_publish.py` — threading: simulate concurrent click + deadline-fire; assert exactly one `publish_post` call to mocked blogger.
- `test_crash_recovery.py` — kill at each in-flight status; assert correct resumption (writing → restart; scoring → restart scorer; preview-sent → re-check deadline; publishing → call `find_post_by_id` mock returning Some/None; no double-post in Some case).
- `test_attestation_pipeline.py` — happy path + Mnemonik MCP failure → attest-pending + retry cadence + 24h escalation.

### E2E tests

- `tests/e2e/test_publish_smoke.sh` — post-deploy live: operator runs `/publish_content TEST_FIXTURE_PROMPT` against the bot; script asserts preview within 90s; clicks approve via Bot API; asserts real post in @mnemonik within 30s; asserts `mnemonic-mcp recall <hash>` returns article body. Teardown: script outputs the chat_id + message_id and PROMPTS operator to delete (manual step is documented; scripted deletion via Bot API `deleteMessage` is a stretch goal).

## Agent Verification Plan

**Source:** user-spec "Как проверить" section (10 agent steps + 3 user steps).

### Verification approach

Per-task `Verify-smoke:` lives in each Implementation Task below. The Final Wave's post-deploy step runs the full live-environment E2E from user-spec.

### Tools required

- `bash` + `curl` — Telegram Bot API: `getMyCommands`, `getUpdates`, `answerCallbackQuery`, `getChat`.
- `ssh` — into op@VM for `journalctl -u content-publisher`, `journalctl -u telegram-ai-agent`, `cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl`.
- `mnemonic-mcp` CLI — `mnemonic-mcp recall <hash>` for AC7.
- `Telegram MCP` (optional, if available in CI) — for simulating operator clicks.

## Risks

| Risk | Mitigation |
|------|-----------|
| Mnemonik MCP CLI signature differs after npm install | Decision 11 verification: `mnemonic-mcp --help` checked as part of role; if `sign_memory` subcommand differs, fail loud |
| Blogger upstream renames internal modules between pin and use | Decision 11 import test catches at Ansible time, before service starts. Pinned ref protects from drift |
| Bot's claude hallucinates args to `publish_content_enqueue` MCP tool | MCP tool internally re-validates: parses `fire_at` as ISO 8601, rejects past dates; truncates `prompt` to 8000 chars; mode ∈ {approval, auto} only. Validation errors return clear messages via MCP back to claude |
| Concurrent queue writes from bot (MCP tool) and worker | Shared `cas_status` from `content_publisher.queue` (Decision 9). Both processes flock; worst case ~10ms wait |
| Hetzner volume mount path varies (numeric ID) | Ansible discovers ID via `ansible_facts.mounts`; templates concrete number into env file + systemd unit; no `*` leaks |
| Mnemonik MCP role's npm install fails (network/registry/repo) | Role exits non-zero with clear "could not install mnemonik-xyz/mcp" message; entire deploy fails — easy to debug |
| Pinned refs go stale | Manual bump: edit `defaults/main.yml` + redeploy. Documented in role README |
| Operator forgets to add publisher bot as channel admin | Ansible role's E2E smoke (Task 7) attempts a `getChat` against `@mnemonik` using the publisher token; fails clearly if bot not in channel |
| Subprocess argv exposes content via `ps` | All content goes via stdin (`asyncio.create_subprocess_exec(..., stdin=PIPE)` + `proc.stdin.write(content_bytes)`) |
| Stale callback buttons after terminal transitions | After publish: bot edits preview message to "Опубликовано..." removing inline keyboard; same for reject/auto-publish |

## User-Spec Deviations

- **Decision 12 (LLM parses slash command args)** — user-spec implies the standard pattern of regex-parsing CLI-style flags. Tech-spec instead routes `/publish_content --at <ISO> [--auto]` through bot's claude (which calls `publish_content_enqueue` MCP tool). **Reason:** robustness on edge cases ("publish this evening" → ISO ambiguity) + reuses existing bot's claude+MCP infrastructure. **Trade-off:** adds Claude API cost per invocation + acknowledged hallucination risk (mitigated by MCP-tool-side re-validation). **Status:** [PENDING USER APPROVAL — user has stated preference to keep during validation round 1].

- **Decision 12 (pip --require-hashes)** — not in user-spec. **Reason:** supply chain safety per security audit; trivial Ansible-side cost. **Status:** [TECHNICAL].

## Acceptance Criteria

User-spec ACs 1-15 inherited as-is. Additional tech-level ACs:

- [ ] **AC-T1 — Module imports verified during role install**: Ansible task `python -c "from mnemonik_blogger.content.formatters import render; from mnemonik_blogger.content.ingest import ingest_article; from mnemonik_blogger.agent import publish_post"` exits 0 (and is asserted via `failed_when` in role); `analyze_blog.py --help` exits 0. Result + SHAs recorded in `work/content-publish-pipeline/decisions.md`.
- [ ] **AC-T2 — No regression in existing services**: post-deploy `systemctl status` of `telegram-ai-agent`, `workspace-manager`, `kaneo-*`, `vaultwarden-*`, `mnemonic-mcp` — all `active (running)`.
- [ ] **AC-T3 — Queue file mode + ownership**: `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` is `op:op 0600`; `/etc/blogger.env` is `root:op 0640`.
- [ ] **AC-T4 — RequiresMountsFor enforces fail-loud**: `umount` volume → `systemctl restart content-publisher` exits non-zero; `journalctl` has explicit "mount missing" message.
- [ ] **AC-T5 — Existing tests pass**: workspace-manager + telegram-ai-agent test suites still green.
- [ ] **AC-T6 — Operator authorization enforced**: foreign `from_user.id` callback returns `answerCallbackQuery` "Not authorized"; HMAC tamper returns "Invalid signature".
- [ ] **AC-T7 — tentative_post_id write-ordering**: pytest verifies the queue write of `tentative_post_id` happens BEFORE the blogger.publish_post call by mocking blogger to raise after tentative_post_id is set; restart-simulation finds the field and triggers `find_post_by_id` lookup.

## Implementation Tasks

### Wave 1 (independent — foundation)

#### Task 1: Sops + secrets template extension
- **Description:** Add 8 new keys to `infrastructure/secrets/secrets.sops.yml.template` (7 user-spec keys + `publish_callback_hmac_secret`). Add empty placeholders to encrypted `secrets.sops.yml` via `sops edit`. Expose as Ansible facts via existing fabric-services sops load pattern.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Files to modify:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/secrets/secrets.sops.yml` (sops-edited)
- **Files to read:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

#### Task 2: Rewrite Mnemonik MCP role for npm install + toggle enabled
- **Description:** Replace `mnemonic_mcp_binary_url` / `mnemonic_mcp_binary_sha256` flow with `npm install -g mnemonik-xyz/mcp#<pinned-sha>` from `github.com/mnemonik-xyz/monorepo`. Update systemd unit to invoke `mnemonic-mcp` from PATH. Toggle `mnemonic_mcp_enabled: true`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** post-deploy `systemctl is-active mnemonic-mcp` → `active`; `mnemonic-mcp --help` exits 0 on PATH.
- **Files to modify:** `infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml`, `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml`, `infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2`
- **Files to read:** `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml` (current binary flow), `infrastructure/ansible/roles/fabric-services/tasks/main.yml` (npm/node install patterns if any)

#### Task 3: Ansible role `content-publisher` — install scaffold + upstream verification
- **Description:** Create new role with `tasks/main.yml` (clones blogger + claude-blog at pinned SHAs, venv with `pip install --require-hashes`, ensures `/var/lib/content-publisher/work/` and volume queue dir with concrete `hetzner_volume_id`, asserts module imports work), `defaults/main.yml`, `templates/blogger.env.j2`, `templates/blogger.mcp.json.j2`, `templates/content-publisher.service.j2` (with `RequiresMountsFor=`, NOT yet started — Python service comes in later tasks). Insert role into `deploy.yml` between fabric-services and telegram-ai-agent.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** `ansible-playbook --syntax-check`; on test deploy `/opt/blogger/`, `/opt/claude-blog/`, `/etc/blogger.env`, `/etc/systemd/system/content-publisher.service` all present; module-import assertion passes; unit `enabled` but `inactive` (no binary yet).
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/{tasks/main.yml, defaults/main.yml, templates/blogger.env.j2, templates/blogger.mcp.json.j2, templates/content-publisher.service.j2, requirements.locked.txt}`, `infrastructure/ansible/playbooks/deploy.yml`, `work/content-publish-pipeline/decisions.md`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/tasks/main.yml`, `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2`, `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2`

### Wave 2 (depends on Wave 1 — content-publisher package)

#### Task 4: `fabric/content-publisher/` — queue + state machine + CAS (shared module)
- **Description:** New Python package `fabric/content-publisher/`. Implement `state.py` (`_write_raw` + `_acquire`/`_release` flock helpers, copied from workspace-manager pattern), `queue.py` (`load`, `append`, `cas_status` — the SHARED CAS primitive both worker and bot import), `models.py` (Pydantic Job + status enum). Centralized mock fixtures in `tests/conftest.py`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `fabric/content-publisher/pyproject.toml`, `fabric/content-publisher/src/content_publisher/{state.py, queue.py, models.py, __init__.py}`, `fabric/content-publisher/tests/{conftest.py, test_queue.py, test_state_machine.py, test_uuid_validation.py}`
- **Files to read:** `fabric/workspace-manager/state.py:_write_raw,_acquire,_release`, `fabric/workspace-manager/models.py`, `fabric/workspace-manager/tests/conftest.py`

#### Task 5: `fabric/content-publisher/` — claude spawn + analyze_blog + renderer (direct import)
- **Description:** Worker step for `writing` → `scoring` → `preview-sent` jobs: creates worktree from sanitized `<id>`, spawns claude via subprocess with prompt piped through stdin (NOT argv); runs `analyze_blog.py` and parses score + truncates issues to first 3; renders preview bytes via `from mnemonik_blogger.content.formatters import render` and `from mnemonik_blogger.content.ingest import ingest_article` (Decision 4); writes `preview_pending=true` to signal bot. Centralized mocks let tests run without real claude/blogger.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/{worker.py, spawn.py, score.py, render.py}`, `fabric/content-publisher/tests/{test_preview_render.py, test_analyze_gate.py, test_spawn_failure_path.py}`
- **Files to read:** `fabric/workspace-manager/dispatch.py` (subprocess pattern, FOR REFERENCE only)

#### Task 6: `fabric/content-publisher/` — publish + deadline-checker + attestation + recovery
- **Description:** Implement publish step (write `tentative_post_id` BEFORE call; invoke `from mnemonik_blogger.agent import publish_post; result = publish_post(...)`; capture `post_url`+`post_id`; CAS `publishing → published`). Implement deadline-checker sub-loop (30s cadence, CAS `preview-sent → publishing` for jobs where `now >= approval_deadline`). Implement attestation step (`mnemonic-mcp sign_memory` subprocess with content via stdin + sha256 binding; on failure: `published → attest-pending` + retry every 5min; 24h escalation: `attest-failed`). Implement crash recovery scan on `main.py` lifespan startup (per state-machine docs above), using `find_post_by_id` from blogger for the `publishing` case.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/{publish.py, deadline.py, attest.py, recovery.py, main.py}`, `fabric/content-publisher/tests/{test_deadline_checker.py, test_attest_retry.py, test_auto_mode.py, integration/test_crash_recovery.py, integration/test_concurrent_publish.py, integration/test_attestation_pipeline.py}`
- **Files to read:** `fabric/workspace-manager/main.py` (lifespan), `infrastructure/ansible/roles/mnemonic-mcp/templates/hooks/user-spec.sh.j2` (mnemonic_sign_memory CLI example)

#### Task 7: Wire `content-publisher.service` to start on deploy + smoke
- **Description:** Update Ansible role: after installing `fabric/content-publisher/` package into the venv, enable and start `content-publisher.service`. `wait_for` task asserts polling within 30s of start. Smoke test (Ansible task) calls publisher-bot's `getChat` against `@mnemonik` channel — fails clearly if bot not admin.
- **Skill:** code-writing
- **Reviewers:** code-reviewer
- **Verify-smoke:** ssh `systemctl is-active content-publisher` → `active`; `journalctl -u content-publisher --since "1 minute ago" | grep -E "queue-poll started|deadline-checker started"` matches both lines (AC3 specific log lines).
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/tasks/main.yml`, `infrastructure/ansible/roles/content-publisher/handlers/main.yml`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/handlers/main.yml`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

### Wave 3 (depends on Wave 2 — bot integration)

#### Task 8: Bot's MCP server — `publish_content_enqueue` tool (uses shared CAS)
- **Description:** Add `@mcp.tool()` `publish_content_enqueue(prompt: str, fire_at: str | None = None, mode: str | None = None) -> dict` in `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`. Imports `from content_publisher.queue import cas_status, append_job` (the SHARED primitive from Wave 2). Validates: prompt non-empty + ≤8000 chars; `fire_at` parses + is future; `mode ∈ {approval, auto, None}`. Atomically appends Job. Returns `{job_id, status}`. Bot's `--mcp-config` already includes a server entry; only this new tool is added.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`, `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/tests/__init__.py` *(NEW dir)*, `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/tests/test_publish_tool.py` *(NEW)*
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`, `fabric/content-publisher/src/content_publisher/queue.py` (Task 4)

#### Task 9: Bot — slash command + callback handlers + preview-dispatch poll
- **Description:** Add `LocalizedBotCommand("publish_content", ...)` to `MOLYANOV_BOT_COMMANDS`. New file `core/handlers/publish.py` with: aiogram message handler for `/publish_content` (passes raw text to bot's claude which calls `publish_content_enqueue` MCP tool — per Deviation #1); 3 callback handlers for `publish:approve|reject|retry:<id[:12]>:<hmac>` with `from_user.id` allowlist check + HMAC validation. Background asyncio task in bot polls queue.jsonl every 5s for jobs where `status=preview-sent AND preview_pending=true`, sends preview DM to OPERATOR_USER_ID with HMAC-tagged inline keyboard, marks `preview_pending=false`. Register `publish_router` in `__main__.py`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Verify-smoke:** unit-test simulates `publish:approve:abc123456789:<hmac>` → CAS preview-sent→publishing → mocked `publish_post` called once.
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py` *(NEW)*, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py`, `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_publish_handlers.py`
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py` (real prefix-based callback pattern), `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py`

### Audit Wave

#### Task 10: Code Audit
- **Description:** Full-feature code-quality audit. Read all source files created/modified across Tasks 1-9. Review holistically: shared CAS primitive correctly imported in both processes; subprocess invocations consistently use stdin for content; error-handling consistency; shared resources match Architecture.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 11: Security Audit
- **Description:** Full-feature OWASP Top 10 audit. Focus on: subprocess argv/env hygiene (Decision 1), path traversal (sanitized job_id), callback HMAC validation (Decision 9), sops env leakage paths, bot's MCP tool input validation, publisher token leakage, attestation content binding (sha256 in metadata).
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 12: Test Audit
- **Description:** Full-feature test-quality audit. Verify every user-spec AC1-15 and every tech-spec AC-T1-7 has at least one unit or integration test target; preview-render test does cross-comparison (not self-equality); mock fixtures centralized in conftest; no dead test code.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 13: Pre-deploy QA
- **Description:** Acceptance testing: run all unit + integration tests. Verify ACs (15 user + 7 tech). Generate AVP report.
- **Skill:** pre-deploy-qa
- **Reviewers:** none

#### Task 14: Deploy
- **Description:** Push branch, dispatch `deploy-fabric.yml`, monitor green (Tofu apply, Ansible playbook). VM uptime preserved (#24/#25 already landed). New role `content-publisher` applied, service active.
- **Skill:** deploy-pipeline
- **Reviewers:** none

#### Task 15: Post-deploy verification
- **Description:** Live environment verification (operator + agent):
  - `/publish_content TEST: explain Mnemonik in one tweet` → expect preview within 90s — Telegram MCP or operator's eyes
  - Click `[✓ Опубликовать]` → expect post in @mnemonik within 30s — Bot API getChatHistory or operator's eyes
  - `mnemonic-mcp recall <hash>` → returns article body — ssh + mnemonic-mcp CLI
  - `systemctl is-active content-publisher mnemonic-mcp` → both `active` — ssh + systemctl
  - `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` → final job status=done — ssh + cat
  - Delete the test post from @mnemonik manually after verification.
  Tools: Telegram MCP (or Bot API curl), ssh, bash, mnemonic-mcp CLI.
- **Skill:** post-deploy-qa
- **Reviewers:** none
