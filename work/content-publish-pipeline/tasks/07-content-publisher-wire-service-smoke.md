---
status: in_progress
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

Task 3 created the `content-publisher` Ansible role scaffold (paths, templates, unit file, import-signature check) but left the unit `enabled` and `inactive`. Task 6 produced the `fabric/content-publisher/` Python package with three sub-loops (`queue-poll`, `deadline-checker`, `cleanup-gc`). This task is the integration glue that flips the role from "scaffolded" to "live": the role now actually installs the package into the venv, enables and starts the systemd unit, and runs one channel-admin smoke check during deploy so the operator finds out about that specific class of misconfiguration immediately rather than discovering it on the first publish attempt.

One operator-error class must surface at deploy time (tech-spec Risks row for publisher bot channel-admin):
- Operator forgets to add the publisher bot as an admin of `@mnemonik` → the post step would silently fail under load. Smoke: first call `getMe` with the publisher bot token to obtain `publisher_bot_id`, then call `getChatMember` with `chat_id=@mnemonik` and `user_id=<publisher_bot_id>` and assert `result.ok == true` AND `result.result.status in {"administrator", "creator"}`. Plain `getChat` cannot validate admin membership — it returns 200 OK for any caller against a public channel.

The `📝 blogger-prompts` topic is created by the existing `telegram-init` role earlier in the same deploy. Task 7 must consume the generated `telegram_topics['blogger-prompts']` mapping (with the sops `blogger_prompts_topic_id` as legacy fallback only), not require the operator to create a topic manually. Do NOT add a separate "read topic by thread_id" smoke: the Bot API has no endpoint that confirms an arbitrary `message_thread_id` exists in a specific chat (`getForumTopic` does not exist, and `getForumTopicIconStickers` is unrelated). Topic creation itself is already fail-loud inside `telegram-init`; fallback-topic mismatch is self-detected by Task 8's topic-handler via journald.

The role must remain idempotent — re-running on a healthy box is a no-op (no unit churn, no false handler firings). Handlers cascade in the same `reload systemd → restart content-publisher` pattern used by `fabric-services`.

This is the last task on the "service is alive on the VM" critical path before the bot integration (Task 8) wires up the human-facing topic handler.

## What to do

