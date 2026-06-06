---
status: planned
depends_on: [1, 2, 3]
wave: 2
skills: [code-writing]
verify: []                         # No Verify-smoke/Verify-user in tech-spec Task 4
reviewers: [code-reviewer, test-reviewer]
teammate_name:
---

# Task 4: `fabric/content-publisher/` — queue + state machine + CAS + env isolation

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Создаём новый Python-пакет `fabric/content-publisher/` — это foundation для всей последующей работы воркера (Tasks 5–6) и бота (Task 8). Пакет содержит **общий примитив CAS-перехода состояния**, который импортируют ОБА процесса (long-running worker и telegram bot), чтобы атомарно мутировать JSONL-очередь на персистентном Hetzner-volume. Без этого Task общая JSONL-шина из tech-spec Architecture (раздел "Shared resources") существовать не может.

Tech-spec Decision 2 явно требует "JSONL queue with atomic CAS (not SQLite)" с реализацией через `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()`, зеркалируя паттерн `fabric/workspace-manager/state.py:70-111` (`_write_raw`, `_acquire`, `_release`). Этот таск:

1. Создаёт скелет нового пакета (pyproject.toml + структура src/tests).
2. Переносит ~30 LOC flock-паттерна из workspace-manager в `content_publisher.state` (БЕЗ импорта из workspace-manager — пакеты должны быть decoupled).
3. Реализует `content_publisher.queue` с четырьмя публичными функциями — `append_job`, `load`, `cas_status`, `find_by_prefix` — которые являются SINGLE SOURCE OF TRUTH для очереди.
4. Реализует `content_publisher.models` с Pydantic Job-моделью (24 поля из tech-spec Data Models) и status enum со всеми 13 состояниями state-machine (queued, writing, scoring, preview-sent, publishing, published, attest-pending, done, rejected, regenerated, publish-failed, attest-failed, failed).
5. Реализует `content_publisher.env.restricted_env(kind)` — allowlist-функцию для генерации env-окружения дочерних процессов (claude/analyze_blog/mnemonik-mcp/blogger_inproc — таблица из tech-spec Data Models).
6. Строгая валидация UUID v4 (отвергает path traversal и control chars — security).
7. Централизованные mock-фикстуры в `tests/conftest.py` (используются всеми тестами в Tasks 5, 6, 8).

После этой задачи Task 5 (writing/scoring) и Task 6 (publish/sub-loops) импортируют готовые `Job`, `cas_status`, `restricted_env`. Task 8 (bot) — те же самые `append_job`, `cas_status` и `find_by_prefix` (для разрешения 12-символьных префиксов из `callback_data` обратно в полный `job_id`).

## What to do

