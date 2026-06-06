---
status: ready
depends_on: [3, 6]
wave: 2
skills: [code-writing]
verify: [smoke]
reviewers: [code-reviewer]
teammate_name:
---

# Task 7: Wire `content-publisher.service` to start on deploy + Ansible smoke

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Task 3 created the `content-publisher` Ansible role scaffold (paths, templates, unit file, import-signature check) but left the unit `enabled` and `inactive`. Task 6 produced the `fabric/content-publisher/` Python package with three sub-loops (`queue-poll`, `deadline-checker`, `cleanup-gc`). This task is the integration glue that flips the role from "scaffolded" to "live": the role now actually installs the package into the venv, enables and starts the systemd unit, and runs three smoke checks during deploy so the operator finds out about misconfiguration immediately rather than discovering it on the first publish attempt.

Two specific operator-error classes must surface at deploy time (tech-spec Risks rows for `📝 blogger-prompts` topic + publisher bot channel-admin):
- Operator forgets to add the publisher bot as an admin of `@mnemonik` → the post step would silently fail under load. Smoke: call `getChat` against `@mnemonik` with the publisher token and assert `200 OK`.
- Operator forgets to create the `📝 blogger-prompts` forum topic, or puts the wrong `BLOGGER_PROMPTS_TOPIC_ID` in sops → the bot's topic-handler in Task 8 would never fire. Smoke: call `getForumTopicIconStickers` with `chat_id = TELEGRAM_FORUM_CHAT_ID` and `message_thread_id = BLOGGER_PROMPTS_TOPIC_ID` and assert `200 OK`.

The role must remain idempotent — re-running on a healthy box is a no-op (no unit churn, no false handler firings). Handlers cascade in the same `reload systemd → restart content-publisher` pattern used by `fabric-services`.

This is the last task on the "service is alive on the VM" critical path before the bot integration (Task 8) wires up the human-facing topic handler.

## What to do

