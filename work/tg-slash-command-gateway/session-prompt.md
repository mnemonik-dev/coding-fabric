# Session prompt — implement tg-slash-command-gateway in the bot fork

Copy everything below the line into a new Claude Code session opened on
`mnemonik-dev/telegram-ai-agent`. It is self-contained: the fork session has
no access to coding-fabric, so all required context is embedded.

---

## Task

Implement the **tg-slash-command-gateway** feature in this repository
(`mnemonik-dev/telegram-ai-agent`, our own fork — source of truth, no
upstream-PR ceremony; `pavel-molyanov/telegram-ai-agent` is a read-only sync
source). Work on a feature branch off `main`, deliver code + tests + docs,
merge to `main`, and tag a release.

## Background — how this bot is deployed (do not assume a laptop setup)

This bot is the single human interface of the coding-fabric VM
(deployed by Ansible; you only need these facts, not the Ansible code):

- Runs as systemd service `telegram-ai-agent`, user `op`,
  `HOME=/var/lib/telegram-ai-agent`, config at
  `/etc/telegram-ai-agent/config.yml`, env at `/etc/telegram-ai-agent/.env`.
- The engine is spawned as a subprocess per message:
  `claude --output-format stream-json ... -p "<prompt>"` (or codex).
  Auth comes ONLY from env vars `CLAUDE_CODE_OAUTH_TOKEN` (subscription,
  primary) / `ANTHROPIC_API_KEY` (pay-per-token fallback) — `claude /login`
  browser credentials do not exist on the VM.
- Claude Code slash commands available to the engine live in
  `$HOME/.claude/commands/*.md` (frontmatter: `description`, optional
  `allowed-tools`). Multi-word commands exist in BOTH spellings on disk
  (`do-task.md` + `do_task.md`) because Telegram bot commands only allow
  `[a-z0-9_]{1,32}` — treat dash spelling as canonical.
- Static per-topic config (`config.yml`) maps forum topics →
  engine/model/cwd. The forum's **General chat has no topic entry** and
  falls through to defaults. Runtime state files next to the source tree:
  `topic_config.json`, `channel_sessions.json`, `session_mapping.json`.
- A VM-side operator helper `bot-claude-reset` already exists (stops the
  service, kills stray engine processes, optionally wipes the session json
  files and rotates tokens in `.env`, restarts). Your in-chat `/relogin`
  must coexist with it, not replace it.

## Operator problems to fix (2026-07-27)

- **P1** — engine/model cannot be changed from chat; the General chat can't
  be configured at all (no topic entry, no way to switch claude ↔ codex).
- **P2** — after Claude quota exhaustion the bot keeps resuming a dead
  session with endless "Thinking..."; no in-chat reset/relogin.
- **P3** — native Claude Code slash commands (`/clear`, `/compact`,
  `/model`, ...) do nothing from Telegram.
- **P4** — molyanov command aliases don't correspond: docs say `/do-task`,
  Telegram can only send `/do_task`, and whether the engine recognizes it
  depends on which file spelling exists. Operators can't predict what works.

**Main idea: every slash command available to a local Claude Code session
must be discoverable and usable from Telegram, including the General chat.**

## Scope — five work items

1. **Command discovery + Telegram registration.** On startup and on
   `/sync_commands`: scan `$HOME/.claude/commands/*.md`, parse frontmatter
   `description`; register bot built-ins + discovered commands via
   `setMyCommands`, names normalized to `[a-z0-9_]{1,32}` (dash→underscore,
   truncate, dedupe). Telegram caps at 100 — prioritize built-ins, then
   alphabetical; log dropped names and list them in `/sync_commands` output.
2. **Alias normalization.** An incoming `/foo_bar args` that is not a bot
   built-in resolves against the registry: exact filename match first, then
   dash spelling. Pass the canonical spelling to the engine as the prompt
   (`"/foo-bar args"`). Result: one command file per command suffices and
   the operator may type either spelling.
3. **Native command mapping.** Data-driven whitelist (single dict) of
   Claude-native commands mapped to bot session operations, NOT prompt
   passthrough: `/clear` & `/new` → drop stored session_id (fresh session
   next message); `/compact` → run `/compact` against the stored session;
   `/model <name|alias>` → persist per-chat model override; `/status` →
   spawn `claude -p "ping"` probe and report engine/model/auth/quota state
   verbatim. Unknown `/commands` fall through to (2), then to plain prompt
   passthrough with a warning reply.
