---
status: ready
depends_on: [4, 5]
wave: 2
skills: [code-writing]
verify: []
reviewers: [code-reviewer, test-reviewer, security-auditor]
teammate_name:
---

# Task 6: content-publisher — publish step + 3 sub-loops + MCP-stdio attestation + just-retry recovery

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Дописываем рабочую часть пакета `fabric/content-publisher/`: финальная стадия пайплайна (publish → published → attest-pending → done), три фоновых под-цикла, attestation через MCP JSON-RPC по stdio к бинарю `mnemonik-mcp`, и crash-recovery на старте процесса. Опирается на queue/state/CAS из Task 4 и worktree+preview из Task 5.

Ключевые архитектурные ограничения, заданные tech-spec:

- Publish — это **in-process** вызов `mnemonik_blogger.agent.run_campaign_from_article` (Decision 4). Сигнатура строго **keyword-only**, БЕЗ позиционных аргументов, БЕЗ `dry_run=` на функции (он только в `Settings`).
- Перед самим вызовом в queue.jsonl записывается `tentative_publish_started_at` — это форензическая метка для редкого случая double-post при crash mid-publish (Decision 8 + AC-T7).
- Attestation идёт **только** через `<mnemonic_mcp_binary> mcp-stdio` (имя бинаря — Ansible fact из Task 2). Никакого `sign-memory` subcommand, никакого payload в argv. Полный envelope передаётся как MCP JSON-RPC по stdin (Architecture step 4, AC-T8).
- Crash recovery для статуса `publishing` — **просто ретрай** через нормальный publish path (Decision 8). Никакого state `recovery-needed`, никаких slash commands. В редком случае двойного поста оператор удалит дубликат вручную, пользуясь `tentative_publish_started_at` как таймстампом.
- `PUBLISH_MODE=auto` требует совпадения токена в двух местах (`/etc/blogger.env` `PUBLISH_AUTO_MODE_TOKEN` == sops `publish_auto_mode_token`) при старте; иначе процесс падает громко (Decision 7, AC-T10).

Терминальные статусы: `done`, `rejected`, `regenerated`, `publish-failed`, `attest-failed`, `failed`. `cleanup_at` устанавливается в момент `preview-sent`; cleanup-gc сносит worktree только у терминальных джоб.

## What to do

1. **`publish.py`** — функция шага publish: получает джобу в статусе `publishing`, пишет `tentative_publish_started_at = now` в queue.jsonl ДО вызова, собирает `Settings` (см. Implementation hints), делает строго keyword-only вызов `run_campaign_from_article(...)` с `attest=False`, читает `pr = result.results[0]` (не `result.posts[0]`). При `pr.ok==True`: CAS `publishing → published`, сохраняет `post_url = pr.primary_url`, `post_message_ids = pr.ids`, считает `content_sha256` от байт `article.md`. При `pr.ok==False`: CAS `publishing → publish-failed`, пишет `publish_error = pr.error` и `notify_pending = true`.

2. **`deadline.py`** — фоновый под-цикл с каденсом 30s. Два независимых прохода по queue.jsonl:
   - джобы `status == preview-sent AND now >= approval_deadline` → CAS `preview-sent → publishing` (далее их подхватит обычный publish-step);
   - джобы `status == attest-pending` → попытка повторной аттестации через `attest.py`. Каденс ретраев — 5 минут (см. attestation flow); 24h escalation идёт в `attest.py`.

3. **`cleanup.py`** — фоновый под-цикл с каденсом 5 минут. Проходит queue.jsonl, для каждой джобы в **терминальном** статусе с `cleanup_at != null AND cleanup_at <= now`: `shutil.rmtree(worktree_path, ignore_errors=False)`, аппендит сериализованную запись в `queue.archive.jsonl` рядом с queue.jsonl (тем же atomic-append паттерном), затем убирает её из queue.jsonl. Не-терминальные джобы НЕ собираются (даже если у них почему-то заполнен `cleanup_at`).

