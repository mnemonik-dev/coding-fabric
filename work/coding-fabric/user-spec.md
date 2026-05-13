---
feature: coding-fabric
work_type: feature
size: L
status: approved
created: 2026-05-12
last_updated: 2026-05-12
source: spec.md (Mnemonic Protocol Agent-Driven Development Loop v0.1.1)
attestation: 9253b6c0-78ea-4127-9397-8b7875f791b3
solana_tx: 2bFxD4pWQZw87xnQcqVPqgTgfJ7VyLmTNiXbE8zCVpKPC61svZNcwYnTeNMiU3NtWheNdPV5fffHcF6iZkykBebf
arweave_tx: 7wd7o88htpgkubidTv5iYi4CDE8xQaY9cnX6Qwa6EfK1
delivery_v0.2.0: tech-spec.md (IaC + CI via OpenTofu + Ansible + GitHub Actions on Hetzner)
related_features:
  - mnemonic-tg-bridge — Rust rewrite of pavel-molyanov/telegram-ai-agent, separate user-spec at work/mnemonic-tg-bridge/user-spec.md
  - ruflo-mnemonic — backlog, separate user-spec when scheduled
note: |
  user-spec acceptance criteria are delivery-mechanism-agnostic. tech-spec v0.2.0 ships
  these ACs through OpenTofu + Ansible + GitHub Actions (replacing v0.1.1's manual
  operator execution). Networking moved from self-hosted WireGuard to Tailscale; no AC
  changes. See work/coding-fabric/bootstrap-checklist.md for the one-time operator
  setup (~30 min).
---

# User Spec — Coding Fabric (Mnemonic Protocol Agent-Driven Development Loop)

## 1. Что делаем

Развёртываем автономную "coding fabric" — самообслуживающую среду разработки для
Mnemonic Protocol, где единственный интерфейс пользователя — Telegram-форум.

В среде работает фиксированный конвейер:

1. Оператор формулирует намерение (фича, баг, рефакторинг, документация) в Telegram-топике
   соответствующего репозитория или отвечает на алерт.
2. Workspace manager создаёт изолированный git-worktree под TASK-ID.
3. telegram-ai-agent поднимает локальный CLI-движок (Claude Code по умолчанию, Codex
   для `demo-client`) с подмонтированным cwd=worktree.
4. Молянов-методология ведёт работу через стандартные фазы: `user-spec` →
   `tech-spec` → `tasks` → TDD-реализация → код-ревью → QA → merge.
5. ruflo выступает substrate-слоем: AgentDB-память, SONA-обучение, swarm-оркестрация,
   hooks, плагин-инструменты, observability. Молянов-валидаторы делегируют
   проверки соответствующим ruflo-плагинам (skeptic→jujutsu, security-auditor→security-audit,
   post-deploy-qa→browser).
6. Локальный Mnemonic MCP-сервер подписывает каждое значимое решение, формируя
   per-feature DAG аттестаций (dog-food).
7. fabric-watchdog раз в 5 минут сканирует здоровье среды и постит аномалии в `ops`-топик;
   `/turn-into-task` превращает алерт в Kaneo-карточку.
8. Сам репозиторий `mnemonic-loop` (фабрика) мерджит свои PR через ту же фабрику с
   safe-mode rollback и автоматическим тегом last-known-good.

Все компоненты живут на одной cloud-VM, к ней доступ только через VPN.

## 2. Зачем

- Один оператор должен поддерживать развитие шести репозиториев Mnemonic Protocol
  без потери качества и без скрытых решений.
- Каждый артефакт (user-spec, tech-spec, task, pre-deploy QA, deploy) должен иметь
  криптографически подтверждённую родословную — Mnemonic Protocol "ест собственный корм".
- Оперативные мелочи (orphaned worktrees, hung tmux, истёкший Solana RPC, утечка диска,
  устаревший last-known-good тег, провалившаяся аттестация) должны автоматически
  становиться задачами через ту же фабрику, а не теряться в Slack-сообщениях.
- Оператор должен иметь возможность работать с телефона: запустил фичу, ушёл —
  пайплайн отрабатывает и репортит обратно в топик.

## 3. Пользователь

Один человек (project owner). Работает только через Telegram-форум. VPN + SSH —
только для административных аварийных задач (например, ручной rollback в safe-mode).

## 4. Объём работы (scope)

### 4.1 In scope (v0.1)

