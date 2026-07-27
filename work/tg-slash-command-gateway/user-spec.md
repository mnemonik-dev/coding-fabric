---
feature: tg-slash-command-gateway
work_type: feature
size: M
status: draft
created: 2026-07-27
last_updated: 2026-07-27
target_repo: git@github.com:mnemonik-dev/telegram-ai-agent.git (own-the-fork, source of truth)
upstream_origin: git@github.com:pavel-molyanov/telegram-ai-agent.git (read-only sync source)
upstream_license: MIT
parent_feature: coding-fabric (consumes via Ansible role T09 — bumps telegram_ai_agent_pin)
note: |
  Companion coding-fabric changes landed on branch claude/tg-claude-slash-commands-irtd4k:
  - underscore-duplicate skill dirs removed from the deployed bundle (duplicate
    frontmatter names broke skill resolution);
  - dash/underscore command alias convention documented
    (infrastructure/ansible/files/claude-skills/README.md);
  - engine model unpinned from the config template
    (telegram_ai_agent_claude_model / per-topic model override);
  - bot-claude-reset operator helper installed to /usr/local/bin (VM-side
    relogin runbook).
  Everything below requires changes in the bot fork itself.
---

# User Spec — tg-slash-command-gateway

Make the Telegram bot a full gateway to the agent's slash-command surface:
every command available to a local Claude Code session (native commands,
molyanov methodology commands, any operator-added command file) must be
discoverable and usable from Telegram — including the forum's General chat.

## 1. Problems (operator report 2026-07-27)

- **P1 — engine cannot be changed from the General chat.** Engine/model are
  fixed per topic in `/etc/telegram-ai-agent/config.yml`; the General chat
  (no `thread_id`) has no topic entry at all, so there is no way to switch
  claude ↔ codex or change model without SSH + redeploy.
- **P2 — no reset/relogin after Claude quota exhaustion.** When the
  subscription quota burns out mid-day, the bot keeps resuming a dead
  session. There is no in-chat way to start a fresh session or re-establish
  auth; the operator has to SSH into the VM.
- **P3 — native Claude Code slash commands are unavailable from Telegram.**
  `/clear`, `/compact`, `/model` etc. either get swallowed by Telegram
  command parsing or are passed as literal prompt text to a `-p` session,
  where interactive-only commands do nothing.
- **P4 — molyanov command aliases don't correspond.** Molyanov docs say
  `/do-task`; Telegram can only send `/do_task` (`[a-z0-9_]` limit); the
  engine-side command file may exist under only one spelling. The operator
  can't predict which alias works, and the agent "does not recognize it at
  once".

## 2. Scope

### 2.1 In scope (bot fork changes)

