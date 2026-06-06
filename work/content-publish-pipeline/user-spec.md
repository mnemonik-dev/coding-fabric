---
created: 2026-06-06
status: draft
type: feature
size: M
---

# User Spec: content-publish-pipeline

## Что делаем

Добавляем в coding-fabric новый pipeline для генерации и публикации контента в TG-канал @mnemonik. Оператор пишет в Telegram `/publish_content <бриф>`, отдельный worker-сервис на VM спавнит claude с подгруженным skill suite `claude-blog`, claude исследует и пишет статью, бот шлёт оператору preview точно в том виде, в каком статья появится в канале, с inline-кнопками `[✓ Опубликовать]` / `[✗ Отклонить]`. После подтверждения (или 5-минутного timeout) `mnemonik-blogger` CLI публикует пост в @mnemonik и регистрирует attestation через локальный Mnemonik MCP сервер — после этого статья recallable через `mnemonic_recall`. Поддерживается отложенная публикация (`--at <ISO>`).

## Зачем

Сейчас каждый пост о Mnemonik пишется и публикуется вручную: ресёрч, оформление под формат TG, постинг. Это блокирует регулярную коммуникацию с комьюнити. Цель — оператор формулирует один **бриф** в TG и получает готовую статью, которая после быстрой апрувки уходит в канал и автоматически становится частью verifiable memory протокола (recallable через Mnemonik MCP — это **продуктовый use-case** самого Mnemonik, eating own dogfood).

## Как должно работать

**Иммедиэйт сценарий:**

1. Оператор в личке боту: `/publish_content Объясни, почему Mnemonik нужен AI-агентам и в чём отличие от обычных vector stores`
2. Бот: «Принял. Бриф в очереди (id `abc12345`). Жду статью.»
3. `content-publisher.service` берёт задачу из очереди, спавнит claude с подгруженным `claude-blog` skill suite в `/var/lib/content-publisher/work/abc12345/`, claude пишет `article.md`.
4. Worker запускает `analyze_blog.py` от claude-blog. Скор `>= 80` (`MNEMONIK_MIN_SCORE` из env) → переходим к preview. Скор `< 80` → worker re-spawn'ит claude с фидбэком от analyze_blog как rewrite-инструкцией; до 3 попыток.
5. После прохождения quality gate worker возвращает статью боту. Бот рендерит её **ровно как она появится в @mnemonik** (включая разбиение на нить, если >4096 символов), отправляет оператору в личку с inline-клавиатурой:
   ```
   [✓ Опубликовать] [✗ Отклонить]
   ```
6. Оператор кликает `[✓ Опубликовать]` ИЛИ молчит 5 минут (`PUBLISH_APPROVAL_TIMEOUT_MIN`, env-конфигурируемый) → бот вызывает `mnemonik-blogger post --from-file article.md --platforms telegram --live`. blogger постит в @mnemonik (через **отдельный** Telegram-бот `@mnemonik_publisher_bot`, который оператор сам создаёт через @BotFather и делает админом канала), регистрирует attestation в локальном Mnemonik MCP через `mnemonic_sign_memory(content=article_body, metadata={post_url, score, prompt})`.
7. Бот заменяет preview-сообщение на: «Опубликовано. Пост: <link>. Receipt: <hash>. Recallable через mnemonic_recall(`<hash>`).»

**Отложенный сценарий:**

1. Оператор: `/publish_content --at 2026-06-10T10:00 <бриф>`
2. Бот добавляет в очередь job с `fire_at=2026-06-10T10:00`. Очередь — JSONL-файл на персистентном volume (`/mnt/HC_Volume_*/content-publisher/queue.jsonl`), переживает destroy-recreate VM.
3. `content-publisher.service` каждые ~30 секунд проверяет очередь и фаерит overdue jobs. Дальше — как иммедиэйт сценарий.

**Отказ оператора:**
- Клик `[✗ Отклонить]` → бот удаляет preview, статья (worktree job) сохраняется ещё 24 часа для возможного дебага, потом удаляется. Attestation НЕ создаётся. Бот: «Отклонено. Бриф снова доступен в очереди для повтора.»