Шесть репозиториев:
- `mnemonic-core`
- `mnemonic-mcp`
- `mnemonic-wasm`
- `mnemonic-demo-client`
- `mnemonic-docs`
- `mnemonic-loop`

Восемь Telegram-топиков:
- `core`, `mcp`, `wasm`, `demo-client`, `docs`, `loop`, `protocol-qa`, `ops`

Пять точек аттестации Mnemonic (формируют per-feature DAG):
1. `user-spec` approved → memory
2. `tech-spec` approved → memory, parent = user-spec
3. task completion → receipt, parent = tech-spec
4. pre-deploy-qa pass → receipt, parent = task receipts
5. deploy + post-deploy verification → agent.state, parent = pre-deploy

Девять классов алертов fabric-watchdog:
- orphaned worktrees
- hung tmux
- expired Solana RPC
- exhausted Irys
- stale ruflo swarms
- disk pressure (пороги 75/85/90% на 50 GB-бюджет под worktrees)
- master clone drift
- stale PRs
- failed Mnemonic attestation
- stale last-known-good tag

Глью-компонент (новый): `workspace-manager` — управление per-task worktree
под `~/code/mnemonic-workspaces/<TASK-ID>` с shared sccache, лимитом 10 активных,
динамическим resolver-API для Telegram-agent (`cwd:"DYNAMIC"` sentinel) и cleanup.

### 4.2 Out of scope (v0.1)

- `ruflo-mnemonic` плагин (отдельный future plugin, backlog)
- multi-operator / федерация
- Solana mainnet (только devnet)
- публичная демо-фабрика
- PM-агент уровня OpenClaw
- VM High Availability

## 5. Acceptance criteria

### 5.1 Telegram-интерфейс
- AC1. Оператор может из любого из 8 топиков отправить намерение и инициировать полный
  pipeline без выхода из Telegram.
- AC2. Все статус-обновления каждой фазы пайплайна (user-spec, tech-spec, task, QA, deploy)
  возвращаются в исходный топик.
- AC3. `ops`-топик работает независимо от `workspace-manager`/`ruflo`/`watchdog`:
  если упал любой из этих компонентов — ops-топик продолжает принимать команды.

### 5.2 Routing и workspace
- AC4. Каждая активная задача имеет собственный worktree в
  `~/code/mnemonic-workspaces/<TASK-ID>`, изолированный sccache используется как shared.
- AC5. Одновременно поддерживается до 10 активных worktree; превышение блокируется.
- AC6. `telegram-ai-agent` корректно разрешает `cwd:"DYNAMIC"` в реальный путь
  worktree через HTTP API `workspace-manager`.
- AC7. Cleanup удаляет worktree и его `.env` при завершении/отмене задачи.

### 5.3 Pipeline и подмена движков
- AC8. Топик `demo-client` использует Codex CLI; остальные семь топиков — Claude Code.
- AC9. `/do-feature` диспатчится через ruflo swarm MCP-инструменты (`swarm_init`,
  `agent_spawn`) с per-task cwd injection.
- AC10. Молянов-валидаторы делегируют к ruflo-плагинам:
  skeptic → jujutsu, security-auditor → security-audit, post-deploy-qa → browser.

### 5.4 Mnemonic attestation
- AC11. Каждое утверждённое `user-spec` создаёт memory-аттестацию. [DEFERRED to backlog: mnemonic-attestation-integration]
- AC12. Каждое утверждённое `tech-spec` создаёт memory-аттестацию с `parent = user-spec`. [DEFERRED to backlog: mnemonic-attestation-integration]
- AC13. Каждая завершённая задача создаёт receipt-аттестацию с `parent = tech-spec`. [DEFERRED to backlog: mnemonic-attestation-integration]
- AC14. Каждый pre-deploy-qa pass создаёт receipt-аттестацию с `parent = task receipts`. [DEFERRED to backlog: mnemonic-attestation-integration]
- AC15. Каждый deploy + post-deploy verification создаёт agent.state-аттестацию
  с `parent = pre-deploy`. [DEFERRED to backlog: mnemonic-attestation-integration]
- AC16. По любой фиче можно построить tamper-evident DAG из пяти узлов. [DEFERRED to backlog: mnemonic-attestation-integration]

