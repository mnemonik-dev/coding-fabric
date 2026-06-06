---
status: planned
depends_on: [9, 10, 11]
wave: 5
skills: [pre-deploy-qa]
verify: []
reviewers: []
teammate_name: qa-runner
---

# Task 12: Pre-deploy QA

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:pre-deploy-qa` — [SKILL.md](~/.claude/skills/pre-deploy-qa/SKILL.md)

## Description

Финальный quality-gate перед деплоем фичи `content-publish-pipeline`. После того как Wave 1–3 (Tasks 1–8) написали код и Audit Wave (Tasks 9–11) подтвердила, что код, безопасность и тесты в порядке, эта задача прогоняет ВСЕ unit + integration тесты двух затронутых пакетов (`fabric/content-publisher/` и `mnemonik-bridge-workspace/telegram-ai-agent/`), проверяет каждый из 28 acceptance criteria (15 user-spec AC1–AC15 + 13 tech-spec AC-T1–AC-T13), и сохраняет машиночитаемый AVP-отчёт в `work/content-publish-pipeline/logs/pre-deploy-qa.json`.

Зачем именно так: пайплайн делает реальные изменения в боевой системе (новый systemd сервис, новый Ansible role, изменения в bot'е, npm install глобального бинарника, изменения в sops). Любая ошибка до деплоя стоит несколько минут CI; та же ошибка после деплоя — час расследования + откат. Этот gate ловит регрессии локально, до того как код доедет до Hetzner VM.

Это терминальная задача Audit/QA фазы: успешный пас → переход к Task 13 (Deploy). Любой `failed` в отчёте → возврат к fixer-агентам (предыдущие волны), а не деплой.

## What to do

1. Прочитать входные документы (см. `pre-deploy-qa` skill: user-spec.md, tech-spec.md, decisions.md).
2. Прогнать unit + integration тесты пакета `fabric/content-publisher/`:
   - `cd fabric/content-publisher && pytest -v`
   - Зафиксировать: total / passed / failed / skipped; список упавших с stack trace, если есть.
3. Прогнать unit тесты пакета `mnemonik-bridge-workspace/telegram-ai-agent/`:
   - `cd mnemonik-bridge-workspace/telegram-ai-agent && pytest -v`
   - Зафиксировать те же метрики; убедиться, что новые `test_publish_handlers.py`, `test_topic_filter.py`, `test_retry_linkage.py` присутствуют и проходят.
4. Проверить КАЖДЫЙ acceptance criterion из user-spec (AC1–AC15) и tech-spec (AC-T1–AC-T13). Для каждого:
   - `passed` — критерий покрыт зелёным тестом / явной проверкой; приложить evidence (имя теста, путь к коду, фрагмент лога).
   - `failed` — фича существует, но критерий не выполняется; severity по правилам skill'а.
   - `not_verifiable` — требует живого окружения, MCP-tool'а или внешнего сервиса (scope Task 14 / post-deploy-qa). Это допустимый статус — не считается failure'ом для pre-deploy gate.
5. Coverage verification (правила из skill'а):
   - Для каждого файла из tech-spec "Files to modify" Tasks 1–8 — проверить, что есть соответствующий тест. Файл без теста → severity `critical`.
   - Если в pyproject.toml/Makefile настроен coverage (pytest --cov) — прогнать и убедиться, что coverage feature-файлов не упал ниже проектного порога.
   - Для каждого AC со статусом `passed` — убедиться, что связанный тест действительно гоняет логику фичи, а не просто `import` или mock-only заглушку.
6. Сгенерировать AVP-отчёт в формате JSON по схеме skill'а и сохранить в `work/content-publish-pipeline/logs/pre-deploy-qa.json`.
7. Определить gate:
   - `status: passed` (нет critical, нет failed AC, тестовый сьют зелёный) → проставить в отчёте `recommendation: proceed_to_deploy`. Это сигнал для feature-execution orchestrator перейти к Task 13.
   - `status: failed` (есть critical / failed AC / упавший тестовый сьют) → `recommendation: return_to_fixer` с перечислением, какие задачи (1–8) нужно пере-исполнить.

## Acceptance Criteria

- [ ] Обе тестовых команды прогнаны (`fabric/content-publisher && pytest -v` и `mnemonik-bridge-workspace/telegram-ai-agent && pytest -v`); итоги (total/passed/failed/skipped) записаны в отчёт.
- [ ] Каждый user-spec AC1–AC15 имеет запись в `acceptanceCriteria[]` с одним из трёх статусов и evidence.
- [ ] Каждый tech-spec AC-T1–AC-T13 имеет запись в `acceptanceCriteria[]` с одним из трёх статусов и evidence.
- [ ] Coverage-verification сделана для каждого файла из Files to modify Tasks 1–8; файлы без тестов помечены `critical` (если есть).
- [ ] AVP-отчёт сохранён в `work/content-publish-pipeline/logs/pre-deploy-qa.json` и валиден против схемы из skill'а.
- [ ] Gate-решение зафиксировано: `passed` → проставить `recommendation: proceed_to_deploy`; `failed` → `recommendation: return_to_fixer` с перечнем затронутых задач.

## Context Files

- [user-spec.md](../user-spec.md) — 15 acceptance criteria (AC1–AC15)
- [tech-spec.md](../tech-spec.md) — 13 tech-level criteria (AC-T1–AC-T13), Testing Strategy section с перечнем всех unit + integration тестов
- [decisions.md](../decisions.md) — отклонения от плана, если есть
- [SKILL.md](~/.claude/skills/pre-deploy-qa/SKILL.md) — методология и схема отчёта

**Контекст уровня проекта:**
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — описание архитектуры coding-fabric

**Файлы кода под верификацию (из Files to modify Tasks 1–8):**
- `fabric/content-publisher/` — весь пакет (queue, state, worker, publish, deadline, cleanup, attest, recovery, main, mcp_client) + тесты
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py` — новый topic-handler + callback handlers
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py` — регистрация publish_router
- `mnemonik-bridge-workspace/telegram-ai-agent/tests/{test_publish_handlers.py, test_topic_filter.py, test_retry_linkage.py}` — новые тесты bot'а
- `infrastructure/ansible/roles/content-publisher/` — Ansible role (verify-only, не пакеты под pytest, но проверить syntax-check был сделан в Task 3)
- `infrastructure/ansible/roles/mnemonic-mcp/` — переписанный role (verify-only)
- `infrastructure/secrets/secrets.sops.yml.template` — 9 новых ключей (verify-only)

## Verification Steps

### Automated

```bash
# Test suite 1 — content-publisher (unit + integration)
cd fabric/content-publisher && pytest -v