4. **`attest.py`** — оркестрация attestation: считает sha256 от содержимого `article.md`, вызывает `mcp_client.sign_memory(...)`, на успехе CAS `published → done` с `attestation_hash`, на неуспехе CAS `published → attest-pending` и инкремент `attest_attempts`. На 24h без успеха (вычисляется от `created_at` либо от первого попадания в `attest-pending`): CAS `attest-pending → attest-failed`, поднимает флаг `notify_pending=true` с сообщением для оператора (DM поток обрабатывается ботом через Task 8).

5. **`mcp_client.py`** — тонкий клиент для MCP-stdio:
   - `asyncio.create_subprocess_exec(<mnemonic_mcp_binary>, "mcp-stdio", stdin=PIPE, stdout=PIPE, stderr=PIPE, env=restricted_env("mnemonik-mcp"))`. Argv **строго** двухэлементный (`[<binary>, "mcp-stdio"]`) — никаких payload-аргументов.
   - Кадрирование MCP JSON-RPC: попробовать сначала формат, который Task 2 зафиксировала во время handshake-фикстуры (см. Implementation hints — newline-delimited JSON vs `Content-Length:` headers). Должна быть одна функция-«speaker» по этому кадрированию.
   - Последовательность: `initialize` (clientInfo `{"name":"content-publisher","version":"0.1"}`, `protocolVersion` тот же, что в spec), читаем ответ, далее `tools/call` для `mnemonic_sign_memory` с `{content, content_sha256, post_url, score, prompt}`, читаем response. Закрываем stdin → дожидаемся завершения процесса.
   - Парсим receipt из tool response, возвращаем как `attestation_hash` (точное поле сверить в Task 2 handshake fixture).

6. **`recovery.py`** — функция `recover_in_flight(queue_path)`, вызывается из `main.py` lifespan startup. Сканирует queue.jsonl, по каждому in-flight статусу:
   - `writing`, `scoring` → CAS на `queued` (рестарт от начала; ingest идемпотентен по filesystem path, worktree уже на месте);
   - `preview-sent` → не трогать, следующий тик deadline-checker сам решит по `approval_deadline`;
   - `publishing` → **просто ретрай** через нормальный publish path. НЕ создавать никакого статуса `recovery-needed`, не открывать slash commands. `tentative_publish_started_at` уже записан до краша и сохраняется как форензическая метка для оператора (Decision 8 + AC-T13);
   - `attest-pending` → не трогать, под-цикл deadline-checker подхватит по расписанию retry.
   - Терминальные статусы — пропускаем (cleanup-gc сделает своё дело).

7. **`main.py`** — FastAPI/asyncio lifespan startup (по образцу `fabric/workspace-manager/main.py`):
   - проверка `PUBLISH_MODE=auto` ⇒ требуется совпадение `PUBLISH_AUTO_MODE_TOKEN` из env и `publish_auto_mode_token` из sops/`/etc/blogger.env`; mismatch → `raise SystemExit(...)` с громким логом (AC-T10);
   - `recover_in_flight(queue_path)`;
   - запускаем три asyncio task: queue-poll loop (из Task 5 — `worker.py`), `deadline_checker_loop()`, `cleanup_gc_loop()`. Каждый из трёх логирует одну стартовую строку (`queue-poll started`, `deadline-checker started`, `cleanup-gc started`) — это завязано на smoke verify в Task 7.

8. **Posts-per-hour rate ceiling (security R2-9)** — внутри `publish.py` перед в-процесс вызовом: считаем сколько джоб со `status == published` (либо строго более продвинутыми) перешло в этот статус за последние 60 минут (по полю, в котором мы фиксируем published_at — добавить как часть CAS `publishing → published`). Если `> N` (env-конфигурируемо, default — взять разумный из sops, e.g. `MNEMONIK_POSTS_PER_HOUR_CEILING`): CAS `publishing → publish-failed` с `publish_error = "rate ceiling exceeded"` и `notify_pending=true`. Тест-кейс `rate_ceiling` (в составе `test_publish_handlers.py` или отдельным файлом) убедится, что ceiling-путь срабатывает строго до in-process call.