1. **Append** to `infrastructure/ansible/roles/content-publisher/tasks/main.yml` (do NOT rewrite — Task 3's scaffold tasks stay in place above):
   - Install the `fabric/content-publisher/` package into the role's venv (editable install from the synced repo path, or `pip install --require-hashes` against the locked requirements — match the pattern Task 3 established for the venv).
   - Reload systemd if any unit template was rendered (via `notify: reload systemd`).
   - Enable and start `content-publisher.service` (`enabled: yes`, `state: started`).
   - `wait_for` task asserting all three startup log lines appear in `journalctl -u content-publisher` within 30 seconds of start: `queue-poll started`, `deadline-checker started`, `cleanup-gc started`. The task must fail with a clear, named message identifying which line is missing.
   - Smoke task: HTTP `POST` to `https://api.telegram.org/bot{{ publisher_bot_token }}/getChat` with `chat_id=@mnemonik`. Non-200 or `ok: false` → fail with a clear message naming the channel-admin runbook (`Publisher bot is not an admin of @mnemonik — see roles/content-publisher/README.md`).
   - Smoke task: HTTP `POST` to `https://api.telegram.org/bot{{ publisher_bot_token }}/getForumTopicIconStickers` with `chat_id={{ telegram_forum_chat_id }}` and `message_thread_id={{ blogger_prompts_topic_id }}`. Non-200 or `ok: false` → fail with a clear message naming the topic-creation runbook.

2. **Create** `infrastructure/ansible/roles/content-publisher/handlers/main.yml` with two handlers mirroring `fabric-services/handlers/main.yml`:
   - `reload systemd` — runs `systemd_service: daemon_reload: true`.
   - `restart content-publisher` — runs `systemd_service: name=content-publisher state=restarted`. Set `failed_when: false` so first-deploy doesn't blow up before the unit's own enable task brings it online (same reasoning as the workspace-manager handler).

3. Wire `notify:` triggers on the relevant Task-3 scaffold tasks (env template, mcp.json template, unit template) so a change to any of those cascades through `reload systemd → restart content-publisher`. The append for Task 7 only needs to add the notifies and the new tasks — Task 3's tasks themselves stay untouched in content.

4. Verify idempotence: running the role twice in a row on a healthy host must report `changed=0` for all Task-7 tasks and must not trigger either handler.

## TDD Anchor

Tests to write BEFORE implementation. For Ansible roles, "tests" are syntax + dry-run + targeted assertions:

- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` — role syntax valid, no parse errors.
- `ansible-playbook --check --diff infrastructure/ansible/playbooks/deploy.yml --tags content-publisher` against a staging inventory — no unexpected `changed` items on a second run (idempotence).
- A targeted smoke-failure rehearsal: temporarily point `publisher_bot_token` at a token whose bot is NOT a channel admin → the `getChat` smoke task MUST fail with the named runbook message (not a generic HTTP 400).
- A targeted topic-missing rehearsal: temporarily set `blogger_prompts_topic_id` to a non-existent thread id → the `getForumTopicIconStickers` smoke task MUST fail with the named topic-creation message.
- `journalctl -u content-publisher --since "1 minute ago"` after `state: started` matches all three log lines (`queue-poll started`, `deadline-checker started`, `cleanup-gc started`).

## Acceptance Criteria

- [ ] `roles/content-publisher/tasks/main.yml` is **appended to** (not rewritten) — Task 3's scaffold tasks remain byte-for-byte at the top of the file.
- [ ] Package install step puts `fabric/content-publisher/` into the role's venv (entry-point `content-publisher` resolvable).
- [ ] `content-publisher.service` is `enabled: yes` and `state: started` at the end of the play.
- [ ] `wait_for` task asserts all three startup lines appear in journal within 30 s of start; failure message names the missing line.
- [ ] Smoke task calls `getChat` on `@mnemonik` with the publisher bot token; failure path emits a clear "publisher bot is not channel admin" message naming the role README runbook.
- [ ] Smoke task calls `getForumTopicIconStickers` against `BLOGGER_PROMPTS_TOPIC_ID`; failure path emits a clear "topic missing or wrong id" message naming the role README runbook.
- [ ] `handlers/main.yml` defines `reload systemd` + `restart content-publisher`; `restart` uses `failed_when: false` for the first-deploy case.
- [ ] Idempotent: a second `ansible-playbook` run on a healthy host reports `changed=0` for Task-7 tasks and fires neither handler.
- [ ] Post-deploy: `systemctl is-active content-publisher` → `active`; `journalctl -u content-publisher --since "1 minute ago"` contains all three start lines; topic + channel smokes pass.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md) — see `#### Task 7` and Risks rows for "Operator forgets to create topic" / "Operator forgets to add publisher bot as channel admin"
- [decisions.md](../decisions.md)
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — project context: pipeline, components, infra constraints
- [handoff-bot-mcp-debug-2026-05-23.md](/Users/syi/src/sessions/coding-fabric/.claude/skills/project-knowledge/references/handoff-bot-mcp-debug-2026-05-23.md)
- [fabric-services/handlers/main.yml](/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/fabric-services/handlers/main.yml) — handler pattern to mirror (reload systemd + restart with `failed_when: false`)
- [fabric-services/tasks/main.yml](/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/fabric-services/tasks/main.yml) — `systemd` enable/start pattern + `notify` wiring reference
- [roles/content-publisher/tasks/main.yml](/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/content-publisher/tasks/main.yml) — Task 3's scaffold (APPEND target)
- [roles/content-publisher/handlers/main.yml](/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/content-publisher/handlers/main.yml) — handler file (CREATE)

## Verification Steps

### Automated
- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` → 0 errors.
- `ansible-playbook --check --diff infrastructure/ansible/playbooks/deploy.yml --tags content-publisher` (second run against staging) → `changed=0` for Task-7 tasks.
- Negative rehearsals: bad publisher token → `getChat` smoke task fails with named runbook message; bad topic id → `getForumTopicIconStickers` smoke task fails with named runbook message.

### Smoke
From tech-spec Verify-smoke (executed against the deployed VM after the role runs):
- `ssh root@vm 'systemctl is-active content-publisher'` → prints `active`.
- `ssh root@vm 'journalctl -u content-publisher --since "1 minute ago" --no-pager'` → output contains all three of: `queue-poll started`, `deadline-checker started`, `cleanup-gc started`.
- During the play itself: `getChat @mnemonik` returns `ok: true` AND `getForumTopicIconStickers` for `BLOGGER_PROMPTS_TOPIC_ID` returns `ok: true` (these are gated inside the role and fail-fast the deploy on miss).

## Details

**Files:**
- `infrastructure/ansible/roles/content-publisher/tasks/main.yml` — **APPEND** new tasks after Task 3's scaffold tasks. Order at the bottom of the file: (a) install package into venv, (b) `notify: reload systemd` triggers added to env/mcp.json/unit template tasks (Task 3's tasks), (c) enable + start `content-publisher.service`, (d) `wait_for` 30 s on the three log lines, (e) `getChat @mnemonik` smoke, (f) `getForumTopicIconStickers` smoke. Do NOT rewrite or reorder Task 3's tasks above.
- `infrastructure/ansible/roles/content-publisher/handlers/main.yml` — **CREATE**. Two handlers: `reload systemd` (daemon_reload) and `restart content-publisher` (with `failed_when: false`). Mirror `fabric-services/handlers/main.yml` exactly in shape and comment style.

**Dependencies:**
- Task 3 — role scaffold (env template, mcp.json template, unit template, venv, paths) must exist before this task adds the live-start steps.
- Task 6 — `fabric/content-publisher/` package with the three sub-loops and their `... started` log lines must exist; without it, the `wait_for` would never see the lines.

**Edge cases:**
- First-ever deploy: `content-publisher.service` does not exist when handlers are flushed at the end of Task 3's tasks. The `restart content-publisher` handler must tolerate this via `failed_when: false` (same trick as `restart workspace-manager` in `fabric-services`).
- Publisher bot token is rotated mid-deploy: `getChat` smoke uses the freshly-templated token from `blogger.env` (Task 3 already drops it at mode 0600). If rotation is stale in sops, smoke fails fast — that's the desired behavior.
- Forum chat id and topic id come from sops-decrypted vars; both must be already on the host by the time this task runs. Fail loudly if `blogger_prompts_topic_id` is undefined (don't pass through to a `0` or empty string that could match a real thread).
- Re-running the role when the service is already `active`: `state: started` is idempotent in Ansible — no restart. `wait_for` still passes because the lines are in the journal from the previous start. No handler fires.
- Telegram API rate limiting on the smoke calls: both `getChat` and `getForumTopicIconStickers` are single calls per deploy — well under any rate ceiling. No retry/backoff needed; non-200 → fail-loud.
- `journalctl --since "1 minute ago"` window: deliberately tight so we measure THIS start, not a previous one. If the unit was started long ago and `state: started` was a no-op, the `--since` window will be empty → that's fine, the test still passes because the lines were logged on the original start (use `--since "10 minutes ago"` if flaky; tech-spec says 1 min, stick with it unless flake is observed).

**Implementation hints:**
- Mirror `fabric-services/handlers/main.yml` cascade pattern: env-template change → `notify: reload systemd` (rewrites daemon state) → `notify: restart content-publisher` (picks up the new env). Apply this notify chain to the three Task-3 templating tasks (env, mcp.json, unit).
- Use `ansible.builtin.uri` module for the two Telegram smoke calls (`getChat`, `getForumTopicIconStickers`). Body format: JSON; method POST; `status_code: 200`; `body_format: json`; `return_content: yes`. Register the result and assert `result.json.ok == true` with a custom `fail` task carrying the named runbook message — this keeps the failure surface readable in Ansible output.
- For `wait_for` on log lines: `community.general.journal_grep` is unavailable in stock Ansible; use `ansible.builtin.shell: journalctl -u content-publisher --since "30 seconds ago" --no-pager | grep -q '...'` wrapped in a `until` loop with `retries: 6 delay: 5`. Three separate small tasks (one per line) yield clearer failure attribution than one composite regex.
- Avoid `community.docker` — this role has no docker. The "analogous" patterns in tech-spec refer to galaxy-collection module patterns (uri, systemd_service); no new collection is required if these are already vendored in the Ansible install.
- Token-in-URL hygiene: the publisher bot token appears in the `uri` URL for the Telegram API calls. Set `no_log: true` on those tasks so the token doesn't end up in Ansible's stdout/json output, then write a clear failure message via `fail:` so the operator still gets actionable feedback. This matches the secrets-hygiene pattern in `fabric-services` symphony.env handling.

## Reviewers

- **code-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-7/code-reviewer-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону (Summary: 1-3 предложения, ревью со ссылками на JSON, без таблиц файндингов и дампов)
- [ ] Если отклонились от спека — описать отклонение и причину
- [ ] Обновить user-spec/tech-spec если что-то изменилось
