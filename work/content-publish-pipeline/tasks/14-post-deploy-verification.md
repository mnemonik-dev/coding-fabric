---
status: planned
depends_on: [13]
wave: 5
skills: [post-deploy-qa]
verify: []
reviewers: []
teammate_name:
---

# Task 14: Post-deploy verification

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:post-deploy-qa` — [skills/post-deploy-qa/SKILL.md](~/.claude/skills/post-deploy-qa/SKILL.md)

## Description

Финальная live-проверка content-publish-pipeline после успешного деплоя (Task 13). Цель — убедиться, что вся цепочка работает на живом окружении end-to-end: оператор пишет бриф в Telegram-топик → бот рендерит preview → оператор апрувит → пост уходит в @mnemonik → MCP-stdio выдаёт receipt и `mnemonic_recall` возвращает тело статьи → все сервисы (старые + новый) живы → очередь на диске в финальном состоянии.

Это **гейт фичи**: 6 проверок проходят — фича считается completed (переход в `work/completed/` через `/done` skill). Любая проверка падает — возврат на fixer-цикл (как минимум один из Tasks 4-8 переоткрывается, исправление, redeploy).

Особенность задачи: она наполовину **операторская** (оператор пишет в Telegram, кликает кнопку, потом удаляет тестовый пост из канала) и наполовину **агентская** (агент дёргает ssh/curl/MCP-stdio). Агент **направляет** оператора: пишет в чат «теперь, пожалуйста, отправь такой-то текст в топик `📝 blogger-prompts` и сообщи мне message_id», ждёт подтверждение, затем сам проверяет состояние через bash/MCP. На любой невыполненный шаг — fail-loud с указанием, какой именно из 6 чеков не прошёл.

Symphony / Kaneo / workspace-manager в этом тесте **не задействованы** (AC2 из user-spec — pipeline полностью независим), но проверяются в чек-листе #4 на регрессию (должны остаться `active (running)`).

## What to do

1. **Подготовка контекста.** Прочитать tech-spec (особенно секцию Architecture и AC-T1-13), user-spec (AC1-15), и пост-деплой отчёт из Task 13 (если есть `work/content-publish-pipeline/logs/deploy/`). Достать из sops/Ansible facts (или из decisions.md Task 1) три переменные, которые понадобятся в проверках:
   - `hetzner_volume_id` — для пути к `queue.jsonl` на томе.
   - `mnemonic_mcp_binary` — фактическое имя бинарника после `npm install -g @mnemonik-xyz/mcp` (Task 2 его дискаверил).
   - `BLOGGER_PROMPTS_TOPIC_ID` — id forum-топика, куда оператор пишет брифы.

2. **Чек #1 — Plain-text бриф в топике → preview reply (≤90s).**
   - Сказать оператору: «Сейчас пожалуйста напиши в Telegram, в forum-топик `📝 blogger-prompts`, ровно такой plain-text (без слешей, без форматирования): `TEST: explain Mnemonik in one tweet`. Когда отправишь — сообщи мне.»
   - Запустить таймер на 90s в момент, когда оператор подтвердил отправку.
   - Через 90s сделать `ssh op@<vm> 'sudo cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl | tail -1 | jq .'` — последняя запись должна иметь `prompt` начинающийся с `TEST: explain Mnemonik`, `status` ∈ {`preview-sent`, `publishing`, `published`, `done`}, `preview_message_id` не-null.
   - Альтернативно (если есть Telegram MCP): через MCP подгрузить последние сообщения в топике и убедиться, что есть бот-сообщение со `reply_to_message_id` = id операторского сообщения и содержит preview-сегменты. Запросить у оператора визуальное подтверждение «preview отрендерился, я вижу кнопки `[✓ Опубликовать] [✗ Отклонить]`».
   - **PASS:** `preview_message_id != null` в queue + оператор видит сообщение в топике.
   - **FAIL:** запись в queue.jsonl застряла в `queued`/`writing`/`scoring` дольше 90s, ИЛИ preview не виден в топике, ИЛИ оператор сообщает, что reply пришёл не в топик, а в основной чат.

3. **Чек #2 — Click `[✓ Опубликовать]` → пост в @mnemonik (≤30s).**
   - Сказать оператору: «Кликни кнопку `[✓ Опубликовать]` под preview. Запиши время клика. Жди до 30 секунд, я тоже жду.»
   - Через 30s сделать `ssh op@<vm> 'sudo cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl | tail -1 | jq .'` — `status` должен быть ∈ {`published`, `attest-pending`, `done`}, `post_url` не-null, `post_message_ids` — непустой список.
   - Через Bot API (curl, токен `blogger_telegram_bot_token` из sops, доступ через ssh op@<vm> на VM) проверить, что пост реально в канале: `curl -s "https://api.telegram.org/bot<TOKEN>/getChat?chat_id=@mnemonik"` отвечает 200 (smoke на доступ); опционально `forwardMessage` тестового поста в служебный чат для верификации, что message_id из queue существует.
   - Альтернатива: попросить оператора подтвердить визуально — «видишь новый пост в @mnemonik, текст начинается со слов о Mnemonik?». Если да — PASS, если нет — FAIL и проверить `publish_error` в queue.
   - В preview-сообщении в топике текст должен поменяться на `✅ Опубликовано: <url>` (inline keyboard убрана). Попросить оператора подтвердить это.
   - **PASS:** `status` ушёл из `publishing` в `published`/`attest-pending`/`done`, `post_url` записан, оператор видит пост в @mnemonik, preview отредактирован.
   - **FAIL:** `status=publish-failed` (читать `publish_error`); ИЛИ `publishing` висит дольше 30s; ИЛИ оператор не видит пост в @mnemonik.

4. **Чек #3 — MCP-stdio handshake → `mnemonic_recall` возвращает тело статьи.**
   - Достать `attestation_hash` из последней записи queue.jsonl (после Task 14 чек #2 он должен быть выставлен — это и есть гейт между `published → attest-pending → done`; если ещё `attest-pending`, подождать минут до 5 и перепроверить — deadline-checker крутится с шагом 5min).
   - Запустить bash one-liner на VM (через ssh op@<vm>):
     ```bash
     ssh op@<vm> '(
       printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"post-deploy-qa\",\"version\":\"0.1\"}}}";
       printf "%s\n" "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"mnemonic_recall\",\"arguments\":{\"id\":\"<receipt_hash>\"}}}"
     ) | sudo -u op env -i PATH=/usr/local/bin:/usr/bin:/bin <mnemonic_mcp_binary> mcp-stdio'
     ```
     (Точные имена `<mnemonic_mcp_binary>` подставить из sops/Ansible facts. `sudo -u op env -i PATH=...` мимикрирует `restricted_env('mnemonik-mcp')` из worker'а — agent должен убедиться, что воркер ходит в MCP тем же путём, что и наш чек.)
   - Распарсить второй JSON-RPC response: в `result.content[0].text` (или аналогичной структуре по факту MCP-схемы) должно лежать тело статьи (markdown), которое идентично `cat /var/lib/content-publisher/work/<job_id>/article.md` (или путь из `article_path` в queue.jsonl).
   - **PASS:** оба JSON-RPC ответа без `error`, тело статьи возвращено, совпадает с локальным `article.md` побайтово (sha256 опционально).
   - **FAIL:** `initialize` падает (значит `mnemonic-mcp.service` мёртв или бинарь сломан → Task 2 регрессия); ИЛИ `mnemonic_recall` отвечает `error` (значит attestation не дошёл → Task 6 проблема); ИЛИ тело статьи отличается от локального (значит content_sha256 расходится → bug).

5. **Чек #4 — Все сервисы healthy.**
   - `ssh op@<vm> 'systemctl is-active content-publisher mnemonic-mcp telegram-ai-agent workspace-manager'` — все 4 строки должны быть `active`.
   - `ssh op@<vm> 'sudo docker compose -f /opt/kaneo/docker-compose.yml ps --format json'` — все контейнеры со статусом `running` и `health=healthy` (где есть healthcheck). То же для `/opt/vaultwarden/docker-compose.yml`.
   - **PASS:** все 4 systemd unit'а `active`; все docker-контейнеры в kaneo и vaultwarden стеках `running (healthy)`.
   - **FAIL:** хотя бы один сервис `failed`/`inactive`; ИЛИ docker-контейнер `unhealthy` или `exited`.
   - Это AC-T2 (no regression) из tech-spec.

6. **Чек #5 — Финальное состояние очереди.**
   - `ssh op@<vm> 'sudo cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl | tail -1 | jq .'`
   - Последняя запись (наш тестовый job): `status == "done"`, `attestation_hash` не-null, `content_sha256` не-null, `post_url` не-null, `post_message_ids` — непустой список.
   - Если `status == "attest-pending"` — подождать до 5 минут, deadline-checker (sub-loop b) должен перевести в `done` (или в `attest-failed` через 24h, но это уже provoke FAIL). Если `attest-pending` висит дольше 5 минут — собрать `journalctl -u content-publisher --since '-5 minutes'` и проверить, что MCP-stdio retry идёт.
   - **PASS:** `status=done`, `attestation_hash != null`.
   - **FAIL:** `status` ∈ {`attest-failed`, `failed`, `publish-failed`, `attest-pending` после 5 минут}.

7. **Чек #6 — Teardown (manual, оператор).**
   - Сказать оператору: «Чек #6 — последний и ручной. Открой @mnemonik в Telegram, найди тестовый пост (тот, что ты только что апрувнул), удали его через UI Telegram (3 клика). Скрипт сам пост не удаляет — это вне MVP scope, см. tech-spec Decision 8 + Риск 'duplicate'. Сообщи, когда удалил.»
   - Дождаться подтверждения от оператора.
   - **PASS:** оператор подтвердил удаление.
   - **FAIL:** оператор отказался / забыл — не блокирует фичу, но **залогировать** в `decisions.md` как known leftover («test post X in @mnemonik не удалён»).

8. **Финальный отчёт.** Записать в `work/content-publish-pipeline/decisions.md` summary в формате Task N (см. шаблон в decisions.md):
   - какой бриф был использован,
   - какой job_id создался,
   - финальные `status`, `post_url`, `attestation_hash`,
   - результат каждого из 6 чеков (PASS/FAIL),
   - если FAIL — кто/что виновато (какой Task регрессировал), какие следующие шаги.
   - Если все 6 PASS — добавить строку «Feature ready for `/done`».

## Acceptance Criteria

- [ ] **Чек #1 PASS** — preview reply в `📝 blogger-prompts` появился в течение 90s после brief'а оператора; `preview_message_id` записан в queue.jsonl.
- [ ] **Чек #2 PASS** — клик `[✓ Опубликовать]` → пост в @mnemonik в течение 30s; `post_url` записан; preview-сообщение отредактировано на `Published: <link>`.
- [ ] **Чек #3 PASS** — MCP-stdio handshake (initialize + tools/call mnemonic_recall) на живом mnemonic-mcp.service возвращает тело статьи, идентичное локальному `article.md`.
- [ ] **Чек #4 PASS** — `content-publisher`, `mnemonic-mcp`, `telegram-ai-agent`, `workspace-manager` все `active`; kaneo + vaultwarden docker-стеки все `Up (healthy)` (AC-T2).
- [ ] **Чек #5 PASS** — финальный job в queue.jsonl: `status=done`, `attestation_hash != null`, `content_sha256 != null`, `post_url != null`, `post_message_ids != []`.
- [ ] **Чек #6 PASS** — оператор удалил тестовый пост из @mnemonik (или зарегистрирован leftover).
- [ ] **Гейт фичи** — если все 6 PASS, в `decisions.md` записано «Feature ready for `/done`». Если хоть один FAIL — записано «Feature NOT ready: <чек> failed because <reason>; back to <Task N>».

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md) — особенно секции Architecture (шаги 1-5 end-to-end flow), Decisions (особенно #8 just-retry, #11 signature inspection, #13 topic dispatch), Agent Verification Plan, Risks, и Acceptance Criteria (AC-T2 = no regression).
- [decisions.md](../decisions.md) — отчёты Task 1-13 (особенно Task 2 — фактическое имя `mnemonic_mcp_binary`; Task 1 — `hetzner_volume_id`, `BLOGGER_PROMPTS_TOPIC_ID`).
- [CLAUDE.md](../../../CLAUDE.md) — operational truths (Hetzner VM, не делать destroy-recreate, sops/age, прямой коммит в trunk-бранч).
- [docs/vm-runbook.md](../../../docs/vm-runbook.md) — connection commands, paths, secrets map (если ssh-параметры не помнятся; gitignored, локальный референс).
- [post-deploy-qa SKILL.md](~/.claude/skills/post-deploy-qa/SKILL.md) — методология запуска AVP на live окружении.
- Live system (читать через ssh op@<vm>):
  - `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` — главный source of truth по job-states.
  - `/var/lib/content-publisher/work/<job_id>/article.md` — локальное тело статьи для сверки с MCP recall.
  - `journalctl -u content-publisher` — логи воркера.
  - `journalctl -u mnemonic-mcp` — логи MCP daemon (отдельный systemd unit, см. Task 2).
  - `/etc/blogger.env` — env worker'а (мод 0600 root:root, через `sudo cat`).

## Verification Steps

### Smoke

Команды, которые агент выполняет в ходе работы — они же являются доказательством PASS для каждого чека (см. секцию `What to do`):

- **Чек #1:** `ssh op@<vm> 'sudo cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl | tail -1 | jq .'` → последняя запись содержит наш `prompt`, `preview_message_id != null`.
- **Чек #2:** `ssh op@<vm> 'sudo cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl | tail -1 | jq .status,.post_url'` → `published`/`attest-pending`/`done` + URL.
- **Чек #3:** MCP-stdio one-liner (см. шаг 4 в What to do) → второй JSON-RPC response содержит `result.content[0].text` с телом статьи.
- **Чек #4:** `ssh op@<vm> 'systemctl is-active content-publisher mnemonic-mcp telegram-ai-agent workspace-manager'` → 4× `active`; `sudo docker compose -f /opt/kaneo/docker-compose.yml ps` + аналогично vaultwarden → все `running`/`healthy`.
- **Чек #5:** `ssh op@<vm> 'sudo cat /mnt/HC_Volume_<id>/content-publisher/queue.jsonl | tail -1 | jq .'` → `status=done`, `attestation_hash != null`.

### User

Чеки, которые требуют **глаз/рук оператора** (агент инструктирует и ждёт подтверждение):

- **Чек #1 — user-side:** оператор пишет в форум-топик `📝 blogger-prompts` ровно текст: `TEST: explain Mnemonik in one tweet` и сообщает агенту message_id (или просто «отправил»). Через 90s видит в топике reply-preview с inline-кнопками. Подтверждает агенту: «preview вижу, кнопки есть».
- **Чек #2 — user-side:** оператор кликает `[✓ Опубликовать]`. Через 30s видит, что preview-сообщение поменялось на `✅ Опубликовано: <link>` (кнопки убраны), и в @mnemonik появился новый пост. Подтверждает оба.
- **Чек #6 — user-side (teardown):** оператор удаляет тестовый пост из @mnemonik через UI Telegram. Подтверждает удаление. (Скрипт **не** удаляет — out of MVP scope, см. tech-spec Decision 8 + входной запрос.)

## Details

**Files:**

Файлы, которые агент **читает/проверяет** (никаких правок в коде):

- `/mnt/HC_Volume_<id>/content-publisher/queue.jsonl` — JSONL очередь; читать через `ssh + sudo cat | jq`. Не редактировать (мод 0600 op:op; правка ломает live worker).
- `/var/lib/content-publisher/work/<job_id>/article.md` — тело статьи; нужно для байтовой сверки с тем, что вернёт `mnemonic_recall`.
- `/etc/blogger.env` — env worker'а; читать через `sudo cat` (мод 0600 root:root). Проверить наличие `TELEGRAM_BOT_TOKEN`, `BLOGGER_PROMPTS_TOPIC_ID`, `CONTENT_PUBLISHER_QUEUE` (sanity).
- `journalctl -u content-publisher --since '-10 minutes'` — на любой FAIL смотреть последние 10 минут.
- `journalctl -u mnemonic-mcp --since '-10 minutes'` — для Чека #3 если MCP-stdio handshake падает.
- `work/content-publish-pipeline/decisions.md` — **писать** финальный отчёт (один блок Task 14 со всеми 6 чеками).

**Dependencies:**

- Task 13 (Deploy) — должен быть `done`. Если ещё не задеплоилось — задача 14 не имеет смысла запускать.
- Все Wave 1-2-3 таски (1-8) реально активны на VM: `content-publisher.service`, `mnemonic-mcp.service`, bot с `publish_router` — иначе чек #1 завалится моментально.
- Доступ ssh op@<vm> работает у агента (`docs/vm-runbook.md` для адреса/опций; не в git'е).
- Доступ к sops для расшифровки `blogger_telegram_bot_token` если понадобится Bot API curl, и для `BLOGGER_PROMPTS_TOPIC_ID` если нет в Ansible facts.
- Telegram MCP **опционален** — можно полагаться на «оператор-eyes» как fallback; так дёшевле и операторы всё равно нужны на чеки #1/2/6.

**Edge cases:**

- **`attest-pending` дольше 5 минут.** deadline-checker крутится раз в 5 минут (sub-loop b); первая попытка attestation идёт сразу, ретраи каждые 5 минут до 24h. Если ровно после `published` чек #5 видит `attest-pending` — нормально, подождать максимум 5 минут. Дольше 5 — это уже сигнал (MCP daemon или mnemonik-чейн отвалились), пора смотреть `journalctl -u mnemonic-mcp` и `journalctl -u content-publisher | grep attest`.
- **Дубль из crash recovery (Decision 8).** Если на этом конкретном прогоне произошёл рестарт worker'а между `tentative_publish_started_at` и реальной отправкой — в @mnemonik будет ДВА теста. Это **не FAIL** Чека #2 — это документированное поведение (Risk row "Worker-crash-mid-publish double-posts"). Оператор удалит оба в чеке #6. Залогировать в decisions.md как «double-post observed; crash-recovery confirmed working as designed».
- **Оператор пишет в топик что-то постороннее во время теста.** Worker подхватит и попытается написать статью на это — будет лишний job в queue. Не страшно: в чеке #5 берём `tail -1`, поэтому подхватим ровно последний (но операторов лучше попросить ничего не писать кроме тестового брифа).
- **Bot API rate limits.** Один наш тестовый пост — далеко от лимитов; беспокоиться не нужно. Если несколько прогонов подряд (повторное падение) — посмотреть в логах `429`.
- **`mnemonic_mcp_binary` имя.** Может оказаться `mnemonik-mcp` (с K) или `mnemonic-mcp` (без). Task 2 это дискаверил и записал в Ansible fact + Task 2's entry в decisions.md. Не хардкодить — читать из decisions.md или sops.
- **`hetzner_volume_id`.** Берётся из Ansible mounts fact, в шаблоне `RequiresMountsFor=` подставляется как int (Decision 10). Найти можно через `ssh op@<vm> 'ls /mnt/ | grep HC_Volume_'` — единственный матч.
- **systemd-units нет на VM (например после destroy-recreate).** Чек #4 завалится. См. CLAUDE.md Key constraint #1 — destroy-recreate происходит при каждом деплое до Tasks #24/#25. Если задача 14 запускается «свежо после деплоя», то всё должно быть на месте; если что-то отсутствует — это значит Task 13 фактически не довёл сервисы до `enabled+started` (бага в Ansible).
- **vaultwarden persistent data.** Известная бага из CLAUDE.md: vaultwarden data НЕ на персистентном томе. После destroy-recreate она потерялась бы. Чек #4 проверяет только что контейнер `Up (healthy)`, а не что данные на месте — это вне MVP scope для content-publish-pipeline.

**Implementation hints:**

- **Минимизируй ssh-калов.** Запускай несколько проверок в одном ssh-выстреле через `&&` или heredoc — каждый ssh-handshake это 0.5-1s round-trip.
- **Не пиши в queue.jsonl напрямую.** Только чтение через `sudo cat | jq`. Любая попытка изменить файл сломает worker'у CAS.
- **Не editить `/etc/blogger.env` на VM**, даже для теста (см. CLAUDE.md Key constraint #4 — drop-ins только для emergency-fix). Если нужен другой `BLOGGER_PROMPTS_TOPIC_ID` для теста — это означает, что deploy кривой → возвращаемся к Task 13.
- **MCP-stdio one-liner — это live-проверка пути 4 из Architecture.** То же самое worker делает для attestation; если наш one-liner работает, а worker'у тот же handshake падает — баг в worker'е (Task 6).
- **Чек #3 опирается на attestation_hash из чека #5,** но чек #5 формально позже. На практике: после Чека #2 ждём `attest-pending → done` (sub-loop b), затем достаём `attestation_hash` и идём в Чек #3. Если порядок интуитивно перепутался — `attestation_hash` появляется не моментально, нужно ретраить чек #3 раз в минуту до 5 минут.
- **Direct-to-trunk:** финальный отчёт коммитится прямо в `claude/review-coding-fabric-spec-uUvMK`, NO `Co-Authored-By` (см. CLAUDE.md Rules).
- **«Feature complete» переход.** После всех 6 PASS — задача `done`. Оператор/агент может запустить `/done` skill, который перенесёт `work/content-publish-pipeline/` в `work/completed/` и обновит `.claude/skills/project-knowledge/`. Сама task-14 этого делать НЕ должна — это уже отдельный шаг.
- **Telegram MCP vs Bot API curl.** Можно использовать любое из двух для чека #2 (проверка наличия поста в канале); Telegram MCP проще если он подключён в сессии. Если нет — `curl` через ssh op@<vm> (там есть доступ в инет).

## Reviewers

Ревьюверов нет (тестовая задача-гейт, как Task 9-13 в этой фиче). Финальный отчёт пишется напрямую в decisions.md.

## Post-completion

- [ ] Записать единый блок отчёта в `work/content-publish-pipeline/decisions.md` (формат Task N из шаблона decisions.md): brief использованный для теста, job_id, финальные `status`/`post_url`/`attestation_hash`, PASS/FAIL по каждому из 6 чеков, и итоговую строку либо «Feature ready for `/done`», либо «Feature NOT ready: <чек> failed because <reason>; back to <Task N>».
- [ ] Если был замечен double-post (crash recovery срабатывал) — отметить как known-good behavior, не как баг.
- [ ] Если был зафиксирован leftover (Чек #6 не сделан) — описать в отчёте, чтобы `/done` skill потом не упирался в «есть тестовый пост в канале».
- [ ] Никаких правок user-spec/tech-spec в норме не требуется (это финальный гейт). Исключение: если в ходе live-теста проявилось расхождение между spec и реальностью (например, attestation идёт не через MCP-stdio, а как-то иначе) — обновить tech-spec соответствующим небольшим патчем.
