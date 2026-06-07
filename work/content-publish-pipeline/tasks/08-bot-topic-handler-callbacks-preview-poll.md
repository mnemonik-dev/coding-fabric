---
status: planned
depends_on: [4, 6]
wave: 3
skills: [code-writing]
verify: [smoke]
reviewers: [code-reviewer, test-reviewer, security-auditor]
teammate_name:
---

# Task 8: Bot — topic-handler + callback handlers + preview-dispatch poll + retry linkage + rejection-rate metric

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Этот таск интегрирует уже работающий content-publisher worker (Task 6) c
telegram-ai-agent: операторский ввод и весь approval-flow живут в выделенном
Telegram-форум-топике `📝 blogger-prompts` — без slash-команд, без LLM в
dispatch-пути и без MCP-инструмента (Decision 13). Topic `blogger-prompts`
создаётся deploy pipeline через роль `telegram-init` (НЕ вручную оператором);
его `message_thread_id` берётся из generated mapping
`telegram_topics['blogger-prompts']` и рендерится в `BLOGGER_PROMPTS_TOPIC_ID`.
Бот ловит сообщение оператора в нужном (chat, thread) и кладёт job в общую
`queue.jsonl` через `content_publisher.queue.append_job(...)`. Worker отдельно
дописывает `preview_pending=true`, а фоновая asyncio-задача бота раз в 5с тянет
очередь и шлёт preview как REPLY на исходный brief в том же топике, с
HMAC-подписанной inline-клавиатурой (Decision 9).

3 callback-хендлера (`publish:approve|reject|retry:<id[:12]>:<hmac8>`)
выполняют операторскую авторизацию (от_user.id == OPERATOR_USER_ID **до**
любых криптопроверок), валидируют HMAC через current ИЛИ previous-секрет (для
zero-downtime ротации), CAS-переводят job в нужный статус и в случае retry
атомарно проставляют `regenerated_to=<new_id>` на старом + создают новый job в
одной CAS-операции. Та же фоновая poll-задача обрабатывает `notify_pending=true`
(spawn-failures, publish-failed, attest-результаты) — редактирует существующее
preview-сообщение in-place, либо публикует новое в топик, если preview ещё нет.

Дополнительно: sliding-window счётчик — если в 10-минутное окно набирается >5
HMAC-ошибок или unauthorized-кликов, бот публикует security-note в топик
(это не блокировка, а сигнал оператору). Никаких новых slash-команд в
`MOLYANOV_BOT_COMMANDS`, никаких изменений в bot's MCP-сервере.

## What to do

