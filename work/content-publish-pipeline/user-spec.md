---
created: 2026-06-06
status: approved
type: feature
size: L
---

# User Spec: content-publish-pipeline

## Что делаем

Добавляем в coding-fabric новый pipeline для генерации и публикации контента в TG-канал @mnemonik. CI/CD создаёт выделенный Telegram forum topic `📝 blogger-prompts` через роль `telegram-init`; оператор не создаёт этот topic вручную. Оператор пишет plain-text бриф в этот topic, отдельный worker-сервис на VM спавнит claude с подгруженным skill suite `claude-blog`, claude исследует и пишет статью, бот шлёт оператору preview точно в том виде, в каком статья появится в канале, с inline-кнопками `[✓ Опубликовать]` / `[✗ Отклонить]` (+ `[🔄 Перегенерировать]` при низком quality score). После подтверждения (или 5-минутного timeout) `mnemonik-blogger` CLI публикует пост в @mnemonik и регистрирует attestation через локальный Mnemonik MCP сервер — после этого статья recallable через `mnemonic_recall`. Поддерживается отложенная публикация (`--at <ISO>`).

## Зачем

Сейчас каждый пост о Mnemonik пишется и публикуется вручную: ресёрч, оформление под формат TG, постинг. Это блокирует регулярную коммуникацию с комьюнити. Цель — оператор формулирует один **бриф** в TG и получает готовую статью, которая после быстрой апрувки уходит в канал и автоматически становится частью verifiable memory протокола (recallable через Mnemonik MCP — это **продуктовый use-case** самого Mnemonik, eating own dogfood).

## Как должно работать

**Иммедиэйт сценарий:**