**Autonomous-режим (после периода валидации):**
- `PUBLISH_MODE=auto` в `/etc/blogger.env` — этап approval пропускается полностью. После quality gate `>= min_score` сразу публикуется. Оператор получает уведомление по факту, не до.

## Критерии приёмки

- [ ] **AC1 — slash-команда регистрируется**: `/publish_content` появляется в bot commands list (`bot_commands.py`), Telegram'овский setMyCommands не падает (формат имени `[a-z0-9_]+`).
- [ ] **AC2 — Kaneo/Symphony НЕ задействованы**: после `/publish_content` нет нового тикета в Kaneo (verify: `GET /api/project/<id>` returns same tasks count) и Symphony `/worktrees` не показывает новых записей.
- [ ] **AC3 — worker спавнит claude с claude-blog skill**: в `/var/lib/content-publisher/work/<id>/` появляется `article.md` написанный claude; в логах `content-publisher.service` видно `Running claude with --skill claude-blog` (или эквивалент по факту реализации).
- [ ] **AC4 — quality gate работает**: при подделке низкого score (mock analyze_blog → 50) job уходит на rewrite; после 3 неудачных попыток оператор получает уведомление «3 попытки исчерпаны, последняя версия в /var/lib/.../article.md, причины: <feedback>».
- [ ] **AC5 — preview соответствует финальному посту**: текст в preview-сообщении бота идентичен (включая форматирование, переносы строк, ссылки) тому, что blogger потом постит в @mnemonik.
- [ ] **AC6 — inline-кнопки работают**: клик `[✓ Опубликовать]` запускает публикацию <30 сек; клик `[✗ Отклонить]` отменяет; 5-минутный timeout без действий = публикация (по умолчанию).
- [ ] **AC7 — Mnemonik MCP attestation**: после успешной публикации `mnemonic_sign_memory` возвращает hash; `mnemonic_recall(<hash>)` возвращает body статьи.
- [ ] **AC8 — scheduled job persists across VM restart**: `/publish_content --at <ISO>` → перезагрузить content-publisher.service → job всё ещё в очереди и фаерится в течение 60 секунд от `<ISO>`.
- [ ] **AC9 — past-date rejection**: `/publish_content --at <past-ISO>` бот отвечает «Время должно быть в будущем» и не добавляет в очередь.
- [ ] **AC10 — spawn failure handling**: при намеренной поломке claude-blog skill path (например, переименуем /opt/claude-blog) worker помечает job как `failed`, шлёт оператору traceback, НЕ ретраит.
- [ ] **AC11 — PUBLISH_MODE=auto**: смена env на `auto` + `systemctl restart content-publisher` → следующая `/publish_content` команда публикует без preview/approval.
- [ ] **AC12 — отдельный publisher-бот**: статья постит `@mnemonik_publisher_bot`, не `@mnemonik_fabric_bot` (operator-бот). В sops лежат два разных токена.

## Ограничения

- **MVP — Telegram канал @mnemonik только.** Discord / Farcaster / Webapp / LinkedIn — отложены в `work/blogger-extra-platforms/` и `work/blogger-site-connector/`. X.com отдельно блокирован: paid API tier ($100/mo) требуется для write access.
- **VM Hetzner НЕ должен пересоздаваться на каждый деплой.** Тасочки #24 и #25 (persistent VM refactor) ещё открыты — текущий CI destroy-recreate'ит VM. Эта фича работает корректно при обоих сценариях (очередь и attestations лежат на персистентном volume, blogger stateless), но deploy-цикл всё ещё страдает.
- **content-publisher.service отдельный сервис, НЕ интегрируется в Symphony.** Один владелец очереди, без race conditions с poll loop'ом Symphony.
- **mnemonik-dev/blogger upstream pinned to a commit ref** в роли (не `main`) для воспроизводимости.
- **mnemonik-dev/claude-blog upstream pinned to a commit ref** — API `analyze_blog.py` это наш контракт.
- **Default mode = approval+timeout.** Auto-mode опциональный (env flag), но это feature-flag, не дефолт.
- **Self-rewrite loop капнут на 3 retries.** Если LLM зависает на ~one issue, лучше показать оператору и не жечь credits.
- **Inline-кнопки в Telegram имеют 64-байтовый лимит на callback_data**. Используем `publish:approve:<job_id[:8]>` (12 байт max) — fits.

