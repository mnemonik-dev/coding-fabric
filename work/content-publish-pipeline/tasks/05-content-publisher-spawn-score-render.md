---
status: planned
depends_on: [3, 4]
wave: 2
skills: [code-writing]
verify: [smoke]
reviewers: [code-reviewer, test-reviewer, security-auditor]
teammate_name:
---

# Task 5: content-publisher — claude spawn + analyze_blog + renderer (direct import)

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Реализуем шаг воркера `content-publisher`, отвечающий за конвейер `writing → scoring → preview-sent` в state machine задания. Это первый «продуктивный» шаг после того, как Task 4 уложила queue + CAS + env-isolation: здесь воркер берёт `queued` job, спавнит `claude` как subprocess (по паттерну из Decision 1 — prompt через stdin, `env=restricted_env("claude")`), вычитывает сгенерированный `article.md`, прогоняет его через `analyze_blog.py`, обрезает issues до 3 для превью и рендерит preview-сегменты прямым импортом `mnemonik_blogger` API (Decision 4 — байт-в-байт совпадение с тем, что потом опубликует `run_campaign_from_article`). Финал шага — атомарный CAS в `preview-sent` с выставлением `approval_deadline`, `preview_pending=true`, `cleanup_at`. Этот шаг изолирован от шага публикации/аттестации (Task 6) и от сетевых эффектов: всё внутри worktree на диске + локальные процессы.

Без этого шага нет смыслового содержимого пайплайна: queue из Task 4 умеет принимать job, но никакой статьи и превью оператор не увидит. С этим шагом — воркер уже может прокачать job до состояния «ждём кнопку оператора в Telegram», а Task 6 потом наденет сверху публикацию.

## What to do

1. Создать модули `fabric/content-publisher/src/content_publisher/{spawn.py, score.py, render.py, worker.py}`:
   - `spawn.py` — асинхронный спавн `claude` через `asyncio.create_subprocess_exec` с `--print --mcp-config /etc/blogger.mcp.json --strict-mcp-config --skill claude-blog/blog-writer`, prompt через stdin, `env=restricted_env("claude")` (из Task 4); никаких аргументов с payload. Возвращает `(article_path, exit_code)`. Non-zero exit → бросает `SpawnFailed` с stderr-хвостом для логов.
   - `score.py` — асинхронный запуск `python /opt/claude-blog/scripts/analyze_blog.py <article_path>` с `env=restricted_env("analyze_blog")` (Task 4). Возвращает `ScoreResult(score: int, issues: list[str])` после парсинга stdout (JSON или fixed-line формат, как у upstream). Non-zero exit → `ScoreFailed`. Truncate issues до первых трёх ВНУТРИ модуля.
   - `render.py` — синхронная функция `render_preview(article_path: Path) -> list[str]`. Реализует ровно три строки из Decision 4: `src = ingest_article(article_path); rendered = render(Platform.TELEGRAM, src); return rendered.segments`. Импорт `Platform` строго `from mnemonik_blogger.config import Platform` (не из `content.voice` — это устаревший/несуществующий путь).
   - `worker.py` — связывает три модуля + CAS-переходы. Берёт `queued` job из queue (через `cas_status` из Task 4), валидирует `job.id` как UUID v4, собирает worktree path `worktree_root / sanitized_id`, делает path-containment check (см. hints), создаёт каталог, проходит `queued→writing → spawn → writing→scoring → score+render → scoring→preview-sent` с записью полей `score`, `issues` (truncated), `preview_segments`, `approval_deadline=now+PUBLISH_APPROVAL_TIMEOUT_MIN`, `preview_pending=true`, `cleanup_at=now+24h`. Любой сбой на `writing`/`scoring`/`render` → CAS в `failed` + `notify_pending=true` + сохранение `error_message`.