1. Создать новый файл `core/handlers/publish.py`:
   - Объявить `publish_router = Router(name="publish")`.
   - Прочитать `TELEGRAM_FORUM_CHAT_ID`, `BLOGGER_PROMPTS_TOPIC_ID`,
     `OPERATOR_USER_ID`, `CALLBACK_HMAC_SECRETS` из окружения (источник —
     `/etc/blogger.env`; `BLOGGER_PROMPTS_TOPIC_ID` в новых deploy берётся из
     `telegram-init` generated topic mapping, sops key — fallback only).
     Fail-loud при отсутствии.
   - В startup hook добавить assertion:
     `assert OPERATOR_USER_ID in get_settings().allowed_user_ids` (импорт
     `from telegram_bot.core.config import get_settings` — тот же,
     которым уже пользуется `__main__.py`). Бот уже имеет глобальный
     `AuthMiddleware`, который пропускает только сообщения от
     `settings.allowed_user_ids` (загружается из operator-bot sops). Этот
     middleware срабатывает **до** нашего topic-handler-а, поэтому
     `OPERATOR_USER_ID` обязан быть в allowlist — иначе сообщения оператора в
     `📝 blogger-prompts` будут отброшены ДО того, как наш фильтр их увидит.
   - Topic-message handler с **точным** фильтром:
     `@publish_router.message(F.chat.id == TELEGRAM_FORUM_CHAT_ID, F.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID, F.from_user.id == OPERATOR_USER_ID)`.
     Тело: извлекает `message.text` как brief и вызывает
     `content_publisher.queue.append_job(prompt=message.text, fire_at=None, mode=None, chat_id=message.chat.id, thread_id=message.message_thread_id, reply_to_message_id=message.message_id)`
     напрямую. БЕЗ MCP-инструмента, БЕЗ LLM, БЕЗ slash-команд (Decision 13).
   - 3 callback-хендлера на `F.data.startswith("publish:")`:
     парсит `publish:<action>:<job_id[:12]>:<hmac8>` (action ∈
     {approve, reject, retry}); validate в порядке
     **(1) `from_user.id == OPERATOR_USER_ID` → иначе ответить "Not authorized"; (2)** HMAC через
     `hmac.compare_digest` с первым секретом, при failure — со вторым; иначе
     "Invalid signature"; **(3)** загрузить job по полному UUID — lookup по
     12-hex-prefix через **`content_publisher.queue.find_by_prefix(job_id_prefix12)`**
     (публичный API, добавлен в Task 4 iteration 2); НЕ реимплементировать
     CAS/lookup-логику в handlers → CAS:
       - approve: `preview-sent → publishing`,
       - reject: `preview-sent → rejected`,
       - retry: в одной CAS-операции на старом job выставить
         `regenerated_to=<new_uuid>` + одновременно создать новый job
         (`append_job(... retries_of=<old_id>, feedback_for_retry=<issues>)`).
         CAS должен быть единой защищённой записью (общий `flock` + read-modify-
         write + `os.replace()` — см. Task 4).
     Любой "проигрыш гонки" (CAS expected mismatch — например, deadline-checker
     уже перевёл в publishing) → `callback.answer("too late, status: <X>")`.
   - Фоновая asyncio-задача `preview_dispatch_loop`:
     раз в 5с читает `queue.jsonl`:
       - для всех `status == "preview-sent" AND preview_pending == true`:
         шлёт **новое** сообщение в топике как REPLY на brief
         (`message_thread_id=<topic_id>`, `reply_to_message_id=<brief_msg_id>`)
         c текстом `"\n— — —\n".join(preview_segments)` и inline-клавиатурой
         `[✓ Опубликовать] [✗ Отклонить]` (+ `[🔄 Перегенерировать]` если
         `score < PUBLISH_MIN_SCORE`); `callback_data` — HMAC-tagged по
         Decision 9. Записывает `preview_message_id`, через CAS
         выставляет `preview_pending=false`;
       - для всех `notify_pending == true`: если есть `preview_message_id` —
         редактирует preview in-place (`reply_markup=None`,
         текст из `publish_error` / `post_url` / `attestation_hash`); если
         preview ещё нет — публикует новое сообщение в топик; CAS
         `notify_pending=false`.
   - Sliding-window rejection-rate metric: in-memory deque с timestamps;
     при >5 HMAC-failures ИЛИ unauthorized-кликов в окне 10мин — бот шлёт
     одно security-note сообщение в топик (`message_thread_id=BLOGGER_PROMPTS_TOPIC_ID`).
     После security-note — окно сбрасывается.
2. В `__main__.py`:
   - Импорт: `from telegram_bot.core.handlers.publish import publish_router, start_preview_dispatch_loop`.
   - Зарегистрировать роутер: `dp.include_router(publish_router)` — расположить
     **перед** `text_router`, чтобы topic-фильтр сработал раньше general-handler-а.
   - В startup hook (рядом с `_periodic_tmp_cleanup`) поднять
     `asyncio.create_task(start_preview_dispatch_loop(bot))`.
   - НЕ трогать `MOLYANOV_BOT_COMMANDS` — никакой новой slash-команды.
3. В deploy wiring:
   - `telegram-init` topic list содержит `blogger-prompts`; роль создаёт его
     через `createForumTopic` и сохраняет thread_id в `/etc/fabric/telegram-topics.yml`
     + `infrastructure/inventory/telegram-topics.yml`.
   - `content-publisher` role var `blogger_prompts_topic_id` получает
     `{{ telegram_topics['blogger-prompts'] }}` (с fallback на старый
     `vault_blogger_prompts_topic_id` только для legacy/manual окружений).