9. **Тесты — пишем ДО кода (TDD).** Полный список в TDD Anchor; ключевые: argv-hygiene attestation (AC-T8), tentative ordering (AC-T7), publishing crash retry NO recovery-needed state (AC-T13), auto-mode two-location binding (AC-T10).

## TDD Anchor

Тесты, которые нужно написать ДО реализации. Все живут в `fabric/content-publisher/tests/` и `fabric/content-publisher/tests/integration/`.

- `tests/test_deadline_checker.py::test_preview_sent_past_deadline_transitions_to_publishing` — preview-sent с `approval_deadline <= now` → CAS на `publishing`.
- `tests/test_deadline_checker.py::test_attest_pending_retried_via_deadline_loop` — attest-pending джоба подхвачена этим же под-циклом и переотправлена в attest.
- `tests/test_cleanup_gc.py::test_terminal_jobs_with_cleanup_at_past_are_gced` — все терминальные статусы, у которых `cleanup_at <= now`, теряют worktree (`rmtree`) и попадают в `queue.archive.jsonl`.
- `tests/test_cleanup_gc.py::test_non_terminal_jobs_are_not_gced_even_if_cleanup_at_set` — не-терминальные джобы НЕ собираются, даже если `cleanup_at` уже прошёл.
- `tests/test_attest_retry.py::test_retry_cadence_5min_then_24h_escalation` — расписание попыток (по моку «время») заканчивается переходом `attest-pending → attest-failed` на 24h.
- `tests/test_attest_envelope.py::test_subprocess_argv_is_exactly_binary_and_mcp_stdio` — proves `argv == [<binary>, "mcp-stdio"]` строго, без payload (AC-T8).
- `tests/test_attest_envelope.py::test_mcp_initialize_then_tools_call_over_stdin` — на mocked subprocess в stdin поступают сначала `initialize`, потом `tools/call mnemonic_sign_memory` с обязательными ключами `{content, content_sha256, post_url, score, prompt}`; receipt парсится из mocked stdout.
- `tests/test_auto_mode.py::test_auto_mode_skips_preview_and_posts_directly` — `PUBLISH_MODE=auto` + совпадающий токен: queued → writing → scoring → publishing → published (preview-sent НЕ возникает); пост-фактум notify в формате `Auto-published: <link>. Receipt: <hash>. Score: <N>.` (AC11).
- `tests/test_auto_mode_token_binding.py::test_mismatched_auto_mode_token_aborts_startup` — `PUBLISH_MODE=auto` без совпадения `PUBLISH_AUTO_MODE_TOKEN` ⇔ `publish_auto_mode_token` → `main.py` lifespan startup поднимает `SystemExit` с громким сообщением; ничто из под-циклов не стартует (AC-T10).
- `tests/test_tentative_write_ordering.py::test_tentative_publish_started_at_written_before_call` — мокаем `run_campaign_from_article` на выброс исключения; убеждаемся, что `tentative_publish_started_at` уже записан в queue.jsonl к моменту падения (AC-T7).
- `tests/test_publishing_retry_on_crash.py::test_publishing_status_just_retries_normal_path` — джоба зафиксирована в `publishing` (симуляция предыдущего краша); `recover_in_flight` ставит её на нормальный publish path; статус `recovery-needed` НЕ существует (отсутствие импорта/enum-значения) (AC-T13).
- `tests/integration/test_crash_recovery.py::test_kill_at_each_in_flight_status` — kill при `writing`, `scoring`, `preview-sent`, `publishing`, `attest-pending`; рестарт; в нормальном (без двойной публикации) сценарии `run_campaign_from_article` вызван ровно один раз; в принудительно-двойном — оператор получает `tentative_publish_started_at` как таймстамп.
- `tests/integration/test_concurrent_publish.py::test_concurrent_callback_and_deadline_fire_publishes_once` — threading: одновременный callback-клик и deadline-fire → ровно один in-process publish (CAS гарантирует winner).
- `tests/integration/test_attestation_pipeline.py::test_happy_then_mcp_failure_then_retry_then_escalation` — happy path (published → done); сценарий с MCP failure: переходит в attest-pending, ретрай через 5 минут, на 24h → attest-failed.

