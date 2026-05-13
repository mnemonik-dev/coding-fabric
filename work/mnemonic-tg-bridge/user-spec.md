---
feature: mnemonic-tg-bridge
work_type: feature
size: S
status: approved
created: 2026-05-12
last_updated: 2026-05-12
upstream_origin: git@github.com:pavel-molyanov/telegram-ai-agent.git
fork_repo: git@github.com:mnemonik-dev/telegram-ai-agent.git
upstream_license: MIT
parent_feature: coding-fabric (consumes via Ansible role T09)
delivery_strategy: own-the-fork (mnemonik-dev/telegram-ai-agent is the source of truth)
upstream_sync_strategy: periodic `git fetch upstream main` + manual rebase as needed; no PR ceremony
note: |
  Decision history:
  - v0.0: Rust rewrite (size L) — cancelled.
  - v0.1: upstream PR to pavel-molyanov (size S, generic feature) — cancelled in favor of own-the-fork.
  - v0.2 (current): own-the-fork. Feature lives on mnemonik-dev/telegram-ai-agent:main as the
    canonical source. coding-fabric T09 Ansible role pins to this fork. We can add
    Mnemonic-specific patches later without upstream-acceptance ceremony. Pavel's
    upstream remains a read-only sync source for bug fixes.
---

# User Spec — mnemonic-tg-bridge (upstream PR for cwd:"DYNAMIC" sentinel)

## 1. Что делаем

Реализуем небольшую feature в апстриме `pavel-molyanov/telegram-ai-agent` (Python):
поддержка опционального сентинела `cwd: "DYNAMIC"` в per-topic config, который
при engine-spawn разрешается в реальный путь через внешний HTTP API. Фича
generic: никакой Mnemonic-специфики, только "external HTTP resolver for per-task cwd",
управляется новой env-переменной `TELEGRAM_AI_AGENT_CWD_RESOLVER_URL` и optional
per-topic field.

Доставка: pull request в upstream. Если PR не принимается / стопорится >30 дней —
fallback на форк `mnemonic-org/telegram-ai-agent` с тем же патчем, к которому
coding-fabric пинится в Ansible-роли T09.

## 2. Зачем

- coding-fabric user-spec AC6 требует, чтобы `telegram-ai-agent` разрешал
  `cwd: "DYNAMIC"` через `workspace-manager` HTTP API. Без этого
  per-task worktree isolation на уровне Telegram-сообщения не работает.
- Альтернатива (drop AC6 → static cwd, только swarm-bridge делает worktree
  isolation) была отвергнута: оператор хочет per-message изоляцию для
  безопасности и repeatability.
- Rewrite на Rust был отвергнут: 80+ Python-файлов upstream'а — это
  значительный объём работы; апстрим активно поддерживается; апстрим-фича
  полезна и другим пользователям бота.

## 3. Пользователь

- Прямой: оператор coding-fabric.
- Косвенный: любой пользователь `pavel-molyanov/telegram-ai-agent`, кому нужна
  динамическая cwd-резолюция (per-task sandbox, ephemeral environments,
  CI-driven bot deployments).

## 4. Объём (scope)

### 4.1 In scope

- Расширение `topic_config.py`: валидация поля `cwd`, признание строки
  `"DYNAMIC"` как сентинела (не пути)
- Расширение `topic_runtime.py`: при engine spawn — если cwd == DYNAMIC, делать
  HTTP POST к resolver-URL, использовать возвращённый путь
- Новый модуль `workspace_resolver.py` (или аналогично) с httpx-клиентом:
  POST + DELETE контракт идентичен coding-fabric workspace-manager API
  (§2.5 в coding-fabric tech-spec)
