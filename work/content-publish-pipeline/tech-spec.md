---
created: 2026-06-06
status: draft
branch: dev
size: L
---

# Tech Spec: content-publish-pipeline

## Solution

Add a new long-running systemd service `content-publisher.service` that owns a JSONL job queue on the persistent Hetzner volume, spawns `claude` subprocesses with the `claude-blog` skill suite to write articles, then publishes them to the @mnemonik Telegram channel via in-process calls to `mnemonik_blogger.agent.run_campaign_from_article` (a keyword-only Python function exported by the upstream blogger package, taking `article: Path`, `platforms: list[Platform]`, `settings: Settings`). The Telegram bot gains a `/publish_content` slash command (and matching MCP tool `publish_content_enqueue`) that enqueues jobs; preview + approval/timeout flow uses aiogram inline keyboards (`publish:approve/reject/retry`). Attestation goes through the Mnemonik MCP server (installed via `npm install -g @mnemonik-xyz/mcp` — a scoped npm package that ships the upstream binary named `mnemonik-mcp` per skeptic round 3; the role's Ansible Task verifies the actual installed binary name at deploy time and templates it everywhere). The worker invokes attestation by speaking MCP JSON-RPC over the `mnemonik-mcp mcp-stdio` transport — there is NO one-shot `sign-memory` subcommand; the `mnemonic_sign_memory` tool is reachable only via JSON-RPC. The pipeline is fully independent of Symphony / Kaneo / workspace-manager.

## Architecture

### What we're building/modifying

- **`infrastructure/ansible/roles/content-publisher/`** *(new role)* — clones `mnemonik-dev/blogger` + `mnemonik-dev/claude-blog` to `/opt/`, creates Python venv with `pip install --require-hashes`, renders `/etc/blogger.env` from sops, installs the systemd unit, ensures persistent-volume mount via `RequiresMountsFor=` resolved to a concrete volume id.
- **`fabric/content-publisher/`** *(new Python package)* — long-running worker process. Mirrors `fabric/workspace-manager/`'s shape (FastAPI `main.py` lifespan, queue-poll loop, deadline-checker sub-loop, `StateStore`-style atomic-write persistence via the existing `_write_raw`/`_acquire`/`_release` pattern in `fabric/workspace-manager/state.py:70-111`) but with a JSONL queue. Spawns `claude` as a standalone subprocess; the publish step is **in-process** (direct Python import of `mnemonik_blogger`).
- **`mnemonik-bridge-workspace/telegram-ai-agent/`** *(extend existing)*:
  - `src/telegram_bot/core/services/bot_commands.py` — append `publish_content` to `MOLYANOV_BOT_COMMANDS`.
  - `src/telegram_bot/core/handlers/publish.py` *(new)* — slash handler + 3 callback handlers (approve/reject/retry).
  - `src/telegram_bot/__main__.py` — register `publish_router` via `dp.include_router(publish_router)`.
  - `mcp-servers/bot/server.py` — add 5th `@mcp.tool()`: `publish_content_enqueue(prompt, fire_at=None, mode=None) -> dict`. Tool binds the actor's `from_user.id` from the MCP session context (NOT from LLM-supplied args).
- **`infrastructure/ansible/roles/mnemonic-mcp/`** *(rewrite)* — replace binary-download flow with `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Toggle `mnemonic_mcp_enabled: true`.
- **`infrastructure/secrets/secrets.sops.yml`** *(extend)* — add 8 keys: `blogger_telegram_bot_token`, `blogger_telegram_channel`, `publish_min_score`, `publish_approval_timeout_min`, `publish_mode`, `blogger_repo_ref`, `claude_blog_repo_ref`, `publish_callback_hmac_secrets`. The HMAC value supports two comma-separated keys (`<current>,<previous>`) for non-disruptive rotation.
- **`infrastructure/ansible/playbooks/deploy.yml`** *(extend)* — insert `- role: content-publisher` between `fabric-services` and `telegram-ai-agent` roles (real positions verified: fabric-services at line 253, telegram-ai-agent at line 290).

### How it works

End-to-end flow for `/publish_content <prompt> [--at <ISO>] [--auto]`:

```
TG /publish_content      ┌──────────────────────────────────────┐
        │                │ bot process (telegram-ai-agent)       │
        ▼                │  - aiogram handler validates from_user│
1. Bot validates ──────► │    against OPERATOR_USER_ID allowlist │
   args (LLM-parse;      │  - calls publish_content_enqueue MCP  │
   see Decision 13)      │    via bot's claude; tool re-binds    │
                         │    _actor_user_id from MCP session    │
                         │    context (NOT LLM args)             │
                         │  - MCP tool imports content_publisher │
                         │    .queue.cas_status (SHARED CAS lib) │
                         └──────────────────────────────────────┘
                              │ atomic append (POSIX O_APPEND +
                              │ fcntl.flock via shared lib)
                              ▼
   /mnt/HC_Volume_<id>/content-publisher/queue.jsonl
   {id, prompt, fire_at|null, mode, status=queued,
    cleanup_at, regenerated_to|null, notify_pending=false, ...}
                              ▲
                              │
                         ┌──────────────────────────────────────┐
2. Worker — THREE sub-loops:│ content-publisher.service             │
                         │  (a) queue-poll (5s):                 │
                         │      pick first job where status=     │
                         │      queued AND fire_at<=now OR null  │
                         │  (b) deadline-checker (30s):          │
                         │      for jobs status=preview-sent     │
                         │      AND now>=approval_deadline:      │
                         │      CAS to publishing (timeout fire) │
                         │      ALSO retry attest-pending here   │
                         │  (c) cleanup-gc (5min):               │
                         │      for jobs cleanup_at<=now AND     │
                         │      status in {rejected, done,       │
                         │      attest-failed, recovery-needed,  │
                         │      failed}:                         │
                         │      rmtree(worktree_path) + archive  │
                         │      record to queue.archive.jsonl    │
                         │                                       │
                         │ Queue picks (sub-loop a):             │
                         │  - CAS status: queued -> writing      │
                         │  - mkdir /var/lib/content-publisher/  │
                         │       work/<sanitized_uuid>/          │
                         │       (id strictly UUID v4 validated) │
                         │  - subprocess (prompt via stdin,      │
                         │     env=restricted_env('claude')):    │
                         │     claude --print --mcp-config       │
                         │       /etc/blogger.mcp.json           │
                         │       --strict-mcp-config             │
                         │       --skill claude-blog/blog-writer │
                         │  - claude writes article.md to        │
                         │     worktree (filesystem output)      │
                         │  - CAS status: writing -> scoring     │
                         │  - subprocess (analyze_blog as path): │
                         │     python                            │
                         │     /opt/claude-blog/scripts/         │
                         │     analyze_blog.py <article_path>    │
                         │     (path is validated worktree-      │
                         │      contained)                       │
                         │  - parse score + issues; truncate     │
                         │     issues array to first 3           │
                         │  - render preview via DIRECT Python   │
                         │     import (see Decision 4):          │
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
                         │     # list[str] — each is one Telegram │
                         │     # message in the thread            │
                         │  - CAS status: scoring -> preview-sent│
                         │     write approval_deadline=now+5min, │
                         │     preview_pending=true,             │
                         │     preview_segments=<list>,          │
                         │     cleanup_at=now+24h                │
                         └──────────────────────────────────────┘
                              │              │
        ┌─────────────────────┘              └──────────────────┐
        ▼ operator clicks                                       ▼ deadline-checker fires
3a. Bot callback (HMAC-tagged,                   3b. Worker deadline-checker:
    operator-only)                               for jobs where status=preview-sent
    @router.callback_query(F.data.startswith     AND now>=approval_deadline:
       ("publish:"))                              CAS preview-sent -> publishing
    Validates from_user==OPERATOR_USER_ID         (same publish flow as 3a)
    Validates HMAC on callback_data (accepts
       either current or previous secret for
       rotation window)
    Reads queue: CAS preview-sent -> publishing
    Worker (NOT bot) does the actual publish:
       BEFORE in-process call: write
       tentative_publish_started_at to queue.jsonl
       Invokes (DIRECT IMPORT, keyword-only signature):
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
           run_campaign_from_article(       \
             article=Path(<article_md>),    \
             platforms=[Platform.TELEGRAM], \
             settings=settings,             \
             attest=False,                  \
             min_score=<min_score>)
       # run_campaign_from_article internally calls
       # render(Platform.TELEGRAM, post) so segments
       # match preview_segments byte-for-byte.
       # attest=False because we handle attestation
       # ourselves via MCP-stdio (Decision 8).
       # CampaignResult.results: list[PublishResult].
       # PublishResult fields: platform, ok, dry_run,
       #   urls: list[str], ids: list[str], error,
       #   primary_url (property).
       pr = result.results[0]
       if pr.ok:
           post_url = pr.primary_url
           post_id = pr.ids[0] if pr.ids else None
           CAS publishing -> published
       else:
           # Live TG API failure path (Risk 4)
           CAS publishing -> publish-failed
           DM operator with pr.error + retry hint

4. Attestation via MCP JSON-RPC over stdio
   Worker spawns:
     subprocess (env=restricted_env('mnemonik-mcp'),
                 argv=["mnemonik-mcp", "mcp-stdio"],
                 stdin=PIPE, stdout=PIPE):
   Writes MCP framed JSON-RPC to stdin:
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
   Argv has no payload (entire envelope is stdin JSON-RPC).
   CAS published -> done (attestation_hash + content_sha256 stored)

5. If attestation fails:
   CAS published -> attest-pending
   deadline-checker (sub-loop b) retries every 5min for 24h
   If still failing: status -> attest-failed, DM operator
```

**Crash recovery on worker startup** — iterates queue.jsonl, for each in-flight job:
- `writing` / `scoring` → restart from start (worktree path persisted, ingest is idempotent on filesystem).
- `preview-sent` → re-check `approval_deadline`; if expired, the next deadline-checker tick handles; otherwise leave alone.
- `publishing` → **CAS to `recovery-needed`** (NEW terminal-ish state). Worker DMs operator: `"Job <id> was publishing at crash. Check @mnemonik manually. If post is missing, re-issue /publish_content with the same prompt (stored at .../article.md). If post is present, no action — receipt may be missing but the post survived."` This is simpler than scanning the channel via Bot API (Decision 8 alternative).
- `attest-pending` → resume retry schedule via sub-loop (b).
- `recovery-needed` / `attest-failed` / `failed` → terminal; cleanup-gc (sub-loop c) eventually GCs the worktree at cleanup_at.

**Why `tentative_publish_started_at` is written before the blogger call:** a crash between `run_campaign_from_article` returning success and the queue write of `published` would leave the job in `publishing` on next worker start. Without a tentative timestamp, we can't distinguish "publish succeeded but we crashed before recording" from "publish itself never executed". Recording the start time + Decision 8 (manual verification) is sufficient: operator looks at @mnemonik for activity in the relevant time window.

**Race: concurrent click + timeout fire** — `content_publisher.queue.cas_status(job_id, expected, target)` uses `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()`. BOTH bot callback handler AND worker deadline-checker MUST go through this shared module (no ad-hoc writes). Winner sets `publishing`; loser reads current state, returns "too late, status: <X>" via `answerCallbackQuery`.

**Multi-segment thread**: a long article (>4096 chars) produces `RenderedPost.segments = [seg0, seg1, ...]` — Telegram emits them as a reply chain. Preview to operator shows all segments joined with `\n— — —\n` visual separators in a single message (or threaded if very long). `mnemonik_blogger`'s `run_campaign_from_article` posts them as a chain internally; `result.results[0]` is the PublishResult for the Telegram platform — `.primary_url` is the URL of the first message in the thread; `.ids` is the list of all Telegram message IDs in the chain (used for crash recovery and channel-history lookups).

### Shared resources

| Resource | Owner (creates) | Consumers | Instance count |
|----------|----------------|-----------|----------------|
| `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` | Ansible role (dir + empty file at deploy, mode 0600 op:op) | content-publisher worker (RW via cas_status), bot callback handlers (RW via cas_status), bot MCP tool (W via cas_status) | 1 (file) |
| `content_publisher.queue.cas_status()` shared helper | `fabric/content-publisher/src/content_publisher/queue.py` | Worker + bot (both processes import this single primitive) | 1 (function) |
| `/etc/blogger.env` | Ansible role (template, mode **0600 root:root** — tightened per security R2-4) | content-publisher.service (EnvironmentFile=) | 1 (file) |
| `/etc/blogger.mcp.json` | Ansible role (template, mode 0644) | claude subprocesses spawned by worker | 1 (file) |
| Mnemonik MCP daemon (`mnemonic-mcp.service`) | `mnemonic-mcp` role (npm install + systemd unit) | content-publisher worker (subprocess CLI), bot's claude (subprocess MCP) | 1 (process) |
| Telegram Bot API client (publisher bot) | `mnemonik_blogger.agent.run_campaign_from_article` (in-process; env-loaded token) | publish step | per-invocation (in-process) |

## Decisions

### Decision 1: Standalone subprocess for claude spawn (not reuse `agent_runner`)
**Decision:** content-publisher worker spawns `claude` directly via `asyncio.create_subprocess_exec("claude", "--print", "--mcp-config", "/etc/blogger.mcp.json", "--strict-mcp-config", stdin=PIPE, env=restricted_env("claude"))`. Prompt sent via stdin (NOT argv). Does NOT import workspace-manager's `agent_runner` (which is a flat module import scoped to that service's package only — `from agent_runner import run_agent`).
**Rationale:** Self-contained worker. stdin avoids argv injection + process-listing leak.
**Alternatives considered:** Refactor `agent_runner` into a shared `fabric/common/` library. Rejected — invasive change to a working service for a marginal DRY win.
**User-spec anchor:** Decision #1 in user-spec.

### Decision 2: JSONL queue with atomic CAS (not SQLite)
**Decision:** Queue persisted as one JSON object per line in `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl`. State transitions via `content_publisher.queue.cas_status()` helper using `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()` (POSIX atomic). Mirrors `fabric/workspace-manager/state.py:_write_raw` (line 70) + `_acquire` (line 103) + `_release` (line 109) pattern.
**Rationale:** Existing codebase pattern. JSONL grep-friendly. Bounded size (<1000 jobs lifetime).
**Alternatives considered:** SQLite — overkill + WAL locking quirks. Redis — adds network dep. Postgres — cross-service coupling.
**User-spec anchor:** Decision #3 + AC8.

### Decision 3: Mnemonik MCP installed via `npm install -g @mnemonik-xyz/mcp` (scoped package); binary spelling + transport verified by Task 2
**Decision:** Rewrite `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml` to replace the bogus binary-download flow (`mnemonic_mcp_binary_url` pointed at non-existent `github.com/mnemonic-ai/mnemonic-mcp`) with `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Skeptic round 3 verified the upstream npm package `@mnemonik-xyz/mcp@0.2.4` ships a binary spelled `mnemonik-mcp` (with K, NOT `mnemonic-mcp` with C). The underlying Rust binary in `mnemonik-xyz/monorepo/mcp` may be spelled differently and live under `~/.local/share/@mnemonik-xyz/mcp/bin/`. Task 2 verifies the exact installed binary name with `which mnemonik-mcp || which mnemonic-mcp` and templates the discovered name into `/etc/blogger.mcp.json`, the systemd unit, and an Ansible fact `mnemonic_mcp_binary` consumed by Task 3's templates. Subcommands available on the binary: `install`, `mcp-stdio`, `doctor` (NOT `sign-memory`, NOT `recall`). Attestation is invoked via MCP JSON-RPC over the `mcp-stdio` transport (see Architecture step 4 + Decision 8).
**Rationale:** Real upstream install path; pin npm version; no build toolchain required on VM; binary name uncertainty is resolved at deploy time by Ansible discovery + fact-templating, so we cannot bake a wrong spelling into the spec.
**Alternatives considered:** `cargo install --git https://github.com/mnemonik-xyz/monorepo --path mcp`. Rejected — npm-published binary is the upstream distribution; building Rust adds toolchain dep without benefit.
**User-spec anchor:** Decision #5 (attestation via local Mnemonik MCP) + AC7.

### Decision 4: Direct Python import of blogger renderer + publisher (not CLI) — verified empirically
**Decision:** Both preview and publish use in-process Python calls into upstream `mnemonik_blogger`. Round 4 verified the real API by reading the upstream source via `gh api`:
```python
from pathlib import Path
from pydantic import SecretStr
from mnemonik_blogger.config import Platform, Settings, TelegramConfig
# Platform is a StrEnum in CONFIG, NOT in content.voice (earlier draft was wrong)
# Values: TELEGRAM, DISCORD, TWITTER, FARCASTER  (NOT "X")
from mnemonik_blogger.content.ingest import ingest_article
# signature: (path: str | Path) -> SourcePost
from mnemonik_blogger.content.formatters import render
# signature: (platform: Platform, post: SourcePost) -> RenderedPost
# render() IS the dispatcher: _RENDERERS[platform](post)
from mnemonik_blogger.agent import run_campaign_from_article, CampaignResult
# Keyword-only signature:
#   def run_campaign_from_article(
#       *, article: str | Path, platforms: list[Platform],
#       settings: Settings, grounding: Grounding | None = None,
#       attest: bool = True, min_score: int | None = None,
#   ) -> CampaignResult
# There is NO `dry_run` positional/kwarg — dry-run is set on Settings.dry_run.

# Preview path (worker):
src = ingest_article(article_md_path)  # str | Path both work
rendered = render(Platform.TELEGRAM, src)
preview_segments = rendered.segments  # list[str], one per TG msg in thread

# Publish path (worker, after operator approves or timeout fires):
settings = Settings(
    dry_run=False,
    min_score=int(env["MNEMONIK_MIN_SCORE"]),
    claude_blog_path=env["MNEMONIK_CLAUDE_BLOG_PATH"],
    telegram=TelegramConfig(
        bot_token=SecretStr(env["TELEGRAM_BOT_TOKEN"]),
        channel=env["TELEGRAM_CHANNEL"],
    ),
)
result: CampaignResult = run_campaign_from_article(
    article=Path(article_md_path),
    platforms=[Platform.TELEGRAM],
    settings=settings,
    attest=False,  # we attest ourselves via MCP-stdio (Decision 8)
    min_score=settings.min_score,
)
# CampaignResult fields: post: SourcePost, results: list[PublishResult],
#   quality: <score report>, blocked: bool
# PublishResult fields: platform, ok, dry_run, urls: list[str],
#   ids: list[str], error: str | None
# PublishResult property: primary_url -> str | None
pr = result.results[0]
if pr.ok:
    post_url = pr.primary_url
    post_id = pr.ids[0] if pr.ids else None
```
The key invariant: `run_campaign_from_article` internally calls `_publish_post()` which uses the same `render(Platform.TELEGRAM, post)` dispatcher, so the bytes sent to Telegram are derived from the same render call as preview_segments. Byte-equality is structural, not assumed.
**Rationale:** Direct import is the only way to satisfy AC5 (byte-equality preview ↔ publish). Earlier drafts referenced `mnemonik-blogger render --stdout` (doesn't exist), `mnemonik_blogger.agent.publish_post` (doesn't exist), and positional args / `dry_run=` kwarg (signature is keyword-only, no `dry_run` arg). Empirical verification via `gh api repos/mnemonik-dev/blogger/contents/...` confirmed real shape.
**Alternatives considered:** Parse `mnemonik-blogger preview` CLI output. Rejected — preview CLI emits decorated multi-segment output (`===== telegram =====` headers) not byte-identical to publish output.
**User-spec anchor:** AC5.

### Decision 5: Separate publisher bot
**Decision:** Operator creates `@mnemonik_publisher_bot` via @BotFather, makes it admin of @mnemonik channel, puts token in sops as `blogger_telegram_bot_token`. `mnemonik_blogger.agent.run_campaign_from_article` reads `TELEGRAM_BOT_TOKEN` from `/etc/blogger.env`, distinct from operator-bot's token in `/etc/telegram-ai-agent/.env`.
**Rationale:** User-spec Decision #6 (Q17=B). Privilege isolation: leak of publisher token = channel post abuse only; leak of operator-bot token = full chat access.
**Alternatives considered:** Reuse operator-bot token with channel admin restriction. Rejected — single token = single compromise blast radius.
**User-spec anchor:** AC12, Decision #6.

### Decision 6: No auto-rewrite on low score; `[🔄 Retry]` button in preview
**Decision:** Score+feedback computed once. Always send preview. If `score < min_score`: preview text adds `Score: X/100 ⚠️ Issues: <first 3 from analyze_blog>` and inline keyboard adds `[🔄 Перегенерировать]`. Click creates a NEW job with same prompt + feedback hint; **same callback handler atomically writes `regenerated_to=<new_id>` to the old job** under the shared CAS lock.
**Rationale:** User-spec Decision #8 — operator's eye is the safety net. Truncation per user-spec AC4.
**Alternatives considered:** Self-rewrite loop with hard cap 3 (reversed in user-spec validation round 1).
**User-spec anchor:** AC4 + Decision #8.

### Decision 7: PUBLISH_MODE=auto with two-location integrity binding
**Decision:** `PUBLISH_MODE` ∈ {`approval`, `auto`}, default `approval`. `auto` skips preview, publishes directly. Operator gets post-fact DM in EXACT format: `Auto-published: <link>. Receipt: <hash>. Score: <N>.` Mode transition (approval→auto or vice versa) requires two-location confirmation:
1. `/etc/blogger.env` `PUBLISH_MODE` value
2. sops key `publish_auto_mode_token` (UUID) MUST equal the value in `/etc/blogger.env` `PUBLISH_AUTO_MODE_TOKEN`

On startup, worker compares the two; mismatch = abort with loud error. On first transition: worker DMs operator with a confirmation: `"Mode changed: approval -> auto. Reply /confirm-auto <token-tail> within 1 hour to enable; service stays in approval mode until confirmed."`
**Rationale:** Per security R2-4: env-flip alone is a single point. Two-location binding + DM challenge = explicit, auditable transition.
**Alternatives considered:** Single env flip (rejected for security). Hardcoded approval-only with auto deferred (rejected — Q11 user wants the path).
**User-spec anchor:** AC11.

### Decision 8: Manual verification for `publishing`-state crash recovery + `/recovery_mark_published` slash command
**Decision:** On worker startup, jobs in `publishing` status are CAS'd to a new `recovery-needed` terminal-ish state. Worker DMs operator with the article path + instructions. Operator handles via TWO new slash commands (BOTH owned by Task 9 alongside the existing publish:approve/reject/retry):
- `/recovery_mark_published <job_id>` — moves `recovery-needed → published` and resumes attestation (sub-loop b retries `attest-pending`).
- `/recovery_mark_failed <job_id>` — moves `recovery-needed → publish-failed` (terminal). Operator may then re-issue `/publish_content` with the original prompt (stored at the worktree path).
Both commands gated by `OPERATOR_USER_ID` allowlist.
**Rationale:** Telegram Bot API doesn't expose a clean "find recent post by content/id" — scanning getUpdates/getChat is brittle. Manual step is rare (only on worker crash mid-publish, <1/year expected) and safer.
**Alternatives considered:** Scan channel via Bot API (`getUpdates` + content_sha256 match). Rejected — Bot API has 100-update history limit; busy period may push post off the window.
**User-spec anchor:** Риск 6 (preserves no-double-post guarantee).

### Decision 9: HMAC-tagged callback_data + 12-char job_id prefix + operator authorization + rotation window
**Decision:** Callback format: `publish:<action>:<job_id[:12]>:<hmac8>` where `hmac8` = first 8 bytes of `HMAC-SHA256(secret, "<action>:<job_id[:12]>")` (hex-encoded, total <50 bytes — fits Telegram's 64-byte limit). Server holds TWO secrets in `CALLBACK_HMAC_SECRETS=<current>,<previous>`; handler accepts either, computed both ways. Rotation: edit sops → redeploy → after window, drop the trailing comma+old secret. Handler ALWAYS validates `callback.from_user.id == OPERATOR_USER_ID` BEFORE HMAC compare. Pattern mirrors `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py:227-228` (real prefix-based handler).
**Rationale:** Telegram callback_data 64-byte limit + 8-char prefix collision (birthday-bound at ~1k jobs); 12-char + HMAC removes both collision and tamper risk; two-secret window enables zero-downtime rotation.
**Alternatives considered:** Single secret with brief outage on rotation. Rejected — public-facing operator-only flow shouldn't have user-visible breakage during routine sec hygiene.
**User-spec anchor:** Ограничения (callback_data 64 байт) + AC6.

### Decision 10: Persistent volume mount enforced via systemd `RequiresMountsFor=` with concrete volume id
**Decision:** content-publisher.service unit declares `RequiresMountsFor=/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher`. The `hetzner_volume_id` is resolved at install time by Ansible (`ansible_facts.mounts | selectattr('device', 'equalto', '/dev/sdb') | map(attribute='mount') | first | regex_replace('^/mnt/HC_Volume_', '')`) into a concrete integer. NO literal `*` ever reaches the unit file. If the fact resolves empty: the template task fails loud (`failed_when: hetzner_volume_id | length == 0`).
**Rationale:** Prevents silent fallback to root disk + prevents useless glob templating.
**Alternatives considered:** `After=mnt-HC_Volume_<id>.mount` — preferred per systemd best practice but adds the same id-resolution requirement.
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
# verify upstream signature still keyword-only with the args we use
sig = signature(run_campaign_from_article)
required = {'article','platforms','settings'}
assert required <= set(sig.parameters), f'missing kwargs: {required - set(sig.parameters)}'
assert all(sig.parameters[n].kind.name == 'KEYWORD_ONLY' for n in required), \
       'run_campaign_from_article args must be keyword-only'
# verify Platform.TELEGRAM enum value
assert Platform.TELEGRAM.value == 'telegram'
"
```
exits 0; `python /opt/claude-blog/scripts/analyze_blog.py --help` exits 0. Failures stop the deploy with clear "blogger upstream API drift" + diff hint.
**Rationale:** User-spec mandates this gate. Earlier import-test variants would NOT have caught round-3 mirages (wrong module path for Platform, missing dry_run kwarg) — the signature inspection above closes that gap.
**User-spec anchor:** Ограничения "обязательная верификация CLI surface".

### Decision 12: pip install with `--require-hashes` for blogger venv
**Decision:** Role generates `requirements.locked.txt` via `pip-compile --generate-hashes` (offline, by the role-author) checked into the role. Install: `pip install --require-hashes -r requirements.locked.txt`. Blogger source ref is separate (cloned by git).
**Rationale:** Supply chain. Public repo + popular dep names = realistic typosquatting risk.
**Alternatives considered:** Trust transitive deps. Rejected.
**User-spec anchor:** [TECHNICAL].

### Decision 13: LLM parses `/publish_content` slash command args (deviates from user-spec)
**Decision:** The aiogram handler for `/publish_content <raw text>` passes the raw text to bot's claude, which calls `publish_content_enqueue(prompt, fire_at, mode)` MCP tool. The tool re-validates ALL fields server-side: `prompt` non-empty + ≤8000 chars (hard cap); `fire_at` parses as ISO 8601 + is in future; `mode` ∈ {None, "approval", "auto"}; and BINDS `_actor_user_id` from the MCP session context (NOT from LLM-supplied args). Auto-mode is REFUSED via this path: tool returns "auto-mode requires direct env config + DM confirm" (forces operator down the env-flag path of Decision 7, where the prompt-injection vector cannot reach).
**Rationale:** Reuses existing bot's claude+MCP path; robust on edge cases ("tomorrow at 9am"). Server-side re-validation + actor-id binding + auto-mode refusal contain the prompt-injection risk security R2-6 raised.
**Alternatives considered:** Plain regex parse in aiogram (no MCP, no LLM). Simpler+cheaper but loses NLU. Documented under User-Spec Deviations.
**User-spec anchor:** [DEVIATION #1 — PENDING USER APPROVAL].

## Data Models

### Queue JSONL schema (`queue.jsonl`)

One JSON object per line. Order = creation order.

```json
{
  "id": "abc12345-...",                       // strict UUID v4, validated
  "created_at": "2026-06-06T14:00:00Z",
  "fire_at": "2026-06-10T10:00:00Z",          // null = immediate
  "approval_deadline": "2026-06-10T10:05:00Z",// set when status=preview-sent
  "cleanup_at": "2026-06-11T10:05:00Z",       // 24h after preview-sent; worktree GC
  "prompt": "Explain why Mnemonik...",
  "feedback_for_retry": null,                  // analyze_blog issues if this is a retry
  "mode": "approval",                          // "approval" | "auto"
  "status": "queued",                          // see state machine
  "score": null,
  "issues": [],                                // analyze_blog feedback; first 3 in preview
  "worktree_path": null,
  "article_path": null,
  "preview_segments": [],                      // list[str] from RenderedPost.segments
  "preview_message_id": null,
  "preview_pending": false,                    // worker→bot signal flag
  "notify_pending": false,                     // worker→bot DM signal (failures, attest done)
  "tentative_publish_started_at": null,        // ISO 8601, written BEFORE blogger call
  "publish_error": null,                        // PublishResult.error if status=publish-failed
  "post_url": null,                             // PublishResult.primary_url
  "post_message_id": null,                      // PublishResult.ids[0] if available
  "content_sha256": null,                      // sha256(article_md_bytes); attestation binding
  "attestation_hash": null,
  "attest_attempts": 0,
  "regenerated_to": null,                      // job_id created by [🔄 Retry]
  "retries_of": null                           // inverse link
}
```

### Job state machine

```
queued ─► writing ─► scoring ─► preview-sent ─┬─► publishing ─┬─► published ─► attest-pending ─► done
   │         │           │            │       ├─► rejected    │
   │         │           │            │       │   (terminal)  │
   │         │           │            │       └─► regenerated │           (24h fail)
   │         │           │            │           (stays at   │              ▼
   │         │           │            │           preview-sent│         attest-failed (terminal)
   │         │           │            │           until GC)   │
   │         │           │            │                       └─► publish-failed (terminal — TG
   │         │           │            │                                   API failure; user-spec
   │         │           │            │                                   Risk 4; operator can
   │         │           │            │                                   /publish_content again)
   └─────────┴───────────┴────────────┘─► failed (terminal — any hard error in writing/scoring/spawn)

(worker startup, job at "publishing")
    └─► recovery-needed (terminal-ish; operator must verify TG manually)
         │
         ├─► /recovery_mark_published <id>  → published → attest-pending → done
         └─► /recovery_mark_failed <id>     → publish-failed (terminal)
```

### `restricted_env(kind)` allowlist table

Helper produces a minimal env dict per subprocess kind (none of the parent process's full os.environ leaks):

| Kind | Variables included | Source |
|------|--------------------|--------|
| `claude` | `PATH`, `HOME=/var/lib/content-publisher`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` (fallback) | `/etc/blogger.env` |
| `analyze_blog` | `PATH`, `MNEMONIK_CLAUDE_BLOG_PATH` | `/etc/blogger.env` |
| `mnemonik-mcp` (binary name templated from Task 2 fact) | `PATH` only | (none — all payload via stdin JSON-RPC envelope; argv = `["<bin>", "mcp-stdio"]`) |
| `blogger_inproc` | (in-process call, no subprocess; uses `TELEGRAM_BOT_TOKEN`, `MNEMONIK_MIN_SCORE` from worker's env) | worker process env |

`test_env_isolation.py` asserts each subprocess kind sees exactly the listed variables and nothing else (specifically asserts that `BOT_TOKEN` from telegram-ai-agent is NOT visible to claude/analyze_blog/mnemonic-mcp subprocesses).

### `/etc/blogger.env` (rendered from sops; mode 0600 root:root)

```
TELEGRAM_BOT_TOKEN={{ blogger_telegram_bot_token }}
TELEGRAM_CHANNEL={{ blogger_telegram_channel | default('@mnemonik') }}
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

### `/etc/blogger.mcp.json` (mcp-stdio transport — binary name resolved at install)

```json
{ "mcpServers": { "mnemonik": { "command": "{{ mnemonic_mcp_binary }}", "args": ["mcp-stdio"] } } }
```

The `{{ mnemonic_mcp_binary }}` Ansible fact is set by Task 2 to whatever binary name `npm install -g @mnemonik-xyz/mcp` actually placed on PATH (skeptic round 3 saw `mnemonik-mcp` with K; the role discovers and templates the discovered value rather than baking in a spelling assumption).

## Dependencies

### New packages
- `mnemonik-dev/blogger` — Python package via `pip install --require-hashes -r requirements.locked.txt` in venv at `/opt/blogger/venv/`. Pinned commit SHA.
- `mnemonik-dev/claude-blog` — Cloned to `/opt/claude-blog/`; not pip-installed. Pinned commit SHA.
- `@mnemonik-xyz/mcp` — Installed via `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Provides `mnemonic-mcp` binary on PATH.

### Using existing (from project)
- `fabric/workspace-manager/state.py` lines 70 (`_write_raw`), 103 (`_acquire`), 109 (`_release`) — READ as reference; we copy ~30 LOC into `fabric/content-publisher/src/content_publisher/state.py`.
- `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py` — `@mcp.tool()` pattern.
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py:227-228` — real prefix-based callback handler pattern.
- `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2` — systemd unit shape.
- `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2` — sops→env-file render.

## Testing Strategy

**Feature size:** L

Mock fixtures centralized in `fabric/content-publisher/tests/conftest.py`: `mock_analyze_blog`, `mock_blogger_run_campaign_from_article`, `mock_mnemonic_mcp`, `mock_claude_subprocess`. Imported by bot-side tests where needed.

### Unit tests

- `test_queue.py` — JSONL round-trip; `cas_status` atomic under simulated concurrent writers (threading); sort by fire_at; idempotent transitions.
- `test_state_machine.py` — every valid + every illegal transition; crash-recovery decisions per status (including `publishing → recovery-needed`).
- `test_preview_render.py` — **cross-comparison byte-equality**: (a) compute `rendered_segments = render(Platform.TELEGRAM, ingest_article(path)).segments`; (b) monkey-patch the platform-specific publisher inside `mnemonik_blogger.agent._publish_post` (e.g. patch `mnemonik_blogger.publish.telegram.send_segments` or whichever is the actual seam in upstream) to capture what would be sent to Telegram instead of calling the real Bot API; (c) call `run_campaign_from_article(article=Path(path), platforms=[Platform.TELEGRAM], settings=Settings(dry_run=False, ...), attest=False)`; (d) assert captured segments == rendered_segments byte-for-byte. The intercept point's exact name is verified by Task 3's import-test (Decision 11).
- `test_analyze_gate.py` — high score (90): preview without `[🔄]`. Low score (50): preview with `[🔄]` + first 3 issues only.
- `test_at_parse.py` — future ISO accepted; past ISO rejected with explicit error.
- `test_uuid_validation.py` — accepts UUID v4 only; rejects `../`, control chars, leading dash, empty, too-long.
- `test_publish_handlers.py` — aiogram callback routing for `publish:approve|reject|retry`; HMAC validation (current+previous secrets accepted; foreign rejected); `from_user.id != OPERATOR_USER_ID` rejected; replay protection (same callback fired twice → second sees `status != preview-sent` and returns "too late").
- `test_deadline_checker.py` — 30s sub-loop: `approval_deadline = now-1s` fires; `now+10s` doesn't.
- `test_cleanup_gc.py` — sub-loop (c): jobs with `cleanup_at <= now` AND terminal status → worktree removed + record archived to `queue.archive.jsonl`. Jobs in non-terminal status with `cleanup_at <= now` are NOT GC'd (safety).
- `test_attest_retry.py` — retry every 5min; escalates to `attest-failed` at 24h.
- `test_spawn_failure_path.py` — claude subprocess non-zero exit → `failed` + `notify_pending=true` (AC10).
- `test_auto_mode.py` — `PUBLISH_MODE=auto` + matching token: skip preview; post-fact DM in exact format (AC11). Mismatched token: worker startup aborts.
- `test_separate_bot_token.py` — `run_campaign_from_article` reads `TELEGRAM_BOT_TOKEN` from `/etc/blogger.env`-style env, NOT operator-bot's `.env` (AC12).
- `test_required_mounts_for.py` — Ansible-render the systemd unit with concrete `hetzner_volume_id`. Assert no literal `*`. Render with unresolved fact → fail loud (AC15).
- `test_tentative_write_ordering.py` — mock `run_campaign_from_article` to RAISE after `tentative_publish_started_at` is set; restart-simulation finds the field; CAS to `recovery-needed` (AC-T7).
- `test_retry_linkage.py` — `[🔄 Retry]` callback creates new job AND atomically writes `regenerated_to=<new_id>` on the old job (Decision 6).
- `test_slash_registration.py` — `publish_content` appears in `MOLYANOV_BOT_COMMANDS`; Telegram-style format validator (`re.fullmatch(r"[a-z0-9_]+", name)`) accepts (AC1).
- `test_env_isolation.py` — each subprocess kind sees only its allowlisted env vars; `BOT_TOKEN` of telegram-ai-agent NOT visible to claude/analyze_blog/mnemonic-mcp.
- `test_attest_envelope.py` — Mnemonik MCP-stdio attestation envelope: argv is exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]` ONLY (no payload); the MCP JSON-RPC `initialize` then `tools/call mnemonic_sign_memory` messages (with `content`, `content_sha256`, `post_url`, `score`, `prompt` in the `arguments` field) are written to stdin; mocked subprocess captures argv + stdin bytes; receipt is parsed from mocked stdout response.
- `test_auto_mode_token_binding.py` — flipping `PUBLISH_MODE=auto` without matching `publish_auto_mode_token` → worker startup raises + journald error.

### Integration tests

- `test_queue_persist.py` — write job → kill+restart worker → resumes from correct status.
- `test_concurrent_publish.py` — threading: concurrent callback click + deadline-fire → exactly one mocked `run_campaign_from_article` call.
- `test_crash_recovery.py` — kill at each in-flight status. `writing` → restart; `scoring` → restart scorer; `preview-sent` → re-check deadline; `publishing` → CAS to `recovery-needed` + DM operator. `recovery-needed` + manual `/recovery_mark_published` slash → status `published`.
- `test_attestation_pipeline.py` — happy + Mnemonik MCP fail → attest-pending + retry cadence + 24h escalation.

### E2E tests

- `tests/e2e/test_publish_smoke.sh` — post-deploy live: `/publish_content TEST_FIXTURE_PROMPT` → preview within 90s → click `[✓]` → post in @mnemonik within 30s → spawn `<mnemonic_mcp_binary> mcp-stdio`, send MCP `tools/call mnemonic_recall {"id": "<receipt_hash>"}` over stdin, parse tool response for body → assert body matches article. Teardown: script prints chat_id + message_id; operator deletes manually. (Scripted deletion via Bot API `deleteMessage` is a stretch goal — out of MVP scope.)

## Agent Verification Plan

**Source:** user-spec "Как проверить".

### Verification approach

Per-task `Verify-smoke:` in each Implementation Task. The Final Wave's post-deploy step runs the full live-environment E2E.

### Tools required

- `bash` + `curl` — Bot API: `getMyCommands`, `getUpdates`, `answerCallbackQuery`, `getChat`.
- `ssh` — `journalctl -u content-publisher`, `journalctl -u telegram-ai-agent`, `cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl`.
- MCP-stdio handshake (bash one-liner): `printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mnemonic_recall","arguments":{"id":"<hash>"}}}' | <mnemonic_mcp_binary> mcp-stdio` — for AC7. (No `mnemonic-mcp recall` CLI subcommand exists.)
- `Telegram MCP` (optional) — operator click simulation.

## Risks

| Risk | Mitigation |
|------|-----------|
| Mnemonik MCP binary name/subcommand differs after npm install | Task 2's binary discovery (`which mnemonik-mcp || which mnemonic-mcp` → fact) + post-install MCP-stdio handshake test catches drift at deploy time; fail-loud |
| `mnemonik_blogger` upstream signature shifts (e.g. adds required kwarg) | Decision 11's `inspect.signature` check at install time asserts keyword-only `article`, `platforms`, `settings` are all present; fail-loud with diff hint |
| Blogger upstream renames internal modules | Decision 11 import test catches at deploy time. Pinned ref protects from drift |
| LLM hallucinates args in `publish_content_enqueue` MCP call | Server-side re-validation; `_actor_user_id` bound from MCP session context, not LLM-supplied; auto-mode rejected via this path (Decision 13) |
| Concurrent queue writes from bot (MCP tool) + worker | Shared `cas_status` via flock; worst case ~10ms wait |
| Hetzner volume id varies | Ansible discovers via mounts fact, templates concrete int |
| Mnemonik MCP role npm install fails | Role exits non-zero with clear message; deploy fails fast |
| Pinned refs go stale | Manual bump: edit `defaults/main.yml` + redeploy |
| Operator forgets to add publisher bot as channel admin | Role's Ansible smoke calls `getChat` on @mnemonik with publisher token; fails clearly |
| Subprocess argv content leak via `ps` | All bulk content via stdin; mnemonik-mcp argv is just `[<binary>, "mcp-stdio"]` (entire MCP JSON-RPC envelope including content/url/score/prompt lives in stdin) |
| in-process publish hardening | systemd unit: `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`. excepthook scrubs article_text from tracebacks before journald |
| HMAC secret rotation breaks live previews | `CALLBACK_HMAC_SECRETS=current,previous` window; runbook in `infrastructure/ansible/roles/content-publisher/README.md` |
| `PUBLISH_MODE=auto` env-flip single-point | Decision 7: two-location binding (`PUBLISH_AUTO_MODE_TOKEN` env value must equal sops); DM challenge on transition |
| Stale callback buttons after terminal transitions | After publish/reject/auto-publish: bot edits preview message removing inline keyboard |
| HMAC secret rotation undetected misuse | journald event log line on every callback HMAC mismatch; operator can grep |
| Worktrees accumulate (cleanup_at GC misses) | `test_cleanup_gc.py` integration test; weekly journalctl review (manual operator task) |

## User-Spec Deviations

### Deviation #1: LLM-based slash-command argument parsing
**What user-spec implies:** plain regex parse of `--at <ISO>` / `--auto` flags from `/publish_content` text.
**What tech-spec does:** routes through bot's claude → MCP tool `publish_content_enqueue`.
**Why:** robust NLU on edge cases ("publish tomorrow at noon"). Reuses existing bot+MCP path. Server-side re-validation + actor-id binding + auto-mode refusal contain the prompt-injection vector.
**Status:** [PENDING USER APPROVAL — user confirmed during validation round 1].

### Deviation #2: `pip install --require-hashes` for blogger venv
**What user-spec says:** silent on supply chain.
**What tech-spec does:** adds locked-hashes pin.
**Why:** public PyPI + popular dep names = realistic typosquatting risk per security audit.
**Status:** [TECHNICAL].

### Deviation #3: Crash recovery in `publishing` state uses manual operator verification (not blogger CLI auto-check)
**What user-spec says:** AC14 + Риск 6 prescribe that on worker restart with a job at `status=publishing`, the worker "проверяет реальный post-status через blogger CLI и НЕ double-post'ит" (queries blogger to determine whether the post actually landed before deciding to re-publish).
**What tech-spec does:** Decision 8 substitutes a manual operator step: worker CAS's the job to `recovery-needed` + DMs the operator with the article path and channel link; operator handles via `/recovery_mark_published` (post landed — resume attestation) or `/recovery_mark_failed` (post missing — re-issue `/publish_content`).
**Why:** skeptic round 2 verified `mnemonik_blogger.agent.find_post_by_id` does not exist in upstream blogger. Skeptic round 3 verified `mnemonik-mcp recall <hash>` is also not a CLI subcommand (it's an MCP tool over mcp-stdio, but it recalls signed memories — not arbitrary Telegram posts). Scanning the channel via Bot API `getUpdates` is brittle (100-update history limit) and would mis-detect during busy periods. Worker-crash-mid-publish is a rare event (<1/year expected), so trading "automated but brittle" for "manual but reliable" is the right call. The no-double-post guarantee from user-spec Риск 6 is preserved because the worker never auto-retries publish on `recovery-needed`.
**Status:** [TECHNICAL — operator must accept the rare manual recovery step].

## Acceptance Criteria

User-spec AC1-15 inherited. Additional tech-level:

- [ ] **AC-T1 — Module imports verified during role install**: Ansible asserts blogger imports (5 names) + analyze_blog --help. Result + SHAs recorded in `work/content-publish-pipeline/decisions.md`.
- [ ] **AC-T2 — No regression in existing services**: post-deploy verify all services healthy:
  - systemd: `telegram-ai-agent`, `workspace-manager`, `mnemonic-mcp.service` (the systemd unit name stays the same regardless of binary spelling), `content-publisher` — `active (running)`
  - docker compose: `kaneo-*` stack at `/opt/kaneo/` and `vaultwarden-*` stack at `/opt/vaultwarden/` — all containers `Up (healthy)` via `docker compose ps`
- [ ] **AC-T3 — File modes**: queue.jsonl `op:op 0600`; `/etc/blogger.env` `root:root 0600`; `/etc/blogger.mcp.json` `root:op 0644`.
- [ ] **AC-T4 — RequiresMountsFor**: `umount` volume → `systemctl restart content-publisher` exits non-zero; journalctl has "mount missing" message.
- [ ] **AC-T5 — Existing tests pass**: workspace-manager + telegram-ai-agent suites green.
- [ ] **AC-T6 — Operator authorization + HMAC**: foreign `from_user.id` callback → "Not authorized"; tampered HMAC → "Invalid signature".
- [ ] **AC-T7 — tentative ordering**: `test_tentative_write_ordering.py` verifies `tentative_publish_started_at` written before `run_campaign_from_article` call.
- [ ] **AC-T8 — Mnemonik MCP argv hygiene + MCP-stdio handshake**: `test_attest_envelope.py` proves subprocess argv is exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]` (no payload data); `integration/test_mcp_stdio_handshake.py` proves the worker correctly performs MCP `initialize` then `tools/call mnemonic_sign_memory` over stdin and parses the receipt from stdout.
- [ ] **AC-T9 — env isolation**: `test_env_isolation.py` proves per-subprocess allowlist.
- [ ] **AC-T10 — auto-mode two-location binding**: `test_auto_mode_token_binding.py` proves mismatched token aborts.
- [ ] **AC-T11 — HMAC rotation runbook**: README documents 4-step rotation; security-audit task signs off.

## Implementation Tasks

### Wave 1 (independent — foundation)

#### Task 1: Sops + secrets template extension
- **Description:** Add 8 new keys to `infrastructure/secrets/secrets.sops.yml.template` (7 user-spec keys + `publish_callback_hmac_secrets` + `publish_auto_mode_token`). Add empty placeholders to encrypted `secrets.sops.yml` via `sops edit`. Expose as Ansible facts via existing fabric-services sops load.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Files to modify:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/secrets/secrets.sops.yml` (sops-edited)
- **Files to read:** `infrastructure/secrets/secrets.sops.yml.template`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

#### Task 2: Rewrite Mnemonik MCP role for npm scoped-package install + binary-name discovery + npm lockfile
- **Description:** Replace `mnemonic_mcp_binary_url`/`mnemonic_mcp_binary_sha256` with `npm install -g @mnemonik-xyz/mcp@<pinned-version>`. Ensure node+npm prerequisite on VM. After install, discover the actual binary name on PATH via `which mnemonik-mcp || which mnemonic-mcp` → save as Ansible fact `mnemonic_mcp_binary`. Update systemd unit + `/etc/blogger.mcp.json` template to use the discovered binary name. Toggle `mnemonic_mcp_enabled: true`. Install via `npm ci` from a checked-in `package-lock.json` (per security R2-8) so transitive deps are also locked. Add publisher-bot token rotation runbook AND mnemonik-mcp version-bump runbook to role README.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** post-deploy `systemctl is-active mnemonic-mcp` → `active`; `$mnemonic_mcp_binary --help` exits 0 and lists subcommand `mcp-stdio`; one-shot JSON-RPC handshake: spawn `$mnemonic_mcp_binary mcp-stdio` and feed an `initialize` message via stdin → expect protocol-version response from stdout. NO `sign-memory` subcommand probe (skeptic round 3 confirmed it doesn't exist).
- **Files to modify:** `infrastructure/ansible/roles/mnemonic-mcp/{defaults/main.yml, tasks/main.yml, templates/mnemonic-mcp.service.j2, files/package-lock.json (NEW), README.md (NEW or extended)}`
- **Files to read:** `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml`, `infrastructure/ansible/roles/fabric-services/tasks/main.yml`

#### Task 3: Ansible role `content-publisher` — install scaffold + upstream verification + README rotation runbook
- **Description:** Create role with `tasks/main.yml` (clones blogger + claude-blog at pinned SHAs, venv with `pip install --require-hashes`, ensures `/var/lib/content-publisher/work/` and volume queue dir with concrete `hetzner_volume_id`, asserts module imports), `defaults/main.yml`, `templates/blogger.env.j2` (mode 0600 root:root), `templates/blogger.mcp.json.j2`, `templates/content-publisher.service.j2` (with `RequiresMountsFor=`, `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`; NOT yet started — Python service in later tasks). `README.md` includes HMAC + auto-mode rotation runbooks. Insert role into `deploy.yml` between fabric-services and telegram-ai-agent.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor
- **Verify-smoke:** `ansible-playbook --syntax-check`; on test deploy: `/opt/blogger/`, `/opt/claude-blog/`, `/etc/blogger.env`, `/etc/systemd/system/content-publisher.service` all present; module-import assertion passes; unit `enabled` but `inactive`.
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/{tasks/main.yml, defaults/main.yml, templates/blogger.env.j2, templates/blogger.mcp.json.j2, templates/content-publisher.service.j2, requirements.locked.txt, README.md}`, `infrastructure/ansible/playbooks/deploy.yml`, `work/content-publish-pipeline/decisions.md`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/tasks/main.yml`, `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2`, `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2`

### Wave 2 (depends on Wave 1)

#### Task 4: `fabric/content-publisher/` — queue + state machine + CAS (shared) + env isolation
- **Description:** New Python package. Implement `state.py` (atomic-write helpers copied from workspace-manager pattern), `queue.py` (`load`, `append`, `cas_status` — the shared CAS primitive both worker and bot import), `models.py` (Pydantic Job + status enum with `recovery-needed`), `env.py` (`restricted_env(kind)` helper per the allowlist table). Centralized mock fixtures in `tests/conftest.py`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `fabric/content-publisher/pyproject.toml`, `fabric/content-publisher/src/content_publisher/{state.py, queue.py, models.py, env.py, __init__.py}`, `fabric/content-publisher/tests/{conftest.py, test_queue.py, test_state_machine.py, test_uuid_validation.py, test_env_isolation.py}`
- **Files to read:** `fabric/workspace-manager/state.py`, `fabric/workspace-manager/models.py`, `fabric/workspace-manager/tests/conftest.py`

#### Task 5: `fabric/content-publisher/` — claude spawn + analyze_blog + renderer (direct import)
- **Description:** Worker step for `writing → scoring → preview-sent` jobs: validates id is UUID v4, creates worktree, spawns claude via subprocess with prompt piped through stdin and `env=restricted_env("claude")`; runs `analyze_blog.py` and truncates issues to first 3; renders preview via `mnemonik_blogger.content.formatters.render(Platform.TELEGRAM, ingest_article(Path(article_path)))` (Decision 4) and stores `preview_segments`; writes `preview_pending=true`. Centralized mocks let tests run without real claude/blogger/MCP.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/{worker.py, spawn.py, score.py, render.py}`, `fabric/content-publisher/tests/{test_preview_render.py, test_analyze_gate.py, test_spawn_failure_path.py, test_separate_bot_token.py, test_required_mounts_for.py}`
- **Files to read:** `fabric/workspace-manager/dispatch.py` (subprocess pattern, REFERENCE only)

#### Task 6: `fabric/content-publisher/` — publish + deadline-checker + cleanup-gc + MCP-stdio attestation + recovery + rate ceiling
- **Description:** Implement publish step: writes `tentative_publish_started_at` BEFORE the call; in-process call to `run_campaign_from_article(article=Path(article_md), platforms=[Platform.TELEGRAM], settings=Settings(...), attest=False, min_score=N)` (keyword-only signature, see Decision 4); captures `pr.primary_url`, `pr.ids[0]` from `result.results[0]`; CAS `publishing → published` on `pr.ok==True` OR `publishing → publish-failed` with `pr.error` stored on `pr.ok==False` + DM operator. Implement deadline-checker sub-loop (30s; also retries `attest-pending`). Implement cleanup-gc sub-loop (5min; rmtree worktrees of terminal jobs whose `cleanup_at <= now`; archive record to `queue.archive.jsonl`). Implement attestation via MCP-stdio: spawn `<mnemonic_mcp_binary> mcp-stdio` subprocess with `env=restricted_env("mnemonik-mcp")`; speak MCP JSON-RPC over stdin (newline-delimited) — `initialize` then `tools/call` for `mnemonic_sign_memory` with arguments `{content, content_sha256, post_url, score, prompt}`; read receipt from stdout; close stdin to exit subprocess. On any failure (subprocess crash, MCP error response, timeout): `published → attest-pending` + retry every 5min via sub-loop (b); 24h escalation: `attest-failed` + DM operator. Implement crash recovery scan on `main.py` startup: `publishing` → CAS `recovery-needed` + DM operator. Implement two-location auto-mode token check on startup (abort if mismatch). Add posts-per-hour rate ceiling counter (security R2-9) — refuses to publish if >N posts in last hour, status `publish-failed` with `publish_error="rate ceiling"`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `fabric/content-publisher/src/content_publisher/{publish.py, deadline.py, cleanup.py, attest.py, recovery.py, main.py, mcp_client.py}`, `fabric/content-publisher/tests/{test_deadline_checker.py, test_cleanup_gc.py, test_attest_retry.py, test_attest_envelope.py, test_auto_mode.py, test_auto_mode_token_binding.py, test_tentative_write_ordering.py, test_publish_failed_path.py, test_rate_ceiling.py, integration/test_crash_recovery.py, integration/test_concurrent_publish.py, integration/test_attestation_pipeline.py, integration/test_mcp_stdio_handshake.py}`
- **Files to read:** `fabric/workspace-manager/main.py` (lifespan pattern)

#### Task 7: Wire `content-publisher.service` to start on deploy + Ansible smoke
- **Description:** Update Ansible role (append to Task-3 role; not a rewrite): install `fabric/content-publisher/` package into venv, enable+start the systemd unit. `wait_for` task asserts polling within 30s of start (specific log line: `queue-poll started`, `deadline-checker started`, `cleanup-gc started`). Ansible task calls publisher-bot's `getChat` on @mnemonik via Bot API — fails clearly if not admin.
- **Skill:** code-writing
- **Reviewers:** code-reviewer
- **Verify-smoke:** ssh `systemctl is-active content-publisher` → `active`; `journalctl -u content-publisher --since "1 minute ago"` matches all 3 start lines.
- **Files to modify:** `infrastructure/ansible/roles/content-publisher/tasks/main.yml` (append), `infrastructure/ansible/roles/content-publisher/handlers/main.yml`
- **Files to read:** `infrastructure/ansible/roles/fabric-services/handlers/main.yml`

### Wave 3 (depends on Wave 2)

#### Task 8: Bot's MCP server — `publish_content_enqueue` tool (uses shared CAS + actor-id binding)
- **Description:** Add `@mcp.tool()` `publish_content_enqueue(prompt: str, fire_at: str | None = None, mode: str | None = None) -> dict` in `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`. Imports `from content_publisher.queue import cas_status, append_job`. Re-validates server-side: prompt non-empty + ≤8000 chars; `fire_at` parses + future; `mode ∈ {None, "approval"}` — `"auto"` REJECTED via this path (Decision 13); binds `_actor_user_id` from MCP session context. Returns `{job_id, status}`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`, `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/tests/__init__.py` *(NEW dir)*, `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/tests/test_publish_tool.py` *(NEW)*
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`, `fabric/content-publisher/src/content_publisher/queue.py` (Task 4)

#### Task 9: Bot — slash commands + callback handlers + preview-dispatch poll + retry linkage + recovery commands + rejection-rate metric
- **Description:** Add 3 new entries to `MOLYANOV_BOT_COMMANDS`: `publish_content`, `recovery_mark_published`, `recovery_mark_failed` (last two per Decision 8). New file `core/handlers/publish.py` with: (a) slash handler for `/publish_content` (passes raw text to bot's claude → MCP tool per Deviation #1); (b) slash handlers for `/recovery_mark_published <job_id>` and `/recovery_mark_failed <job_id>` (operator-only; resolve full UUID by 8-char prefix in queue, CAS `recovery-needed → published` or `recovery-needed → publish-failed`); (c) 3 callback handlers for `publish:approve|reject|retry:<id[:12]>:<hmac>` with `from_user.id` allowlist + HMAC validation (accept current OR previous secret); on retry: atomically write `regenerated_to=<new_id>` on old job + create new job (single CAS-protected operation). Background asyncio task polls queue.jsonl every 5s for `status=preview-sent AND preview_pending=true`, sends preview DM (joined `preview_segments` with `\n— — —\n` separator) with HMAC-tagged inline keyboard, marks `preview_pending=false`. Same poll sends DMs for `notify_pending=true` (spawn failures, publish-failed, attest results). Sliding-window counter (security R2-9): if >5 HMAC failures or unauthorized callbacks in 10-min window → DM operator. Register `publish_router` in `__main__.py`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer, security-auditor
- **Verify-smoke:** unit `test_publish_handlers.py`: simulate `publish:approve:abc123456789:<hmac>` → CAS preview-sent→publishing → mocked `run_campaign_from_article` called once.
- **Files to modify:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py` *(NEW)*, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py`, `mnemonik-bridge-workspace/telegram-ai-agent/tests/{test_publish_handlers.py, test_retry_linkage.py, test_slash_registration.py}`
- **Files to read:** `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py`, `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py`

### Audit Wave

#### Task 10: Code Audit
- **Description:** Full-feature code-quality audit. Read all source files created/modified across Tasks 1-9. Review holistically: shared CAS primitive correctly imported in both processes; subprocess invocations consistently use stdin + restricted_env; in-process publish hardening (excepthook scrub); error-handling consistency; shared resources match Architecture.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 11: Security Audit
- **Description:** Full-feature OWASP audit. Focus: subprocess argv/env hygiene; path traversal; callback HMAC + replay protection + rotation; sops env leakage paths; bot MCP tool input validation + actor-id binding; publisher token leakage (excepthook, coredumps); attestation content-sha256 binding; auto-mode two-location integrity.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 12: Test Audit
- **Description:** Full-feature test-quality audit. Verify every user-spec AC1-15 + tech-spec AC-T1-11 has at least one test target; preview-render cross-comparison; centralized conftest; HMAC + replay + rotation covered; cleanup-gc covered; AC-T7 covered.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 13: Pre-deploy QA
- **Description:** Run unit + integration tests. Verify ACs (15 user + 11 tech). Generate AVP report.
- **Skill:** pre-deploy-qa
- **Reviewers:** none

#### Task 14: Deploy
- **Description:** Push branch, dispatch `deploy-fabric.yml`, monitor green. VM uptime preserved (#24/#25 landed). New role applied, service active.
- **Skill:** deploy-pipeline
- **Reviewers:** none

#### Task 15: Post-deploy verification
- **Description:** Live verification:
  - `/publish_content TEST: explain Mnemonik in one tweet` → preview within 90s — Telegram MCP / operator eyes
  - Click `[✓]` → post in @mnemonik within 30s — Bot API / operator eyes
  - Recall the post: spawn `<mnemonic_mcp_binary> mcp-stdio`, send MCP `initialize` then `tools/call mnemonic_recall {"id": "<receipt_hash>"}` via stdin → expect article body in tool response — ssh + bash one-liner. (There is NO `mnemonic-mcp recall <hash>` CLI subcommand; recall is an MCP JSON-RPC tool only.)
  - Services healthy: `systemctl is-active content-publisher mnemonic-mcp telegram-ai-agent workspace-manager` (all `active`) AND `docker compose -f /opt/kaneo/docker-compose.yml ps` + `docker compose -f /opt/vaultwarden/docker-compose.yml ps` (all healthy) — ssh
  - `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` → job status=done — ssh + cat
  - Delete test post manually after verification.
  Tools: Telegram MCP (or Bot API curl), ssh, bash, mnemonic-mcp CLI.
- **Skill:** post-deploy-qa
- **Reviewers:** none