### 5.5 QA-гейты по task_type
- AC17. Schema/CBOR-изменения требуют cross-platform byte-equivalence proof и human approval.
- AC18. Изменения crypto-примитивов требуют third-party verification proof.
- AC19. Изменения MCP surface требуют client compatibility tests.
- AC20. WASM-bindings требуют browser test + byte-equivalence.
- AC21. Full-mode integration требует devnet round-trip + independent verifier recovery.
- AC22. Bump crypto-crate dependency требует human approval gate.
- AC23. Изменения `mnemonic-loop` требуют smoke-test gate (см. §5.8).
- AC24. PR-шаблон содержит четыре обязательных секции: what changed, automated validation,
  protocol conformance, mnemonic attestation. Cap 500 строк на PR.

### 5.6 Cohabitation
- AC25. `ruflo-autopilot` выключен в `core`, `mcp`, `wasm`, `protocol-qa`, `loop`;
  включён в `docs`, `demo-client`.
- AC26. `MEMORY_NAMESPACE` зафиксирован per topic; ruflo-rag-memory выключена в `protocol-qa`.
- AC27. ruflo-aidefence выключена в `core`, `mcp` (ложные срабатывания на hex keys).
- AC28. Project Knowledge молянов-методологии остаётся канонической; ruflo-substrate
  не перезаписывает её.

### 5.7 Watchdog и operational toil
- AC29. `fabric-watchdog` запускается каждые 5 минут.
- AC30. Все 9 классов алертов из §4.1 покрыты и постятся в `ops`-топик.
- AC31. `/turn-into-task` конвертирует алерт в Kaneo-карточку, привязанную к нужному репо.
- AC32. Disk-pressure алерты используют пороги 75/85/90% от бюджета 50 GB.

### 5.8 Recursive self-hosting
- AC33. PR в `mnemonic-loop` мерджатся через ту же фабрику.
- AC34. После каждой успешной non-loop задачи обновляется git-тег `last-known-good`.
- AC35. Перед мерджем любого `mnemonic-loop` PR прогоняется smoke-gate: тривиальная docs-task
  end-to-end на кандидатной фабрике, бюджет 15 минут.
- AC36. Существует `fabric-safe-mode` rollback-скрипт (через SSH): останавливает сервисы,
  checkout last-known-good, rebuild, restart, нотификация в `ops`-топик.
- AC37. Сценарий "deliberate break" из Phase 5 успешно прогоняется и доказывает,
  что rollback работает.

### 5.9 Secrets и trust boundaries
- AC38. Все секреты живут в Vaultwarden на VM; `.env` генерируется per-worktree и удаляется
  при cleanup.
- AC39. Не существует mainnet-ключа Solana ни в одной системе фабрики (только devnet).
- AC40. Sanitized logging filter удаляет:
  - base58 ключи
  - JWK fragments
  - токены формата `sk-`, `api_`, `ANTHROPIC_`
  - Telegram file URLs
- AC41. Агенты имеют широкие локальные права внутри своего worktree, но не имеют права:
  писать вне worktree, читать Vaultwarden напрямую, пушить в `main`, инициировать
  production-deploy.

### 5.10 Backups
- AC42. Ночной бэкап VM-состояния выполняется автоматически и проверяется (test restore
  как минимум один раз во время Phase 0).

## 6. Manual user actions (one-time setup)

- Provision cloud VM (≥ 8 vCPU, ≥ 32 GB RAM, ≥ 200 GB disk).
- Поднять VPN, добавить оператора.
- Создать Telegram-бота, форум, 8 топиков.
- Установить Vaultwarden, ввести учётные записи (Solana devnet, Irys testnet, Telegram bot,
  Anthropic API, OpenAI/Codex API, GitHub, прочие).
- Клонировать master-копии 6 репозиториев в выделенный каталог.
- Зарегистрировать Solana devnet wallet, профондировать Irys testnet.
- Поставить ruflo (full CLI install), молянов-инструменты, локальный Mnemonic MCP-сервер.
- Закрепить `MEMORY_NAMESPACE` per topic.
- Прогнать Phase 2 end-to-end smoke на тестовой фиче.

Recurring (по факту работы):
- Утверждать human-gated PR (schema/CBOR, crypto-crate bump).
- Реагировать на ops-алерты через `/turn-into-task` или ручной разбор.
- Раз в Phase 5 — провести deliberate break + safe-mode rollback exercise.

## 7. Deploy approach

- Один cloud VM, VPN-fronted. SSH открыт только через VPN.
- Фабрика как монорепо-инфраструктура: docker-compose / systemd на VM (детали в tech-spec).
- "Деплой" в смысле production не существует — protocol-qa работает в режиме
  `MNEMONIC_MODE=full` против Solana devnet + Arweave testnet.