2. Написать тесты в `fabric/content-publisher/tests/`:
   - `test_preview_render.py` — Decision 4 cross-comparison. Берём фикстурную `article.md` (короткую, чтобы один сегмент). Считаем `expected = render(Platform.TELEGRAM, ingest_article(path)).segments`. Monkey-patch'им внутренний платформенный sender — конкретное имя/путь определяем грепом upstream (см. Implementation Hints; `mnemonik_blogger.agent._publish_post` — стартовая гипотеза, не подтверждённый seam) — захватываем то, что бы туда улетело. Вызываем `run_campaign_from_article(article=Path(path), platforms=[Platform.TELEGRAM], settings=Settings(dry_run=False, ...), attest=False)`. Assert `captured_segments == expected` ПО БАЙТАМ.
   - `test_analyze_gate.py` — два кейса: (a) high score (>= min_score) — `worker` НЕ ставит флаг для `[🔄]` (issues пусто/в коротком списке), переходит в `preview-sent` нормально; (b) low score (< min_score) — issues длиной 7 элементов → в job в `preview-sent` остаётся ровно первые 3, остальные обрезаны.
   - `test_spawn_failure_path.py` — claude subprocess завершается с exit 1 (mock). Assert: статус job становится `failed` (НЕ `writing`/`scoring`), `notify_pending=true`, в job записан `error_message` со stderr-хвостом, worktree остался для последующего cleanup.
   - `test_separate_bot_token.py` — (AC12) спавн `claude` происходит с `env`, в котором НЕТ `TELEGRAM_BOT_TOKEN` оператора-бота; subprocess для `analyze_blog` тоже не видит токен; токен `TELEGRAM_BOT_TOKEN` из `/etc/blogger.env` остаётся ТОЛЬКО для in-process publisher (Task 6), но в spawn-шагах его в env быть не должно.
   - `test_required_mounts_for.py` — (AC15) рендер unit-файла `content-publisher.service.j2` с конкретным `hetzner_volume_id` (фикстура) даёт строку `RequiresMountsFor=/mnt/HC_Volume_<int>/content-publisher` без литерала `*` и без шаблонных следов `{{` `}}`; при пустом факте задача рендера падает.

3. Запустить тесты, убедиться что они падают, реализовать модули, добиться зелени, прогнать smoke (см. Verification).

## TDD Anchor