1. **Append** to `infrastructure/ansible/roles/content-publisher/tasks/main.yml` (do NOT rewrite — Task 3's scaffold tasks stay in place above):
   - Install the `fabric/content-publisher/` package into the role's venv (editable install from the synced repo path, or `pip install --require-hashes` against the locked requirements — match the pattern Task 3 established for the venv).
   - Reload systemd if any unit template was rendered (via `notify: reload systemd`).
   - Enable and start `content-publisher.service` (`enabled: yes`, `state: started`).
   - `wait_for` task asserting all three startup log lines appear in `journalctl -u content-publisher` within 30 seconds of start: `queue-poll started`, `deadline-checker started`, `cleanup-gc started`. Anchor the log-window to the unit's `ActiveEnterTimestamp` (`systemctl show -p ActiveEnterTimestamp content-publisher --value`) rather than a relative `--since "30 seconds ago"` — this way, slow Ansible execution between `state: started` and the journal-grep step never misses the start lines. The task must fail with a clear, named message identifying which line is missing.
   - Channel-admin smoke: HTTP `POST` to `https://api.telegram.org/bot{{ blogger_telegram_bot_token }}/getMe` to discover `publisher_bot_id = result.json.result.id`, then `POST` to `.../getChatMember` with `chat_id=@mnemonik` and `user_id={{ publisher_bot_id }}`. Non-200, `ok: false`, or `result.json.result.status not in ['administrator', 'creator']` → fail with a clear message naming the channel-admin runbook (`Publisher bot is not an admin of @mnemonik — see roles/content-publisher/README.md`).
   - Do NOT add a topic-existence smoke by `message_thread_id`. The topic is created by `telegram-init` earlier in deploy, and there is no Bot API read endpoint for validating a specific thread id. If an operator uses the legacy fallback id and it is wrong, Task 8's topic-handler self-detects the mismatch at first use via journald.

2. **Create** `infrastructure/ansible/roles/content-publisher/handlers/main.yml` with two handlers mirroring `fabric-services/handlers/main.yml`:
   - `reload systemd` — runs `systemd_service: daemon_reload: true`.
   - `restart content-publisher` — runs `systemd_service: name=content-publisher state=restarted`. Set `failed_when: false` so first-deploy doesn't blow up before the unit's own enable task brings it online (same reasoning as the workspace-manager handler).

3. Wire `notify:` triggers on the relevant Task-3 scaffold tasks (env template, mcp.json template, unit template) so a change to any of those cascades through `reload systemd → restart content-publisher`. The append for Task 7 only needs to add the notifies and the new tasks — Task 3's tasks themselves stay untouched in content.

4. Verify idempotence: running the role twice in a row on a healthy host must report `changed=0` for all Task-7 tasks and must not trigger either handler.

## TDD Anchor

Tests to write BEFORE implementation. For Ansible roles, "tests" are syntax + dry-run + targeted assertions:

- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` — role syntax valid, no parse errors.
- `ansible-playbook --check --diff infrastructure/ansible/playbooks/deploy.yml --tags content-publisher` against a staging inventory — no unexpected `changed` items on a second run (idempotence).
- A targeted smoke-failure rehearsal: temporarily point `blogger_telegram_bot_token` at a token whose bot is NOT a channel admin → the `getChatMember` smoke task MUST fail with the named runbook message (status `left`/`member`/`kicked`/etc.), not a generic HTTP 400.
- `journalctl -u content-publisher --since "$(systemctl show -p ActiveEnterTimestamp content-publisher --value)"` after `state: started` matches all three log lines (`queue-poll started`, `deadline-checker started`, `cleanup-gc started`).

## Acceptance Criteria

- [ ] `roles/content-publisher/tasks/main.yml` is **appended to** (not rewritten) — Task 3's scaffold tasks remain byte-for-byte at the top of the file.
- [ ] Package install step puts `fabric/content-publisher/` into the role's venv (entry-point `content-publisher` resolvable).
- [ ] `content-publisher.service` is `enabled: yes` and `state: started` at the end of the play.
- [ ] `wait_for` task asserts all three startup lines appear in journal within 30 s of start, anchored to the unit's `ActiveEnterTimestamp` (not relative `--since "30 seconds ago"`); failure message names the missing line.
- [ ] Channel-admin smoke calls `getMe` then `getChatMember` on `@mnemonik` with `user_id=<publisher_bot_id>` using `{{ blogger_telegram_bot_token }}`; assertion checks `result.json.result.status in ['administrator', 'creator']`; failure path emits a clear "publisher bot is not channel admin" message naming the role README runbook.
- [ ] `blogger-prompts` is provisioned by `telegram-init` earlier in deploy and `content-publisher` receives `telegram_topics['blogger-prompts']` as `blogger_prompts_topic_id` (legacy sops fallback only).
- [ ] NO extra topic-existence smoke by `message_thread_id` is added (no real Bot API endpoint exists for that read path). Legacy fallback mismatch is self-detected at first use via journald.
- [ ] `handlers/main.yml` defines `reload systemd` + `restart content-publisher`; `restart` uses `failed_when: false` for the first-deploy case.
- [ ] Idempotent: a second `ansible-playbook` run on a healthy host reports `changed=0` for Task-7 tasks and fires neither handler.
- [ ] Post-deploy: `systemctl is-active content-publisher` → `active`; `journalctl -u content-publisher --since "$(systemctl show -p ActiveEnterTimestamp content-publisher --value)"` contains all three start lines; channel-admin smoke passes.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md) — see `#### Task 7`, Decision 13 (topic created by `telegram-init`; sops topic id is legacy fallback only), and the channel-admin row ("Operator forgets to add publisher bot as channel admin" — `getChatMember`-based smoke)
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
- Negative rehearsal: bad publisher token (or bot not admin) → `getChatMember` smoke task fails with named runbook message.

### Smoke
From tech-spec Verify-smoke (executed against the deployed VM after the role runs):
- `ssh root@vm 'systemctl is-active content-publisher'` → prints `active`.
- `ssh root@vm 'journalctl -u content-publisher --since "$(systemctl show -p ActiveEnterTimestamp content-publisher --value)" --no-pager'` → output contains all three of: `queue-poll started`, `deadline-checker started`, `cleanup-gc started`.
- During the play itself: `getMe` + `getChatMember` against `@mnemonik` with `{{ blogger_telegram_bot_token }}` returns `ok: true` AND `result.json.result.status in ['administrator', 'creator']` (gated inside the role and fail-fast the deploy on miss). No topic-existence smoke is run — tech-spec Risks row 11 documents the self-detected-at-first-use journald path instead.

## Details

**Files:**
- `infrastructure/ansible/roles/content-publisher/tasks/main.yml` — **APPEND** new tasks after Task 3's scaffold tasks. Order at the bottom of the file: (a) install package into venv, (b) `notify: reload systemd` triggers added to env/mcp.json/unit template tasks (Task 3's tasks), (c) enable + start `content-publisher.service`, (d) capture `ActiveEnterTimestamp` via `command: systemctl show -p ActiveEnterTimestamp content-publisher --value` register `cp_active_ts`, (e) `wait_for` / shell-grep against `journalctl --since "{{ cp_active_ts.stdout }}"` for the three log lines (30 s budget), (f) `getMe` + `getChatMember @mnemonik` admin-check smoke. Do NOT rewrite or reorder Task 3's tasks above. Do NOT add a topic-existence smoke by thread id — `telegram-init` owns topic creation, and there is no read endpoint for an arbitrary topic id.
- `infrastructure/ansible/roles/content-publisher/handlers/main.yml` — **CREATE**. Two handlers: `reload systemd` (daemon_reload) and `restart content-publisher` (with `failed_when: false`). Mirror `fabric-services/handlers/main.yml` exactly in shape and comment style.