# Test suite 2 — telegram-ai-agent (new publish/topic/retry tests + regression)
cd mnemonik-bridge-workspace/telegram-ai-agent && pytest -v
```

Оба прогона должны быть зелёными (0 failed). Skipped допускаются только если в тесте есть `pytest.skip()` с причиной "requires live env" — такие AC уходят в `not_verifiable`.

Coverage (только `fabric/content-publisher/` — в `mnemonik-bridge-workspace/telegram-ai-agent/` `pytest-cov` отсутствует в dev-deps, поэтому `--cov` там не запускается; MVP-решение, см. Decision в decisions.md / Pre-deploy QA):

```bash
cd fabric/content-publisher && pytest --cov=content_publisher --cov-report=term-missing
```

Для `telegram-ai-agent` — только plain `pytest -v` (см. Automated выше); coverage этого пакета верифицируется вручную через cross-reference с test-audit отчётом (Task 11) и проверкой "каждый файл из Files to modify имеет тест".

Сравнить coverage feature-файлов content-publisher с baseline в `fabric/content-publisher/pyproject.toml` (если порог задан) или с CLAUDE.md проектного уровня. Падение → `critical`.

## Details

**Файлы:**
- `work/content-publish-pipeline/logs/pre-deploy-qa.json` — итоговый AVP-отчёт (создать, mkdir -p родительский путь, если нужно).
- `work/content-publish-pipeline/logs/` — может уже существовать (см. `logs/techspec/` в `git status`).

**Зависимости:**
- Tasks 9 (Code Audit), 10 (Security Audit), 11 (Test Audit) — должны быть `done`. Их JSON-отчёты в `work/content-publish-pipeline/logs/audit/{code-audit.json, security-audit.json, test-audit.json}` дают вход для cross-reference: если test-audit (Task 11) уже отметил отсутствие теста для AC-T7, эта задача должна это поднять в свой отчёт, а не игнорировать.

**Edge cases:**
- AC8 (scheduled job persists across VM restart) — может быть `not_verifiable` без живой VM; искать unit-эквивалент в `test_queue_persist.py` (integration). Если интеграционный тест есть и зелёный → `passed`; если нет → `not_verifiable`.
- AC9 (past-date `--at` rejection) — `not_verifiable`. Причина: scheduled-publish (`--at`) deferred to a follow-up feature per Decision 13 (queue schema хранит `fire_at=null`, в Task 8 нет scheduled-publish пути). Тест на past-date rejection не существует в MVP — gate не должен это помечать как `failed`.
- AC14 (crash recovery) — покрыт `integration/test_crash_recovery.py`; gate проходит, если тест зелёный.
- AC15 (RequiresMountsFor блокирует поллер) — покрыт `test_required_mounts_for.py` (Ansible render check). Полная семантика — только на VM, но рендер-проверки достаточно для pre-deploy.
- AC-T2 (no regression in existing services) — `not_verifiable` без VM; проверять только наличие зелёных тестов workspace-manager + telegram-ai-agent suites (AC-T5).
- AC-T3 (file modes), AC-T4 (umount → restart fails) — `not_verifiable` без VM.
- AC1 переинтерпретирован per Decision 13 — проверять, что `test_topic_filter.py` покрывает все 3 negative + 1 positive кейс, а не slash command.
- E2E `tests/e2e/test_publish_smoke.sh` — `not_verifiable` (требует живого окружения); этот тест прогоняется в Task 14 (post-deploy verification).

**Implementation hints:**
- Использовать схему JSON-отчёта из skill'а: топ-уровень `status`, `summary`, `testSuite`, `acceptanceCriteria[]`, `coverage`, плюс добавить `recommendation` и `notes`.
- Для каждого AC указывать `id` (e.g. "AC1", "AC-T7"), `status`, `severity` (для failed), `evidence` (test name + brief log line / file:lineno), `notes` (если `not_verifiable` — почему).
- При множестве `not_verifiable` AC (ожидаемо для этой фичи — AC2/AC8/AC9/AC11/AC15/AC-T2/AC-T3/AC-T4) — отчёт всё равно может быть `status: passed`, если все остальные `passed` и тестовый сьют зелёный. AC9 — особый случай: scheduled-publish (`--at`) deferred per Decision 13, переедет в follow-up feature, а не в Task 14. Остальные `not_verifiable` AC переходят в scope Task 14.
- Если pytest падает на каком-то конкретном тесте — НЕ маскировать (--no-strict, -x на удобный момент). Отчёт должен показать реальный failed-список и блокировать gate.
- Не редактировать никакие production-файлы. Эта задача только читает и пишет один отчёт.

## Reviewers

Не назначены (`reviewers: []`) — эта задача сама ЯВЛЯЕТСЯ ревью-этапом (gate перед деплоем). Её итог — JSON-отчёт, который читает feature-execution orchestrator.

## Post-completion

- [ ] Записать краткий отчёт в `work/content-publish-pipeline/decisions.md` по шаблону: Summary (1-3 предложения: сколько AC `passed`/`failed`/`not_verifiable`, какие тестовые сьюты зелёные), ссылка на `logs/pre-deploy-qa.json`, итоговое gate-решение.
- [ ] Если gate `failed` — описать, к каким задачам (1–8) возвращается фича и почему (какой именно AC / какой упавший тест), не создавать новый код в этой задаче.
- [ ] Если в процессе обнаружено отклонение от спека (например, тест который зелёный, но проверяет не то, что AC требует) — описать в decisions.md и пометить severity.
- [ ] Обновить user-spec/tech-spec ТОЛЬКО если в процессе QA выяснилось, что AC формулировка неточная (редкий случай); в нормальном потоке — нет.