4. **Engine switching everywhere, incl. General chat.** `/engine
   claude|codex` and `/model <m>` persist to runtime `topic_config.json`
   keyed by `(chat_id, thread_id or "general")` — General chat becomes a
   first-class config key. Runtime overrides take precedence over static
   `config.yml`; `/engine reset` clears the override; overrides survive
   restart.
5. **In-chat reset/relogin.** `/relogin` → kill stray engine subprocesses
   for that chat, drop its session state, re-read `.env`, run the `/status`
   probe, report outcome. Additionally, when an engine failure matches
   auth/quota patterns (rate_limit, invalid api key, oauth revoked), reply
   with an actionable message naming `/relogin` and the VM-side
   `bot-claude-reset` runbook instead of silent "Thinking...".
   **Never accept token values via chat** — Telegram history is not a
   secret store; rotation stays VM-side.

## Hard constraints

- New logic goes in dedicated new modules (suggested:
  `command_registry.py`, `native_commands.py`, `runtime_overrides.py`);
  existing handlers only call into them — keep the upstream-rebase surface
  small.
- Back-compat: existing static `config.yml` topics keep working unchanged;
  no schema break for `topic_config.json` entries that already exist.
- Existing single-operator `ALLOWED_USER_IDS` auth middleware unchanged.
- No Rust rewrite, no tmux/`/tui`/voice/MCP-server changes.
- Match the repo's existing style, test layout, and CI (`uv sync`,
  `uv run ruff check`, `uv run mypy src/`, `uv run pytest tests/ -v` must
  all be green).

## Acceptance criteria

- AC1. Typing `/` in the bot chat shows the discovered command list
  (spot-check `do_task`, `new_user_spec`, `write_code`) with descriptions.
- AC2. `/do_task <args>` and `/do-task <args>` (as text) reach the engine
  as the same canonical command — no "unknown command" fallback.
- AC3. `/engine codex` in the **General chat** switches subsequent
  General-chat messages to codex; `/engine claude` switches back; both
  survive a bot restart.
- AC4. `/model sonnet` changes the model for the current chat without
  redeploy; `/status` reports effective engine+model AND their source
  (runtime override vs static config vs default).
- AC5. `/clear` (or `/new`) starts a fresh session — next reply does not
  resume the previous session_id.
- AC6. With a deliberately broken `CLAUDE_CODE_OAUTH_TOKEN`, a message
  yields an actionable auth-error reply (no endless "Thinking..."); after
  the operator fixes `.env` on the VM, `/relogin` restores service without
  systemctl access.
- AC7. `/sync_commands` re-scans and re-registers after a command file is
  added engine-side; no restart needed.
- AC8. Tests: unit — registry scan/normalize/dedupe/100-cap, alias
  precedence, native-command dispatch, override precedence; integration —
  General-chat engine switch end-to-end with mocked engines.
- AC9. Fork README documents the feature + the own-the-fork note.

## Suggested plan

1. Code-research first: locate message handlers, engine spawn sites,
   session store, and how bot built-in commands are registered today.
   The layout as of pin `873751b` was
   `src/telegram_bot/core/{config.py, handlers/, services/{claude.py,
   codex_mcp.py, topic_config.py, topic_runtime.py, tmux_spawn.py}}` —
   verify, it may have drifted.
2. Implement items 1–2 (registry + aliases), then 4 (overrides), then 3
   (native map), then 5 (/relogin + error surfacing). Tests alongside each.
3. Quality gate: ruff + mypy + full pytest.
4. Merge to `main` (ff preferred), tag `v0.2.0+mnemonik.1`, push tag.
5. Report the tag + commit SHA in your final summary — the operator will
   bump `telegram_ai_agent_pin` in coding-fabric
   (`infrastructure/ansible/roles/telegram-ai-agent/defaults/main.yml`)
   and redeploy; that part is NOT your task.

## Verification on the VM (operator will do after pin bump + deploy)

AC1–AC7 walked from the operator's Telegram client in the General chat and
one forum topic; `sudo bot-claude-reset --wipe-sessions` still works.
