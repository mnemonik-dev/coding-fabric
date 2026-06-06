---
status: done
depends_on: []
wave: 1
skills: [code-writing]
verify: []
reviewers: [code-reviewer, security-auditor]
teammate_name:
---

# Task 1: Sops + secrets template extension

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Расширяем sops-инфраструктуру под content-publish-pipeline: фундамент для всех остальных волн. Добавляем 10 новых секретов/конфигов в `infrastructure/secrets/secrets.sops.yml.template` (документированный шаблон под `cp ... && sops -e -i ...`), кладём пустые/placeholder-значения в зашифрованный `secrets.sops.yml` через `sops edit`, и пробрасываем их как Ansible facts в playbook `deploy.yml` тем же способом, что и существующие vault_* ключи (см. `playbooks/deploy.yml` строки 50-102 — это `pre_tasks`-блок playbook'а, где sops грузится через `community.sops.load_vars: name: sops` и мапится в `vault_*` через `set_fact`; роль `fabric-services` сама sops не грузит — она потребляет уже-готовые `vault_*` facts, выставленные в `pre_tasks` playbook'а).

Решение #13 из tech-spec вводит **topic-based dispatch** вместо slash-команды: оператор пишет в forum-топик `📝 blogger-prompts` в существующем forum-чате. Поэтому в списке ключей есть `blogger_prompts_topic_id` — message_thread_id этого топика, без него bot-handler не сможет фильтровать сообщения (`F.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID`).

Решение #7 (PUBLISH_MODE=auto with two-location integrity binding) требует отдельный ключ `publish_auto_mode_token`: при `PUBLISH_MODE=auto` worker на старте сверяет значение из `/etc/blogger.env` с sops-значением; mismatch → loud abort. Это 10-й ключ — без него рендер `/etc/blogger.env` (`PUBLISH_AUTO_MODE_TOKEN={{ publish_auto_mode_token | default('') }}`, tech-spec строка 421) сломается в Task 3.

Это foundation-задача: ничего не деплоится, ничего не запускается. Wave-1 параллельна с Task 2 (mnemonic-mcp role rewrite) и Task 3 (content-publisher role scaffold), но Task 3 и Task 7 (Ansible smoke) потребляют `vault_*` facts, которые создаются здесь — поэтому без Task 1 они упрутся в undefined-var.

## What to do

1. **Расширить `infrastructure/secrets/secrets.sops.yml.template`** — добавить новый блок-секцию (например `# === Content Publisher ===`) с 10 ключами и человекочитаемыми комментариями для каждого:
   - `blogger_telegram_bot_token` — токен `@mnemonik_publisher_bot` от @BotFather; оператор создаёт бота сам и делает его админом канала @mnemonik (см. tech-spec Decision 5, AC12).
   - `blogger_telegram_channel` — `@mnemonik` (или другой канал); default в шаблоне Ansible — `@mnemonik`.
   - `blogger_prompts_topic_id` — `message_thread_id` форум-топика `📝 blogger-prompts` в существующем `telegram_forum_chat_id`. Оператор создаёт топик в Telegram-клиенте, копирует thread_id (через `getUpdates` или @RawDataBot). См. tech-spec Decision 13.
   - `publish_min_score` — `int`, минимальный score из `analyze_blog.py` для авто-одобрения preview; рекомендованный default `80`.
   - `publish_approval_timeout_min` — `int`, минут до auto-publish после preview-sent; default `5`.
   - `publish_mode` — `"approval"` или `"auto"`, default `"approval"` (см. tech-spec Decision 7).
   - `publish_auto_mode_token` — секрет для two-location integrity binding режима `auto` (см. tech-spec Decision 7). При `PUBLISH_MODE=auto` worker на старте сверяет значение в `/etc/blogger.env` (`PUBLISH_AUTO_MODE_TOKEN`) с sops-значением; mismatch → loud abort. Генерация: `openssl rand -hex 32`. Для `PUBLISH_MODE=approval` значение не используется (placeholder `""` ОК).
   - `blogger_repo_ref` — pinned commit SHA для `mnemonik-dev/blogger` (см. Decision 11).
   - `claude_blog_repo_ref` — pinned commit SHA для `mnemonik-dev/claude-blog`.
   - `publish_callback_hmac_secrets` — секрет(ы) для HMAC inline-callback_data. Формат: `"<current>"` или `"<current>,<previous>"` для zero-downtime ротации (см. Decision 9). Генерация: `openssl rand -hex 32`.