## Acceptance Criteria

- [ ] **(a)** Publish step пишет `tentative_publish_started_at` в queue.jsonl ДО вызова `run_campaign_from_article` (AC-T7).
- [ ] **(b)** In-process call строго keyword-only по сигнатуре `run_campaign_from_article(article=Path(...), platforms=[Platform.TELEGRAM], settings=Settings(dry_run=False, min_score=N, claude_blog_path=..., telegram=TelegramConfig(...)), attest=False, min_score=...)` — никаких позиционных аргументов, никакого `dry_run=` как аргумента функции.
- [ ] **(c)** Чтение результата — `pr = result.results[0]` (НЕ `result.posts[0]`); сохраняются `pr.primary_url` (→ `post_url`) и `pr.ids` (→ `post_message_ids`).
- [ ] **(d)** При `pr.ok == True`: CAS `publishing → published`; считается и сохраняется `content_sha256`.
- [ ] **(e)** При `pr.ok == False`: CAS `publishing → publish-failed` с сохранением `publish_error = pr.error` И установкой `notify_pending = true`.
- [ ] **(f)** Deadline-checker под-цикл (каденс 30s): обрабатывает джобы `status == preview-sent AND now >= approval_deadline` через CAS на `publishing`, И ретраит `attest-pending` через `attest.py`.
- [ ] **(g)** Cleanup-gc под-цикл (каденс 5min): для терминальных джоб с `cleanup_at <= now` выполняет `shutil.rmtree(worktree_path)` и аппендит запись в `queue.archive.jsonl`; не-терминальные джобы НЕ удаляются.
- [ ] **(h)** Attestation: `asyncio.create_subprocess_exec` с argv строго `[<mnemonic_mcp_binary>, "mcp-stdio"]` БЕЗ payload, `env=restricted_env("mnemonik-mcp")`; MCP JSON-RPC `initialize` затем `tools/call mnemonic_sign_memory` с ключами `{content, content_sha256, post_url, score, prompt}` пишутся в stdin построчно (newline-delimited); receipt парсится из stdout; stdin закрывается на выход (AC-T8).
- [ ] **(i)** Attestation failure → CAS `published → attest-pending` + ретрай через под-цикл (deadline-checker) каждые 5min; 24h escalation → `attest-failed` + поднятие `notify_pending=true` для DM оператору (поток DM выполняет бот в Task 8).
- [ ] **(j)** `main.py` lifespan startup сканирует queue.jsonl: `writing` / `scoring` → рестарт от начала (CAS на `queued`); `preview-sent` → не трогать, deadline-checker сам разрулит по `approval_deadline`; `publishing` → **просто ретрай** через нормальный publish path (Decision 8, AC-T13); `attest-pending` → возобновляется через расписание deadline-checker.
- [ ] **(k)** Two-location auto-mode token check на startup: `PUBLISH_MODE=auto` AND `/etc/blogger.env` `PUBLISH_AUTO_MODE_TOKEN` == sops `publish_auto_mode_token` → auto-режим включён; mismatch → процесс падает с громким сообщением (AC-T10).
- [ ] **(l)** Posts-per-hour rate ceiling (R2-9): если за последний час уже опубликовано `> N` джоб → CAS `publishing → publish-failed` с `publish_error = "rate ceiling exceeded"` и `notify_pending = true` (без in-process call).
- [ ] Статус `recovery-needed` в коде/enum ОТСУТСТВУЕТ (грепом по репо подтверждается отсутствие имени).
- [ ] Тесты из TDD Anchor зелёные.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md)
- `fabric/workspace-manager/main.py` — FastAPI lifespan + asyncio task pattern, REFERENCE only.
- `fabric/workspace-manager/state.py` — atomic-write + flock helpers, паттерн для аппенда в `queue.archive.jsonl`.
- `fabric/content-publisher/src/content_publisher/queue.py` — shared `cas_status`, `append_job`, `load` (созданы в Task 4).
- `fabric/content-publisher/src/content_publisher/models.py` — Job + status enum (Task 4).
- `fabric/content-publisher/src/content_publisher/env.py` — `restricted_env(kind)` (Task 4).
- `fabric/content-publisher/src/content_publisher/worker.py` — queue-poll loop из Task 5.

