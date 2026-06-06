# Code Research — content-publish-pipeline

Date: 2026-06-06.
Scope: identify reusable patterns + existing code touchpoints for the new
content-publish-pipeline feature. No code-researcher subagent — focused parallel
reads.

## Reusable patterns found

### 1. Slash command registration
`mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py`

- `LocalizedBotCommand(name, ru, en)` tuples.
- Two tuple groups: `PUBLIC_BOT_COMMANDS` (start, clear, cancel, language, mode, stream, engine, resume, kill, tui) and `MOLYANOV_BOT_COMMANDS` (user_spec_planning, tech_spec_planning, decompose_tech_spec, do_task, ...).
- Telegram requires underscores in slash names — already enforced.
- **For new feature**: add a `PUBLISH_BOT_COMMANDS` tuple (or extend MOLYANOV) with:
  - `publish_content` — "Опубликовать пост" / "Publish a post"
  - Optionally `schedule_post` — "Запланировать пост" / "Schedule a post"

### 2. Inline keyboard + callback handler
`mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py`,
`cancel.py`, `commands.py`.

- Pattern: `@router.callback_query(F.data.startswith("PREFIX:"))`
- `callback_data` carries IDs (chat_id, thread_id, session prefix etc).
- **For new feature**: handler at `core/handlers/publish.py` with:
  - `@router.callback_query(F.data.startswith("publish:approve:"))`
  - `@router.callback_query(F.data.startswith("publish:reject:"))`
  - Callback data format: `publish:approve:<job_id[:8]>` — short to fit Telegram's 64-byte limit.

### 3. Bot's MCP server tool registration
`mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`

- Pattern: `@mcp.tool()` decorator on typed async functions.
- Existing tools: `send_message`, `send_document`, `send_image`, `create_topic`.
- **For new feature**: add `publish_content_enqueue(prompt: str, fire_at: str | None = None, mode: str = "approval") -> dict` — writes a JSON line to /mnt/HC_Volume_*/content-publisher/queue.jsonl and returns the job_id. Used by bot's claude when operator says `/publish-content ...`.

### 4. Systemd service + timer template
- Service template: `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2` (Symphony pattern)
- Timer template: `infrastructure/ansible/roles/fabric-services/templates/fabric-watchdog.timer.j2` (5-min cadence)
- **For new feature**:
  - `content-publisher.service.j2` — long-running process (worker loop, reads queue, spawns claude with claude-blog skill, scores, sends preview to bot)
  - Alternative: timer-driven `content-publisher.timer` every 30s — simpler, but worse latency
  - **Decision**: long-running .service with internal sleep loop. Same as Symphony — single owner of the queue file.

### 5. Ansible role layout
- Existing roles to mirror: `mnemonic-mcp/`, `fabric-services/`, `telegram-ai-agent/`.
- Each has `defaults/main.yml`, `tasks/main.yml`, `templates/`, `handlers/`, optionally `molecule/`.
- **For new feature**: `infrastructure/ansible/roles/content-publisher/` with same shape.

### 6. Mnemonik MCP server is deployed locally
`infrastructure/ansible/roles/mnemonic-mcp/` exists — a local Mnemonik MCP server runs on the VM. The worker can call `mnemonic_sign_memory` / `mnemonic_recall` against it directly without going through the bot's claude.

## Touchpoints (files we'll edit)

| File | Change |
|------|--------|
| `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/services/bot_commands.py` | Add `publish_content` slash command |
| `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py` | NEW — slash + callback handlers, calls MCP tool, renders preview |
| `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py` | Add `publish_content_enqueue` MCP tool |
| `infrastructure/ansible/roles/content-publisher/` | NEW role |
| `infrastructure/ansible/playbooks/deploy.yml` | Add `- role: content-publisher` after fabric-services |
| `infrastructure/secrets/secrets.sops.yml` | Add `blogger_telegram_bot_token`, `blogger_telegram_channel='@mnemonik'`, `publish_min_score=80`, `publish_approval_timeout_min=5` |

## Pinning notes

- **mnemonik-dev/blogger**: pin to a commit ref in role defaults (not `main`) for reproducible deploys.
- **mnemonik-dev/claude-blog**: same — pin ref. The `analyze_blog.py` API is the contract.

## Open questions deferred to tech-spec

- Exact queue file format (JSONL schema with all needed fields).
- Exact MCP tool signature for `publish_content_enqueue`.
- Self-rewrite loop: how is the analyze_blog feedback passed back into claude's next attempt (system prompt? user message?).
- Concurrent jobs: serial (one at a time) or parallel? Recommend serial for MVP.