2. **Расширить зашифрованный `infrastructure/secrets/secrets.sops.yml`** — через `sops infrastructure/secrets/secrets.sops.yml` (открывает $EDITOR с расшифрованной копией; сохранение → автоматически шифрует обратно). Добавить те же 10 ключей с **placeholder-значениями** (НЕ настоящими секретами):
   - токены / SHA / hmac / auto_mode_token — пустая строка `""` (default-friendly; роли используют `| default('')`).
   - `blogger_telegram_channel: "@mnemonik"`.
   - `blogger_prompts_topic_id: "0"` (строка, как у `telegram_ops_thread_id`; ноль = "оператор ещё не сконфигурил").
   - `publish_min_score: 80`, `publish_approval_timeout_min: 5`, `publish_mode: "approval"`.
   - Не коммитить настоящие токены/SHA — это задача оператора уже после мерджа.

3. **Расширить `infrastructure/ansible/playbooks/deploy.yml`** — в существующий `set_fact`-блок "Expose sops keys as flat vault_* facts" в `pre_tasks` (строки 62-102) добавить 10 новых `vault_*`-маппингов с `default('')` / `default(0)` / `default('approval')` где уместно. Сохранить `no_log: true` на уровне task. Не создавать отдельный set_fact — расширить существующий, чтобы все sops-факты жили в одном месте (паттерн).

## TDD Anchor

Sanity-проверка (не unit-тест в `pytest` — задача чисто конфигурационная, runtime-кода нет):

- **`sops -d infrastructure/secrets/secrets.sops.yml | python3 -c "import sys, yaml; d=yaml.safe_load(sys.stdin); req=['blogger_telegram_bot_token','blogger_telegram_channel','blogger_prompts_topic_id','publish_min_score','publish_approval_timeout_min','publish_mode','publish_auto_mode_token','blogger_repo_ref','claude_blog_repo_ref','publish_callback_hmac_secrets']; missing=[k for k in req if k not in d]; assert not missing, missing"`** — расшифрованный YAML парсится и все 10 ключей присутствуют (пустые placeholder-значения допустимы).
- **`grep -E '^(blogger_telegram_bot_token|blogger_telegram_channel|blogger_prompts_topic_id|publish_min_score|publish_approval_timeout_min|publish_mode|publish_auto_mode_token|blogger_repo_ref|claude_blog_repo_ref|publish_callback_hmac_secrets):' infrastructure/secrets/secrets.sops.yml.template`** — все 10 ключей задокументированы в template (`wc -l` == 10).
- **`ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml`** — playbook валиден после правок `set_fact`.

## Acceptance Criteria