4. Тесты:
   - `tests/test_publish_handlers.py`:
     - approve: позитив (CAS preview-sent→publishing, mocked publish-флоу
       вызывается ровно один раз);
     - reject: CAS preview-sent→rejected;
     - replay protection: тот же callback дважды → второй ответ
       `"too late, status: publishing"`;
     - HMAC: current OK, previous OK, foreign FAIL → "Invalid signature";
     - operator allowlist: `from_user.id != OPERATOR_USER_ID` → "Not authorized"
       **И HMAC compare НЕ вызывается** (порядок check'ов — load-bearing).
   - `tests/test_topic_filter.py` (AC-T12):
     - positive: правильный chat+thread+operator → `append_job` вызван 1 раз;
     - negative 1: чужой chat_id → НЕ enqueue;
     - negative 2: правильный chat_id, чужой thread_id → НЕ enqueue;
     - negative 3: правильный chat+thread, чужой from_user.id → НЕ enqueue.
   - `tests/test_retry_linkage.py`:
     - retry callback: на старом job записан `regenerated_to=<new_id>`,
       новый job создан с `retries_of=<old_id>` и `feedback_for_retry=[…issues]`,
       обе записи попали в одну CAS-транзакцию (verified через mock или
       проверку, что между записями нет промежуточного состояния).

## TDD Anchor

Тесты пишем ДО реализации:
- `tests/test_publish_handlers.py::test_approve_happy_path` — корректный
  HMAC-callback approve → CAS `preview-sent → publishing` + worker spawn-mock
  вызван один раз.
- `tests/test_publish_handlers.py::test_reject_happy_path` — CAS
  `preview-sent → rejected`.
- `tests/test_publish_handlers.py::test_replay_protection` — повторный
  identical callback → `callback.answer("too late, status: publishing")`,
  spawn-mock больше не вызывается.
- `tests/test_publish_handlers.py::test_hmac_current_and_previous_accepted` —
  оба секрета валидны.
- `tests/test_publish_handlers.py::test_hmac_foreign_rejected` — поломанный
  HMAC → `"Invalid signature"`.
- `tests/test_publish_handlers.py::test_operator_allowlist_before_hmac` —
  чужой `from_user.id` → `"Not authorized"`, HMAC compare НЕ вызван (мок).
- `tests/test_topic_filter.py::test_positive_match_enqueues` — все 3
  поля совпали → `append_job` вызван 1 раз.
- `tests/test_topic_filter.py::test_negative_wrong_chat` — НЕ enqueue.
- `tests/test_topic_filter.py::test_negative_wrong_thread` — НЕ enqueue.
- `tests/test_topic_filter.py::test_negative_wrong_user` — НЕ enqueue.
- `tests/test_retry_linkage.py::test_retry_writes_regenerated_to_atomically` —
  на старом job `regenerated_to=<new_id>`, на новом `retries_of=<old_id>` +
  `feedback_for_retry=<issues>`; обе записи внутри одного CAS-окна.
- `tests/test_publish_handlers.py::test_preview_poll_sends_reply` — job c
  `status=preview-sent, preview_pending=true` → бот отправил message c
  `reply_to_message_id=<brief_id>` + `message_thread_id=<topic_id>` +
  joined `preview_segments`; после отправки `preview_pending=false`.
- `tests/test_publish_handlers.py::test_notify_pending_edits_in_place` —
  job с `notify_pending=true` и существующим `preview_message_id` →
  `bot.edit_message_text` вызван с тем же message_id, `reply_markup=None`.
- `tests/test_publish_handlers.py::test_rejection_rate_security_note` —
  6 unauthorized-кликов подряд → `bot.send_message` с security-note вызван
  один раз в топике.
- `tests/test_publish_handlers.py::test_auto_mode_post_fact_reply_format` —
  job c `mode=auto`, `notify_pending=true`, заполненными `post_url`,
  `attestation_hash`, итоговым `score` → `bot.send_message` вызван с текстом
  ровно `f"Auto-published: {post_url}. Receipt: {attestation_hash}. Score: {score}."`
  (формат из tech-spec Decision 7).
- `tests/test_publish_handlers.py::test_startup_asserts_operator_in_allowlist` —
  патчим `telegram_bot.core.config.get_settings` так, чтобы
  `get_settings().allowed_user_ids` не содержал `OPERATOR_USER_ID` → startup
  падает с AssertionError; positive-кейс: при наличии в allowlist startup OK.

## Acceptance Criteria

- [ ] Новый файл `core/handlers/publish.py` создан, объявлен
  `publish_router = Router(name="publish")`.
- [ ] Topic-handler с **точным** фильтром
  `F.chat.id == TELEGRAM_FORUM_CHAT_ID, F.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID, F.from_user.id == OPERATOR_USER_ID`;
  env-переменные читаются из окружения процесса (источник `/etc/blogger.env`).
- [ ] Topic-handler вызывает `content_publisher.queue.append_job(prompt=message.text, fire_at=None, mode=None, chat_id=message.chat.id, thread_id=message.message_thread_id, reply_to_message_id=message.message_id)`
  напрямую. БЕЗ MCP, БЕЗ LLM.
- [ ] 3 callback-хендлера для
  `publish:<approve|reject|retry>:<id[:12]>:<hmac8>` зарегистрированы через
  `F.data.startswith("publish:")`.
- [ ] HMAC валидируется через `hmac.compare_digest` с CURRENT секретом и
  fallback на PREVIOUS (`CALLBACK_HMAC_SECRETS=<current>,<previous>`); валиден
  любой из двух. Tampered HMAC → `"Invalid signature"`.
- [ ] `from_user.id != OPERATOR_USER_ID` → `"Not authorized"` **до** HMAC-
  compare (порядок check'ов load-bearing).
- [ ] Retry-callback атомарно (в одной CAS-операции) пишет
  `regenerated_to=<new_id>` на старом job + создаёт новый job c
  `retries_of=<old_id>` и `feedback_for_retry=<issues>`.
- [ ] Фоновая asyncio-задача polls `queue.jsonl` каждые 5с; для
  `status=preview-sent AND preview_pending=true` шлёт preview как REPLY в
  топик: `reply_to_message_id=<brief_msg_id>`, `message_thread_id=<topic_id>`,
  text = `"\n— — —\n".join(preview_segments)`, inline-клавиатура с
  HMAC-tagged `callback_data`. Кнопка `[🔄 Перегенерировать]` добавляется
  только при `score < PUBLISH_MIN_SCORE`.
- [ ] После отправки preview бот записывает `preview_message_id` и CAS-ит
  `preview_pending=false`.
- [ ] Та же задача обрабатывает `notify_pending=true`: editing in-place при
  существующем `preview_message_id`, либо новое сообщение в топик; CAS
  `notify_pending=false`.
- [ ] **Auto-mode post-fact reply format (tech-spec Decision 7):** для job,
  опубликованного в `mode=auto` (без preview), `notify_pending`-обработчик
  публикует в топик новое сообщение строго в формате
  `Auto-published: <link>. Receipt: <hash>. Score: <N>.` (`<link>` =
  `post_url`, `<hash>` = `attestation_hash`, `<N>` = итоговый score). Этот
  формат проверяется тестом и не должен расходиться с tech-spec.
- [ ] Startup hook проверяет
  `OPERATOR_USER_ID in get_settings().allowed_user_ids`
  (где `get_settings` — импорт из
  `telegram_bot.core.config`, как и в `__main__.py`) и fail-loud
  при несоответствии (защита от silent-drop сообщений оператора глобальным
  `AuthMiddleware`).
- [ ] CI/CD создаёт topic `blogger-prompts`: `deploy.yml` добавляет его в
  `telegram-init`, а `content-publisher` получает `blogger_prompts_topic_id`
  из `telegram_topics['blogger-prompts']`, НЕ как manual-only sops prerequisite.
- [ ] Sliding-window rejection counter: >5 HMAC-failures ИЛИ unauthorized-
  кликов в 10мин → бот шлёт security-note в топик, окно сбрасывается.
- [ ] `publish_router` зарегистрирован в `__main__.py` через
  `dp.include_router(publish_router)` **до** `text_router`.
- [ ] `start_preview_dispatch_loop` поднят как `asyncio.create_task(...)` в
  startup hook.
- [ ] НЕТ новой slash-команды в `MOLYANOV_BOT_COMMANDS`. НЕТ изменений в
  bot's MCP-сервере (`mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/`).
- [ ] Все тесты из TDD Anchor зелёные. `uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest` проходят.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md) — Decision 9 (HMAC + rotation), Decision 13 (topic-based dispatch)
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — проектные правила, компоненты, pipeline, rules (включая правила про qualifty bar и testing).

**Код (modify):**
- [mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py](../../../mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py) *(NEW)*
- [mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py](../../../mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py)
- `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_publish_handlers.py` *(NEW)*
- `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_topic_filter.py` *(NEW)*
- `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_retry_linkage.py` *(NEW)*

**Код (read для паттернов):**
- [mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py](../../../mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py) — каноничный пример
  prefix-based callback handler-а (`F.data.startswith("ttui:")`, строки 227-228);
  паттерн "parse → message present → topic binding → state check → answer".
- [mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/forum_topic.py](../../../mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/forum_topic.py) —
  пример работы с `F.forum_topic_*` и `message.message_thread_id`.
- [mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py](../../../mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py) — строки 270-310:
  порядок `dp.include_router(...)`, регистрация startup hooks.
- `fabric/content-publisher/src/content_publisher/queue.py` *(создаётся в Task 4)* —
  публичный API `append_job`, `cas_status`, `find_by_prefix`, `load`.

## Verification Steps

### Automated
- `cd mnemonik-bridge-workspace/telegram-ai-agent && uv run pytest tests/test_publish_handlers.py tests/test_topic_filter.py tests/test_retry_linkage.py -v` → all pass.
- `cd mnemonik-bridge-workspace/telegram-ai-agent && uv run ruff check . && uv run ruff format --check . && uv run mypy src/` → all green.
- `cd mnemonik-bridge-workspace/telegram-ai-agent && uv run pytest` → весь suite green (no regressions).

### Smoke
- `test_publish_handlers.py`: симулировать callback
  `publish:approve:abc123456789:<valid_hmac>` → CAS `preview-sent→publishing`
  выполнен, mocked publish-флоу вызван ровно 1 раз.
- `test_topic_filter.py`: 4 кейса (1 положительный + 3 отрицательных по AC-T12) —
  только положительный кейс вызывает `append_job`.
- `python -c "from telegram_bot.core.handlers.publish import publish_router; print(publish_router.name)"` →
  выводит `publish` без import-errors (проверка чистоты импорта).

## Details

**Files:**
- `core/handlers/publish.py` *(NEW)* — `publish_router`, topic-handler, 3
  callback-handler-а, `preview_dispatch_loop`, sliding-window counter, парсер
  callback_data, HMAC-validate. Держать <500 LOC; при разрастании выносить
  HMAC и parser в `core/handlers/_publish_callbacks.py`.
- `__main__.py` — добавить импорт, `dp.include_router(publish_router)` **до**
  `text_router` (текущий порядок: 279-286), запуск
  `asyncio.create_task(start_preview_dispatch_loop(bot))` в startup hook рядом
  с `_periodic_tmp_cleanup` (текущая позиция: ~302-309).
- `tests/test_publish_handlers.py`, `test_topic_filter.py`,
  `test_retry_linkage.py` — все 3 файла НОВЫЕ; используют моки aiogram (без
  реального Telegram API), мок `content_publisher.queue` и tmp-path для
  `queue.jsonl`.

**Dependencies:**
- Task 4 (`content_publisher.queue` с публичным API `append_job`, `cas_status`,
  `find_by_prefix`).
- Task 6 (worker заполняет `preview_pending=true`, `preview_segments`,
  `notify_pending=true`, `publish_error`, `post_url`, `attestation_hash`).
- aiogram 3.x уже в зависимостях бота. `hmac`, `hashlib`, `asyncio`,
  `collections.deque` — stdlib.
- ENV (источник `/etc/blogger.env`, рендерится Task 3):
  `TELEGRAM_FORUM_CHAT_ID`, `BLOGGER_PROMPTS_TOPIC_ID`, `OPERATOR_USER_ID`,
  `CALLBACK_HMAC_SECRETS`, `PUBLISH_MIN_SCORE`.

**Edge cases:**
- callback от bot itself (`callback.from_user.is_bot=True`) → reject "Not authorized".
- `callback.message is None` / `InaccessibleMessage` (старше 48ч) → `callback.answer()` и выход (см. `tail.py:244-249`).
- worker и bot race: оператор кликнул approve пока deadline-checker уже
  перевёл preview-sent → publishing → CAS expected mismatch → `callback.answer("too late, status: publishing")`.
- две одновременные approve-нажатия от того же оператора (двойной тап) →
  второй CAS видит `status=publishing` → "too late, status: publishing".
- callback_data >50 байт после конкатенации (regex-bound: action ∈ {approve,reject,retry} ≤7; uuid_prefix=12; hmac8=16 hex; разделители = 3) → теоретически 7+1+12+1+16+8 ≈ 45 ≤ 64.
- `preview_segments=[]` (worker не успел заполнить, но `preview_pending=true`) — отправить плейсхолдер "Preview unavailable", залогировать, CAS `preview_pending=false`.
- `notify_pending=true` без существующего `preview_message_id` → НОВОЕ
  сообщение в топик (не editing).
- env var missing → fail-loud на старте процесса бота, не на первом callback.
- multi-worker scenarios: только один worker и один bot — race c самим собой не возникает; но CAS всё равно обязателен из-за гонок bot↔worker.

**AuthMiddleware (load-bearing assumption):**
- Бот's `AuthMiddleware` сидит на dispatcher-level и **глобально** фильтрует
  все входящие `Update` по `settings.allowed_user_ids` (sops-encrypted список
  из operator-bot конфига; читается через
  `from telegram_bot.core.config import get_settings`). Этот фильтр
  срабатывает **раньше** нашего topic-handler-а — значит, если
  `OPERATOR_USER_ID` не в `settings.allowed_user_ids`, сообщения оператора в
  `📝 blogger-prompts` будут отброшены до того, как наш router их увидит, и
  баг будет silent (нет логов в нашем модуле).
- Сейчас для publish-flow и общего бота — один и тот же оператор, так что в
  проде проблема не материализуется. Но добавляем startup-assertion
  (`assert OPERATOR_USER_ID in get_settings().allowed_user_ids`) как
  fail-loud guard на будущее (например, если кто-то поменяет
  `OPERATOR_USER_ID` отдельно от operator-bot allowlist). NB: aiogram-бот —
  чистый dispatcher без FastAPI/Starlette, поэтому никакого `app.state`/
  `app.state.allowed_user_ids` в этом процессе НЕТ. Источник truth для
  allowlist — singleton `get_settings()`.

**`find_by_prefix` — НЕ реимплементировать в handlers:**
- Импортировать строго: `from content_publisher.queue import find_by_prefix`
  (публичный API, добавлен в Task 4 iteration 2). Запрещено реимплементировать
  логику lookup/CAS в `core/handlers/publish.py` — это нарушит инвариант
  "вся очередь-логика инкапсулирована в `content_publisher.queue`".

**Implementation hints:**
- HMAC: `hmac.new(secret.encode(), f"{action}:{job_id_prefix}".encode(), hashlib.sha256).digest()[:8].hex()` — 16 hex символов; compare через `hmac.compare_digest` (constant-time).
- CALLBACK_HMAC_SECRETS парсится один раз на старте: `secrets = [s.strip() for s in os.environ["CALLBACK_HMAC_SECRETS"].split(",") if s.strip()]`; ожидаем 1 или 2 элемента.
- Prefix-based callback registration: `@publish_router.callback_query(F.data.startswith("publish:"))` — единый хендлер диспатчит на 3 action по парсингу. Паттерн — `tail.py:227-228`.
- Topic-handler фильтр **должен** проверять все 3 поля в одном `@router.message(...)`: если разбить на несколько декораторов, aiogram прогонит все по очереди и любое одно поле может протечь.
- Background poll: использовать `asyncio.create_task(...)` в startup hook;
  единственный shutdown — graceful через `dp.shutdown.register(...)` (см. как делается с `_periodic_tmp_cleanup`).
- Не использовать `await callback.message.reply(...)` для preview — нужно
  именно `bot.send_message(chat_id=..., message_thread_id=..., reply_to_message_id=..., ...)`, потому что preview шлётся в ответ на исходный brief, а не на callback message.
- `find_by_prefix(job_id_prefix12)` должен возвращать **уникальный** match;
  при collision (теоретически возможно, 12 hex = 48 бит) — отклонить с
  "Invalid signature" (внутренне залогировать collision как security-event).
- Sliding window: `collections.deque[float]` с `now() - 600`-cutoff; не
  использовать external rate-limiter (overkill).
- Не логировать сырой `callback.data` (атакер может писать в журнал через
  крафченный payload) — пример в `tail.py:237-241`.

## Reviewers

- **code-reviewer** → `work/content-publish-pipeline/logs/working/task-8/code-reviewer-{round}.json`
- **test-reviewer** → `work/content-publish-pipeline/logs/working/task-8/test-reviewer-{round}.json`
- **security-auditor** → `work/content-publish-pipeline/logs/working/task-8/security-auditor-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md (Summary 1-3 предложения + ссылки на JSON каждого ревью-раунда).
- [ ] Если отклонились от спека — описать отклонение и причину (особенно если порядок include_router был изменён или поведение sliding-window отличается).
- [ ] Обновить user-spec/tech-spec если в процессе обнаружились изменения в публичном API `content_publisher.queue` (Task 4) или сигнатурах ENV.