1. Оператор в topic `📝 blogger-prompts`: `Объясни, почему Mnemonik нужен AI-агентам и в чём отличие от обычных vector stores`
2. Бот: «Принял. Бриф в очереди (id `abc12345`). Жду статью.»
3. `content-publisher.service` берёт задачу из очереди, спавнит claude с подгруженным `claude-blog` skill suite в `/var/lib/content-publisher/work/abc12345/`, claude пишет `article.md`.
4. Worker запускает `analyze_blog.py` от claude-blog → получает score + feedback (массив issues). **Quality gate решений не принимает сам**: и при высоком, и при низком score статья идёт оператору на preview — score просто показан в шапке сообщения и при `< MNEMONIK_MIN_SCORE` (дефолт 80) добавляется inline-кнопка `[🔄 Перегенерировать]`.
5. Бот рендерит статью **ровно как она появится в @mnemonik** — для этого worker возвращает не сырую markdown, а результат `mnemonik-blogger render --platform telegram --from-file article.md --stdout` (или импортированной render-функции blogger'а — точный механизм фиксируется в tech-spec). Это гарантирует byte-equality: один и тот же код рендерит и preview, и финальную публикацию.
6. Preview отправляется оператору в личку:
   ```
   Score: 85/100  (порог 80)
   ────────────────────
   <текст поста как он появится в @mnemonik>
   ────────────────────
   [✓ Опубликовать]  [✗ Отклонить]
   ```
   Если `score < 80`:
   ```
   Score: 62/100  ⚠️  (порог 80)
   Issues: <первые 3 из analyze_blog feedback>
   ────────────────────
   <текст>
   ────────────────────
   [✓ Опубликовать]  [✗ Отклонить]  [🔄 Перегенерировать]
   ```
7. Оператор кликает `[✓ Опубликовать]` ИЛИ молчит 5 минут (`PUBLISH_APPROVAL_TIMEOUT_MIN`, env-конфигурируемый):
   - Бот проверяет статус job атомарно: если `status != 'preview-sent'` (например, уже опубликовано или отклонено в другом callback'е) — отвечает «too late, статус: <X>».
   - Если `status == 'preview-sent'` → меняет на `'publishing'` атомарно (CAS-style через переписывание queue.jsonl), затем вызывает `mnemonik-blogger post --from-file article.md --platforms telegram --live`.
   - blogger постит в @mnemonik (через **отдельный** Telegram-бот `@mnemonik_publisher_bot`, оператор создаёт его сам через @BotFather и делает админом канала), регистрирует attestation в локальном Mnemonik MCP через `mnemonic_sign_memory(content=article_body, metadata={post_url, score, prompt})`.
8. Если кликнут `[🔄 Перегенерировать]` — worker возвращается к шагу 3 с тем же `prompt` + `feedback`, но новым `job_id` (старый помечен `regenerated_to=<new_id>`).
9. Бот заменяет preview-сообщение на: «Опубликовано. Пост: <link>. Receipt: <hash>. Recallable через `mnemonic_recall(<hash>)`.»

**Отложенный сценарий:**

1. Оператор: `/publish_content --at 2026-06-10T10:00 <бриф>`
2. Бот добавляет в очередь job с `fire_at=2026-06-10T10:00`. Очередь — JSONL-файл на персистентном volume (`/mnt/HC_Volume_*/content-publisher/queue.jsonl`), переживает destroy-recreate VM. Approval-deadline (`approval_deadline=<fire_at+5min>`) также записывается в queue запись, чтобы timeout-логика переживала рестарт worker'а.
3. `content-publisher.service` каждые ~30 секунд проверяет очередь и фаерит overdue jobs. Дальше — как иммедиэйт сценарий.

**Отказ оператора:**
- Клик `[✗ Отклонить]` → атомарный переход `preview-sent → rejected`. Preview-сообщение заменяется на «Отклонено. Бриф сохранён, можно повторить руками.» Worktree сохраняется 24 часа, потом удаляется. Attestation НЕ создаётся.

**Autonomous-режим (`PUBLISH_MODE=auto` в `/etc/blogger.env`):**
- Этап preview/approval пропускается. После рендера статья сразу публикуется. Скор всё равно вычисляется и логируется, но НЕ блокирует. Оператор получает пост-фактум уведомление: «Auto-published: <link>. Receipt: <hash>. Score: <N>.»

**Восстановление после рестарта worker'а:**
- При старте `content-publisher.service` сканирует queue.jsonl на in-flight статусы (`writing` / `scoring` / `preview-sent` / `publishing`). Для каждого решает: `writing` / `scoring` → перезапустить с начала (worktree уже на диске, prompt известен). `preview-sent` → проверить `approval_deadline`: если прошёл — публикуем сразу; если нет — оставляем как есть, бот получит callback или timeout сработает. `publishing` → проверить через blogger CLI, был ли пост реально создан (по URL/post_id); если да → завершить как `published`; если нет → пометить `failed` и уведомить оператора (может быть double-post иначе).

## Критерии приёмки

- [ ] **AC1 — slash-команда регистрируется**: `/publish_content` появляется в bot commands list (`bot_commands.py`), Telegram'овский setMyCommands не падает (формат имени `[a-z0-9_]+`).
- [ ] **AC2 — Kaneo/Symphony НЕ задействованы**: после `/publish_content` нет нового тикета в Kaneo (verify: `GET /api/project/<id>` returns same tasks count) и Symphony `/worktrees` не показывает новых записей.
- [ ] **AC3 — worker спавнит claude с claude-blog skill**: в `/var/lib/content-publisher/work/<id>/` появляется `article.md` написанный claude; в логах `content-publisher.service` видно `Running claude with --skill claude-blog` (или эквивалент по факту реализации).
- [ ] **AC4 — quality gate показывает score + issues**: при mock score=50 preview содержит `Score: 50/100 ⚠️`, список issues и кнопку `[🔄 Перегенерировать]`. При score=90 кнопки `[🔄]` нет.
- [ ] **AC5 — preview byte-identical с финальным постом**: raw text preview-сообщения совпадает с raw text поста в @mnemonik; Telegram entities (bold/italic/links) сгенерированы из одного источника (`article.md`), а не двумя разными рендерерами. Проверка — pytest, который читает test-article.md, вызывает render-функцию blogger'а, и сравнивает байтово с тем, что blogger потом постит.
- [ ] **AC6 — inline-кнопки работают**: клик `[✓ Опубликовать]` запускает публикацию <30 сек; клик `[✗ Отклонить]` отменяет; 5-минутный timeout без действий = публикация (по умолчанию); `[🔄 Перегенерировать]` создаёт новый job_id со старым prompt+feedback.
- [ ] **AC7 — Mnemonik MCP attestation**: после успешной публикации `mnemonic_sign_memory` возвращает hash; `mnemonic_recall(<hash>)` возвращает body статьи.
- [ ] **AC8 — scheduled job persists across VM restart**: `/publish_content --at <ISO>` → перезагрузить content-publisher.service → job всё ещё в очереди и фаерится в течение 60 секунд от `<ISO>`.
- [ ] **AC9 — past-date rejection**: `/publish_content --at <past-ISO>` бот отвечает «Время должно быть в будущем» и не добавляет в очередь.
- [ ] **AC10 — spawn failure handling**: при намеренной поломке claude-blog skill path (переименовать /opt/claude-blog) worker помечает job как `failed`, шлёт оператору traceback, НЕ ретраит.
- [ ] **AC11 — PUBLISH_MODE=auto**: смена env на `auto` + `systemctl restart content-publisher` → следующая `/publish_content` команда публикует без preview/approval, оператор получает пост-фактум уведомление.
- [ ] **AC12 — отдельный publisher-бот**: статья постит `@mnemonik_publisher_bot`, не `@mnemonik_fabric_bot` (operator-бот). В sops лежат два разных токена.
- [ ] **AC13 — race click-vs-timeout**: симулировать одновременные click `[✓]` и timeout fire — должна сработать ровно одна публикация. Атомарный CAS на `status` в queue.jsonl блокирует вторую попытку, второй callback получает «too late, статус: publishing».
- [ ] **AC14 — crash recovery на старте**: убить worker во время `status=writing` → restart → worker замечает overdue in-flight job и резюмирует. Убить во время `status=publishing` → restart → worker проверяет реальный post-status через blogger CLI и НЕ double-post'ит.
- [ ] **AC15 — RequiresMountsFor блокирует поллер до mount'a**: systemd unit имеет `RequiresMountsFor=/mnt/HC_Volume_*/content-publisher` — если volume не примонтирован, сервис не стартует, а валится с понятным error message.
- [ ] **AC16 — topic создаётся CI/CD**: роль `telegram-init` создаёт `blogger-prompts` через Bot API в том же deploy pipeline, сохраняет `message_thread_id` в `/etc/fabric/telegram-topics.yml` и `infrastructure/inventory/telegram-topics.yml`; `content-publisher` получает `BLOGGER_PROMPTS_TOPIC_ID` из этой generated mapping. Оператор НЕ создаёт topic вручную.

## Ограничения

- **MVP — Telegram канал @mnemonik только.** Discord / Farcaster / Webapp / LinkedIn / X.com — отложены. Конкретно:
  - **Webapp blog page** → `work/blogger-site-connector/` (нужно решить webapp stack + написать новый адаптер).
  - **LinkedIn** → `work/blogger-extra-platforms/` (нужен OAuth-flow для company-page write, не в upstream blogger).
  - **Discord, Farcaster** → `work/blogger-extra-platforms/` (адаптеры есть в upstream blogger, нужны только токены/webhooks).
  - **X.com** заблокирован paid API tier ($100/mo).
- **VM Hetzner НЕ должен пересоздаваться на каждый деплой.** Тасочки #24 и #25 (persistent VM refactor) ещё открыты — текущий CI destroy-recreate'ит VM. Эта фича работает корректно при обоих сценариях (очередь и attestations лежат на персистентном volume, blogger stateless), но deploy-цикл всё ещё страдает.
- **content-publisher.service зависит от смонтированного `/mnt/HC_Volume_*`** — systemd unit имеет `RequiresMountsFor=/mnt/HC_Volume_*/content-publisher` и `ConditionPathIsMountPoint=/mnt/HC_Volume_*`. Без volume сервис не стартует (понятный fail, а не молчаливый бубнёж на root disk).
- **content-publisher.service отдельный сервис, НЕ интегрируется в Symphony.** Один владелец очереди, без race conditions с poll loop'ом Symphony.
- **mnemonik-dev/blogger upstream pinned to a commit ref** в роли (не `main`) для воспроизводимости. До commit'a tech-spec'a — обязательная верификация CLI surface (`blogger post --from-file --platforms telegram --live` и `blogger render --platform telegram --stdout`) прямой проверкой в склонированном репо.
- **mnemonik-dev/claude-blog upstream pinned to a commit ref** — API `analyze_blog.py` это наш контракт.
- **Default mode = approval+timeout.** Auto-mode опциональный (env flag), но это feature-flag, не дефолт.
- **No auto-rewrite на низком score.** Quality gate показывает score+feedback оператору в preview; оператор сам решает [🔄 Перегенерировать] или [✓ Опубликовать]. Это экономит Claude credits и оставляет операторскую глазурь как единственный safety-net.
- **Inline-кнопки в Telegram имеют 64-байтовый лимит на callback_data**. Используем `publish:<action>:<job_id[:8]>` (e.g. `publish:approve:abc12345`) — fits.
- **Concurrent jobs serial**: worker обрабатывает один job за раз. Очередь FIFO + sort'ировка по `fire_at`. Параллелизм — следующая итерация.

## Риски

- **Риск 1: Mnemonik MCP сервер недоступен в момент публикации.** Пост уйдёт в TG (committed, откатить нельзя), но attestation не запишется. **Митигация:** retry-очередь для attestations отдельно от публикации (status=`attest-pending` в queue.jsonl). Каждые 5 минут worker пробует attestation повторно. Если за 24 часа не записан — оператор получает уведомление, статья остаётся в queue.failed.jsonl для ручного re-sign.
- **Риск 2: claude-blog upstream меняет API `analyze_blog.py`.** Quality gate начинает возвращать нерелевантный score или падает. **Митигация:** pinned commit ref в Ansible role; обновление через явный PR; tech-spec фиксирует expected signature.
- **Риск 3: Оператор отошёл, 5-минутный timeout сработал, плохой пост ушёл в канал.** **Митигация:** 5 минут — дефолт, конфигурируется в `PUBLISH_APPROVAL_TIMEOUT_MIN`. Можно поставить 30 или 60. Удаление поста после публикации — через стандартный Telegram-клиент (бот не пытается).
- **Риск 4: Telegram API outage во время publish.** blogger получает 5xx/timeout от Telegram. **Митигация:** blogger CLI имеет встроенный retry с backoff (verify в tech-spec). Если все retries fail — job=`publish-failed`, attestation НЕ записывается (нет URL), оператор получает уведомление: «TG publish failed после N retries, draft в /var/lib/.../article.md». Manual /retry — через ту же команду с тем же prompt.
- **Риск 5: Race click-vs-timeout вызывает double-publish.** **Митигация:** AC13 — атомарный CAS на `status` в queue.jsonl. Перед publish'ом worker (или callback handler) делает read-modify-write одной операцией: если status != `preview-sent`, отказ с понятным сообщением.
- **Риск 6: Crash worker'а в `status=publishing` после фактического TG-поста, но до записи `published` — restart double-post'ит.** **Митигация:** AC14 — на старте worker проверяет через blogger CLI / Telegram API был ли пост реально создан (по сохранённому intermediate `tentative_post_id`). Если да — пишет `published` и продолжает с attestation. Если нет — публикует.
- **Риск 7: Telegram rate-limit при scheduled постах оптом.** Реально маловероятно (постим редко), но возможно. **Митигация:** worker сериализует jobs (никакого параллелизма); blogger CLI имеет встроенный backoff.
- **Риск 8: Уязвимость publisher-бота.** Утечка `blogger_telegram_bot_token` → постит от имени @mnemonik. **Митигация:** токен в sops; отдельный бот от operator-бота (полная изоляция scope, операторский бот видит только internal чаты); ротация через @BotFather при подозрении.
- **Риск 9: Volume не примонтирован при старте сервиса** → poller пишет в root disk, на следующем VM-recreate всё теряется. **Митигация:** `RequiresMountsFor=/mnt/HC_Volume_*/content-publisher` + `ConditionPathIsMountPoint=/mnt/HC_Volume_*` в systemd unit. Без volume сервис не стартует.

## Технические решения

- Решили **выделить отдельный systemd-сервис `content-publisher.service`** для очереди + worker'a, потому что Symphony (workspace-manager.service) уже владеет своей очередью и состоянием — смешивать нельзя без сильной связки. Отдельный сервис = понятная ownership очереди + изоляция отказов.
- Решили **НЕ задействовать Kaneo и Symphony** в этом pipeline, потому что они дают state-machine + worktree-management, которые здесь не нужны: одна стадия (write), нет PR, нет ревью другим движком, нет коммитов в репо. Лишний overhead = больше поверхности для отладки.
- Решили **очередь хранить как JSONL на персистентном volume** (`/mnt/HC_Volume_*/content-publisher/queue.jsonl`), потому что это переживает destroy-recreate VM (ровно как Kaneo Postgres из тасочки #22). Атомарные state-transitions через переписывание всего файла + `os.rename` (POSIX atomic). FIFO + sort по `fire_at`.
- Решили **preview byte-identical с публикацией через единый renderer**: или импортируем `mnemonik_blogger.render` напрямую в bot/worker, или вызываем `mnemonik-blogger render --platform telegram --stdout`. Точный выбор — в tech-spec, после прочтения upstream API. Pytest проверит byte-equality.
- Решили **claude-blog skill подгружать через path env** (`MNEMONIK_CLAUDE_BLOG_PATH=/opt/claude-blog`), потому что worker сам спавнит claude и передаёт ему skill explicitly. Никакой магии с `~/.claude/skills` symlink'ами.
- Решили **attestation писать через локальный Mnemonik MCP сервер** (он уже задеплоен ролью `mnemonic-mcp`), а не через external API, потому что (а) меньше зависимостей; (б) это product use-case — Mnemonik recall'ит свои собственные публикации.
- Решили **создать отдельный Telegram-бот для публикации** (`@mnemonik_publisher_bot` через @BotFather), потому что operator-бот живёт во внутренних чатах с твоей сессией, а publisher выйдет в публичный канал — разные threat models, разные scope привилегий. Изоляция = бесплатная (5 мин в @BotFather).
- Решили **failure spawn'a claude НЕ ретраить** (Q18=A), потому что 99% таких failures = structural (missing skill file, OOM, путь сломан) — retry не поможет, а маскирует баг.
- Решили **НЕ делать auto-rewrite на низком score** — quality gate показывает score+feedback оператору в preview, кнопка [🔄 Перегенерировать] оставляет решение за человеком. Экономит credits, упрощает код, операторский глаз = единственный safety-net.
- Решили **MVP только TG-канал**, потому что у тебя есть рабочий токен + права админа, а другие платформы требуют либо денег (X), либо нового адаптера (webapp, LinkedIn), либо просто новых ключей (Discord/Farcaster — отложены, тривиально добавятся позже).
- Решили **атомарный CAS на status в queue.jsonl** для решения race click-vs-timeout: и callback handler, и timer оба делают read-modify-write на job_id запись; кто первый меняет status на `publishing`, тот публикует.
- Решили **persistent approval-deadline в queue.jsonl** (поле `approval_deadline=<preview_sent_at+timeout_min>`), чтобы timeout пережил рестарт worker'a / VM.
- Решили **crash-recovery scan на старте worker'а**: проходим по queue, для каждого in-flight статуса (writing / scoring / preview-sent / publishing) принимаем решение resume / verify / fail.
- Решили **PUBLISH_MODE=auto в MVP**, потому что это env-flag + 3 строки кода (skip preview branch); архитектурно поддерживает long-term vision автономного бота из Q11; цена реализации низкая.

## Тестирование

**Unit-тесты:** делаются всегда. Покрытие: queue persist/load с CAS, scheduled-fire timing, analyze_blog gate decision (mocked), inline-callback handler routing (publish:approve/reject/retry), preview-renderer byte-equality, crash-recovery state-machine decisions.

**Интеграционные тесты:** делаем — (a) очередь survives kill + restart (write → kill worker → restart → assert job still there → fires); (b) atomic status transition под симуляцией concurrent click+timeout (threading + assert один публикейш); (c) crash в `publishing` → restart → no double-post (mock blogger CLI отслеживает invocations).

**E2E тесты:** делаем (ручной + ограниченный) — `/publish_content <тестовый бриф>` на боевом VM, проверяем preview, кликаем `[✓ Опубликовать]`, верифицируем пост в @mnemonik и `mnemonic_recall(<hash>)`. **Без staging-канала** — реальный канал, реальный пост. Безопасность даёт preview+approval. После успешного раунда — удаляем тестовый пост из канала вручную.

## Как проверить

### Агент проверяет

| Шаг | Инструмент | Ожидаемый результат |
|-----|-----------|-------------------|
| 1. Slash-команда зарегистрирована | `getMyCommands` через Bot API | `publish_content` в списке для оператора |
| 2. `/publish_content тест` → enqueue | `journalctl -u telegram-ai-agent` + `journalctl -u content-publisher` | Бот логит enqueue, worker логит spawn claude |
| 3. Quality gate: высокий score | mock analyze_blog → 90 | Preview без `[🔄]` кнопки |
| 4. Quality gate: низкий score | mock → 50 | Preview с `Score: 50/100 ⚠️`, issues, `[🔄 Перегенерировать]` |
| 5. Inline `[✓ Опубликовать]` | сим. `answerCallbackQuery` | blogger CLI запускается, выход 0; пост в @mnemonik |
| 6. 5-min timeout без действий | tail logs + wait | blogger CLI запускается автоматически |
| 7. Mnemonik MCP attestation | `mnemonic_recall(<hash>)` через MCP | Возвращает body статьи |
| 8. Scheduled job survives restart | enqueue с `--at <ISO+10min>`; `systemctl restart`; ждать | Job фаерится в течение 60 сек от `<ISO>` |
| 9. Past-date rejected | `/publish_content --at 2020-01-01T00:00 ...` | Бот: «Время должно быть в будущем» |
| 10. Spawn failure | Переименовать `/opt/claude-blog` | Worker `failed`, traceback в TG, нет ретраев |
| 11. Race click-vs-timeout | unit тест с threading | Ровно один publish, второй callback видит `status=publishing` |
| 12. Crash в `publishing` | kill -9 worker; перезапустить | Worker проверяет post-status; нет double-post |
| 13. Volume not mounted | umount + systemctl restart | Сервис fail с понятным сообщением, не пишет на root |
| 14. PUBLISH_MODE=auto | env=auto + restart | Следующий enqueue публикует без preview |
| 15. Preview byte-equality | pytest test_render | Output rendererа == output blogger.publish для одного и того же article.md |

### Пользователь проверяет

- **Голос статьи соответствует tone Mnemonik** — оператор оценивает content quality по существу, не только по claude-blog score. Зачем: score — proxy, не replacement для редакторского вкуса.
- **Создан и настроен `@mnemonik_publisher_bot` в @BotFather, и сделан админом канала @mnemonik с правом Manage Posts** — одноразовая ручная задача, проверяется одним постом-тестом.
- **При `[🔄 Перегенерировать]` новая версия меняет что-то осмысленное** — операторская оценка: claude не зацикливается на одной и той же фразе, реально учитывает analyze_blog feedback.