- Cleanup в `finally`-блоке: DELETE при engine exit / `/cancel` / timeout / crash
- Обработка ошибок resolver:
  - 409 capacity → friendly message в исходный топик ("workspace pool busy, try
    again later"), engine не спавнится
  - 5xx / timeout → retry с экспоненциальным backoff, max 3 попытки
  - resolver-URL не задан, но cwd=DYNAMIC → fail-fast с понятной ошибкой
- Unit-тесты: positive path + 409 + 5xx + cleanup + idempotency
- Документация апстрима: README.md (англ + ru) обновлены, architecture.md описывает
  новую опцию
- Pre-PR проверки: апстрим CI зелёный, code style соблюдён
- Open the PR, итерации по review (max 3-4 круга)

### 4.2 Out of scope

- Любые Mnemonic-специфичные изменения в Python-коде (бот остаётся generic)
- Rewrite в Rust (закрыто решением "reuse upstream")
- Изменения tmux execution mode, `/tui`, voice transcription, bot-MCP-server,
  session resume, photo/document/forward batching, modal detection
- Per-topic config schema beyond добавления одного опционального поля
- Изменение поведения когда cwd — обычный путь (back-compat обязательна)

## 5. Acceptance criteria

### 5.1 Implementation
- AC1. `cwd: "DYNAMIC"` в topic config принимается валидатором без error;
  существующие топики со статическим cwd работают как раньше (back-compat)
- AC2. При engine spawn в DYNAMIC-топике делается POST к
  `${TELEGRAM_AI_AGENT_CWD_RESOLVER_URL}/worktree` с
  `{task_id, repo, base_ref, topic}` (поля сериализуются из контекста сообщения)
- AC3. Cleanup: при engine exit / `/cancel` / timeout / crash делается
  `DELETE /worktree/{task_id}` в `finally`-блоке. No worktree leak invariant
  покрыт тестом.
- AC4. Resolver 409 (capacity) → бот посылает в топик "workspace pool busy, try
  again later" (текст конфигурируем через locale файл) и НЕ спавнит engine
- AC5. Resolver 5xx / timeout → 3 retries с backoff (1s, 2s, 4s); после неудачи
  сообщение в топик с диагностикой
- AC6. `cwd=DYNAMIC` без заданного `TELEGRAM_AI_AGENT_CWD_RESOLVER_URL` → fail-fast
  при загрузке топик-конфига с понятным error message
- AC7. Logging: все HTTP вызовы логируются на DEBUG; токены и URL'ы НЕ
  утекают на INFO/выше (sanitizer-friendly, генерик)

### 5.2 Tests
- AC8. Unit-тесты покрывают: positive path, 409, 5xx + retry, timeout + retry,
  cleanup в finally, idempotency (повторный DELETE того же task_id → 404 терпимо)
- AC9. Один интеграционный тест с реальным httpx mock-сервером, проверяющий
  end-to-end resolution + cleanup

### 5.3 Upstream PR
- AC10. Документация: README.md (en + ru) описывает новую опцию;
  `.claude/skills/project-knowledge/references/architecture.md` обновлён
- AC11. CI upstream'а зелёный на форк-ветке
- AC12. PR открыт в `pavel-molyanov/telegram-ai-agent`, заголовок и описание по
  стилю апстрима; PR-тело включает: motivation, design summary, тесты,
  back-compat note
- AC13. До 3 раундов review-итераций — все upstream-комментарии адресованы
- AC14. PR merge'нут — есть release tag (или зафиксирован коммит на main)

### 5.4 Fallback (на случай если PR stallит)
- AC15. Если PR без movement > 30 дней с момента открытия:
  - создаём fork `mnemonic-org/telegram-ai-agent` (или эквивалент)
  - cherry-pick патча на fork
  - tag релиз `v<upstream-base>+mnemonic.1`
  - pin coding-fabric T09 Ansible role на этот форк
  - upstream PR остаётся открытым; при merge'е возвращаемся на upstream

### 5.5 Coding-fabric consumption (cross-feature AC)
- AC16. coding-fabric T09 Ansible role успешно ставит бот с pinned commit,
  включающим эту фичу, и `cwd: "DYNAMIC"` сентинел работает end-to-end
  на VM (проверяется coding-fabric T13 e2e-smoke)

## 6. Manual user actions

Однократно при подготовке PR:
- Fork pavel-molyanov/telegram-ai-agent → mnemonic-org/telegram-ai-agent (или
  собственный fork оператора)
- Set up GitHub PAT с write-доступом к форку (нужен для CI-теста)
- Связаться с pavel-molyanov через GitHub issue или Telegram (опционально, но
  повышает шансы принятия PR)

Постоянных операторских действий нет — после merge'а в upstream фича работает
автоматически.

## 7. Deploy approach

Этот feature не имеет собственного deploy-target — он встраивается в upstream.
"Деплой" = успешный PR merge или fallback fork release.

После этого coding-fabric T09 Ansible role pin'ит соответствующий commit/tag
и бот ставится `git clone + uv sync` на VM.

## 8. Verification (что и когда можно проверить)

### 8.1 Локально (на ноутбуке оператора, без VM)
- `uv sync` в форке; `pytest tests/` — все тесты зелёные
- Запуск бота локально против моков (Telegram mock + workspace-manager mock);
  отправка сообщения → engine spawn → cleanup
- `ruff` / `mypy` upstream'овский — без новых нарушений

### 8.2 В CI upstream'а (после открытия PR)
- Зелёный workflow run на форк-ветке
- Code review feedback по комментариям

### 8.3 На coding-fabric VM (после T09 деплоя)
- E2E smoke (coding-fabric T13): отправка test-сообщения в один из 8 топиков
  → workspace-manager регистрирует worktree → engine spawn в этом cwd →
  движок отвечает → engine exit → worktree удалён
- Watchdog (coding-fabric T18) проверяет orphaned-worktrees как один из 9
  классов алертов — invariant контроль на стороне fabric'а

## 9. Implementation timeline

- День 1: fork + изучение upstream architecture.md + topic_config.py / topic_runtime.py
- День 2: реализация workspace_resolver.py + патчи в topic_config / topic_runtime
- День 3: тесты + документация + локальный smoke
- День 4: PR опубликован; начинаются review-итерации
- День 5-14: review-итерации (зависит от responsiveness апстрима)
- День 15+: либо merge, либо ожидание; на 30 день — fallback fork

Total estimate: S (1-2 недели), если PR быстро принимают.

## 10. Risks

| # | Risk | Mitigation |
|---|------|------------|
| R1 | Pavel отвергает фичу как too-niche | Pre-PR обсуждение через issue; preserved fork path как fallback |
| R2 | PR stallит без явного отказа | AC15 — 30-day timeout → fork |
| R3 | Upstream вводит конкурирующее изменение в `topic_config.py` | Регулярный rebase на upstream/main; если merge-conflict — переписать |
| R4 | Review требует архитектурного передела фичи | Готовы к итерациям; для S-объёма терпимо |
| R5 | Coding-fabric T09 застывает в stub-mode пока бридж не merge'нут | T09 stub-mode уже спроектирован graceful (см. coding-fabric tasks/09.md) |
| R6 | После merge'а бот ломается в обратной совместимости | AC1 + back-compat тесты должны это поймать на PR-фазе |

## 11. Out of scope (v0.1)

- Multi-resolver (1 URL — достаточно)
- WebSocket / long-poll resolver (HTTP — достаточно)
- Per-topic resolver override (один глобальный URL — достаточно)
- Authentication между бот'ом и resolver'ом (предполагается loopback или tailnet,
  то есть trust-by-network)
- Перенос фичи в `.claude/skills/topic-setup/` бот-скилл (опционально, может
  быть позже)

## 12. Out of scope (cancelled from original v0.0 stub)

Старая user-spec предполагала Rust rewrite размером L. Отменено решением "reuse
upstream as-is". Соответствующие пункты больше не актуальны:
- Rust skeleton + Telegram crate выбор
- Engine spawner на Rust
- Single-binary deploy
- Cross-compile под x86_64-unknown-linux-gnu

Эти пункты могут вернуться, если upstream становится неподдерживаемым (R2 / R3
переходит в "fork forever") — но это решение out of scope здесь.