- `tests/test_preview_render.py::test_preview_segments_byte_equal_to_publish_segments` — preview-рендер (`render(Platform.TELEGRAM, ingest_article(path)).segments`) совпадает с тем, что `run_campaign_from_article` отправил бы в Telegram (через перехват внутреннего sender'а) — БАЙТ-В-БАЙТ.
- `tests/test_preview_render.py::test_render_uses_config_platform_import` — `Platform` импортируется из `mnemonik_blogger.config`, не из `.content.voice` (защита от регрессии).
- `tests/test_analyze_gate.py::test_high_score_no_retry_marker` — score ≥ `MNEMONIK_MIN_SCORE` → job без флага низкого скора, issues не обязательны в превью.
- `tests/test_analyze_gate.py::test_low_score_truncates_issues_to_three` — score < min → ровно 3 issues в job (даже если analyze_blog вернул больше), порядок сохраняется.
- `tests/test_spawn_failure_path.py::test_claude_nonzero_exit_marks_failed_and_notify_pending` — claude exit 1 → CAS в `failed`, `notify_pending=true`, `error_message` непуст.
- `tests/test_spawn_failure_path.py::test_analyze_blog_nonzero_exit_marks_failed_and_notify_pending` — `analyze_blog.py` exit 1 → то же поведение.
- `tests/test_separate_bot_token.py::test_claude_env_excludes_telegram_token` — env, переданный в `create_subprocess_exec` для claude, не содержит ключа `TELEGRAM_BOT_TOKEN`.
- `tests/test_separate_bot_token.py::test_analyze_env_excludes_telegram_token` — то же для analyze_blog.
- `tests/test_required_mounts_for.py::test_unit_template_renders_concrete_volume_id` — рендер с `hetzner_volume_id=105783873` → строка `RequiresMountsFor=/mnt/HC_Volume_105783873/content-publisher`.
- `tests/test_required_mounts_for.py::test_unit_template_no_literal_wildcard` — отрендеренный текст не содержит подстроки `HC_Volume_*` и литерала `*` в строке `RequiresMountsFor`.

## Acceptance Criteria

- [ ] Воркер забирает первую job со статусом `queued`, валидирует `job.id` как UUID v4 (через `uuid.UUID(job.id, version=4)` + сравнение каноничного string), создаёт worktree `/var/lib/content-publisher/work/<sanitized_id>/`. Резолв path содержится внутри `CONTENT_PUBLISHER_WORKTREE_ROOT` (path-containment, см. hints).
- [ ] Спавн: `asyncio.create_subprocess_exec("claude", "--print", "--mcp-config", "/etc/blogger.mcp.json", "--strict-mcp-config", "--skill", "claude-blog/blog-writer", stdin=asyncio.subprocess.PIPE, env=restricted_env("claude"))`. Prompt передаётся ИСКЛЮЧИТЕЛЬНО через stdin (через `proc.communicate(input=prompt.encode())` или эквивалент `write` + `close`), не через argv.
- [ ] После успешного завершения `claude` воркер находит `article.md` внутри worktree (известный относительный путь, который claude пишет в файловую систему по skill workflow); сохраняет абсолютный путь в `job.article_path`.
- [ ] CAS `writing → scoring` атомарно (через `content_publisher.queue.cas_status`).
- [ ] Запуск `python /opt/claude-blog/scripts/analyze_blog.py <article_path>` через `asyncio.create_subprocess_exec` с `env=restricted_env("analyze_blog")`. Перед запуском `<article_path>` валидируется path-containment относительно worktree (`article_path.resolve().is_relative_to(worktree.resolve())`).
- [ ] Парсинг stdout `analyze_blog.py` даёт `score: int` и `issues: list[str]`. Если формат разъехался — ловим исключение, в job фиксируем `failed` + `notify_pending=true`.
- [ ] Issues обрезаются до первых трёх ДО записи в `job.issues` (для отображения в превью).
- [ ] Preview рендерится через прямой импорт: `from pathlib import Path; from mnemonik_blogger.config import Platform; from mnemonik_blogger.content.ingest import ingest_article; from mnemonik_blogger.content.formatters import render; preview_segments = render(Platform.TELEGRAM, ingest_article(article_path)).segments`. Импорт `Platform` именно из `mnemonik_blogger.config` (не из `.content.voice`).
- [ ] `job.preview_segments` сохраняется как `list[str]` (то самое поле в queue JSONL).
- [ ] CAS `scoring → preview-sent` атомарно + одновременно (в той же CAS-операции) выставляются `approval_deadline = now() + PUBLISH_APPROVAL_TIMEOUT_MIN мин`, `preview_pending = true`, `cleanup_at = now() + 24h`.
- [ ] При любом сбое (claude non-zero exit, analyze_blog non-zero exit, парс stdout, render-исключение): CAS в `failed`, выставляются `notify_pending = true` и `error_message` (хвост stderr или текст исключения); worktree НЕ удаляется (cleanup-gc Task 6 заберёт по `cleanup_at`).
- [ ] Все subprocess'ы видят ТОЛЬКО переменные из allowlist `restricted_env` соответствующего kind (claude/analyze_blog) — никаких утечек `TELEGRAM_BOT_TOKEN`, sops-секретов и т.п. (AC12 + AC-T9).
- [ ] Тесты из TDD Anchor зелёные.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md)
- [CLAUDE.md (project context — canonical, no project-knowledge skill initialized)](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — Project overview, pipeline, components map, constraints.
- [CLAUDE.md (architecture + patterns)](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — Architecture, Rules, Build & Test sections.

**Spec sections to read first:**
- tech-spec.md → Solution + Architecture diagram (особенно блок `writing → scoring → preview-sent`)
- tech-spec.md → Decision 1 (subprocess + stdin)
- tech-spec.md → Decision 4 (direct Python import; верифицированный API)
- tech-spec.md → `restricted_env(kind)` allowlist table
- tech-spec.md → Queue JSONL schema
- tech-spec.md → Job state machine
- tech-spec.md → Testing Strategy (релевантные тесты: `test_preview_render.py`, `test_analyze_gate.py`, `test_spawn_failure_path.py`, `test_separate_bot_token.py`, `test_required_mounts_for.py`)

**Code to modify:**
- `fabric/content-publisher/src/content_publisher/worker.py` *(new)*
- `fabric/content-publisher/src/content_publisher/spawn.py` *(new)*
- `fabric/content-publisher/src/content_publisher/score.py` *(new)*
- `fabric/content-publisher/src/content_publisher/render.py` *(new)*
- `fabric/content-publisher/tests/test_preview_render.py` *(new)*
- `fabric/content-publisher/tests/test_analyze_gate.py` *(new)*
- `fabric/content-publisher/tests/test_spawn_failure_path.py` *(new)*
- `fabric/content-publisher/tests/test_separate_bot_token.py` *(new)*
- `fabric/content-publisher/tests/test_required_mounts_for.py` *(new)*

**Code to read (REFERENCE only — do NOT import):**
- [/Users/syi/src/sessions/coding-fabric/fabric/workspace-manager/dispatch.py](/Users/syi/src/sessions/coding-fabric/fabric/workspace-manager/dispatch.py) — паттерн `asyncio.create_subprocess_exec` (см. `_run_git` около строк 490–511) для понимания стиля, env-merge, returncode handling. Этот модуль НЕ импортируется — это другая сервис-область (Symphony).

**From Task 4 (already merged by the time Task 5 starts):**
- `fabric/content-publisher/src/content_publisher/queue.py` — `cas_status`, `load`, `append_job`
- `fabric/content-publisher/src/content_publisher/env.py` — `restricted_env(kind)`
- `fabric/content-publisher/src/content_publisher/models.py` — `Job`, status enum
- `fabric/content-publisher/tests/conftest.py` — фикстуры `mock_analyze_blog`, `mock_claude_subprocess`, `mock_blogger_run_campaign_from_article`

## Verification Steps

### Automated
- `cd fabric/content-publisher && pytest tests/test_preview_render.py tests/test_analyze_gate.py tests/test_spawn_failure_path.py tests/test_separate_bot_token.py tests/test_required_mounts_for.py -v` → все зелёные.
- `cd fabric/content-publisher && pytest -v` → весь сьют пакета зелёный (включая регрессии по Task 4).

### Smoke
- `python -c "from mnemonik_blogger.config import Platform; from mnemonik_blogger.content.ingest import ingest_article; from mnemonik_blogger.content.formatters import render; assert Platform.TELEGRAM.value == 'telegram'"` — выполняется внутри venv `/opt/blogger/venv/` (или локального dev-venv с теми же пинами). Должен exit 0. Если падает на ImportError — значит import path Decision 4 разъехался с upstream, остановиться и сначала чинить Decision 11 в Task 3.
- `python -c "from content_publisher.render import render_preview; from pathlib import Path; segs = render_preview(Path('tests/fixtures/short_article.md')); assert isinstance(segs, list) and all(isinstance(s, str) for s in segs)"` — рендер на фикстурной короткой статье возвращает `list[str]`.
- `python -c "from content_publisher.env import restricted_env; e = restricted_env('claude'); assert 'TELEGRAM_BOT_TOKEN' not in e and 'PATH' in e and 'HOME' in e"` — allowlist для claude.

## Details

**Files:**
- `fabric/content-publisher/src/content_publisher/spawn.py` *(new)* — async `spawn_claude(prompt: str, worktree: Path) -> Path` (returns article_path). Подход: используем `asyncio.create_subprocess_exec` с фиксированным набором аргументов (`claude --print --mcp-config /etc/blogger.mcp.json --strict-mcp-config --skill claude-blog/blog-writer`), `stdin=PIPE`, `cwd=worktree`, `env=restricted_env("claude")`. Prompt передаётся через `proc.communicate(input=prompt.encode())`. Non-zero exit → `raise SpawnFailed(stderr_tail)`. Точный набор pipe'ов (stdout/stderr) — на усмотрение реализатора, если поведение AC соблюдено.
- `fabric/content-publisher/src/content_publisher/score.py` *(new)* — async `analyze(article_path: Path) -> ScoreResult`, где `ScoreResult` — простой dataclass с `score: int` и `issues: list[str]`. Подход: `create_subprocess_exec` для `python /opt/claude-blog/scripts/analyze_blog.py <article_path>` с `env=restricted_env("analyze_blog")`, парс stdout под фактический формат upstream (см. `analyze_blog.py` — JSON-объект `{"score": N, "issues": [...]}` либо построчный формат — определить чтением upstream). Truncate issues до 3 ПЕРЕД return. Выбор конкретного JSON-парсера/regex — на усмотрение реализатора.
- `fabric/content-publisher/src/content_publisher/render.py` *(new)* — sync `render_preview(article_path: Path) -> list[str]`: импорт `Platform`/`ingest_article`/`render` ровно как в Decision 4 (без алиасов и переэкспортов); возвращает `RenderedPost.segments`. Если у `render(...)` другая форма return type — адаптировать вызов, не меняя байт-идентичность сегментов.
- `fabric/content-publisher/src/content_publisher/worker.py` *(new)* — координатор шага. Один публичный entry-point вида `async def run_writing_to_preview(job: Job) -> None`. Подход: последовательность (1) UUID v4 validate + path-containment, (2) CAS `queued→writing`, (3) `spawn_claude`, (4) CAS `writing→scoring`, (5) `analyze`, (6) `render_preview`, (7) CAS `scoring→preview-sent` с записью deadlines в той же CAS-операции. Все исключения шагов (`SpawnFailed`, `ScoreFailed`, общие `Exception`) сводятся к CAS в `failed` + `notify_pending=true` + `error_message`. Конкретный layout `try/except` и логирование — на усмотрение реализатора.
- Тестовые файлы — см. AC + TDD Anchor.

**Dependencies:**
- Task 4 (queue, env, models, CAS) — должна быть смержена ДО старта.
- venv `/opt/blogger/venv/` с установленными `mnemonik_blogger` (через Task 3 Ansible role на тестовом stenде; локально — pip install из pinned ref для dev).
- `/opt/claude-blog/scripts/analyze_blog.py` доступен по пути (на VM — через Task 3; локально — клон + симлинк в фикстурах).
- `claude` CLI на PATH (для smoke; в unit-тестах — mocked).

**Edge cases:**
- UUID v4 невалиден (traversal, control chars, не-uuid строка) → `raise ValueError` ДО создания каталога; job остаётся `queued`, в логе ERROR (следующий тик не возьмёт его, потому что воркер увидит ту же ошибку — это OK, оператор увидит лог + Task 6 cleanup-gc через 24h).
- Worktree уже существует (повторный запуск после крэша на `writing`) — это нормально, не падать; перезаписать `article.md` (claude всё равно сгенерит заново). Crash-recovery «`writing` → restart» из tech-spec Architecture опирается на это поведение.
- `article.md` отсутствует после успешного exit claude (skill workflow не сработал) → `SpawnFailed("article.md not produced")`.
- Path traversal в `article_path` (например, claude умудрился записать вне worktree) → `score` валидирует `is_relative_to(worktree.resolve())` и падает в `failed`.
- `analyze_blog.py` вывел не-JSON / неожиданный формат → парсинг бросает, шаг падает в `failed`.
- `render(Platform.TELEGRAM, ...)` бросает (например, статья ингестится пустой) → шаг падает в `failed`.
- Issues длиной 0 — это валидно (всё ок, нет претензий); не падать, просто `[]` в job.issues.
- Параллельный воркер уже взял эту job (CAS `queued→writing` returns False) → молча пропустить, ждать следующий тик.

**Implementation hints:**
- Используй `asyncio.create_subprocess_exec` (НЕ shell-форма), список аргументов; никогда не строй командную строку через f-string из prompt.
- Для stdin: `proc = await create_subprocess_exec(..., stdin=asyncio.subprocess.PIPE); stdout, stderr = await proc.communicate(input=prompt.encode())`. `communicate(input=...)` сам закрывает stdin — это правильный безопасный паттерн (альтернатива: `proc.stdin.write(prompt.encode()); await proc.stdin.drain(); proc.stdin.close()` — тоже валидно, выбирай по контексту, но не оставляй stdin открытым).
- Path containment для worktree: `sanitized = job.id` (после UUID v4 validate уже безопасный hex+dashes); `worktree = (worktree_root / sanitized).resolve()`; assert `worktree.resolve() == (worktree_root / sanitized).resolve()` и `worktree.parent == worktree_root.resolve()` — гарантирует что нет `..` или абсолюта.
- Path containment для article: `article_path.resolve().is_relative_to(worktree.resolve())` — стандарт Python 3.9+.
- `restricted_env("claude")` уже из Task 4 — не пересобирать env вручную здесь; если allowlist неполный — фиксить в Task 4, а не локально.
- Truncate issues: `issues[:3]` — простой срез достаточен (порядок upstream считается «по убыванию важности» по умолчанию).
- Для теста byte-equality: ДО написания теста сгрепай upstream `mnemonik_blogger` source на предмет реальной точки вызова Telegram sender'а (кандидаты — `_publish_post`, `_send_telegram`, `publish_telegram`, `send_message` внутри `mnemonik_blogger.agent` / `mnemonik_blogger.platforms.telegram` и т.п.). Подтверди точный module path и имя функции; именно туда — monkey-patch для перехвата исходящих сегментов. В тесте оставь комментарий со ссылкой на верифицированный API (файл:строка из upstream). Имя `mnemonik_blogger.agent._publish_post`, упомянутое в Testing Strategy, — рабочая гипотеза, а не подтверждённый seam; не полагайся на него без проверки.
- Для теста required-mounts: рендер Jinja-шаблона из Task 3 через `jinja2.Template` напрямую, фиксируя `hetzner_volume_id=105783873`; не дёргать `ansible-playbook` целиком — слишком тяжело для unit-уровня.
- `cas_status` из Task 4 принимает мутацию через callback / kwargs (см. контракт Task 4) — записывай `approval_deadline`, `preview_pending`, `cleanup_at`, `preview_segments`, `score`, `issues` в одной CAS-операции, не разбивай на несколько (иначе race с deadline-checker из Task 6).
- Логи: structured (jsonl или structlog, как делает workspace-manager) — `job_id`, `step`, `status_from`, `status_to`, `duration_ms`. Без секретов и без полного prompt'а в логах.

## Reviewers

- **code-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-5/code-reviewer-{round}.json`
- **test-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-5/test-reviewer-{round}.json`
- **security-auditor** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-5/security-auditor-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону (Summary: 1-3 предложения, ревью со ссылками на JSON, без таблиц файндингов и дампов)
- [ ] Если отклонились от спека — описать отклонение и причину (особенно: если seam name для preview byte-equality теста отличается от того, что предполагалось в Task 3 — зафиксировать настоящее имя)
- [ ] Обновить user-spec/tech-spec если что-то изменилось (например, если allowlist `restricted_env("claude")` пришлось расширить — обновить таблицу в Data Models tech-spec.md)
