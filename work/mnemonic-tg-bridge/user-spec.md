---
feature: mnemonic-tg-bridge
work_type: feature
size: M
status: draft
created: 2026-05-12
last_updated: 2026-05-12
source_python: git@github.com:pavel-molyanov/telegram-ai-agent.git
target_language: rust
parent_feature: coding-fabric (consumes this feature's artefact via Ansible role T09)
---

# User Spec — mnemonic-tg-bridge (Rust port of pavel-molyanov/telegram-ai-agent)

## 1. Что делаем

Переписываем `telegram-ai-agent` (Python, `git@github.com:pavel-molyanov/telegram-ai-agent.git`)
на Rust как самостоятельный 7-й репозиторий протокола `mnemonic-tg-bridge`.

Bridge — это transport-слой coding-fabric: принимает Telegram-сообщения в 8 топиках,
для каждого топика поднимает соответствующий CLI-движок (Claude Code / Codex)
с per-task isolated worktree, разрешённым через `workspace-manager` HTTP API,
и форвардит stdout/stderr движка обратно в топик.

## 2. Зачем

- **Language alignment**: остальные протокольные репозитории (`mnemonic-core`,
  `mnemonic-mcp`, `mnemonic-wasm`) — Rust. Bridge на Python создаёт дополнительный
  стек-сплит и cognitive load.
- **Performance/footprint**: компилируемый бинарь без runtime интерпретатора; ниже
  RAM/CPU footprint на VM.
- **Single binary deploy**: Ansible-роль ставит один файл вместо `pip install`
  цепочки зависимостей.
- **Stronger guarantees**: Rust type system + ownership убирает классы багов
  (race conditions при cwd-resolution lifecycle, leaked process handles), которые
  типичны для Python async кода.

## 3. Open questions (требуют интервью / исследования)

Прежде чем эта user-spec может стать `approved`, надо ответить на:

1. **API surface к workspace-manager**: какие ровно вызовы делает Python-версия в
   текущей реализации? Нужно прочитать `pavel-molyanov/telegram-ai-agent` исходники.
2. **Telegram crate выбор**: `teloxide` (high-level, активная разработка) или
   `grammers` (low-level, ближе к MTProto)? Что есть в Python-версии и как это
   мапится на Rust?
3. **Engine spawn lifecycle**: как Python-версия обрабатывает таймауты, crashes,
   операторские `/cancel`? Поведенческая совместимость нужна или мы переосмысливаем?
4. **Config schema**: формат per-topic config (YAML?), поддерживаемые поля,
   обратная совместимость с Python-конфигами или break-change?
5. **Stdio forwarding**: как Python форвардит длинные ответы движка обратно?
   Stream-and-edit-message vs N сообщений? Что от этого ждать в Rust?
6. **Mid-conversation state**: помнит ли bridge контекст между сообщениями в топике,
   или каждый сюжет — отдельный engine launch?
7. **Crate dependencies**: каких ещё нужно (tokio, reqwest, serde, tracing) и в
   каких версиях?
8. **Tests**: какие интеграционные тесты есть в Python-версии и можем ли мы их
   портировать as-is (через тот же набор моков)?
9. **CI cross-compile**: где собираем релизный binary (x86_64-unknown-linux-gnu для
   Hetzner VM)? GitHub Actions, cross-rs, или custom?
10. **Versioning + Ansible release**: как coding-fabric T09 (Ansible role) узнаёт
    про новую версию? Tag-on-release → release URL в Ansible defaults?

## 4. Initial acceptance criteria (TBD до закрытия open questions)

- [ ] AC1. Bridge — single static binary, cross-compiled для `x86_64-unknown-linux-gnu`
- [ ] AC2. Telegram-сообщение в любом из 8 топиков порождает engine launch с правильным
       `--cwd` (разрешённым через `workspace-manager`)
- [ ] AC3. `cwd: "DYNAMIC"` config sentinel реализован, поведенчески совместим с
       Python-версией
- [ ] AC4. Engine exit / `/cancel` / timeout вызывают `DELETE /worktree/{task_id}`
       в `finally` блоке (no orphans guarantee)
- [ ] AC5. 409 capacity ответы surface'ятся пользователю в топик с понятным
       сообщением
- [ ] AC6. Все логи проходят через sanitizer (тот же regex set что у Python-версии)
- [ ] AC7. Per-topic engine choice: Claude Code default, Codex для `demo-client`
- [ ] AC8. Конфигурация — YAML, schema-совместима или с migration script
- [ ] AC9. Релиз — GitHub release с прикреплённым binary; Ansible role (T09 в
       coding-fabric) тянет binary по URL pinned в defaults
- [ ] AC10. CI собирает binary + smoke test + публикует release на tag push

(дополним после интервью)

## 5. Размер и сроки

S → M в зависимости от ответов на open questions. Грубо: 3–7 дней календарных,
1 разработчик. Не блокирует coding-fabric — T09 в coding-fabric работает в
stub-mode пока bridge не зарелизен.

## 6. Out of scope

- Federation / multi-bot (один бот на одного оператора)
- Public-facing demo
- Mainnet-aware features
- Migration of historical Python-bridge state (мы стартуем с чистого листа)

## 7. Next step

Запустить `user-spec-planning` skill для адаптивного интервью + code-research subagent,
который прочтёт исходники `pavel-molyanov/telegram-ai-agent` и заполнит open questions.
После этого статус → `approved` → можно запускать `/new-tech-spec`.