- [ ] В `secrets.sops.yml.template` присутствуют все 10 ключей (`blogger_telegram_bot_token`, `blogger_telegram_channel`, `blogger_prompts_topic_id`, `publish_min_score`, `publish_approval_timeout_min`, `publish_mode`, `publish_auto_mode_token`, `blogger_repo_ref`, `claude_blog_repo_ref`, `publish_callback_hmac_secrets`) с поясняющими комментариями (источник значения, формат, ссылка на BotFather/openssl/RawDataBot где уместно).
- [ ] В зашифрованном `secrets.sops.yml` присутствуют те же 10 ключей с placeholder/empty-значениями; реальные токены НЕ закоммичены.
- [ ] `blogger_prompts_topic_id` присутствует и задокументирован отдельно (Decision 13 в tech-spec — это THE ключевой identifier для topic-handler).
- [ ] `publish_auto_mode_token` присутствует и задокументирован отдельно (Decision 7 — two-location integrity binding; нужен для рендера `PUBLISH_AUTO_MODE_TOKEN` в `/etc/blogger.env`, иначе Task 3 / Task 6 упрутся в undefined-var или silent-empty).
- [ ] `publish_callback_hmac_secrets` поддерживает формат `<current>,<previous>` для ротации (документировано в комментарии шаблона; формат подтверждается комментарием, не валидацией — валидация на уровне worker'а).
- [ ] В `playbooks/deploy.yml` "Expose sops keys" `set_fact` (в `pre_tasks`) расширен 10 новыми `vault_*`-маппингами с разумными `default(...)`; `no_log: true` сохранён.
- [ ] `sops -d` на `secrets.sops.yml` возвращает парсимый YAML; ключи на месте (sanity из TDD Anchor).
- [ ] `ansible-playbook --syntax-check playbooks/deploy.yml` — passes.
- [ ] Существующие 20+ vault_* facts (tailscale, kaneo, telegram, claude, github, restic) не задеты — diff показывает только добавления.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md)
- [code-research.md](../code-research.md)
- [CLAUDE.md](../../../CLAUDE.md) — единственный project-overview документ (PK skill в этом репозитории не инициализирован). Содержит: компонент-карту (`Components map`), Infrastructure / Hetzner / persistent volume, sops-file false-positive в GitGuardian, Key constraints (Direct-to-trunk, no Co-Authored-By).
- [infrastructure/secrets/secrets.sops.yml.template](../../../infrastructure/secrets/secrets.sops.yml.template) — текущий шаблон, формат комментариев и секций
- [infrastructure/secrets/secrets.sops.yml](../../../infrastructure/secrets/secrets.sops.yml) — encrypted; редактируется через `sops <path>`
- [infrastructure/ansible/playbooks/deploy.yml](../../../infrastructure/ansible/playbooks/deploy.yml) — строки 50-102: `pre_tasks`-блок с `community.sops.load_vars` + `set_fact` маппинг `sops.* → vault_*`
- [infrastructure/ansible/roles/fabric-services/tasks/main.yml](../../../infrastructure/ansible/roles/fabric-services/tasks/main.yml) — роль НЕ грузит sops сама; потребляет уже-готовые `vault_*` facts, выставленные в playbook `pre_tasks` (паттерн, который Task 3 будет повторять для content-publisher)
- [infrastructure/ansible/roles/telegram-ai-agent/README.md](../../../infrastructure/ansible/roles/telegram-ai-agent/README.md) — раздел "Required Secrets" + пример `community.sops.load_vars`

## Verification Steps

### Automated
- `sops -d infrastructure/secrets/secrets.sops.yml > /tmp/decrypted.yml && python3 -c "import yaml; d=yaml.safe_load(open('/tmp/decrypted.yml')); req=['blogger_telegram_bot_token','blogger_telegram_channel','blogger_prompts_topic_id','publish_min_score','publish_approval_timeout_min','publish_mode','publish_auto_mode_token','blogger_repo_ref','claude_blog_repo_ref','publish_callback_hmac_secrets']; missing=[k for k in req if k not in d]; print('MISSING:', missing) or (None if not missing else (_ for _ in ()).throw(AssertionError(missing)))"` → нет missing.
- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` → no syntax errors.
- `git diff infrastructure/secrets/secrets.sops.yml.template` — diff показывает только добавления (existing keys не тронуты).
- `rm /tmp/decrypted.yml` — обязательно удалить расшифрованную копию после проверки (не commit'ить, не оставлять на диске).

## Details

**Files:**

- `infrastructure/secrets/secrets.sops.yml.template` — добавить блок-секцию `# === Content Publisher ===` с 10 ключами и комментариями (источник значения, формат, ссылки на @BotFather / `openssl rand -hex 32` / @RawDataBot для thread_id). Текущее состояние: 111 строк, документирует все существующие sops-секреты — формат секций уже устоявшийся (`# === <name> ===` + многострочные `# `-комментарии перед каждым ключом).

- `infrastructure/secrets/secrets.sops.yml` — encrypted-at-rest YAML (age). Редактируется ТОЛЬКО через `sops infrastructure/secrets/secrets.sops.yml` (открывает $EDITOR с decrypted-копией, сохраняет → автоматически шифрует). НЕ редактировать напрямую как plaintext-файл. Текущее состояние неизвестно (зашифровано), но из деплоя видно, что все ключи из template'а уже там.

- `infrastructure/ansible/playbooks/deploy.yml` (строки 62-102, `pre_tasks`) — расширить существующий `set_fact` "Expose sops keys as flat vault_* facts". Не создавать новый task; добавить 10 новых строчек в существующий маппинг. Сохранить `no_log: true` и `delegate_to: localhost`/`become: false` контекст (они уже на уровне всего блока). Использовать `default(...)` для всех новых ключей, чтобы первый деплой с пустыми placeholder'ами не падал.