## Verification Steps

### Automated
- `pytest fabric/content-publisher/tests/test_deadline_checker.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_cleanup_gc.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_attest_retry.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_attest_envelope.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_auto_mode.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_auto_mode_token_binding.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_tentative_write_ordering.py -v` → all pass
- `pytest fabric/content-publisher/tests/test_publishing_retry_on_crash.py -v` → all pass
- `pytest fabric/content-publisher/tests/integration/ -v` → all pass
- `grep -RIn "recovery-needed\|recovery_needed\|RECOVERY_NEEDED" fabric/content-publisher/` → empty (Decision 8 gate)

## Details

**Files to modify:**
- `fabric/content-publisher/src/content_publisher/publish.py` *(NEW)* — publish-step функция; ordering `tentative_publish_started_at` → in-process call → CAS; rate-ceiling pre-check.
- `fabric/content-publisher/src/content_publisher/deadline.py` *(NEW)* — 30s sub-loop: preview-sent → publishing, и retry attest-pending.
- `fabric/content-publisher/src/content_publisher/cleanup.py` *(NEW)* — 5min sub-loop: terminal jobs → rmtree + archive.
- `fabric/content-publisher/src/content_publisher/attest.py` *(NEW)* — обёртка: считает sha256, дергает `mcp_client.sign_memory`, CAS published → done | published → attest-pending; 24h escalation → attest-failed.
- `fabric/content-publisher/src/content_publisher/mcp_client.py` *(NEW)* — `sign_memory(article, content_sha256, post_url, score, prompt)`; spawn `<binary> mcp-stdio` строго двухэлементным argv; MCP JSON-RPC speaker (initialize → tools/call); парсинг receipt; закрытие stdin для нормального завершения.
- `fabric/content-publisher/src/content_publisher/recovery.py` *(NEW)* — `recover_in_flight(queue_path)`; per-status решения по списку из Decision 8.
- `fabric/content-publisher/src/content_publisher/main.py` *(NEW)* — FastAPI app + lifespan: two-location auto-mode token gate → `recover_in_flight` → start 3 sub-loops (queue-poll, deadline-checker, cleanup-gc), каждый логирует свою стартовую строку.
- `fabric/content-publisher/tests/test_deadline_checker.py` *(NEW)*.
- `fabric/content-publisher/tests/test_cleanup_gc.py` *(NEW)*.
- `fabric/content-publisher/tests/test_attest_retry.py` *(NEW)*.
- `fabric/content-publisher/tests/test_attest_envelope.py` *(NEW)*.
- `fabric/content-publisher/tests/test_auto_mode.py` *(NEW)*.
- `fabric/content-publisher/tests/test_auto_mode_token_binding.py` *(NEW)*.
- `fabric/content-publisher/tests/test_tentative_write_ordering.py` *(NEW)*.
- `fabric/content-publisher/tests/test_publishing_retry_on_crash.py` *(NEW)*.
- `fabric/content-publisher/tests/integration/test_crash_recovery.py` *(NEW)*.
- `fabric/content-publisher/tests/integration/test_concurrent_publish.py` *(NEW)*.
- `fabric/content-publisher/tests/integration/test_attestation_pipeline.py` *(NEW)*.

**Files to read (REFERENCE only):**
- `fabric/workspace-manager/main.py` — FastAPI lifespan + `asyncio.create_task` pattern для трёх параллельных sub-loops; стартовый порядок (recovery до запуска под-циклов).

**Dependencies:**
- Task 4 (`queue.py`, `state.py`, `models.py`, `env.py`) — обязательный фундамент для CAS и атомарных записей.
- Task 5 (`worker.py`, `spawn.py`, `score.py`, `render.py`) — queue-poll loop, который запускается рядом с deadline-checker и cleanup-gc.
- `mnemonik_blogger.agent.run_campaign_from_article` — keyword-only, в venv `/opt/blogger/venv/` (создаёт Task 3).
- `<mnemonic_mcp_binary>` — Ansible fact из Task 2.

