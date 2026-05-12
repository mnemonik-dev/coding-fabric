# Ansible Role: telegram-init

Create and manage a Telegram forum supergroup with eight topics via the Telegram Bot API, fully idempotent.

## Overview

This role orchestrates the setup of a Telegram forum used as the command-and-control interface for the Mnemonic Protocol coding fabric. It:

1. Verifies the bot is an administrator in the chat with forum management permissions
2. Fetches existing forum topics (paginated) and builds a mapping
3. Creates missing canonical topics with configured icon colors
4. Renders an inventory file (`telegram-topics.yml`) with the `name → message_thread_id` mapping
5. Performs a healthcheck (post and delete a test message to confirm write access)

## Requirements

### Prerequisites (Bootstrap Checklist)

These steps are performed **once** by the operator and are out of scope for this role:

- Create a Telegram bot via [@BotFather](https://t.me/botfather)
- Create a Telegram **supergroup** (not a regular group)
- Enable **forum mode** on the supergroup
- Add the bot as a member of the supergroup
- **Promote the bot to administrator** with the following permissions:
  - "Manage topics" (required for topic create/delete)
  - "Post messages" (required for healthcheck)
  - "Delete messages" (required for healthcheck cleanup)
- Record `TELEGRAM_BOT_TOKEN` and `TELEGRAM_FORUM_CHAT_ID` in the sops secrets file

See `work/coding-fabric/bootstrap-checklist.md` for detailed one-time setup instructions.

### Variables

The role consumes two secrets from `infrastructure/secrets/secrets.sops.yml`:

- `TELEGRAM_BOT_TOKEN` — Bot token from @BotFather (format: `123456:ABC-DEF...`)
- `TELEGRAM_FORUM_CHAT_ID` — Forum supergroup chat ID (negative integer, e.g. `-1001234567890`)

These may also be provided via environment variables for testing:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_FORUM_CHAT_ID`

### Canonical Topics

The role creates (or verifies) exactly eight topics:

| Topic | Repository | Purpose |
|-------|-----------|---------|
| `core` | mnemonic-core | Core protocol development |
| `mcp` | mnemonic-mcp | Model Context Protocol integration |
| `wasm` | mnemonic-wasm | WASM bindings and compilation |
| `demo-client` | mnemonic-demo-client | Demo client and UI |
| `docs` | mnemonic-docs | Documentation and guides |
| `loop` | mnemonic-loop | Coding fabric infrastructure |
| `protocol-qa` | mnemonic-qa | Protocol testing and validation |
| `ops` | fabric-ops | Operational alerts and bot commands |

Each topic is assigned a configured icon color (see `defaults/main.yml`).

## Role Behavior

### Idempotency

- **First run:** Fetches existing topics, creates missing ones, renders inventory file
- **Second run:** Fetches existing topics, skips creation, re-renders inventory only if mapping changed
- **Topic deletion:** If a topic is manually deleted from Telegram, the role will recreate it with a new `message_thread_id` on next run

### Rate Limiting

- Respects Telegram's 30 msg/sec rate limit by sleeping **0.5 seconds between API calls**
- Retries transient failures (5xx errors) up to 3 times with 2-second backoff

### Pagination

- Handles paginated `getForumTopics` response (100 topics per page)
- Continues fetching until all topics are retrieved

### Security

- **No plaintext tokens:** Bot token marked `no_log: true` on all tasks
- **No token in error messages:** URI task errors exclude request body
- **Secrets isolation:** Tokens loaded from sops at task start; never written to disk

## Output

### Inventory File

After successful execution, the role writes:

```yaml
infrastructure/ansible/inventory/telegram-topics.yml
```

Example content:

```yaml
---
telegram_topics:
  core: 2
  demo-client: 5
  docs: 6
  loop: 7
  mcp: 3
  ops: 9
  protocol-qa: 8
  wasm: 4

canonical_topics:
  core:
    name: core
    repository: mnemonic-core
    description: Core protocol development
  # ... (8 entries, one per topic)
```

This file is the **source of truth** for downstream roles (e.g., `mnemonic-tg-bridge`, `fabric-watchdog`) that need to post to specific topics.

### Healthcheck

The role posts a test message to the `ops` topic and deletes it after 5 seconds, confirming that:
- The bot can send messages
- Topic access is functional
- Message deletion (cleanup) works

## Failure Modes

### Bot is not admin

```
FAILED - Bot is not an administrator in the chat.
Actions required (see bootstrap-checklist.md):
1. Add bot to the forum supergroup
2. Promote bot to administrator with "Manage topics" permission
3. Ensure supergroup has forum mode enabled
```

**Recovery:** See bootstrap checklist. Re-run the role once the bot has admin rights.

### Secrets not found

```
FAILED - TELEGRAM_BOT_TOKEN or TELEGRAM_FORUM_CHAT_ID not found in secrets or environment
```

**Recovery:** Ensure `infrastructure/secrets/secrets.sops.yml` is decrypted and contains both secrets, or set the environment variables.

### Telegram API errors (5xx, timeouts)

The role retries up to 3 times with exponential backoff. If all retries fail, it will report the HTTP status and reason.

**Recovery:** Check Telegram API status; retry the role run.

## Testing

Run molecule tests against a mocked Telegram API:

```bash
cd infrastructure/ansible/roles/telegram-init
molecule test -s default
```

The test suite:
1. Mocks Telegram API responses for all required endpoints
2. Runs the role in a Docker container
3. Verifies idempotency (runs twice, second run makes no changes)
4. Asserts that all 8 topics are present in the output inventory

## Example Playbook

```yaml
- hosts: all
  roles:
    - role: telegram-init
      vars:
        telegram_bot_token: "{{ vault_telegram_bot_token }}"
        telegram_forum_chat_id: "{{ vault_telegram_forum_chat_id }}"
```

## Dependencies

- `ansible.builtin.uri` — HTTP requests to Telegram Bot API
- `ansible.builtin.template` — Render inventory file from Jinja2 template
- Python 3 (for YAML parsing in Jinja2)

## References

- [Telegram Bot API — Forum Topics](https://core.telegram.org/bots/api#forum)
- [Bootstrap Checklist](../../../work/coding-fabric/bootstrap-checklist.md)
- [Tech Spec — Role 5](../../../work/coding-fabric/tech-spec.md#23-ansible-roles-10)
- [User Spec — §4.1, AC1–AC3](../../../work/coding-fabric/user-spec.md)

## Acceptance Criteria

From task-05.md and tech-spec §2.3:

- [ ] All eight topics exist after first run; identical IDs after second run
- [ ] `infrastructure/ansible/inventory/telegram-topics.yml` updated and reflects current chat
- [ ] Bot can post to each topic (verified by test send + delete)
- [ ] `ansible-lint` passes

## Author

Mnemonic Protocol — coding-fabric / Task 05

## License

AGPL-3.0