## Риски

- **Риск 1: Mnemonik MCP сервер недоступен в момент публикации.** Пост уйдёт в TG (это commit'нуто, откатить нельзя), но attestation не запишется. **Митигация:** retry-очередь для attestations отдельно от публикации. Если за N часов attestation не записан, оператор получает уведомление; статья остаётся в queue.failed.jsonl для ручного re-sign.
- **Риск 2: claude-blog upstream меняет API `analyze_blog.py`.** Quality gate начинает падать или возвращать неправильный score → массовые блокировки. **Митигация:** pinned commit ref в Ansible role; обновление через явный PR.
- **Риск 3: Оператор отошёл, 5-минутный timeout сработал, плохой пост ушёл в канал.** **Митигация:** 5 минут — дефолт, конфигурируется в `PUBLISH_APPROVAL_TIMEOUT_MIN`. Можно поставить 30 или 60. Удаление поста после публикации — через стандартный Telegram-клиент.
- **Риск 4: Self-rewrite loop жжёт Claude credits.** **Митигация:** hard cap 3 retries; алёрт оператору если >50% jobs за неделю упёрлись в этот cap (показатель проблемы с min_score или с upstream).
- **Риск 5: Telegram rate-limit на posting (30 msg/sec, 1 msg/sec в один канал).** Реально не должно сработать (мы постим редко), но при scheduled-постах оптом — может. **Митигация:** blogger retries с backoff (это уже в blogger upstream); worker сериализует jobs, не параллелит.
- **Риск 6: Уязвимость доступа к publisher-боту.** Если кто-то получит `blogger_telegram_bot_token`, сможет постить от имени @mnemonik. **Митигация:** токен в sops (encrypted-at-rest); отдельный бот от operator-бота (изоляция privilege); ротация через @BotFather при подозрении.

## Технические решения

- Решили **выделить отдельный systemd-сервис `content-publisher.service`** для очереди + worker'a, потому что Symphony (workspace-manager.service) уже владеет своей очередью и состоянием — смешивать нельзя без сильной связки. Отдельный сервис = понятная ownership очереди + изоляция отказов.
- Решили **НЕ задействовать Kaneo и Symphony** в этом pipeline, потому что они дают state-machine + worktree-management, которые здесь не нужны: одна стадия (write), нет PR, нет ревью другим движком, нет коммитов в репо. Лишний overhead = больше поверхности для отладки.
- Решили **очередь хранить как JSONL на персистентном volume** (`/mnt/HC_Volume_*/content-publisher/queue.jsonl`), потому что это переживает destroy-recreate VM (ровно как Kaneo Postgres из тасочки #22). Атомарность append через `os.write` на single line — без локов.
- Решили **claude-blog skill подгружать через path env** (`MNEMONIK_CLAUDE_BLOG_PATH=/opt/claude-blog`), потому что worker сам спавнит claude и передаёт ему skill explicitly. Никакой магии с `~/.claude/skills` symlink'ами.
- Решили **attestation писать через локальный Mnemonik MCP сервер** (он уже задеплоен ролью `mnemonic-mcp`), а не через external API, потому что (а) меньше зависимостей; (б) это product use-case — Mnemonik recall'ит свои собственные публикации.
- Решили **создать отдельный Telegram-бот для публикации** (`@mnemonik_publisher_bot` через @BotFather), потому что operator-бот живёт во внутренних чатах с твоей сессией, а publisher выйдет в публичный канал — разные threat models, разные scope привилегий. Изоляция = бесплатная (5 мин в @BotFather).
- Решили **failure spawn'a claude НЕ ретраить** (Q18=A), потому что 99% таких failures = structural (missing skill file, OOM, путь сломан) — retry не поможет, а маскирует баг.
- Решили **self-rewrite на низком score (Q15=C)** вместо просто блока, потому что analyze_blog даёт concrete feedback что улучшить — естественно скормить обратно claude. Cap 3 retries.
- Решили **MVP только TG-канал**, потому что у тебя есть рабочий токен + права админа, а другие платформы требуют либо денег (X), либо нового адаптера (webapp, LinkedIn), либо просто новых ключей (Discord/Farcaster — отложены, тривиально добавятся позже).

## Тестирование

**Unit-тесты:** делаются всегда, не обсуждаются. Покрытие: queue persist/load, scheduled-fire timing, analyze_blog gate decision logic (mocked), inline-callback handler routing.

**Интеграционные тесты:** делаем — тест очереди (write → kill worker → restart → assert job still there → fires); тест quality-gate (mock claude-blog scorer возвращает 50, проверяем что worker re-spawn'ит до 3 раз и потом фейлит).

**E2E тесты:** делаем (ручной + ограниченный) — `/publish_content <тестовый бриф>` на боевом VM, проверяем что preview приходит, кликаем `[✓]`, верифицируем пост в @mnemonik и `mnemonic_recall(<hash>)`. **Без staging-канала** — реальный канал, реальный пост. Безопасность даёт preview+approval. После успешного раунда — удаляем тестовый пост из канала вручную.

## Как проверить

### Агент проверяет

| Шаг | Инструмент | Ожидаемый результат |
|-----|-----------|-------------------|
| 1. Slash-команда зарегистрирована | `getMyCommands` через Bot API | `publish_content` присутствует в списке для оператора |
| 2. `/publish_content тест` от оператора | `journalctl -u telegram-ai-agent` + `journalctl -u content-publisher` | Бот логит enqueue, worker логит spawn claude |
| 3. Quality gate с высоким score | mock analyze_blog возвращает 90 | Worker НЕ ретраит, отправляет preview боту |
| 4. Quality gate с низким score | mock возвращает 50 | Worker логит rewrite attempt с feedback, цикл до 3 раз |
| 5. Inline `[✓ Опубликовать]` | curl Bot API `answerCallbackQuery` симуляция | blogger CLI запускается, выход 0; пост появляется в @mnemonik (через `getUpdates` или поиск) |
| 6. 5-min timeout без действий | wait 5 min + tail logs | blogger CLI запускается автоматически; пост публикуется |
| 7. Mnemonik MCP attestation | `mnemonic_recall(<hash>)` через MCP | Возвращает body опубликованной статьи |
| 8. Scheduled job survives restart | enqueue с `--at <ISO+10min>`; `systemctl restart content-publisher`; ждать | Job фаерится в течение 60 сек от `<ISO>` |
| 9. Past-date rejected | `/publish_content --at 2020-01-01T00:00 ...` | Бот отвечает ошибкой времени, в queue ничего не добавлено |
| 10. Spawn failure | Переименовать `/opt/claude-blog` → spawn упадёт | Worker помечает job=`failed`, оператор получает Telegram-сообщение с traceback; нет ретраев |

### Пользователь проверяет

- **Preview соответствует финальному виду поста** — оператор глазами сверяет что `[✓ Опубликовать]` приведёт к посту с тем же форматированием, что в превью. Зачем: автоматически unicode-эквивалентность проверить сложно, человек видит лучше.
- **Голос статьи соответствует tone Mnemonik** — оператор оценивает content quality по существу, не только по claude-blog score. Зачем: score — proxy, не replacement для редакторского вкуса.
- **Создан и настроен `@mnemonik_publisher_bot` в @BotFather, и сделан админом канала @mnemonik с правом Manage Posts** — одноразовая ручная задача, проверяется одним постом-тестом.