**Dependencies:**
- Никаких зависимостей от других задач (wave 1, foundation).
- Сабсиквентные таски (Task 3, 5, 6, 7) будут потреблять созданные здесь `vault_*` facts.
- Локально: установлен `sops` + `age` private key в `~/.config/sops/age/keys.txt` (см. `CLAUDE.md`).

**Edge cases:**
- **Sops редактирование требует age-ключ.** Если `SOPS_AGE_KEY_FILE` не выставлен или ключ отсутствует — `sops edit` падает. Это нормально и должно стопнуть таск — не пытаться обходить.
- **`blogger_prompts_topic_id` — строка**, не int (как `telegram_ops_thread_id: "0"` в шаблоне). Telegram thread_ids в Ansible хранятся как строки, в env-файл рендерятся as-is, в Python приводятся к int на стороне bot-handler'а.
- **`publish_callback_hmac_secrets` — ОДНА строка** с опциональной запятой: `"abc...,def..."`. НЕ список. Parsing на стороне worker'а: `secrets.split(',')`.
- **`publish_min_score` и `publish_approval_timeout_min` — числа** в YAML; в env-файл рендерятся как `"80"` / `"5"` через jinja-фильтр (worker парсит как int). Для шаблона `secrets.sops.yml.template` можно оставить как `int` — sops это допускает.
- **False-positive в GitGuardian:** see `CLAUDE.md` — sops file детектируется как "Generic Password". Не действовать; уже задокументировано как known issue.
- **Не коммитить расшифрованные копии.** Если использовал `sops -d ... > /tmp/...` для sanity-проверки — удалить `/tmp/...` сразу после.

**Implementation hints:**

- Паттерн комментариев в template — посмотреть существующие секции `# === Telegram ===` (строки 38-46) и `# === Restic backups ===` (78-86): двойной `==` обрамляет название, многострочные `# `-комментарии перед каждым ключом с источником/форматом/командой-генератором.

- Для `publish_callback_hmac_secrets` в комментарии явно показать оба формата: `"abc...hex..."` (single) и `"abc...,def..."` (rotation window). Описать процедуру ротации в один абзац: "edit sops → redeploy → after window drop trailing comma + old secret".

- Маппинг в `set_fact` блока deploy.yml (`pre_tasks`) читать как один длинный YAML-словарь. Новые ключи добавлять под существующими, сохраняя выравнивание двоеточий (как в текущем файле; см. строки 70-101). Пример нового маппинга:
  ```yaml
  vault_blogger_telegram_bot_token:        "{{ sops.blogger_telegram_bot_token | default('') }}"
  vault_blogger_prompts_topic_id:          "{{ sops.blogger_prompts_topic_id | default('0') }}"
  vault_publish_min_score:                 "{{ sops.publish_min_score | default(80) }}"
  vault_publish_mode:                      "{{ sops.publish_mode | default('approval') }}"
  vault_publish_auto_mode_token:           "{{ sops.publish_auto_mode_token | default('') }}"
  vault_publish_callback_hmac_secrets:     "{{ sops.publish_callback_hmac_secrets | default('') }}"
  ```
  (Это hint про стилистику — не финальный список; смотреть по факту все 10 ключей.)

- `community.sops.load_vars` уже подключён через `requirements.yml` и используется на строке 57 `deploy.yml` — ничего регистрировать заново не нужно.

- Роль `fabric-services` — **только пример паттерна "потребитель vault_*"**. Изменять её в этой задаче НЕ нужно (Task 3 создаст новую `content-publisher` роль по тому же паттерну).

- Direct-to-trunk: после правок коммит идёт прямо в `claude/review-coding-fabric-spec-uUvMK` (см. `CLAUDE.md` Key constraint #5). Никаких PR, никакого `Co-Authored-By` (см. Rules в CLAUDE.md).

## Reviewers

- **code-reviewer** → `work/content-publish-pipeline/logs/working/task-1/code-reviewer-{round}.json`
- **security-auditor** → `work/content-publish-pipeline/logs/working/task-1/security-auditor-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону (Summary: 1-3 предложения, ревью со ссылками на JSON, без таблиц файндингов и дампов). Включить все раунды ревью.
- [ ] Если отклонились от спека — описать отклонение и причину.
- [ ] Обновить user-spec/tech-spec если что-то изменилось (например, если добавился 11-й ключ или один из 10 оказался лишним — синхронизировать tech-spec Architecture "What we're building/modifying" + Decisions 7/13 + `/etc/blogger.env` template).