- Backups ночные.
- ops-топик и Telegram-бот служат always-available admin back-channel и должны
  переживать падение любого другого компонента.

## 8. Verification (что можно проверить и когда)

### 8.1 Без деплоя (в процессе разработки)
- Локальная сборка `workspace-manager` (HTTP API, worktree create/destroy).
- Mock-вызовы Telegram-agent против локального workspace-manager API.
- Локальная подпись через Mnemonic MCP-сервер.
- Локальный прогон smoke-gate сценария на тестовой docs-задаче.

### 8.2 На VM (после Phase 0)
- VPN + SSH дают доступ только оператору.
- Vaultwarden и Kaneo доступны через VPN.
- Telegram-бот отвечает в `ops`.

### 8.3 End-to-end (Phase 2)
- Создаётся тестовая фича в одном из топиков.
- Полный пайплайн (user-spec → tech-spec → tasks → TDD → review → QA → merge) отрабатывает
  без вмешательства оператора кроме approval gate.
- Все пять Mnemonic-аттестаций созданы и связаны в DAG.
- Worktree cleanup отработал.

### 8.4 Watchdog (Phase 5)
- Запуск `fabric-watchdog` показывает алерты в `ops` для всех 9 классов (поднимаются
  искусственно для теста).
- `/turn-into-task` создаёт Kaneo-карточку.

### 8.5 Safe-mode (Phase 5)
- "Deliberate break" сценарий: вносим заведомо ломающее изменение в `mnemonic-loop`,
  система откатывается на `last-known-good` через `fabric-safe-mode` скрипт, нотификация
  падает в `ops`.

## 9. Implementation phases (источник — spec.md §11)

- Phase 0 (d1-3): VM, VPN, Vaultwarden, Kaneo, Telegram forum, master clones, sanitized logging, backups.
- Phase 1 (d4-6): workspace-manager + sccache, telegram-ai-agent `DYNAMIC` sentinel patch.
- Phase 2 (d7-9): ruflo + молянов + Mnemonic совмещены в одной сессии; end-to-end smoke.
- Phase 3 (d10-13): `/do-feature` ↔ ruflo swarm bridge с per-task cwd injection.
- Phase 4 (d14-16): `task_type` field, PR template enforcement, byte-equivalence + MCP
  compatibility harnesses.
- Phase 5 (d17-21): fabric-watchdog, `/turn-into-task`, last-known-good auto-update,
  fabric-safe-mode (deliberate-break test), smoke-gate, первый recursive self-merge.

Итого ~21 день.

## 10. Risks

- R1. **Recursive self-hosting bricking the fabric.** Митигация: smoke-gate + last-known-good +
  fabric-safe-mode + независимый `ops`-топик.
- R2. **Sanitization regex пропускает секрет.** Митигация: tests с известными secret-формами,
  периодический аудит.
- R3. **Single operator burnout.** Митигация: вся операционная нагрузка превращается в
  задачи через `/turn-into-task`; нет ожидания живой реакции от оператора.
- R4. **ruflo/молянов конфликт.** Митигация: per-topic feature-toggles
  (autopilot/aidefence/rag-memory), Project Knowledge молянова — канонический источник.
- R5. **Solana RPC / Irys testnet outage блокирует protocol-qa.** Митигация: алерты в `ops`,
  ручное переключение endpoint, не блокирует другие топики.
- R6. **Disk pressure при 10 параллельных worktrees + sccache.** Митигация: пороги 75/85/90%
  на 50 GB-бюджет, авто-cleanup завершённых задач.

## 11. Open questions (вне scope v0.1)

- Когда переход на ruflo-mnemonic плагин (отдельный backlog).
- Когда введение второго оператора (требует пересмотра MEMORY_NAMESPACE и Vaultwarden ACL).
- Когда добавление mainnet и каких политик утверждения.
- **(2026-05 scope change)** Mnemonic MCP attestation integration (AC11–AC16,
  tech-spec D14, §2.6) is descoped from coding-fabric v0.2.x and moved to the
  backlog feature `mnemonic-attestation-integration` (see
  `work/mnemonic-attestation-integration/`). Rationale: the local Mnemonic MCP
  server is not yet production-ready. coding-fabric deploys cleanly without
  it; the role `mnemonic-mcp` is gated by `mnemonic_mcp_enabled` (default
  `false`) and installs only no-op stub hooks until re-enabled.