1. **Scaffold пакета.** Создать `fabric/content-publisher/pyproject.toml` (PEP 621), `src/content_publisher/__init__.py`, директорию `tests/`. Зависимости: `pydantic>=2.x`. Конфиг `pytest` + `ruff` + `mypy` по образцу `fabric/workspace-manager/pyproject.toml` (если есть).
2. **`state.py` — atomic-write + flock helpers.** Перенести (НЕ импортировать) три приватные функции/паттерна из `fabric/workspace-manager/state.py:70-111`: `_write_raw(path, data)` (tempfile + fsync + `os.replace` + dir fsync), `_acquire(lock_path)` (открыть файл + `fcntl.flock(LOCK_EX)`), `_release(fh)` (`flock(LOCK_UN)` + close). Оставить как module-level функции, принимающие путь параметром (а не методы класса — потому что JSONL-файл и lock-файл будут параметризованы env-переменной `CONTENT_PUBLISHER_QUEUE`, а не зашиты в конструктор).
3. **`models.py` — Pydantic Job + JobStatus enum.**
   - `JobStatus` (StrEnum) с 13-ю значениями: `queued`, `writing`, `scoring`, `preview-sent`, `publishing`, `published`, `attest-pending`, `done`, `rejected`, `regenerated`, `publish-failed`, `attest-failed`, `failed`.
   - `Job` (BaseModel) с 27+ полями ровно по схеме tech-spec Data Models: `id`, `created_at`, `fire_at`, `approval_deadline`, `cleanup_at`, `prompt`, `feedback_for_retry`, `mode`, `status`, `score`, `issues`, `worktree_path`, `article_path`, `preview_segments`, `preview_message_id`, `preview_pending`, `notify_pending`, `chat_id`, `thread_id`, `reply_to_message_id`, `tentative_publish_started_at`, `publish_error`, `post_url`, `post_message_ids`, `published_at`, `attest_pending_since`, `error_message`, `content_sha256`, `attestation_hash`, `attest_attempts`, `regenerated_to`, `retries_of`. Типы соответствующие (datetime/int/str/list/Optional). `mode` тип `Literal["approval", "auto"]`.
     - **`published_at: datetime | None = None`** — устанавливается transition'ом `publishing → published` (текущим `datetime.now(UTC)`). Используется Task 6 для подсчёта posts-per-hour rate-ceiling (security R2-9).
     - **`attest_pending_since: datetime | None = None`** — устанавливается transition'ом `published → attest-pending` (текущим `datetime.now(UTC)`). Используется Task 6's attest-retry sub-loop как якорь для измерения "24h elapsed since first entry into attest-pending" (escalation в `attest-failed` per Decision 8).
     - **`error_message: str | None = None`** — устанавливается transition'ами в terminal-error состояния (`failed`, `publish-failed`, `attest-failed`); хранит текст исключения/ошибки для диагностики оператором. Tasks 5 и 6 пишут+проверяют это поле.
   - Field validator на `id` — строго UUID v4 формат: regex `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`. Отвергает path traversal (`..`, `/`, `\`), control chars (`\x00`-`\x1f`), любое отклонение от RFC 4122 v4.
   - State-machine helper: `Job.allowed_transitions() -> dict[JobStatus, set[JobStatus]]` — карта разрешённых переходов из state-machine диаграммы tech-spec.
4. **`queue.py` — SHARED CAS-примитив.** Четыре публичные функции:
   - `append_job(queue_path: Path, *, prompt: str, mode: str | None, chat_id: int | None, thread_id: int | None, reply_to_message_id: int | None, fire_at: datetime | None = None) -> Job` — генерирует UUID v4, создаёт `Job(status="queued", created_at=now())`, под flock делает append одного JSON-line к файлу (используя `state._acquire` + write в append-mode + `state._release`). Возвращает созданный Job.
   - `load(queue_path: Path) -> list[Job]` — читает JSONL целиком; парсит каждую строку через `Job.model_validate_json`; пропускает (с warning в лог) пустые/битые строки.
   - `cas_status(queue_path: Path, job_id: str, expected: JobStatus, target: JobStatus, *, updates: dict | None = None) -> Job` — **критический атомарный примитив**. Под `_acquire(lock_path)`: читает все строки; находит job по `id`; проверяет `status == expected`; проверяет `target in allowed_transitions[expected]`; **автоматически устанавливает timestamp-поля по target-status'у** (см. ниже); применяет caller-supplied `updates` к dict-форме job (caller-overrides могут перекрыть auto-set, например для тестов); записывает все строки через `_write_raw` в temp + `os.replace`; возвращает обновлённый Job. Бросает `StaleStateError(actual_status=...)` если expected не совпал. Бросает `IllegalTransitionError` если переход не разрешён state-machine'ом. Бросает `JobNotFoundError`.
     - **Auto-set timestamp matrix** (применяется до caller-supplied `updates`):
       - `target == "published"` → `published_at = datetime.now(UTC)`
       - `target == "attest-pending"` → `attest_pending_since = datetime.now(UTC)`
       - `target in {"failed", "publish-failed", "attest-failed"}` → если в `updates` передан `error_message`, он сохраняется; если нет — поле остаётся как есть (caller обязан передать причину для diagnostic). Это не auto-set таймстампа, а контракт: caller MUST передать `updates={"error_message": "..."}` при переходе в terminal-error.
   - `find_by_prefix(queue_path: Path, job_id_prefix12: str) -> Optional[Job]` — **нужен Task 8 (bot callback_data resolver)**. Принимает 12-символьный префикс UUID (так как Telegram `callback_data` ограничен 64 байтами и хранит первые 12 hex-символов вместо полного UUID). Загружает очередь через `load()`, ищет первый Job, у которого `id.startswith(job_id_prefix12)`. Возвращает `Job` при точном префиксном совпадении; `None` если ни одного матча; при коллизии (статистически почти невозможной для UUID v4 с 12-hex = 48 бит, но defensive) — **бросает `ValueError("ambiguous prefix: N matches")`** (выбор: fail-loud, потому что молчаливый выбор первого может опубликовать чужой контент по чужому callback). Валидирует длину префикса (== 12) и что все символы — lowercase hex; иначе `ValueError`.
5. **`env.py` — `restricted_env(kind)` allowlist helper.** Принимает `kind: Literal["claude", "analyze_blog", "mnemonik-mcp", "blogger_inproc"]`, возвращает `dict[str, str]` по таблице из tech-spec Data Models раздела `restricted_env(kind)`. Источник переменных — `os.environ` (заранее загружено systemd из `/etc/blogger.env`). Для `blogger_inproc` возвращает все переменные текущего процесса (in-process call). Для `mnemonik-mcp` возвращает ТОЛЬКО `{"PATH": ...}` (см. AC-T9). Любая переменная, не найденная в `os.environ`, опускается (НЕ ошибка) — но `claude` обязан содержать хоть `CLAUDE_CODE_OAUTH_TOKEN` ИЛИ `ANTHROPIC_API_KEY`; иначе `RuntimeError`.
6. **`tests/conftest.py` — централизованные fixtures.** Создать фикстуры, которые используются всеми тестами этого Task'а И последующих (Tasks 5, 6, 8 импортируют их же):
   - `tmp_queue_dir(tmp_path) -> Path` — изолированная директория для очереди + lock.
   - `tmp_queue_path(tmp_queue_dir) -> Path` — путь к `queue.jsonl` внутри неё.
   - `make_job(...)` — factory-функция, возвращающая `Job` с заполненными required-полями и UUID v4.
   - Заглушки (для следующих тасков, но добавляем здесь): `mock_analyze_blog`, `mock_blogger_run_campaign_from_article`, `mock_mnemonic_mcp_stdio`, `mock_claude_subprocess` — пока возвращают MagicMock с дефолтным поведением; Task 5/6 будут уточнять конкретные return-values.
7. **Тесты** (см. TDD Anchor) — пишем ДО реализации (RED), реализуем (GREEN).

## TDD Anchor

Тесты пишем первыми; каждый должен FAIL до реализации.

- `tests/test_queue.py::test_append_job_creates_uuid_v4_and_queued_status` — append добавляет одну строку с правильным форматом UUID + status=queued.
- `tests/test_queue.py::test_append_is_atomic_under_concurrent_writers` — threading.Barrier + 10 потоков параллельно вызывают `append_job` → ровно 10 строк, ни одной коррупции, все id уникальны.
- `tests/test_queue.py::test_load_skips_blank_and_malformed_lines` — JSONL с пустыми строками и битым JSON → возвращает только валидные Job'ы; warning в лог.
- `tests/test_queue.py::test_cas_status_succeeds_when_expected_matches` — `cas_status(id, queued, writing)` → новый status сохранён в файле; возвращённый Job отражает изменение.
- `tests/test_queue.py::test_cas_status_raises_stale_when_expected_mismatch` — текущий status=scoring, expected=queued → `StaleStateError` + файл НЕ изменён.
- `tests/test_queue.py::test_cas_status_atomic_under_concurrent_clicks` — два потока одновременно вызывают `cas_status(id, preview-sent, publishing)`; ровно один SUCCESS, другой получает StaleStateError. **Главный security-критичный тест — моделирует race-condition concurrent callback click + deadline-fire из tech-spec Architecture.**
- `tests/test_queue.py::test_cas_status_updates_field_atomically` — `updates={"score": 85}` записаны вместе с переходом status (одна fsync).
- `tests/test_find_by_prefix.py::test_exact_12char_match_returns_job` — очередь с одним Job, `find_by_prefix(id[:12])` → возвращает этот Job.
- `tests/test_find_by_prefix.py::test_prefix_not_found_returns_none` — префикс не совпадает ни с одним Job → возвращает `None` (не raise).
- `tests/test_find_by_prefix.py::test_ambiguous_prefix_raises_value_error` — два Job-а синтетически подделаны с одинаковым 12-hex префиксом → `ValueError("ambiguous prefix: 2 matches")`. (Реализуется fail-loud, чтобы не опубликовать чужой контент по чужому callback.)
- `tests/test_find_by_prefix.py::test_invalid_prefix_length_raises` — `find_by_prefix("abc")` (длина != 12) → `ValueError`.
- `tests/test_find_by_prefix.py::test_invalid_prefix_chars_raises` — `find_by_prefix("XYZ123456789")` (не lowercase hex) → `ValueError`.
- `tests/test_state_machine.py::test_all_valid_transitions_allowed` — каждый переход из диаграммы state-machine разрешён.
- `tests/test_state_machine.py::test_illegal_transitions_raise` — `queued → published` (без промежуточных), `done → writing` (terminal → activity) → `IllegalTransitionError`.
- `tests/test_state_machine.py::test_terminal_states_have_no_outgoing` — `done`, `rejected`, `regenerated`, `publish-failed`, `attest-failed`, `failed` — пустые исходящие множества (Task 6 трактует `regenerated` как terminal-ish: job заменён на свой regenerated_to-потомок и сам больше не двигается).
- `tests/test_state_machine.py::test_transition_to_published_sets_published_at` — `cas_status(id, publishing, published)` → `published_at` установлен в timestamp ~ now (`abs(now - published_at) < 1s`); до перехода поле было `None`.
- `tests/test_state_machine.py::test_transition_to_attest_pending_sets_attest_pending_since` — `cas_status(id, published, attest-pending)` → `attest_pending_since` установлен в timestamp ~ now; до перехода поле было `None`.
- `tests/test_state_machine.py::test_transition_to_terminal_error_records_error_message` — `cas_status(id, writing, failed, updates={"error_message": "claude crashed: exit 137"})` → `error_message == "claude crashed: exit 137"` в сохранённом Job. То же самое для `publish-failed` и `attest-failed`.
- `tests/test_job_model.py::test_model_has_all_required_fields` — `Job.model_fields` содержит ВСЕ имена полей из tech-spec Data Models, включая новые: `published_at`, `attest_pending_since`, `error_message`. Fail-loud, если поле забыто.
- `tests/test_job_model.py::test_new_timestamp_fields_are_optional_datetime` — типы `published_at`, `attest_pending_since` — `Optional[datetime]` (или `datetime | None`); дефолт — `None`; принимают как ISO-8601 строку, так и `datetime` объект через Pydantic.
- `tests/test_job_model.py::test_error_message_is_optional_str` — тип `error_message` — `Optional[str]`; дефолт — `None`; принимает произвольную строку (включая многострочные tracebacks).
- `tests/test_uuid_validation.py::test_accepts_canonical_uuid_v4` — ловеркейс canonical form.
- `tests/test_uuid_validation.py::test_rejects_uuid_v1` — version != 4 → `ValidationError`.
- `tests/test_uuid_validation.py::test_rejects_path_traversal` — `id="../../etc/passwd"` → reject.
- `tests/test_uuid_validation.py::test_rejects_control_chars` — `\x00`, `\n`, `\r` в id → reject.
- `tests/test_uuid_validation.py::test_rejects_uppercase_hex` — UUID допускает только lowercase per наш regex (consistency).
- `tests/test_env_isolation.py::test_claude_env_subset_matches_allowlist` — `restricted_env("claude")` содержит ровно `{PATH, HOME, CLAUDE_CODE_OAUTH_TOKEN}` (или `ANTHROPIC_API_KEY` fallback) — НЕ содержит `TELEGRAM_BOT_TOKEN`, `CALLBACK_HMAC_SECRETS`, etc.
- `tests/test_env_isolation.py::test_mnemonik_mcp_env_has_only_PATH` — `restricted_env("mnemonik-mcp")` имеет только `{PATH}`.
- `tests/test_env_isolation.py::test_analyze_blog_env_has_PATH_and_claude_blog_path` — содержит ровно `{PATH, MNEMONIK_CLAUDE_BLOG_PATH}`.
- `tests/test_env_isolation.py::test_claude_env_raises_if_no_oauth_or_api_key` — отсутствие обоих секретов → `RuntimeError` при сборке env для `claude`.

## Acceptance Criteria

- [ ] **AC1 — Python package scaffold.** `fabric/content-publisher/pyproject.toml` существует (PEP 621), `pip install -e fabric/content-publisher` работает; директории `src/content_publisher/` и `tests/` присутствуют.
- [ ] **AC2 — `content_publisher.state` module.** Содержит `_write_raw(path, data)`, `_acquire(lock_path)`, `_release(fh)`. Поведение зеркалирует `fabric/workspace-manager/state.py:70-111` — tempfile + fsync + `os.replace` + dir fsync, `fcntl.flock(LOCK_EX)`. **НЕТ импортов из `workspace-manager`** — модули decoupled.
- [ ] **AC3 — `content_publisher.queue` shared CAS primitive.** Четыре публичные функции `append_job`, `load`, `cas_status`, `find_by_prefix`. Реализация под `fcntl.flock(LOCK_EX)`; запись через temp + `os.replace`. Поведение race-safe (доказано `test_cas_status_atomic_under_concurrent_clicks`). `find_by_prefix(job_id_prefix12)` нужен Task 8 для разрешения 12-символьных префиксов из Telegram `callback_data` обратно в полный `job_id`; при ambiguous prefix — `ValueError` (fail-loud). Этот модуль импортируется ОБОИМИ процессами (worker + bot) — никаких других путей записи в `queue.jsonl` существовать не должно.
- [ ] **AC4 — `content_publisher.models.Job`** Pydantic-модель с 27+ полями из tech-spec Data Models раздела "Queue JSONL schema". Все типы соответствуют schema (datetime/int/str/list/Optional). **Обязательно присутствуют** три поля, требуемые Tasks 5/6: `published_at: datetime | None = None`, `attest_pending_since: datetime | None = None`, `error_message: str | None = None`. `JobStatus` StrEnum со всеми 13 состояниями (queued, writing, scoring, preview-sent, publishing, published, attest-pending, done, rejected, regenerated, publish-failed, attest-failed, failed). `Job.allowed_transitions()` возвращает карту переходов state-machine.
- [ ] **AC4b — `cas_status` auto-устанавливает timestamps на правильных переходах.** `publishing → published` автоматически записывает `published_at = now(UTC)`. `published → attest-pending` автоматически записывает `attest_pending_since = now(UTC)`. Переходы в terminal-error состояния (`failed`, `publish-failed`, `attest-failed`) требуют от caller'а передать `updates={"error_message": "..."}` и корректно сохраняют это значение в JSONL. Доказывается тестами `test_transition_to_published_sets_published_at`, `test_transition_to_attest_pending_sets_attest_pending_since`, `test_transition_to_terminal_error_records_error_message`.
- [ ] **AC5 — `content_publisher.env.restricted_env(kind)`** — реализует allowlist-таблицу из tech-spec Data Models. Четыре kind'а: `claude`, `analyze_blog`, `mnemonik-mcp`, `blogger_inproc`. Для `claude`: fail-loud если ни `CLAUDE_CODE_OAUTH_TOKEN`, ни `ANTHROPIC_API_KEY` не присутствует в `os.environ`.
- [ ] **AC6 — Строгая UUID v4 валидация.** Field validator на `Job.id` отвергает: UUID других версий, path traversal (`..`, `/`, `\`), control chars (`\x00`-`\x1f`), uppercase hex, любые отклонения от canonical RFC 4122 v4 формата.
- [ ] **AC7 — `tests/conftest.py` с centralized mock fixtures.** Содержит `tmp_queue_dir`, `tmp_queue_path`, `make_job` factory, заглушки `mock_analyze_blog`, `mock_blogger_run_campaign_from_article`, `mock_mnemonic_mcp_stdio`, `mock_claude_subprocess` (используются Tasks 5/6/8).
- [ ] **AC8 — Все тесты из TDD Anchor проходят.** `pytest fabric/content-publisher/tests/ -v` → all green; `test_cas_status_atomic_under_concurrent_clicks` доказывает race-safety.
- [ ] **AC9 — Tech-spec Decision 2 соблюдён.** CAS реализован через `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()` (POSIX-atomic rename); НЕ через SQLite, НЕ через Redis.
- [ ] **AC10 — Decoupling от workspace-manager.** В `fabric/content-publisher/src/**/*.py` нет ни одного `import workspace_manager` / `from workspace_manager`. Паттерн ~30 LOC скопирован, а не reused.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md) — особенно Decision 2 (atomic CAS), Data Models (Queue JSONL schema + state machine + restricted_env table), Architecture (Shared resources)
- [decisions.md](../decisions.md)
- Project knowledge: каноничные PK-файлы (`project.md`, `architecture.md`, `patterns.md`) для этого репозитория не созданы. Основной context — `tech-spec.md` + репозиторный [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) (project overview, pipeline, components map, Rules секция со списком "do not commit secrets" и "always read before edit").

**Code files to read (current state — для понимания паттерна, который копируем):**
- [`fabric/workspace-manager/state.py`](../../../fabric/workspace-manager/state.py) — строки 70-111 (`_write_raw`, `_acquire`, `_release`) — **источник паттерна, который мы копируем (НЕ импортируем)**. Сейчас это методы класса `StateStore`; нам нужны module-level функции с параметризованными путями.
- [`fabric/workspace-manager/models.py`](../../../fabric/workspace-manager/models.py) — пример Pydantic-моделей с regex-валидаторами (`_validate_task_id`, `reject_traversal`); образец для UUID v4 валидатора.
- [`fabric/workspace-manager/tests/conftest.py`](../../../fabric/workspace-manager/tests/conftest.py) — образец организации conftest с `tmp_path`-based fixtures (`tmp_dirs`, `tmp_settings`).

**Code files to modify (create new):**
- `fabric/content-publisher/pyproject.toml` — PEP 621 manifest; deps: `pydantic>=2`; dev-deps: `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- `fabric/content-publisher/src/content_publisher/__init__.py` — пустой, либо реэкспорт `Job`, `JobStatus`, `cas_status`, `append_job`, `load`, `find_by_prefix`, `restricted_env`.
- `fabric/content-publisher/src/content_publisher/state.py` — module-level `_write_raw`, `_acquire`, `_release` (copy ~30 LOC от workspace-manager).
- `fabric/content-publisher/src/content_publisher/queue.py` — `append_job`, `load`, `cas_status`, `find_by_prefix` + исключения `StaleStateError`, `IllegalTransitionError`, `JobNotFoundError`.
- `fabric/content-publisher/src/content_publisher/models.py` — `JobStatus` enum, `Job` Pydantic-модель, `allowed_transitions()`.
- `fabric/content-publisher/src/content_publisher/env.py` — `restricted_env(kind)`.
- `fabric/content-publisher/tests/conftest.py` — фикстуры (см. step 6).
- `fabric/content-publisher/tests/test_queue.py` — см. TDD Anchor (7 тестов).
- `fabric/content-publisher/tests/test_find_by_prefix.py` — см. TDD Anchor (5 тестов).
- `fabric/content-publisher/tests/test_state_machine.py` — см. TDD Anchor (6 тестов; включая 3 новых на auto-set timestamp/error fields).
- `fabric/content-publisher/tests/test_job_model.py` — см. TDD Anchor (3 теста на схему Job: presence of all fields, types of `published_at`/`attest_pending_since`/`error_message`).
- `fabric/content-publisher/tests/test_uuid_validation.py` — см. TDD Anchor (5 тестов).
- `fabric/content-publisher/tests/test_env_isolation.py` — см. TDD Anchor (4 теста).

## Verification Steps

### Automated

- `cd fabric/content-publisher && pip install -e . && pytest tests/ -v` → all pass (≥30 tests из TDD Anchor).
- `pytest fabric/content-publisher/tests/test_queue.py::test_cas_status_atomic_under_concurrent_clicks -v` → проходит надёжно (race-safety критичен).
- `ruff check fabric/content-publisher/` → clean.
- `mypy fabric/content-publisher/src/` → clean.
- `grep -rE "from workspace_manager|import workspace_manager" fabric/content-publisher/src/` → пустой вывод (AC10 — decoupling).

## Details

**Files to create:**
- `fabric/content-publisher/pyproject.toml` — манифест пакета; `[project] name="content-publisher" requires-python=">=3.11"`; dep `pydantic>=2`; dev `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- `fabric/content-publisher/src/content_publisher/__init__.py` — package marker; public re-exports.
- `fabric/content-publisher/src/content_publisher/state.py` — `_write_raw(path: Path, raw_bytes: bytes)`, `_acquire(lock_path: Path) -> IO`, `_release(fh: IO)`; в отличие от workspace-manager (где функции были методами `StateStore`), здесь module-level + параметризованные путём (потому что queue-path берётся из env `CONTENT_PUBLISHER_QUEUE`).
- `fabric/content-publisher/src/content_publisher/models.py` — `JobStatus` (StrEnum, 13 значений), `Job` (BaseModel, 24+ полей), `Job.allowed_transitions() -> dict[JobStatus, set[JobStatus]]`.
- `fabric/content-publisher/src/content_publisher/queue.py` — `append_job`, `load`, `cas_status`, исключения.
- `fabric/content-publisher/src/content_publisher/env.py` — `restricted_env(kind: Literal[...]) -> dict[str, str]`.
- `fabric/content-publisher/tests/conftest.py` — centralized fixtures.
- `fabric/content-publisher/tests/test_queue.py`, `test_find_by_prefix.py`, `test_state_machine.py`, `test_job_model.py`, `test_uuid_validation.py`, `test_env_isolation.py`.

**Dependencies:**
- Внешние: `pydantic>=2`. Тесты: `pytest`, `pytest-asyncio` (для будущих Tasks 5–6).
- Зависит от Tasks 1–3 (Wave 1) — sops-ключи + Ansible-роль + mnemonik-mcp binary discovery — НО Task 4 их использует только косвенно (через env-переменные при runtime; на этапе unit-тестов мы их мокаем). Поэтому фактически Task 4 можно реализовать БЕЗ работающего Wave 1.

**Edge cases:**
- `cas_status` под concurrent click+timeout — главный race-сценарий из tech-spec Architecture ("Race: concurrent click + timeout fire"). Тест `test_cas_status_atomic_under_concurrent_clicks` доказывает: ровно один поток успешно делает переход, второй получает `StaleStateError(actual_status=publishing)`.
- Пустые/битые строки в JSONL (например, после kill -9 в середине append) — `load()` пропускает с warning, не падает.
- Append-mode без flock небезопасен на ext4 при многобайтных строках — поэтому append ТОЖЕ под `_acquire(lock_path)`. (Альтернатива — `os.write` с `O_APPEND`-атомарностью, но это работает только до `PIPE_BUF` ~ 4096 байт; JSON-строки могут быть длиннее.)
- UUID v4 валидация — отвергает path traversal даже до построения filesystem-path; защита глубокая.
- `restricted_env("claude")` — fail-loud, если оба `CLAUDE_CODE_OAUTH_TOKEN` и `ANTHROPIC_API_KEY` отсутствуют (защита от запуска claude без auth, что приведёт к молчаливой деградации).
- **Terminal-error contract.** `cas_status` НЕ навязывает наличие `error_message` в `updates` при переходе в `failed`/`publish-failed`/`attest-failed` (cas остаётся generic-примитивом). Однако Tasks 5/6 обязаны передавать его — иначе оператор не сможет диагностировать терминальную ошибку. Если впоследствии понадобится более строгий contract — добавить проверку в `cas_status` (тогда отдельный TDD-тест).
- **Auto-set vs caller-override.** Если caller передал в `updates` явный `published_at` (например, для backfill/тестов), он перекрывает auto-set значение. Порядок: сперва auto-set по target, затем `updates.update(...)`.

**Implementation hints:**
- Скопировать ~30 LOC из `fabric/workspace-manager/state.py` (строки 70-111: `_write_raw`, `_acquire`, `_release`). НЕ делать `from workspace_manager.state import _write_raw` — пакеты должны быть decoupled (AC10). Изменения при копировании: (а) функции module-level (не методы класса); (б) принимают `path` параметром; (в) `_write_raw` принимает уже-сериализованные байты (JSONL), а не dict для `json.dump`.
- POSIX-атомарность `os.replace` — гарантируется на одном filesystem; queue.jsonl + temp-файл должны быть в одной директории (это и так так — `tempfile.mkstemp(dir=path.parent)`).
- Lock-файл — `Path(str(queue_path) + ".lock")`, та же директория. Не пытаться lock'ать сам `queue.jsonl` (advisory flock на файле работает, но конвенция workspace-manager — отдельный `.lock`).
- Для `cas_status`: ВСЕГДА считывать весь файл под flock; не пытаться "in-place патчить нужную строку" — JSONL не позиционируем без полного парса.
- `Job.id` validator — `re.fullmatch` с UUID v4 regex; стандартный `uuid.UUID(v).version == 4` проверка тоже подойдёт, но regex дешевле и явно отвергает upper-case/spaces/traversal сразу.
- Для `test_cas_status_atomic_under_concurrent_clicks`: использовать `threading.Barrier(2)` + два потока, каждый вызывает `cas_status` на одном и том же job; собирать exceptions; assert один SUCCESS + один `StaleStateError`.
- Conftest `mock_*` фикстуры на этапе Task 4 — простые `MagicMock()`; их return-values уточнятся в Task 5/6.

**Security notes:**
- UUID v4 строгая валидация перед использованием в filesystem-path (worktree_path) — это первая линия защиты от traversal.
- `restricted_env` — allowlist-based (НЕ blacklist); по умолчанию ничего не пробрасывается.
- В тестах `test_env_isolation.py` устанавливать "мусорные" env-переменные (например, `FAKE_SECRET=should-not-leak`) и проверять, что они НЕ попадают в результат `restricted_env`.

## Reviewers

- **code-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-4/code-reviewer-{round}.json`
- **test-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-4/test-reviewer-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в `decisions.md` (Summary: 1-3 предложения, ссылки на JSON-репорты ревью со всех раундов, без таблиц findings).
- [ ] Если отклонились от спека (например, добавили доп. поле в `Job` или изменили имя функции) — описать отклонение и причину.
- [ ] Обновить `tech-spec.md` Data Models секцию, если в процессе обнаружились нестыковки между описанной JSONL schema и реальной реализацией.
- [ ] Подтвердить отсутствие импортов из `workspace_manager` (AC10) — финальный `grep -rE "workspace_manager" fabric/content-publisher/src/`.