1. **Command discovery + Telegram registration.**
   - On startup (and on `/sync_commands`), scan the engine HOME's
     `~/.claude/commands/*.md`, parse frontmatter `description`.
   - Register the union of (bot built-ins + discovered commands) via
     Telegram `setMyCommands`, names normalized to `[a-z0-9_]{1,32}`
     (dashes → underscores, truncate, dedupe). Telegram caps at 100
     commands — prefer bot built-ins, then discovered commands
     alphabetically; log what was dropped.
   - Result: Telegram UI autocompletes every agent command (the "main
     idea": all agent slash commands visible in the TG client).
2. **Alias normalization (fixes P4).** Incoming `/foo_bar args` that is not
   a bot built-in resolves against the discovered registry: exact filename
   match first, then dash-spelling (`foo-bar`). The resolved canonical
   spelling is what gets passed to the engine as the prompt
   (`"/foo-bar args"`), so only ONE command file per command is needed
   engine-side and the operator may type either spelling.
3. **Native command mapping (fixes P3).** Whitelist of Claude-native
   commands meaningful in non-interactive mode, mapped to bot session
   operations instead of prompt passthrough:
   - `/clear`, `/new` → drop stored `session_id` for the chat/topic
     (resume=False on next message);
   - `/compact` → run engine with `/compact` against the stored session;
   - `/model <name|alias>` → persist per-chat model override (see P1
     storage below) and confirm;
   - `/status` → spawn `claude -p "ping"` probe, report auth/model/quota
     errors verbatim to the chat.
   Unknown `/commands` fall through to alias normalization (2), then to
   plain prompt passthrough with a warning.
4. **Engine switching everywhere, incl. General chat (fixes P1).**
   - `/engine claude|codex` and `/model <m>` persist to the bot's runtime
     `topic_config.json` keyed by `(chat_id, thread_id-or-"general")` — the
     General chat gets a first-class config key instead of falling through
     to static defaults.
   - Runtime overrides take precedence over `/etc/telegram-ai-agent/config.yml`;
     `/engine reset` clears the override.
5. **In-chat reset/relogin (fixes P2).**
   - `/relogin` → same effect as VM-side `bot-claude-reset --wipe-sessions`:
     kill stray engine subprocesses for that chat, drop session state,
     re-read `.env`, run the `/status` probe, report the outcome.
   - On engine failure that matches auth/quota patterns (rate_limit,
     invalid api key, oauth revoked), the bot replies with an actionable
     message naming `/relogin` and the VM runbook instead of silent
     "Thinking...".
   - Token VALUES are never accepted via chat (Telegram history is not a
     secret store) — rotation stays VM-side via `bot-claude-reset`.

### 2.2 Out of scope

- Rust rewrite; tmux/`/tui`/voice changes; MCP server changes.
- Accepting credentials through Telegram messages.
- Multi-operator ACLs (existing single-operator allowlist unchanged).
- coding-fabric Ansible changes beyond bumping `telegram_ai_agent_pin`
  (companion changes already landed, see frontmatter note).

## 3. Acceptance criteria

- AC1. After deploy, typing `/` in the bot chat shows the discovered command
  list (spot-check: `do_task`, `new_user_spec`, `write_code`) with
  descriptions from frontmatter.
- AC2. `/do_task <args>` and (via text) `/do-task <args>` reach the engine
  as the same canonical command; engine transcript shows the command file
  was loaded (no "unknown command" fallback).
- AC3. `/engine codex` in the **General chat** switches the engine for
  subsequent General-chat messages; `/engine claude` switches back; both
  survive bot restart (persisted runtime config).
- AC4. `/model sonnet` changes the model for the current chat/topic without
  redeploy; `/status` reports the active engine+model.
- AC5. `/clear` (or `/new`) starts a fresh session — next reply does not
  resume the previous session_id.
- AC6. With a deliberately broken `CLAUDE_CODE_OAUTH_TOKEN`, sending a
  message yields an actionable auth-error reply (not endless "Thinking...");
  after fixing `.env` on the VM, `/relogin` restores service without
  systemctl access.
- AC7. `/sync_commands` re-scans and re-registers after a new command file
  is added engine-side; no bot restart needed.
- AC8. Unit tests: registry scan/normalization/dedupe/100-cap; alias
  resolution precedence; native-command whitelist dispatch; runtime
  override precedence over static config. Integration test: General-chat
  engine switch end-to-end with mocked engines.
- AC9. Upstream-sync hygiene: changes isolated to new modules + minimal
  handler wiring, documented in fork README (own-the-fork strategy, but
  keep rebase surface small).

## 4. Verification on coding-fabric VM

1. Deploy fork pin bump via T09 role; `systemctl is-active telegram-ai-agent`.
2. Walk AC1–AC7 from the operator's Telegram client (General chat + one
   forum topic).
3. `sudo bot-claude-reset --wipe-sessions` still works and coexists with
   `/relogin` (VM-side path remains the credential-rotation path).

## 5. Risks

| # | Risk | Mitigation |
|---|------|------------|
| R1 | Telegram 100-command cap truncates the registry | Deterministic priority + log dropped names; `/sync_commands` output lists them |
| R2 | Native-command semantics drift with claude CLI releases | Whitelist is data-driven (single dict); `/status` probe surfaces breakage fast |
| R3 | Runtime overrides vs static config.yml precedence confusion | `/status` always prints effective engine/model/source; `/engine reset` documented |
| R4 | Fork rebase pain against upstream handler refactors | New logic in dedicated modules (command_registry.py, native_commands.py); handlers only call into them |
