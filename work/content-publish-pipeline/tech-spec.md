---
created: 2026-06-06
status: approved
branch: dev
size: L
---

# Tech Spec: content-publish-pipeline

## Solution

Add a new long-running systemd service `content-publisher.service` that owns a JSONL job queue on the persistent Hetzner volume, spawns `claude` subprocesses with the `claude-blog` skill suite to write articles, then publishes them to the @mnemonik Telegram channel via in-process calls to `mnemonik_blogger.agent.run_campaign_from_article` (keyword-only signature taking `article: Path`, `platforms: list[Platform]`, `settings: Settings`). Operator interaction lives entirely inside a **dedicated Telegram forum topic** `📝 blogger-prompts` (a forum topic in the operator's existing forum chat). Any plain-text message in that topic is treated as a publish brief — no slash command, no LLM-in-the-loop arg parsing. The bot replies in-thread with the rendered preview + inline approve/reject/retry buttons; on approve (or timeout), the bot edits its preview message to "Published: <link>". Attestation goes through the Mnemonik MCP server (installed via `npm install -g @mnemonik-xyz/mcp` — scoped npm package shipping the `mnemonik-mcp` binary per skeptic round 3; Ansible discovers the actual installed name at deploy time and templates it everywhere). The worker invokes attestation by speaking MCP JSON-RPC over the `mnemonik-mcp mcp-stdio` transport — there is NO one-shot `sign-memory` subcommand; the `mnemonic_sign_memory` tool is reachable only via JSON-RPC. The pipeline is fully independent of Symphony / Kaneo / workspace-manager.

## Architecture

### What we're building/modifying

- **`infrastructure/ansible/roles/content-publisher/`** *(new role)* — clones `mnemonik-dev/blogger` + `mnemonik-dev/claude-blog` to `/opt/`, creates Python venv with `pip install --require-hashes` (Deviation #1, APPROVED), renders `/etc/blogger.env` from sops, installs the systemd unit, ensures persistent-volume mount via `RequiresMountsFor=` resolved to a concrete volume id.
- **`fabric/content-publisher/`** *(new Python package)* — long-running worker process. Mirrors `fabric/workspace-manager/`'s shape (FastAPI `main.py` lifespan, queue-poll loop, deadline-checker sub-loop, `StateStore`-style atomic-write persistence via the existing `_write_raw`/`_acquire`/`_release` pattern in `fabric/workspace-manager/state.py:70-111`) but with a JSONL queue. Spawns `claude` as a standalone subprocess; the publish step is **in-process** (direct Python import of `mnemonik_blogger`).
- **`mnemonik-bridge-workspace/telegram-ai-agent/`** *(extend existing)*:
  - `src/telegram_bot/core/handlers/publish.py` *(new)* — topic-message handler (matches `message.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID`) + 3 callback handlers (approve/reject/retry). NO slash command (Deviation #1 dropped after operator chose topic-based dispatch).
  - `src/telegram_bot/__main__.py` — register `publish_router` via `dp.include_router(publish_router)`.
  - No MCP tool change (the bot's aiogram handler writes directly to the queue using the shared `content_publisher.queue` Python primitive — both processes have R/W access to the volume path).
- **`infrastructure/ansible/roles/mnemonic-mcp/`** *(rewrite)* — replace binary-download flow with `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Toggle `mnemonic_mcp_enabled: true`.
- **`infrastructure/secrets/secrets.sops.yml`** *(extend)* — add 9 keys: `blogger_telegram_bot_token`, `blogger_telegram_channel`, `blogger_prompts_topic_id` (NEW, the message_thread_id of `📝 blogger-prompts`), `publish_min_score`, `publish_approval_timeout_min`, `publish_mode`, `blogger_repo_ref`, `claude_blog_repo_ref`, `publish_callback_hmac_secrets`. The HMAC value supports two comma-separated keys (`<current>,<previous>`) for non-disruptive rotation.
- **`infrastructure/ansible/playbooks/deploy.yml`** *(extend)* — insert `- role: content-publisher` between `fabric-services` (line 253) and `telegram-ai-agent` (line 290).

### How it works

End-to-end flow:

```
Operator (in TG forum topic 📝 blogger-prompts):
   Plain text message: "Explain why Mnemonik matters for AI agents"
        │
        ▼
1. Bot's topic-handler matches:
   message.chat.id == TELEGRAM_FORUM_CHAT_ID
   AND message.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID
   AND message.from_user.id == OPERATOR_USER_ID
   (skip all messages that fail any of these)
        │
        ▼
   Handler creates Job + appends to queue.jsonl via
   content_publisher.queue.append_job(
       prompt=message.text,
       fire_at=None,         # MVP: immediate only; --at deferred
       mode=None,            # honors PUBLISH_MODE env
       reply_to_message_id=message.message_id,
       chat_id=message.chat.id,
       thread_id=message.message_thread_id,
   )
        │ atomic append (fcntl.flock + O_APPEND)
        ▼
   /mnt/HC_Volume_<id>/content-publisher/queue.jsonl
                              ▲
                              │ poll 5s / 30s / 5min
                         ┌──────────────────────────────────────┐
2. Worker — THREE sub-loops:│ content-publisher.service             │
                         │  (a) queue-poll (5s):                 │
                         │      pick first status=queued job     │
                         │  (b) deadline-checker (30s):          │
                         │      preview-sent + approval_deadline │
                         │      past now → CAS to publishing     │
                         │      ALSO retries attest-pending      │
                         │  (c) cleanup-gc (5min):               │
                         │      terminal jobs past cleanup_at →  │
                         │      rmtree worktree + archive record │
                         │                                       │
                         │ Queue picks (sub-loop a):             │
                         │  - CAS status: queued -> writing      │
                         │  - mkdir worktree from sanitized UUID │
                         │  - subprocess (prompt via stdin,      │
                         │     env=restricted_env("claude")):    │
                         │     claude --print --mcp-config       │
                         │       /etc/blogger.mcp.json           │
                         │       --strict-mcp-config             │
                         │       --skill claude-blog/blog-writer │
                         │  - article.md emitted to worktree     │
                         │  - CAS status: writing -> scoring     │
                         │  - subprocess: python                 │
                         │     /opt/claude-blog/scripts/         │
                         │     analyze_blog.py <article_path>    │
                         │  - parse score; truncate issues to 3  │
                         │  - render preview via DIRECT import:  │
                         │     from pathlib import Path           │
                         │     from mnemonik_blogger.config       │
                         │       import Platform, Settings        │
                         │     from mnemonik_blogger.content      │
                         │       .ingest import ingest_article    │
                         │     from mnemonik_blogger.content      │
                         │       .formatters import render        │
                         │     src=ingest_article(<article_path>) │
                         │     rendered=render(                   │
                         │       Platform.TELEGRAM, src)          │
                         │     preview_segments=rendered.segments │
                         │  - CAS status: scoring -> preview-sent │
                         │     write approval_deadline=now+5min,  │
                         │     preview_pending=true,              │
                         │     preview_segments=<list>,           │
                         │     cleanup_at=now+24h                 │
                         └──────────────────────────────────────┘
                              │              │
        ┌─────────────────────┘              └──────────────────┐
        ▼ bot's preview-poll                                    ▼ worker's deadline-checker
3a. Bot polls queue every 5s for                3b. Worker deadline-checker:
    preview-sent jobs with                       for jobs status=preview-sent
    preview_pending=true:                        AND now>=approval_deadline:
    Sends a NEW message in the topic as           CAS preview-sent -> publishing
    a REPLY to operator's brief message:          (same publish flow as 3a)
       reply_to_message_id=<brief_msg_id>
       message_thread_id=<topic_id>
       text=<joined preview_segments
              with "\n— — —\n">
       reply_markup=inline keyboard
         [✓ Опубликовать] [✗ Отклонить]
         [🔄 Перегенерировать]?  (if low score)
    Records preview_message_id in queue
    CAS preview_pending=false

3a-callback. Operator clicks button (in topic):
    @router.callback_query(F.data.startswith("publish:"))
    Validates from_user==OPERATOR_USER_ID
    Validates HMAC on callback_data (current or
       previous secret accepted for rotation)
    Reads queue: CAS preview-sent -> publishing
    Worker (NOT bot) does the actual publish:
       BEFORE in-process call: write
       tentative_publish_started_at to queue.jsonl
       Invokes (DIRECT IMPORT, keyword-only):
         from mnemonik_blogger.agent import \
              run_campaign_from_article,    \
              CampaignResult
         settings = Settings(
             dry_run=False,
             min_score=<from env>,
             claude_blog_path=<from env>,
             telegram=TelegramConfig(
                 bot_token=SecretStr(<token>),
                 channel="@mnemonik"))
         result: CampaignResult = \
           run_campaign_from_article(
             article=Path(<article_md>),
             platforms=[Platform.TELEGRAM],
             settings=settings,
             attest=False,                  # we attest via MCP-stdio (Decision 8)
             min_score=<min_score>)
       pr = result.results[0]
       if pr.ok:
           CAS publishing -> published
           Bot EDITS its preview message in-place:
             "✅ Опубликовано: <pr.primary_url>
              Receipt: <hash>"
             reply_markup=None  (keyboard removed)
       else:
           CAS publishing -> publish-failed (with pr.error)
           Bot EDITS preview to:
             "⚠ Telegram API failed: <pr.error>
              Repost the brief in this topic to retry."
             reply_markup=None

4. Attestation via MCP JSON-RPC over stdio
   Worker spawns:
     subprocess (env=restricted_env('mnemonik-mcp'),
                 argv=[<mnemonic_mcp_binary>, "mcp-stdio"],
                 stdin=PIPE, stdout=PIPE):
   Writes MCP JSON-RPC to stdin:
     {"jsonrpc":"2.0","id":1,"method":"initialize",
      "params":{"protocolVersion":"2024-11-05",
                "capabilities":{},
                "clientInfo":{"name":"content-publisher",
                              "version":"0.1"}}}
     <reads init response from stdout>
     {"jsonrpc":"2.0","id":2,"method":"tools/call",
      "params":{"name":"mnemonic_sign_memory",
                "arguments":{"content":"<article_md>",
                             "content_sha256":"<sha>",
                             "post_url":"<url>",
                             "score":<N>,
                             "prompt":"<brief>"}}}
     <reads tool response with receipt>
     <closes stdin → subprocess exits>
   Argv has no payload (full envelope is stdin JSON-RPC).
   CAS published -> done (attestation_hash + content_sha256 stored)

5. If attestation fails:
   CAS published -> attest-pending
   deadline-checker (sub-loop b) retries every 5min for 24h
   If still failing: status -> attest-failed; bot DMs operator
   AS A NEW MESSAGE in the topic (not editing the original).
```

**Crash recovery on worker startup** — iterates queue.jsonl, for each in-flight job:
- `writing` / `scoring` → restart from start (worktree path persisted, ingest is idempotent on filesystem).
- `preview-sent` → re-check `approval_deadline`; if expired, the next deadline-checker tick handles; otherwise leave alone.
- `publishing` → **just retry the publish** (Decision 8 round 5). If the post actually landed before the crash, this creates a duplicate; operator deletes it manually via the Telegram client (3 clicks). Worker-crash-mid-publish is rare (<1/year expected); the rare embarrassment-minute is cheaper than maintaining a recovery-needed state machine + slash commands.
- `attest-pending` → resume retry schedule via sub-loop (b).
- `attest-failed` / `failed` / `rejected` / `publish-failed` / `done` → terminal; cleanup-gc (sub-loop c) GCs the worktree at cleanup_at.

**Why `tentative_publish_started_at` is still written before the blogger call:** it provides a timestamp for the duplicate-detection note in the rare double-post case ("post created around <T>; if you see this time in your channel, it's the recovered duplicate").

**Race: concurrent click + timeout fire** — `content_publisher.queue.cas_status(job_id, expected, target)` uses `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()`. BOTH bot callback handler AND worker deadline-checker MUST go through this shared module. Winner sets `publishing`; loser reads current state, returns "too late, status: <X>" via `answerCallbackQuery`.

**Multi-segment thread**: a long article (>4096 chars) produces `RenderedPost.segments = [seg0, seg1, ...]`. The bot's preview reply joins them with `\n— — —\n` visual separators. `mnemonik_blogger`'s `run_campaign_from_article` posts segments as a Telegram reply chain internally; `result.results[0]` is the `PublishResult` for the Telegram platform — `.primary_url` is the URL of the first message in the thread, `.ids` is the list of all Telegram message IDs in the chain.

### Shared resources

| Resource | Owner (creates) | Consumers | Instance count |
|----------|----------------|-----------|----------------|
| `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` | Ansible role (dir + empty file at deploy, mode 0600 op:op) | content-publisher worker (RW via cas_status), bot topic-handler (W via append_job), bot callback handler (RW via cas_status) | 1 (file) |
| `content_publisher.queue` shared Python module (`append_job`, `cas_status`, `load`) | `fabric/content-publisher/src/content_publisher/queue.py` | Worker + bot (both processes import this single module) | 1 (module) |
| `/etc/blogger.env` | Ansible role (template, mode 0600 root:root — tightened per security R2-4) | content-publisher.service (EnvironmentFile=) | 1 (file) |
| `/etc/blogger.mcp.json` | Ansible role (template, mode 0644) | claude subprocesses spawned by worker | 1 (file) |
| Mnemonik MCP daemon (`mnemonic-mcp.service`) | `mnemonic-mcp` role (npm install + systemd unit) | content-publisher worker (subprocess mcp-stdio), bot's claude (subprocess MCP) | 1 (process) |
| Telegram Bot API client (publisher bot) | `mnemonik_blogger.agent.run_campaign_from_article` (in-process; env-loaded token) | publish step | per-invocation (in-process) |

## Decisions

### Decision 1: Standalone subprocess for claude spawn (not reuse `agent_runner`)
**Decision:** content-publisher worker spawns `claude` directly via `asyncio.create_subprocess_exec("claude", "--print", "--mcp-config", "/etc/blogger.mcp.json", "--strict-mcp-config", stdin=PIPE, env=restricted_env("claude"))`. Prompt sent via stdin.
**Rationale:** self-contained worker; stdin avoids argv injection.
**Alternatives considered:** Refactor `agent_runner` into a shared `fabric/common/` library — rejected, invasive change for marginal DRY win.
**User-spec anchor:** Decision #1 in user-spec.

### Decision 2: JSONL queue with atomic CAS (not SQLite)
**Decision:** Queue persisted as one JSON object per line. State transitions via `content_publisher.queue.cas_status()` helper using `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()`. Mirrors `fabric/workspace-manager/state.py:_write_raw/_acquire/_release`.
**Rationale:** existing codebase pattern; grep-friendly; bounded size.
**Alternatives considered:** SQLite (overkill); Redis (network dep); Postgres (cross-service coupling).
**User-spec anchor:** Decision #3 + AC8.

### Decision 3: Mnemonik MCP via `npm install -g @mnemonik-xyz/mcp`; binary spelling discovered at deploy time
**Decision:** Replace bogus binary-download with `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Skeptic round 3 verified `@mnemonik-xyz/mcp@0.2.4` ships a binary spelled `mnemonik-mcp` (with K); Task 2 discovers the actual binary name via `which mnemonik-mcp || which mnemonic-mcp` and templates it everywhere via Ansible fact `mnemonic_mcp_binary`. Binary subcommands: `install`, `mcp-stdio`, `doctor` (NOT `sign-memory`, NOT `recall`). Attestation goes via MCP JSON-RPC over `mcp-stdio` transport (Architecture step 4).
**Rationale:** real upstream install path; pin npm version; binary-name uncertainty resolved at deploy time.
**Alternatives considered:** `cargo install` from the Rust source in `mnemonik-xyz/monorepo/mcp` — rejected, npm-published binary is the upstream-supported distribution.
**User-spec anchor:** Decision #5 + AC7.

### Decision 4: Direct Python import of blogger renderer + publisher
**Decision:** Both preview and publish use in-process Python calls. Empirically verified upstream API (round 4):
```python
from pathlib import Path
from pydantic import SecretStr
from mnemonik_blogger.config import Platform, Settings, TelegramConfig
# Platform values: TELEGRAM, DISCORD, TWITTER, FARCASTER
from mnemonik_blogger.content.ingest import ingest_article  # (path: str | Path) -> SourcePost
from mnemonik_blogger.content.formatters import render  # (platform: Platform, post: SourcePost) -> RenderedPost
from mnemonik_blogger.agent import run_campaign_from_article, CampaignResult
# Keyword-only signature; NO dry_run arg (it's on Settings).

# Preview path (worker):
src = ingest_article(article_md_path)
rendered = render(Platform.TELEGRAM, src)
preview_segments = rendered.segments  # list[str]

# Publish path (worker):
settings = Settings(dry_run=False, min_score=N, claude_blog_path=..., telegram=...)
result: CampaignResult = run_campaign_from_article(
    article=Path(article_md_path),
    platforms=[Platform.TELEGRAM],
    settings=settings,
    attest=False,  # we attest via MCP-stdio
    min_score=settings.min_score,
)
pr = result.results[0]  # PublishResult: platform, ok, dry_run, urls, ids, error, primary_url
if pr.ok:
    post_url = pr.primary_url
    post_ids = pr.ids  # list of TG message_ids in the thread
```
**Rationale:** Direct import is the only way to satisfy AC5 (byte-equality). `run_campaign_from_article` internally uses the same `render(Platform.TELEGRAM, post)` dispatcher, so byte-equality is structural.
**Alternatives considered:** Parse `mnemonik-blogger preview` CLI output — rejected (decorated output, not byte-identical to publish).
**User-spec anchor:** AC5.

### Decision 5: Separate publisher bot via sops `blogger_telegram_bot_token`
**Decision:** Operator creates `@mnemonik_publisher_bot` via @BotFather, makes it admin of @mnemonik channel, puts token in sops. `mnemonik_blogger.agent.run_campaign_from_article` reads `TELEGRAM_BOT_TOKEN` from `/etc/blogger.env`, distinct from operator-bot's token.
**Rationale:** privilege isolation: leak of publisher token = channel post abuse only.
**Alternatives considered:** Reuse operator-bot token with channel admin restriction — rejected, single token = single compromise blast radius.
**User-spec anchor:** AC12, Decision #6.

### Decision 6: No auto-rewrite on low score; `[🔄 Retry]` button in preview
**Decision:** Score+feedback computed once; preview always sent (in-topic, as a reply to operator's brief). If `score < min_score`: preview text shows `Score: X/100 ⚠️ Issues: <first 3>` and inline keyboard adds `[🔄 Перегенерировать]`. Click creates a NEW job with same prompt + feedback hint; same callback handler atomically writes `regenerated_to=<new_id>` on the old job.
**Rationale:** User-spec Decision #8 — operator's eye is safety net. Truncation per AC4.
**Alternatives considered:** self-rewrite loop with hard cap 3 — reversed in user-spec validation round 1.
**User-spec anchor:** AC4 + Decision #8.

### Decision 7: PUBLISH_MODE=auto with two-location integrity binding
**Decision:** `PUBLISH_MODE` ∈ {`approval`, `auto`}, default `approval`. `auto` skips preview, publishes directly. Operator gets post-fact reply in the topic: `Auto-published: <link>. Receipt: <hash>. Score: <N>.` Mode transition requires two-location confirmation: `/etc/blogger.env` `PUBLISH_AUTO_MODE_TOKEN` must equal sops `publish_auto_mode_token`. Mismatch on startup → worker aborts loud.
**Rationale:** env-flip alone is a single point (security R2-4); two-location binding makes mode change auditable.
**Alternatives considered:** single env flip (rejected for security); hardcoded approval-only (rejected — Q11 user wants the path).
**User-spec anchor:** AC11.

### Decision 8: Just-retry on `publishing`-state crash recovery (accept rare double-post)
**Decision:** On worker startup, jobs in `publishing` status are retried via the normal publish path. If the post landed before the crash, this creates a duplicate in @mnemonik; operator deletes the duplicate manually via the Telegram client. No special state, no recovery slash commands. `tentative_publish_started_at` is still recorded before the publish call so the operator has a timestamp hint when deciding which duplicate to delete.
**Rationale:** Worker-crash-mid-publish is rare (<1/year expected). Trading "automated but brittle" (was: scan channel via Bot API or use non-existent `find_post_by_id`) for "manual but simple" (operator clicks delete on duplicate) eliminates 2 slash commands + 1 state + ~150 LOC and preserves the user-spec Риск 6 spirit (operator has final control over channel content).
**Alternatives considered:** Manual recovery via `/recovery_mark_published` slash (round 4) — rejected by operator in round 5 as overengineered for the actual incidence rate.
**User-spec anchor:** Риск 6 (operator-controlled channel content, no automated post-creation under uncertainty).

### Decision 9: HMAC-tagged callback_data + 12-char job_id prefix + operator authorization + rotation window
**Decision:** Callback format: `publish:<action>:<job_id[:12]>:<hmac8>` where `hmac8` = first 8 bytes of `HMAC-SHA256(secret, "<action>:<job_id[:12]>")` (hex-encoded; total <50 bytes — fits Telegram's 64-byte limit). Server holds TWO secrets in `CALLBACK_HMAC_SECRETS=<current>,<previous>`; handler accepts either. Rotation: edit sops → redeploy → after window, drop the trailing comma+old secret. Handler ALWAYS validates `callback.from_user.id == OPERATOR_USER_ID` BEFORE HMAC compare. Pattern mirrors `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py:227-228`.
**Rationale:** Telegram callback_data 64-byte limit + 8-char prefix collision-bound; 12-char + HMAC removes both. Two-secret window enables zero-downtime rotation.
**Alternatives considered:** Single secret with brief outage on rotation — rejected.
**User-spec anchor:** Ограничения (callback_data 64 байт) + AC6.

### Decision 10: Persistent volume mount enforced via systemd `RequiresMountsFor=` with concrete volume id
**Decision:** content-publisher.service unit declares `RequiresMountsFor=/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher`. The `hetzner_volume_id` is resolved at install time by Ansible (`ansible_facts.mounts | selectattr('device', 'equalto', '/dev/sdb') | ...`). No literal `*` ever reaches the unit file. Empty fact → template task fails loud.
**Rationale:** prevents silent fallback to root disk.
**Alternatives considered:** `After=mnt-HC_Volume_<id>.mount` — equivalent but more setup.
**User-spec anchor:** Риск 9, AC15.

### Decision 11: Pinned upstream refs + role asserts real import surface + signature inspection at install
**Decision:** Role defaults pin `blogger_repo_ref` and `claude_blog_repo_ref` to specific commit SHAs. Ansible role runs:
```
python -c "
from pathlib import Path
from inspect import signature
from mnemonik_blogger.config import Platform, Settings
from mnemonik_blogger.content.ingest import ingest_article
from mnemonik_blogger.content.formatters import render
from mnemonik_blogger.agent import run_campaign_from_article, CampaignResult
sig = signature(run_campaign_from_article)
required = {'article','platforms','settings'}
assert required <= set(sig.parameters), f'missing kwargs: {required - set(sig.parameters)}'
assert all(sig.parameters[n].kind.name == 'KEYWORD_ONLY' for n in required), \
       'run_campaign_from_article args must be keyword-only'
assert Platform.TELEGRAM.value == 'telegram'
"
```
exits 0; `python /opt/claude-blog/scripts/analyze_blog.py --help` exits 0.
**Rationale:** import + signature inspection catches the round-3-style upstream drift at deploy time.
**User-spec anchor:** Ограничения "обязательная верификация CLI surface".

### Decision 12: pip install with `--require-hashes` for blogger venv
**Decision:** Role generates `requirements.locked.txt` via `pip-compile --generate-hashes` (offline, by the role-author) checked into the role. Install: `pip install --require-hashes -r requirements.locked.txt`. Blogger source ref is pinned separately by git.
**Rationale:** supply-chain hardening. Operator explicitly approved this trade-off in round 5 (small ongoing maintenance cost vs. real typosquatting risk on public PyPI).
**Alternatives considered:** Plain `pip install .` (rejected by operator as accepting too much supply-chain risk); rewrite blogger in another language (rejected — 2-4 weeks of work for a marginal risk reduction, since crates.io/npm have analogous issues).
**User-spec anchor:** [TECHNICAL — APPROVED by operator round 5].

### Decision 13: Topic-based dispatch (no slash command, no LLM-in-the-loop arg parsing)
**Decision:** Operator interacts with the pipeline exclusively inside a dedicated Telegram forum topic `📝 blogger-prompts` (existing in the operator's forum chat; thread_id stored in sops as `blogger_prompts_topic_id`). Bot's aiogram handler matches `message.chat.id == TELEGRAM_FORUM_CHAT_ID AND message.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID AND message.from_user.id == OPERATOR_USER_ID`; matching messages are enqueued as publish briefs directly (the full `message.text` becomes the `prompt`). NO `/publish_content` slash command. NO LLM in the dispatch path. NO MCP tool. Bot's preview reply lives in the same topic as a `reply_to_message_id=<brief_msg_id>` message; approval edits the preview in-place to "Published: <link>".
**Rationale:** eliminates the prompt-injection vector from earlier drafts; cheaper (no Claude API tokens per command); simpler code; better UX (single conversation thread per article, full audit trail in topic). Operator chose this in round 5 over LLM-parse.
**Alternatives considered:** Plain `/publish_content <prompt>` slash with regex-parsed `--at` / `--auto` flags — rejected (still adds a slash to MOLYANOV_BOT_COMMANDS for one-off use; topic-based reaches feature parity for MVP). LLM-parses-args via MCP tool — explicitly rejected by operator (prompt-injection risk, cost).
**User-spec anchor:** AC1 (the slash command requirement is replaced with the topic-handler; operator confirmed this still satisfies "operator triggers publish via TG" intent).

## Data Models

### Queue JSONL schema (`queue.jsonl`)

One JSON object per line.

```json
{
  "id": "abc12345-...",                       // UUID v4, strictly validated
  "created_at": "2026-06-06T14:00:00Z",
  "fire_at": null,                             // MVP: immediate only; reserved for future
  "approval_deadline": null,                   // set when status=preview-sent
  "cleanup_at": null,                          // 24h after preview-sent; worktree GC
  "prompt": "Explain why Mnemonik...",         // full message.text from the topic
  "feedback_for_retry": null,                  // analyze_blog issues if this is a [🔄] retry
  "mode": "approval",                          // "approval" | "auto"
  "status": "queued",                          // see state machine
  "score": null,
  "issues": [],
  "worktree_path": null,
  "article_path": null,
  "preview_segments": [],                      // from RenderedPost.segments
  "preview_message_id": null,                  // bot's preview reply message_id
  "preview_pending": false,                    // worker→bot signal
  "notify_pending": false,                     // worker→bot DM signal (failures, attest done)
  "chat_id": null,                             // operator's forum chat id (echoes sops)
  "thread_id": null,                           // BLOGGER_PROMPTS_TOPIC_ID
  "reply_to_message_id": null,                 // operator's brief message_id (for reply)
  "tentative_publish_started_at": null,        // for crash-duplicate timestamp hint
  "publish_error": null,                       // PublishResult.error if status=publish-failed
  "post_url": null,                            // PublishResult.primary_url
  "post_message_ids": [],                      // PublishResult.ids (thread)
  "content_sha256": null,                      // sha256(article_md_bytes)
  "attestation_hash": null,
  "attest_attempts": 0,
  "regenerated_to": null,                      // job_id created by [🔄]
  "retries_of": null                           // inverse link
}
```

### Job state machine

```
queued ─► writing ─► scoring ─► preview-sent ─┬─► publishing ─┬─► published ─► attest-pending ─► done
   │         │           │            │       ├─► rejected    │
   │         │           │            │       │   (terminal)  │            (24h fail)
   │         │           │            │       └─► regenerated │              ▼
   │         │           │            │           (stays at   │         attest-failed (terminal)
   │         │           │            │           preview-sent│
   │         │           │            │           until GC)   └─► publish-failed (terminal — TG
   │         │           │            │                                   API failure; bot
   │         │           │            │                                   reports in topic)
   └─────────┴───────────┴────────────┘─► failed (terminal — hard error in writing/scoring/spawn)

(worker startup, job at "publishing"): just retry the publish path; if the post already landed,
operator deletes the duplicate manually (Decision 8).
```

### `restricted_env(kind)` allowlist table

| Kind | Variables included | Source |
|------|--------------------|--------|
| `claude` | `PATH`, `HOME=/var/lib/content-publisher`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` (fallback) | `/etc/blogger.env` |
| `analyze_blog` | `PATH`, `MNEMONIK_CLAUDE_BLOG_PATH` | `/etc/blogger.env` |
| `mnemonik-mcp` | `PATH` only | (none — all payload in stdin MCP JSON-RPC) |
| `blogger_inproc` | (in-process call) | worker process env (`TELEGRAM_BOT_TOKEN`, `MNEMONIK_MIN_SCORE`, etc.) |

`test_env_isolation.py` asserts each subprocess kind sees exactly the listed variables and nothing else.

### `/etc/blogger.env` (rendered from sops; mode 0600 root:root)

```
TELEGRAM_BOT_TOKEN={{ blogger_telegram_bot_token }}
TELEGRAM_CHANNEL={{ blogger_telegram_channel | default('@mnemonik') }}
TELEGRAM_FORUM_CHAT_ID={{ telegram_forum_chat_id }}
BLOGGER_PROMPTS_TOPIC_ID={{ blogger_prompts_topic_id }}
OPERATOR_USER_ID={{ telegram_ai_agent_allowed_user_ids[0] }}
CALLBACK_HMAC_SECRETS={{ publish_callback_hmac_secrets }}
PUBLISH_MODE={{ publish_mode | default('approval') }}
PUBLISH_AUTO_MODE_TOKEN={{ publish_auto_mode_token | default('') }}
PUBLISH_APPROVAL_TIMEOUT_MIN={{ publish_approval_timeout_min | default(5) }}
MNEMONIK_CLAUDE_BLOG_PATH=/opt/claude-blog
MNEMONIK_MIN_SCORE={{ publish_min_score | default(80) }}
CONTENT_PUBLISHER_QUEUE=/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher/queue.jsonl
CONTENT_PUBLISHER_WORKTREE_ROOT=/var/lib/content-publisher/work
MNEMONIK_DRY_RUN=false
```

### `/etc/blogger.mcp.json` (mcp-stdio transport)

```json
{ "mcpServers": { "mnemonik": { "command": "{{ mnemonic_mcp_binary }}", "args": ["mcp-stdio"] } } }
```

## Dependencies

### New packages
- `mnemonik-dev/blogger` — Python package via `pip install --require-hashes -r requirements.locked.txt` in venv at `/opt/blogger/venv/`. Pinned commit SHA.
- `mnemonik-dev/claude-blog` — Cloned to `/opt/claude-blog/`; not pip-installed. Pinned commit SHA.
- `@mnemonik-xyz/mcp` — `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Provides binary on PATH (name discovered by Task 2).

### Using existing (from project)
- `fabric/workspace-manager/state.py:_write_raw,_acquire,_release` — READ as reference; ~30 LOC copied into `fabric/content-publisher/src/content_publisher/state.py`.
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py:227-228` — real prefix-based callback handler pattern.
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/` (other handlers) — examples for `@router.message(F.chat.id == ... & F.message_thread_id == ...)` filters used by the topic-handler.
- `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2` — systemd unit shape.
- `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2` — sops→env-file render.

## Testing Strategy

**Feature size:** L

Mock fixtures centralized in `fabric/content-publisher/tests/conftest.py`: `mock_analyze_blog`, `mock_blogger_run_campaign_from_article`, `mock_mnemonic_mcp_stdio`, `mock_claude_subprocess`.

### Unit tests

- `test_queue.py` — JSONL round-trip; `cas_status` under simulated concurrent writers; sort; idempotent transitions.
- `test_state_machine.py` — every valid + every illegal transition; crash-recovery decisions per status.
- `test_preview_render.py` — **cross-comparison byte-equality**: compute `rendered_segments = render(Platform.TELEGRAM, ingest_article(path)).segments`; monkey-patch the platform-specific publisher inside `mnemonik_blogger.agent._publish_post` (e.g. patch the Telegram sender seam) to capture sent segments; call `run_campaign_from_article(article=Path(path), platforms=[Platform.TELEGRAM], settings=Settings(dry_run=False, ...), attest=False)`; assert captured == `rendered_segments` byte-for-byte. Intercept point's exact name verified by Task 3's import-test.
- `test_analyze_gate.py` — high score: preview without `[🔄]`. Low score: preview with `[🔄]` + first 3 issues.
- `test_uuid_validation.py` — UUID v4 only; rejects traversal, control chars, etc.
- `test_publish_handlers.py` — topic-handler: matches operator messages in the correct (chat, thread) tuple; ignores messages from non-operators or other topics. Callback handlers: HMAC validation (current + previous accepted; foreign rejected); `from_user.id != OPERATOR_USER_ID` rejected; replay protection (same callback fired twice → second sees `status != preview-sent` and returns "too late").
- `test_topic_filter.py` — explicit negative cases: message from operator but wrong topic (e.g. operator's main DM) → ignored; message in correct topic but from non-operator → ignored; message in correct topic from operator → enqueued.
- `test_deadline_checker.py` — sub-loop timing.
- `test_cleanup_gc.py` — sub-loop (c): terminal jobs with `cleanup_at <= now` → worktree removed + archive record. Non-terminal jobs are NOT GC'd.
- `test_attest_retry.py` — retry cadence + 24h escalation.
- `test_spawn_failure_path.py` — claude subprocess non-zero exit → `failed` + `notify_pending=true` (AC10).
- `test_auto_mode.py` — `PUBLISH_MODE=auto` + matching token: skip preview; post-fact notify in exact format (AC11).
- `test_separate_bot_token.py` — `run_campaign_from_article` reads `TELEGRAM_BOT_TOKEN` from `/etc/blogger.env`-style env, not operator-bot's `.env` (AC12).
- `test_required_mounts_for.py` — Ansible render with concrete `hetzner_volume_id`; no literal `*`; unresolved fact → fail loud (AC15).
- `test_tentative_write_ordering.py` — mock `run_campaign_from_article` to raise after `tentative_publish_started_at` is set (AC-T7).
- `test_retry_linkage.py` — `[🔄]` callback creates new job AND writes `regenerated_to=<new_id>` on old job (Decision 6).
- `test_env_isolation.py` — each subprocess kind sees only its allowlisted env vars.
- `test_attest_envelope.py` — Mnemonik MCP-stdio attestation envelope: argv is exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]` ONLY; MCP JSON-RPC `initialize` then `tools/call mnemonic_sign_memory` written to stdin; receipt parsed from mocked stdout.
- `test_auto_mode_token_binding.py` — flipping `PUBLISH_MODE=auto` without matching token → worker startup raises.
- `test_publishing_retry_on_crash.py` — simulated crash at `publishing`: restart worker → publish retried via normal path (Decision 8). Asserts the existence of `tentative_publish_started_at` for forensics.

### Integration tests

- `test_queue_persist.py` — write job → kill+restart worker → resumes from correct status.
- `test_concurrent_publish.py` — threading: concurrent callback click + deadline-fire → exactly one mocked publish.
- `test_crash_recovery.py` — kill at each in-flight status. `writing` → restart; `scoring` → restart scorer; `preview-sent` → re-check deadline; `publishing` → just retry (no manual recovery state); assert publish was called exactly once total in the no-duplicate case AND that in a forced-duplicate case the operator gets a clear timestamp via `tentative_publish_started_at`.
- `test_attestation_pipeline.py` — happy + Mnemonik MCP failure → attest-pending + retry cadence + 24h escalation.

### E2E tests

- `tests/e2e/test_publish_smoke.sh` — post-deploy live: write a plain-text brief in the `📝 blogger-prompts` topic → expect preview reply within 90s → click `[✓]` → post in @mnemonik within 30s → MCP-stdio handshake (bash one-liner) to call `mnemonic_recall` → assert body matches. Teardown: script prints chat_id + message_id of the test post; operator deletes manually.

## Agent Verification Plan

### Tools required

- `bash` + `curl` — Bot API: `getMyCommands` (verifies NO `publish_content` slash registered, since we use topic instead), `getUpdates`, `answerCallbackQuery`, `getChat`, `getForumTopicIconStickers`.
- `ssh` — `journalctl -u content-publisher`, `journalctl -u telegram-ai-agent`, `cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl`.
- MCP-stdio handshake (bash one-liner) for `mnemonic_recall`.
- Telegram MCP (optional) — operator click simulation.

## Risks

| Risk | Mitigation |
|------|-----------|
| Mnemonik MCP binary name/subcommand differs after npm install | Task 2's binary discovery (`which mnemonik-mcp || which mnemonic-mcp`) + post-install MCP-stdio handshake test catches drift at deploy time |
| `mnemonik_blogger` upstream signature shifts | Decision 11's `inspect.signature` check at install time asserts keyword-only `article`, `platforms`, `settings` are present; fail-loud with diff hint |
| Operator forgets to create `📝 blogger-prompts` topic OR puts wrong topic_id in sops | No deploy-time smoke (the Bot API has no `getForumTopic` read endpoint that exists and takes a thread_id). Self-detected at first use: bot's topic-handler logs the mismatched `message_thread_id` to journald; operator debugs via `journalctl -u telegram-ai-agent | grep thread_id` within minutes. |
| Concurrent queue writes from bot (topic-handler + callback) and worker | Shared `cas_status` via flock; worst case ~10ms wait |
| Hetzner volume id varies | Ansible discovers via mounts fact, templates concrete int |
| Mnemonik MCP role's npm install fails | Role exits non-zero with clear message; deploy fails fast |
| Pinned refs go stale | Manual bump: edit `defaults/main.yml` + redeploy |
| Operator forgets to add publisher bot as channel admin | Role's Ansible smoke calls `getChat` on @mnemonik with publisher token; fails clearly |
| Subprocess argv content leak via `ps` | All bulk content via stdin; mnemonik-mcp argv is just `[<binary>, "mcp-stdio"]` |
| in-process publish hardening | systemd unit: `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`; excepthook scrubs article_text from tracebacks |
| HMAC secret rotation breaks live previews | `CALLBACK_HMAC_SECRETS=current,previous` window; runbook in role README |
| `PUBLISH_MODE=auto` env-flip single-point | Decision 7: two-location binding |
| Stale callback buttons after terminal transitions | After publish/reject/auto-publish: bot edits preview message removing inline keyboard |
| Worker-crash-mid-publish double-posts to @mnemonik | Decision 8: rare (<1/year); operator deletes duplicate manually using the `tentative_publish_started_at` timestamp hint |
| Worktrees accumulate (cleanup-gc misses) | `test_cleanup_gc.py` integration test; weekly journalctl review (manual operator task) |
| Operator types a non-prompt message in the topic by accident (e.g. status update) | Acceptable trade-off — that message gets enqueued, claude tries to write an article from it, low score, operator just `[✗ Отклонить]`s. Cost: ~$0.05 in API tokens + 60s. To avoid: prefix-trigger like "publish:" (rejected — back to parsing). |

## User-Spec Deviations

### Deviation #1: `pip install --require-hashes` for blogger venv
**What user-spec says:** silent on supply chain.
**What tech-spec does:** adds locked-hashes pin.
**Why:** public PyPI + popular dep names = realistic typosquatting risk per security audit.
**Status:** [TECHNICAL — APPROVED by operator round 5].

(Deviation #1 from rounds 1–4 — LLM-parses-args — DROPPED in round 5. Operator chose topic-based dispatch with NO LLM in the path. Deviation #3 from round 4 — manual recovery — also DROPPED in round 5 via Decision 8 just-retry approach.)

## Acceptance Criteria

User-spec AC1-15 inherited. AC1 (slash-command registration) reinterpreted per Decision 13 as "topic-handler is registered and filters correctly" — operator confirmed during round 5. Additional tech-level:

- [ ] **AC-T1 — Module imports verified during role install**: Ansible asserts 5 imports + `inspect.signature` check. Result + SHAs recorded in `work/content-publish-pipeline/decisions.md`.
- [ ] **AC-T2 — No regression in existing services**: post-deploy verify all services healthy:
  - systemd: `telegram-ai-agent`, `workspace-manager`, `mnemonic-mcp.service`, `content-publisher` — `active (running)`
  - docker compose: `kaneo-*` stack at `/opt/kaneo/` and `vaultwarden-*` stack at `/opt/vaultwarden/` — all containers `Up (healthy)` via `docker compose ps`
- [ ] **AC-T3 — File modes**: queue.jsonl `op:op 0600`; `/etc/blogger.env` `root:root 0600`; `/etc/blogger.mcp.json` `root:op 0644`.
- [ ] **AC-T4 — RequiresMountsFor**: `umount` volume → `systemctl restart content-publisher` exits non-zero.
- [ ] **AC-T5 — Existing tests pass**: workspace-manager + telegram-ai-agent suites green.
- [ ] **AC-T6 — Operator authorization + HMAC**: foreign `from_user.id` callback → "Not authorized"; tampered HMAC → "Invalid signature".
- [ ] **AC-T7 — tentative ordering**: `test_tentative_write_ordering.py` verifies `tentative_publish_started_at` written before `run_campaign_from_article` call.
- [ ] **AC-T8 — mnemonik-mcp argv hygiene + MCP-stdio handshake**: subprocess argv is exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]`; integration test proves the worker performs MCP `initialize` then `tools/call mnemonic_sign_memory` over stdin and parses the receipt.
- [ ] **AC-T9 — env isolation**: per-subprocess allowlist.
- [ ] **AC-T10 — auto-mode two-location binding**: mismatched token → worker startup aborts.
- [ ] **AC-T11 — HMAC rotation runbook**: README documents 4-step rotation.
- [ ] **AC-T12 — Topic filter correctness**: bot ignores messages NOT in `(chat=TELEGRAM_FORUM_CHAT_ID, thread=BLOGGER_PROMPTS_TOPIC_ID, from=OPERATOR_USER_ID)`; enqueues those that match. Negative tests cover all 3 dimensions.
- [ ] **AC-T13 — Just-retry crash recovery**: `test_publishing_retry_on_crash.py` proves on-restart a `publishing` job is retried via the normal path (no special state, no slash commands).

## Implementation Tasks

### Wave 1 (independent — foundation)

#### Task 1: Sops + secrets template extension
- **Description:** Add 9 new keys to `infrastructure/secrets/secrets.sops.yml.template` (including `blogger_prompts_topic_id` per Decision 13). Add empty placeholders to encrypted `secrets.sops.yml` via `sops edit`. Expose as Ansible facts via fabric-services sops load.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Files to modify:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/secrets/secrets.sops.yml` (sops-edited)
- **Files to read:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

#### Task 2: Rewrite Mnemonik MCP role for npm scoped-package install + binary-name discovery
- **Description:** Replace bogus binary-download with `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Ensure node+npm prerequisite. After install, discover the actual binary name via `which mnemonik-mcp || which mnemonic-mcp` → save as Ansible fact `mnemonic_mcp_binary`. Update systemd unit + `/etc/blogger.mcp.json` template to use the discovered binary name. Toggle `mnemonic_mcp_enabled: true`. Install transitive deps via `npm ci` from a checked-in `package-lock.json` (security R2-8). Add publisher-bot token rotation + mnemonik-mcp version-bump runbooks to role README.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** post-deploy `systemctl is-active mnemonic-mcp` → `active`; `$mnemonic_mcp_binary --help` lists subcommand `mcp-stdio`; one-shot JSON-RPC handshake: spawn `$mnemonic_mcp_binary mcp-stdio` and feed an `initialize` message via stdin → expect protocol-version response.
- **Files to modify:** `infrastructure/ansible/roles/mnemonic-mcp/{defaults/main.yml, tasks/main.yml, templates/mnemonic-mcp.service.j2, files/package-lock.json (NEW), README.md (NEW or extended)}`
- **Files to read:** `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

#### Task 3: Ansible role `content-publisher` — install scaffold + upstream verification + README runbooks
- **Description:** Create role; clones blogger+claude-blog at pinned SHAs, venv with `pip install --require-hashes -r requirements.locked.txt`, ensures `/var/lib/content-publisher/work/` and volume queue dir with concrete `hetzner_volume_id`, asserts module imports + `inspect.signature` check (Decision 11). Templates: `blogger.env.j2` (mode 0600 root:root), `blogger.mcp.json.j2`, `content-publisher.service.j2` (with `RequiresMountsFor=<concrete>`, `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`). Role README includes HMAC + auto-mode + publisher-token rotation runbooks. Insert role into `playbooks/deploy.yml` between fabric-services and telegram-ai-agent.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** `ansible-playbook --syntax-check`; on test deploy: paths present; module-import + signature assertion passes; unit `enabled` but `inactive`.
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/{tasks/main.yml, defaults/main.yml, templates/blogger.env.j2, templates/blogger.mcp.json.j2, templates/content-publisher.service.j2, requirements.locked.txt, README.md}`, `infrastructure/ansible/playbooks/deploy.yml`, `work/content-publish-pipeline/decisions.md`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/tasks/main.yml`, `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2`, `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2`

### Wave 2 (depends on Wave 1 — content-publisher package)

#### Task 4: `fabric/content-publisher/` — queue + state machine + CAS + env isolation
- **Description:** New Python package. `state.py` (atomic-write + flock helpers copied from workspace-manager pattern), `queue.py` (`append_job`, `load`, `cas_status` — the SHARED CAS primitive both worker and bot import), `models.py` (Pydantic Job + status enum), `env.py` (`restricted_env(kind)` allowlist helper). Centralized mock fixtures in `tests/conftest.py`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `fabric/content-publisher/pyproject.toml`, `fabric/content-publisher/src/content_publisher/{state.py, queue.py, models.py, env.py, __init__.py}`, `fabric/content-publisher/tests/{conftest.py, test_queue.py, test_state_machine.py, test_uuid_validation.py, test_env_isolation.py}`
- **Files to read:** `fabric/workspace-manager/state.py:_write_raw,_acquire,_release`, `fabric/workspace-manager/models.py`, `fabric/workspace-manager/tests/conftest.py`

#### Task 5: `fabric/content-publisher/` — claude spawn + analyze_blog + renderer (direct import)
- **Description:** Worker step for `writing → scoring → preview-sent`: validates UUID v4, creates worktree, spawns claude via subprocess with prompt piped through stdin + `env=restricted_env("claude")`; runs `analyze_blog.py` and truncates issues to 3; renders preview via `render(Platform.TELEGRAM, ingest_article(Path(article_path)))` (Decision 4) and stores `preview_segments`; writes `preview_pending=true`. Mock fixtures from Task 4's conftest let tests run without real claude/blogger/MCP.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/{worker.py, spawn.py, score.py, render.py}`, `fabric/content-publisher/tests/{test_preview_render.py, test_analyze_gate.py, test_spawn_failure_path.py, test_separate_bot_token.py, test_required_mounts_for.py}`
- **Files to read:** `fabric/workspace-manager/dispatch.py` (subprocess pattern, REFERENCE only)

#### Task 6: `fabric/content-publisher/` — publish + 3 sub-loops + MCP-stdio attestation + just-retry recovery
- **Description:** Publish step: writes `tentative_publish_started_at` BEFORE call; in-process call to `run_campaign_from_article(article=Path(article_md), platforms=[Platform.TELEGRAM], settings=Settings(...), attest=False, min_score=N)`; captures `pr.primary_url`, `pr.ids` from `result.results[0]`; CAS `publishing → published` on `pr.ok==True` OR `publishing → publish-failed` with `pr.error` stored + `notify_pending=true` on `pr.ok==False`. Deadline-checker sub-loop (30s; also retries `attest-pending`). Cleanup-gc sub-loop (5min; rmtree terminal jobs' worktrees, archive). Attestation via MCP-stdio: spawn `<mnemonic_mcp_binary> mcp-stdio`; speak MCP JSON-RPC over stdin (newline-delimited) — `initialize` then `tools/call mnemonic_sign_memory` with `{content, content_sha256, post_url, score, prompt}`; read receipt from stdout; close stdin. On failure: `published → attest-pending` + retry every 5min; 24h: `attest-failed` + DM operator. Crash recovery on `main.py` startup: jobs at `publishing` are simply retried via the normal publish path (Decision 8 — accept rare double-post). Two-location auto-mode token check on startup. Posts-per-hour rate ceiling counter (security R2-9).
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/{publish.py, deadline.py, cleanup.py, attest.py, recovery.py, main.py, mcp_client.py}`, `fabric/content-publisher/tests/{test_deadline_checker.py, test_cleanup_gc.py, test_attest_retry.py, test_attest_envelope.py, test_auto_mode.py, test_auto_mode_token_binding.py, test_tentative_write_ordering.py, test_publishing_retry_on_crash.py, integration/test_crash_recovery.py, integration/test_concurrent_publish.py, integration/test_attestation_pipeline.py}`
- **Files to read:** `fabric/workspace-manager/main.py` (lifespan pattern)

#### Task 7: Wire `content-publisher.service` to start on deploy + Ansible smoke
- **Description:** Update Ansible role: install `fabric/content-publisher/` package into venv, enable+start the systemd unit. `wait_for` task asserts polling within 30s of start (log lines: `queue-poll started`, `deadline-checker started`, `cleanup-gc started`). Ansible task calls publisher-bot's `getMe` (records bot_id) then `getChatMember(chat_id=@mnemonik, user_id=<bot_id>)` and asserts `result.status in {"administrator", "creator"}` — fails clearly if publisher-bot isn't admin of @mnemonik. NO topic-existence smoke (no real Bot API endpoint supports it); topic_id mismatch is self-detected at first use via journald.
- **Skill:** code-writing
- **Reviewers:** code-reviewer
- **Verify-smoke:** ssh `systemctl is-active content-publisher` → `active`; `journalctl -u content-publisher --since "1 minute ago"` matches all 3 start lines; topic + channel smokes pass.
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/tasks/main.yml` (append), `infrastructure/ansible/roles/content-publisher/handlers/main.yml`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/handlers/main.yml`

### Wave 3 (depends on Wave 2 — bot integration)

#### Task 8: Bot — topic-handler + callback handlers + preview-dispatch poll + retry linkage + rejection-rate metric
- **Description:** New file `core/handlers/publish.py` with: (a) topic-message handler `@router.message(F.chat.id == TELEGRAM_FORUM_CHAT_ID, F.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID, F.from_user.id == OPERATOR_USER_ID)` — extracts `message.text` as the publish brief and calls `content_publisher.queue.append_job(...)` directly with `chat_id`, `thread_id`, `reply_to_message_id` echoed for the preview reply (Decision 13); (b) 3 callback handlers for `publish:approve|reject|retry:<id[:12]>:<hmac>` with `from_user.id` allowlist + HMAC validation (accept current OR previous secret); on retry: atomically write `regenerated_to=<new_id>` on old job + create new job (single CAS-protected). Background asyncio task polls queue.jsonl every 5s for `status=preview-sent AND preview_pending=true`, sends preview AS A REPLY in the topic (joined `preview_segments` with `\n— — —\n`) with HMAC-tagged inline keyboard, records `preview_message_id`, marks `preview_pending=false`. Same poll handles `notify_pending=true` (spawn failures, publish-failed, attest results) by editing the existing preview message in-place OR posting a new message in the topic if no preview exists yet. Sliding-window counter: if >5 HMAC failures or unauthorized callbacks in 10-min window → bot posts a security note in the topic. Register `publish_router` in `__main__.py`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Verify-smoke:** unit `test_publish_handlers.py`: simulate `publish:approve:abc123456789:<hmac>` → CAS preview-sent→publishing → mocked publish called once. `test_topic_filter.py`: 3 negative cases all rejected, 1 positive case enqueued.
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py` *(NEW)*, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py`, `mnemonik-bridge-workspace/telegram-ai-agent/tests/{test_publish_handlers.py, test_topic_filter.py, test_retry_linkage.py}`
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/` (other existing handlers for message-filter patterns)

### Audit Wave

#### Task 9: Code Audit
- **Description:** Full-feature code-quality audit. Read all source files created/modified across Tasks 1–8. Review holistically: shared CAS primitive correctly imported in both processes; subprocess invocations consistently use stdin + restricted_env; in-process publish hardening (excepthook scrub); error-handling consistency; shared resources match Architecture.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 10: Security Audit
- **Description:** Full-feature OWASP audit. Focus: subprocess argv/env hygiene; path traversal; callback HMAC + replay + rotation; sops env leakage; topic-filter correctness (rejects all 3 negative cases); publisher token leakage (excepthook, coredumps); attestation content-sha256 binding; auto-mode two-location integrity.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 11: Test Audit
- **Description:** Full-feature test-quality audit. Verify every user-spec AC1-15 + tech-spec AC-T1-13 has at least one test target; preview-render cross-comparison; centralized conftest; HMAC + replay + rotation covered; cleanup-gc covered; just-retry crash recovery covered.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 12: Pre-deploy QA
- **Description:** Run unit + integration tests. Verify ACs (15 user + 13 tech). Generate AVP report.
- **Skill:** pre-deploy-qa
- **Reviewers:** none

#### Task 13: Deploy
- **Description:** Push branch, dispatch `deploy-fabric.yml`, monitor green. VM uptime preserved (#24/#25 landed). New role applied, service active.
- **Skill:** deploy-pipeline
- **Reviewers:** none

#### Task 14: Post-deploy verification
- **Description:** Live verification:
  - Operator writes "TEST: explain Mnemonik in one tweet" as a plain message in the `📝 blogger-prompts` topic → preview reply within 90s — Telegram MCP / operator eyes
  - Click `[✓]` → post in @mnemonik within 30s — Bot API / operator eyes
  - MCP-stdio handshake: `mnemonic_recall` returns body — ssh + bash one-liner
  - Services healthy (systemctl + docker compose ps for kaneo/vaultwarden)
  - `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` final job `status=done`
  - Delete test post manually after.
  Tools: Telegram MCP (or Bot API curl), ssh, bash, MCP-stdio one-liner.
- **Skill:** post-deploy-qa
- **Reviewers:** none