**Dependencies:**
- Task 3 — role scaffold (env template, mcp.json template, unit template, venv, paths) must exist before this task adds the live-start steps.
- Task 6 — `fabric/content-publisher/` package with the three sub-loops and their `... started` log lines must exist; without it, the `wait_for` would never see the lines.

**Edge cases:**
- First-ever deploy: `content-publisher.service` does not exist when handlers are flushed at the end of Task 3's tasks. The `restart content-publisher` handler must tolerate this via `failed_when: false` (same trick as `restart workspace-manager` in `fabric-services`).
- Publisher bot token is rotated mid-deploy: smoke uses the freshly-templated `{{ blogger_telegram_bot_token }}` from `blogger.env` (Task 3 already drops it at mode 0600, sourced from sops `blogger_telegram_bot_token`). If rotation is stale in sops, smoke fails fast — that's the desired behavior.
- Topic id correctness is not separately re-validated by this task. New deploys get the id from `telegram-init`; if an operator intentionally uses the legacy sops fallback and it is wrong, the failure surfaces at Task 8's topic-handler when the operator first writes in the wrong-resolved topic — the handler logs the inbound `message_thread_id` and the operator runs `journalctl -u telegram-ai-agent | grep thread_id` to compare against the configured value.
- Re-running the role when the service is already `active`: `state: started` is idempotent in Ansible — no restart. The journal-grep step still passes because `ActiveEnterTimestamp` points to the original start (which already logged the three lines). No handler fires.
- Telegram API rate limiting on the smoke calls: `getMe` + `getChatMember` are two single calls per deploy — well under any rate ceiling. No retry/backoff needed; non-200 → fail-loud.
- Journal-window anchoring: using `--since "$(systemctl show -p ActiveEnterTimestamp content-publisher --value)"` makes the window exactly cover the lifetime of the current unit instance, regardless of how long Ansible spent between `state: started` and the grep step. This avoids the flake mode where slow CI execution pushes the grep window past a fixed relative `--since "30 seconds ago"`.

**Implementation hints:**
- Mirror `fabric-services/handlers/main.yml` cascade pattern: env-template change → `notify: reload systemd` (rewrites daemon state) → `notify: restart content-publisher` (picks up the new env). Apply this notify chain to the three Task-3 templating tasks (env, mcp.json, unit).
- Use `ansible.builtin.uri` module for the two Telegram smoke calls (`getMe`, `getChatMember`). Body format: JSON; method POST; `status_code: 200`; `body_format: json`; `return_content: yes`. Register the result and assert `result.json.ok == true` AND `result.json.result.status in ['administrator', 'creator']` with a custom `fail` task carrying the named runbook message — this keeps the failure surface readable in Ansible output.
- Chain `getMe` → `getChatMember`: register the `getMe` result as `tg_me`, then pass `user_id: "{{ tg_me.json.result.id }}"` into the `getChatMember` body. Keep both tasks tagged `content-publisher` so they run inside the same role pass.
- Do NOT call `getForumTopic` or `getForumTopicIconStickers` for topic validation. The former does not exist in the Bot API; the latter takes zero parameters and returns globally-available icon stickers (cannot confirm a specific topic exists in a specific chat). Topic creation belongs to `telegram-init`; legacy fallback mismatch is a Task 8 journald-debug path.
- For the log-line check: `community.general.journal_grep` is unavailable in stock Ansible; first capture the start timestamp via `command: systemctl show -p ActiveEnterTimestamp content-publisher --value` registered as `cp_active_ts`, then use `ansible.builtin.shell: journalctl -u content-publisher --since "{{ cp_active_ts.stdout }}" --no-pager | grep -q '...'` wrapped in an `until` loop with `retries: 6 delay: 5`. Three separate small tasks (one per line) yield clearer failure attribution than one composite regex.
- Avoid `community.docker` — this role has no docker. The "analogous" patterns in tech-spec refer to galaxy-collection module patterns (uri, systemd_service); no new collection is required if these are already vendored in the Ansible install.
- Token-in-URL hygiene: `{{ blogger_telegram_bot_token }}` appears in the `uri` URL for the Telegram API calls. Set `no_log: true` on those tasks so the token doesn't end up in Ansible's stdout/json output, then write a clear failure message via `fail:` so the operator still gets actionable feedback. This matches the secrets-hygiene pattern in `fabric-services` symphony.env handling.
- Variable name discipline: the sops/tech-spec canonical key is `blogger_telegram_bot_token` — do NOT introduce a `publisher_bot_token` alias. Reference the sops key directly in all `uri` URLs.

## Reviewers

- **code-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-7/code-reviewer-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону (Summary: 1-3 предложения, ревью со ссылками на JSON, без таблиц файндингов и дампов)
- [ ] Если отклонились от спека — описать отклонение и причину
- [ ] Обновить user-spec/tech-spec если что-то изменилось