**Edge cases:**
- Двойной in-process publish при гонке callback + deadline: CAS гарантирует, что только один поток успешно сделает `preview-sent → publishing`; loser получит «too late».
- Worker крашится между записью `tentative_publish_started_at` и фактическим запросом к Telegram API — крайне редкий случай; на рестарте recovery просто ретраит publish; дубликат удаляется оператором вручную (Decision 8).
- MCP-stdio subprocess не отвечает на `initialize`: timeout читалки → закрываем stdin, kill subprocess, считаем это failure → CAS published → attest-pending.
- 24h-эскалация: точка отсчёта — `created_at` джобы (либо первый ввод в `attest-pending`; зафиксировать в attest.py и одинаково в тесте).
- Cleanup-gc натыкается на отсутствующий worktree (уже удалён вручную): `shutil.rmtree(..., ignore_errors=False)` — обрабатывать `FileNotFoundError` как успех и всё равно архивировать.
- Rate ceiling — считаем только успешно опубликованные за окно; CAS на `publish-failed` происходит ДО in-process вызова, никаких частичных пост-эффектов.
- `PUBLISH_MODE=approval` + наличие `PUBLISH_AUTO_MODE_TOKEN` — нормально (просто игнорируется); проверка integrity только когда `PUBLISH_MODE=auto`.

**Implementation hints:**
- **MCP framing.** Official MCP spec (stdio transport) допускает два варианта: (1) `Content-Length:` headers по образцу LSP, (2) newline-delimited JSON. Какой именно говорит `@mnemonik-xyz/mcp@<pinned>` — фиксируется в Task 2 handshake-фикстуре. Не угадывай: либо подтяни фикстуру из Task 2, либо в `mcp_client.py` сделай одну точку — функцию-writer/reader — и реализуй ту, которая согласуется с фикстурой; добавь TODO-комментарий с указанием на фикстуру.
- Используй `asyncio.create_subprocess_exec(<binary>, "mcp-stdio", stdin=PIPE, stdout=PIPE, stderr=PIPE, env=restricted_env("mnemonik-mcp"))`. `stderr=PIPE` — собирать для диагностики, в `attest.py` логировать tail на failure.
- `Settings(...)` для publish-step: `dry_run=False`, `min_score=<int from env>`, `claude_blog_path=<from env>`, `telegram=TelegramConfig(bot_token=SecretStr(<token from env>), channel=<from env>)`. `SecretStr` — обязательная обёртка для токена.
- `recover_in_flight` — синхронный helper (одного прохода по queue.jsonl достаточно). Запускать ДО трёх asyncio task; иначе deadline-checker/cleanup-gc могут наскочить на промежуточные состояния.
- Для атомарного аппенда в `queue.archive.jsonl` — переиспользовать паттерн `_write_raw` / flock из `fabric/workspace-manager/state.py` (тот же, что в Task 4).
- Три startup log lines (`queue-poll started`, `deadline-checker started`, `cleanup-gc started`) — буквально так, без вариаций; Ansible smoke в Task 7 проверяет их регулярками.
- Тест `test_publishing_retry_on_crash.py` должен заодно подтвердить отсутствие имени `recovery-needed` в кодовой базе (например, `assert 'recovery-needed' not in set(s.value for s in JobStatus)`) — это hard-gate Decision 8.

## Reviewers

- **code-reviewer** → `work/content-publish-pipeline/logs/working/task-6/code-reviewer-{round}.json`
- **test-reviewer** → `work/content-publish-pipeline/logs/working/task-6/test-reviewer-{round}.json`
- **security-auditor** → `work/content-publish-pipeline/logs/working/task-6/security-auditor-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону (Summary: 1-3 предложения, ревью со ссылками на JSON, без таблиц файндингов и дампов).
- [ ] Если отклонились от спека — описать отклонение и причину (особенно если MCP framing оказался иным, чем зафиксировал Task 2).
- [ ] Обновить user-spec/tech-spec если что-то изменилось (например, поправка имени поля в MCP receipt, точная точка отсчёта 24h-эскалации).
