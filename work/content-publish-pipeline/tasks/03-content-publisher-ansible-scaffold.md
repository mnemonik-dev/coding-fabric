---
status: planned
depends_on: []
wave: 1
skills: [code-writing]
verify: [smoke]
reviewers: [code-reviewer, security-auditor]
teammate_name:
---

# Task 3: Ansible role `content-publisher` — install scaffold + upstream verification + README runbooks

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Создаём новую Ansible-роль `content-publisher`, которая разворачивает на ВМ инсталляционный фундамент для будущего Python-сервиса публикации (его код придёт в Wave 2 — задачи 4–7). Роль:

1. Клонирует `mnemonik-dev/blogger` и `mnemonik-dev/claude-blog` по закреплённым commit-SHA (Decision 11) в `/opt/blogger/` и `/opt/claude-blog/`.
2. Создаёт Python-venv в `/opt/blogger/venv/` и ставит зависимости через `pip install --require-hashes -r requirements.locked.txt` (Decision 12 — supply-chain hardening).
3. Проверяет, что апстрим действительно экспортирует требуемый импорт-сёрфейс и сигнатуру `run_campaign_from_article` (Decision 11) — это страховка от round-3-style upstream drift.
4. Рендерит `/etc/blogger.env` (mode 0600 root:root, из sops) и `/etc/blogger.mcp.json` с подставленным `{{ mnemonic_mcp_binary }}` от Task 2.
5. Кладёт systemd-юнит-шаблон `content-publisher.service.j2` с жёсткими ограничениями: `RequiresMountsFor=<concrete>` (Decision 10 — никаких литералов `*`, путь резолвится через `ansible_facts.mounts`), `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true` (Risks — in-process publish hardening).
6. Готовит каталоги: `/var/lib/content-publisher/work/` (worktrees) и `/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher/` (очередь и архив на persistent volume).
7. Включает (`enabled=yes`), но НЕ стартует юнит — Python-сервис ещё не существует, его пакет приедет в Task 7. На этом этапе деплой должен оставить юнит в состоянии `enabled / inactive`.
8. Кладёт README роли с тремя runbook-разделами: ротация HMAC-секрета (`CALLBACK_HMAC_SECRETS=current,previous`), переключение `PUBLISH_MODE=auto` (с двухместным privacy-binding из Decision 7), ротация `blogger_telegram_bot_token`.
9. Регистрирует роль в `playbooks/deploy.yml` строго между `fabric-services` (Phase 7, начинается в строке 253) и `telegram-ai-agent` (Phase 8, начинается в строке 290) — её Phase 7.5.

Зачем именно сейчас: на этой волне фабрика ещё не знает, как публиковать контент. Роль закладывает инфраструктурный фундамент (репозитории, venv, env-файлы, systemd-юнит, точки монтирования), на который Wave 2 положит Python-код. Параллельно Wave 1 уже создал секреты (Task 1) и Mnemonik MCP-биндинг (Task 2), которыми эта роль пользуется через template-переменные.

## What to do

1. Создать структуру каталогов роли в `infrastructure/ansible/roles/content-publisher/` по канону fabric-services: `tasks/main.yml`, `defaults/main.yml`, `templates/` (три j2-файла), `requirements.locked.txt`, `README.md`. Опционально — `handlers/main.yml` и `meta/main.yml` если они нужны для зависимостей/перезапуска.
2. В `defaults/main.yml` закрепить:
   - `blogger_repo_url`, `blogger_repo_ref` (полный commit SHA, не tag)
   - `claude_blog_repo_url`, `claude_blog_repo_ref` (полный commit SHA)
   - `blogger_install_dir: /opt/blogger`, `claude_blog_install_dir: /opt/claude-blog`
   - `content_publisher_work_dir: /var/lib/content-publisher/work`
   - дефолтные значения для нечувствительных переменных, упомянутых в `/etc/blogger.env` (`publish_mode: approval`, `publish_min_score: 80`, `publish_approval_timeout_min: 5`, `blogger_telegram_channel: '@mnemonik'`)
3. В `tasks/main.yml`:
   - Установить системные пакеты (`python3`, `python3-venv`, `python3-pip`, `git`) — повторно ставится OK, idempotent.
   - `ansible.builtin.git`: клон blogger + claude-blog с `version: {{ ..._repo_ref }}` (SHA), `update: yes`. Каталог-владелец — `{{ base_operator_user }}` (`op`).
   - Создать venv: `python3 -m venv {{ blogger_install_dir }}/venv` (`creates:` чтобы быть idempotent).
   - Скопировать `requirements.locked.txt` из роли на ВМ (либо использовать `pip` модуль с `extra_args: --require-hashes`).
   - Установить зависимости в venv: `pip install --require-hashes -r requirements.locked.txt` (`{{ blogger_install_dir }}/venv/bin/pip`). Это центральный supply-chain-control (Decision 12).
   - Поставить сам blogger в venv: `pip install --no-deps -e {{ blogger_install_dir }}` (без deps — все зависимости уже зафиксированы).
   - Резолвить `hetzner_volume_id` через `ansible_facts.mounts | selectattr('device', 'equalto', '/dev/sdb') | ...` (Decision 10) — сохранить в факт. Пустой результат → задача `fail:` с понятным сообщением.
   - Создать `/var/lib/content-publisher/work/` (`mode: 0750`, owner `op`).
   - Создать `/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher/` и `/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher/archive/` (`mode: 0750`, owner `op`).
   - Рендерить `/etc/blogger.env` из `templates/blogger.env.j2` (`mode: '0600'`, `owner: root`, `group: root`).
   - Рендерить `/etc/blogger.mcp.json` из `templates/blogger.mcp.json.j2` (`mode: '0640'`, `owner: root`, `group: op` — чтобы сервис мог читать).
   - Рендерить `/etc/systemd/system/content-publisher.service` из `templates/content-publisher.service.j2`. После рендера — `systemd: daemon_reload: yes, enabled: yes, state: stopped` (НЕ `started`).
   - Запустить пост-инсталляционный assert (см. ниже) и `python /opt/claude-blog/scripts/analyze_blog.py --help` для smoke-проверки claude-blog.
4. Реализовать пост-инсталляционный assert как `ansible.builtin.command` с inline-Python (точный код из Decision 11):
   ```
   {{ blogger_install_dir }}/venv/bin/python -c "
   from pathlib import Path
   from inspect import signature
   from mnemonik_blogger.config import Platform, Settings
   from mnemonik_blogger.content.ingest import ingest_article
   from mnemonik_blogger.content.formatters import render
   from mnemonik_blogger.agent import run_campaign_from_article, CampaignResult
   sig = signature(run_campaign_from_article)
   required = {'article','platforms','settings'}
   assert required <= set(sig.parameters), f'missing kwargs: {required - set(sig.parameters)}'
   assert all(sig.parameters[n].kind.name == 'KEYWORD_ONLY' for n in required), \
          'run_campaign_from_article args must be keyword-only'
   assert Platform.TELEGRAM.value == 'telegram'
   "
   ```
   `failed_when: rc != 0`, вывод stderr попадает в Ansible-лог при падении.
5. Шаблоны:
   - `templates/blogger.env.j2` — содержимое см. tech-spec, "Data Models / `/etc/blogger.env`" (строки 411–428). Все переменные приходят через `vars:` от вышестоящего playbook (sops-снимок). Источники нетривиальных переменных:
     - `OPERATOR_USER_ID={{ telegram_ai_agent_allowed_user_ids[0] }}` — берём ПЕРВЫЙ элемент списка `telegram_ai_agent_allowed_user_ids`. Этот список НЕ принадлежит роли `content-publisher` — он загружается ролью `telegram-ai-agent` (см. `infrastructure/ansible/roles/telegram-ai-agent/templates/.env.j2:13` как пример использования). Цепочка происхождения значения: sops-ключ `telegram_allowed_user_ids` (JSON-array-as-string, например `"[206475911]"`) → парсится в playbook `deploy.yml` на строках 80–82 в `vault_telegram_allowed_user_ids` (`from_json`) → пробрасывается в роль `telegram-ai-agent` на строке 322 как `telegram_ai_agent_allowed_user_ids: "{{ vault_telegram_allowed_user_ids | default([]) }}"`. Для phase 7.5 (content-publisher) надо в том же духе пробросить тот же список как `vars:` блок к роли — либо переиспользуя уже-резолвленный `vault_telegram_allowed_user_ids`, либо повторно через `telegram_ai_agent_allowed_user_ids` (предпочтительно — повторно, чтобы роль не зависела от внутреннего имени `vault_*`). Fail-loud: если список пуст или undefined — Jinja упадёт на `[0]` с IndexError/UndefinedError, что соответствует supply-chain-стратегии Decision 12.
   - `templates/blogger.mcp.json.j2` — однострочный JSON: `{ "mcpServers": { "mnemonik": { "command": "{{ mnemonic_mcp_binary }}", "args": ["mcp-stdio"] } } }`. `mnemonic_mcp_binary` приходит как факт из роли `mnemonic-mcp` (Task 2).
   - `templates/content-publisher.service.j2` — структура по образцу `fabric-services/templates/workspace-manager.service.j2`, но с обязательными строками:
     - `RequiresMountsFor=/mnt/HC_Volume_{{ hetzner_volume_id }}/content-publisher` (НЕ литерал `*`)
     - `LimitCORE=0`
     - `ProtectSystem=strict`
     - `NoNewPrivileges=true`
     - `EnvironmentFile=-/etc/blogger.env`
     - `Environment="PYTHONUNBUFFERED=1"`
     - `WorkingDirectory={{ blogger_install_dir }}`
     - `ExecStart=` — поинтер на будущий `content-publisher` Python-entrypoint (точный модуль уточняется в Task 6; на данном этапе допустимо ставить `/opt/content-publisher/venv/bin/python -m content_publisher.main` как заглушка-плейсхолдер, который не будет работать без Wave 2; юнит всё равно enabled but inactive)
     - `User={{ base_operator_user }}`
     - `Restart=always`, `RestartSec=10`, `StartLimitInterval=60s`, `StartLimitBurst=3`
6. `requirements.locked.txt` — сгенерирован офлайн `pip-compile --generate-hashes` поверх blogger-вых зависимостей при закреплённом `blogger_repo_ref`. Кладём в роль готовый файл (не генерируется при `ansible-playbook` — это compile-time артефакт). В README роли — секция "How to regenerate requirements.locked.txt" с командой и подсказкой про офлайн-режим.
7. `README.md` роли — три runbook-раздела:
   - **HMAC secret rotation** — алгоритм: положить новый секрет в начало `CALLBACK_HMAC_SECRETS` (`new,current`), задеплоить, подождать `PUBLISH_APPROVAL_TIMEOUT_MIN` минут, убрать старый (`new`). Объяснить, что окно `current,previous` поддерживает живые превью.
   - **Switching `PUBLISH_MODE` to `auto`** — что такое two-location integrity binding (Decision 7), какие именно две переменные надо синхронизировать в sops (`publish_mode` + `publish_auto_mode_token`), что сервис на старте сверяет обе и фейлится при рассинхроне.
   - **Rotating `blogger_telegram_bot_token`** — порядок: создать новый токен у `@BotFather`, положить в sops под тем же ключом, передеплоить, проверить логи `content-publisher.service`. Предупредить, что `/etc/blogger.env` имеет mode 0600 root:root — служба читает на старте.
8. Регистрация в `playbooks/deploy.yml`: вставить новый `- role: content-publisher` блок между концом `- role: fabric-services` (строка 253) и `- role: telegram-ai-agent` (строка 290). Передать через `vars:` все блогер-специфичные значения из `sops.*` и явный `mnemonic_mcp_binary` (от Task 2). `tags: [content-publisher, deploy]`. Для нечувствительных полей — `default()` с разумными значениями, как делают соседние phase-блоки.
9. Записать короткий отчёт в `work/content-publish-pipeline/decisions.md` (см. Post-completion).

## TDD Anchor

Тесты для ansible-ролей пишем ДО реализации в виде syntactic + поведенческих проверок. Запускаются в CI и локально:

- `infrastructure/ansible/tests/test_content_publisher_role.yml` — Ansible-уровневый тест в `--check` или `molecule` стиле: прогон роли с фиктивными vars; assert по `stat:` что созданы все ожидаемые файлы (`/etc/blogger.env`, `/etc/blogger.mcp.json`, `/etc/systemd/system/content-publisher.service`, `/opt/blogger/venv/bin/python`).
- `ansible-playbook infrastructure/ansible/playbooks/deploy.yml --syntax-check` — глобальный syntax OK после врезки роли в playbook.
- На реальном test-deploy (Verify-smoke ниже) — отдельный assert-task в роли проверяет наличие `RequiresMountsFor=` с конкретным путём (НЕ `*`), mode `/etc/blogger.env` = `0600`, owner root.
- Import-assert (см. шаг 4 выше) сам является TDD-якорем: до Task 3 этот `python -c` упадёт ImportError; после правильной установки — пройдёт.

## Acceptance Criteria

- [ ] Создана роль `infrastructure/ansible/roles/content-publisher/` со структурой: `tasks/main.yml`, `defaults/main.yml`, `templates/blogger.env.j2`, `templates/blogger.mcp.json.j2`, `templates/content-publisher.service.j2`, `requirements.locked.txt`, `README.md`.
- [ ] Роль клонирует `mnemonik-dev/blogger` и `mnemonik-dev/claude-blog` по pinned commit SHA (не tag, не branch) — значения объявлены в `defaults/main.yml`.
- [ ] `pip install --require-hashes -r requirements.locked.txt` успешно отрабатывает в venv `/opt/blogger/venv/`. Файл `requirements.locked.txt` сгенерирован через `pip-compile --generate-hashes` и закоммичен в роль.
- [ ] Import-test из Decision 11 (Platform, Settings, ingest_article, render, run_campaign_from_article + signature/kind проверки + `Platform.TELEGRAM.value == 'telegram'`) исполняется как Ansible-task и завершается с rc=0 на test-deploy.
- [ ] `/etc/blogger.env` отрендерен из шаблона с mode `0600`, owner `root`, group `root`. Содержит все переменные из tech-spec (строки 411–428 spec'а).
- [ ] `/etc/blogger.mcp.json` отрендерен с подставленным `{{ mnemonic_mcp_binary }}` (значение приходит от роли `mnemonic-mcp`, Task 2). НЕ содержит литерала `{{ mnemonic_mcp_binary }}` — Jinja-подстановка завершилась.
- [ ] systemd-юнит `content-publisher.service` содержит обязательные строки: `RequiresMountsFor=/mnt/HC_Volume_<concrete_id>/content-publisher` (БЕЗ литералов `*`), `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`. `hetzner_volume_id` резолвится через `ansible_facts.mounts`; пустой результат → role fails loud.
- [ ] Роль `content-publisher` вставлена в `infrastructure/ansible/playbooks/deploy.yml` между концом `- role: fabric-services` (текущая строка 253) и началом `- role: telegram-ai-agent` (текущая строка 290).
- [ ] `README.md` роли содержит три runbook-раздела: HMAC secret rotation, switching PUBLISH_MODE=auto (two-location binding), publisher-token rotation.
- [ ] После деплоя `systemctl is-enabled content-publisher.service` → `enabled`; `systemctl is-active content-publisher.service` → `inactive` (Python-сервис ещё не существует, придёт в Task 7).
- [ ] `ansible-playbook infrastructure/ansible/playbooks/deploy.yml --syntax-check` проходит без ошибок.
- [ ] **No-regression on existing services** (carryover from tech-spec AC-T2, строки 535–537): после прогона роли врезанной в `deploy.yml` все ранее установленные сервисы по-прежнему здоровы — Task 3 владеет ответственностью за это требование, потому что именно эта задача мутирует `playbooks/deploy.yml` (вставляет phase 7.5 между fabric-services и telegram-ai-agent). Проверка на test-deploy: `systemctl is-active telegram-ai-agent workspace-manager mnemonic-mcp.service` → все `active`; `docker compose ps` в `/opt/kaneo/` и `/opt/vaultwarden/` → все контейнеры `Up (healthy)`. Регрессия = поломанная врезка (например, неправильный indent в YAML или сдвинутые номера строк) — ловится здесь, а не на финальной пост-деплой верификации.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md) — раздел `#### Task 3:` (строка 569), `Decision 10` (строка 302), `Decision 11` (строка 308), `Decision 12` (строка 330), `Risks` (строки 499–518), `Data Models` (строки 411–434), `AC-T2` (строки 535–537 — no-regression criteria)
- [decisions.md](../decisions.md)
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — project context: pipeline, components map (`telegram-ai-agent` at `/opt/telegram-ai-agent/`, `workspace-manager` at `/opt/fabric/workspace-manager/`, planned `blogger` at `/opt/blogger/`, planned `claude-blog` at `/opt/claude-blog/`), infrastructure (Hetzner ccx33, persistent volume `fabric-data` mounted at `/mnt/HC_Volume_105783873/`), key operational constraints (CI deploy is canonical, no destroy-recreate, sops at `infrastructure/secrets/secrets.sops.yml`)
- [infrastructure/ansible/roles/fabric-services/tasks/main.yml](../../../infrastructure/ansible/roles/fabric-services/tasks/main.yml) — venv pattern (`Create Python venv`, `Install ... into venv`)
- [infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2](../../../infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2) — systemd-юнит-shape для зеркалирования
- [infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2](../../../infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2) — sops→env-file render
- [infrastructure/ansible/playbooks/deploy.yml](../../../infrastructure/ansible/playbooks/deploy.yml) — phase ordering, точки врезки 253 ↔ 290; см. также строки 80–82 (как `sops.telegram_allowed_user_ids` парсится в `vault_telegram_allowed_user_ids`) и строку 322 (`telegram_ai_agent_allowed_user_ids: "{{ vault_telegram_allowed_user_ids | default([]) }}"` — где этот список становится role var для `telegram-ai-agent`)
- [infrastructure/ansible/roles/telegram-ai-agent/templates/.env.j2](../../../infrastructure/ansible/roles/telegram-ai-agent/templates/.env.j2) — строка 13 (`{% set _ids = telegram_ai_agent_allowed_user_ids | default([]) -%}`) — каноничный пример того, как `telegram_ai_agent_allowed_user_ids` рендерится в env-файл соседней роли

## Verification Steps

### Automated

- `ansible-playbook infrastructure/ansible/playbooks/deploy.yml --syntax-check` → exit 0, no errors.
- Если используется molecule/локальный role-test — `ansible-playbook infrastructure/ansible/tests/test_content_publisher_role.yml --check` → all asserts pass.

### Smoke

Выполнить на test-deploy после прогона роли:

- `ssh op@<vm> 'systemctl is-enabled content-publisher.service'` → `enabled`.
- `ssh op@<vm> 'systemctl is-active content-publisher.service'` → `inactive` (это нормально на Wave 1).
- `ssh op@<vm> 'stat -c "%a %U:%G" /etc/blogger.env'` → `600 root:root`.
- `ssh op@<vm> 'test -f /opt/blogger/venv/bin/python && echo OK'` → `OK`.
- `ssh op@<vm> 'test -f /opt/claude-blog/scripts/analyze_blog.py && echo OK'` → `OK`.
- `ssh op@<vm> 'grep -E "^RequiresMountsFor=" /etc/systemd/system/content-publisher.service'` → строка содержит реальный путь `/mnt/HC_Volume_<int>/content-publisher` без литералов `*` или `{{`.
- `ssh op@<vm> 'grep -E "^(LimitCORE|ProtectSystem|NoNewPrivileges)=" /etc/systemd/system/content-publisher.service'` → три совпадения: `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`.
- `ssh op@<vm> '/opt/blogger/venv/bin/python -c "from inspect import signature; from mnemonik_blogger.agent import run_campaign_from_article; print(set(signature(run_campaign_from_article).parameters))"'` → набор содержит `{article, platforms, settings}`.
- `ssh op@<vm> 'cat /etc/blogger.mcp.json | python -c "import json,sys; d=json.load(sys.stdin); assert d[\"mcpServers\"][\"mnemonik\"][\"command\"] != \"{{ mnemonic_mcp_binary }}\"; print(d)"'` → JSON парсится, command — реальный путь, не Jinja-литерал.

## Details

**Files to modify:**

- `infrastructure/ansible/roles/content-publisher/tasks/main.yml` — основной playbook роли (новый файл). Содержит: установку пакетов, клон обоих репозиториев по SHA, создание venv, `pip install --require-hashes`, резолвинг `hetzner_volume_id`, создание директорий, рендер трёх templates, daemon_reload + enabled+stopped, import-assert.
- `infrastructure/ansible/roles/content-publisher/defaults/main.yml` — все переменные роли с разумными дефолтами; pinned SHA для blogger и claude-blog; пути установки.
- `infrastructure/ansible/roles/content-publisher/templates/blogger.env.j2` — env-файл по схеме из tech-spec (строки 411–428); все значения через Jinja-переменные.
- `infrastructure/ansible/roles/content-publisher/templates/blogger.mcp.json.j2` — однострочный MCP-конфиг с подставленным `{{ mnemonic_mcp_binary }}`.
- `infrastructure/ansible/roles/content-publisher/templates/content-publisher.service.j2` — systemd-юнит; зеркалирует `workspace-manager.service.j2` + четыре жёстких ограничения (`RequiresMountsFor`, `LimitCORE`, `ProtectSystem`, `NoNewPrivileges`).
- `infrastructure/ansible/roles/content-publisher/requirements.locked.txt` — pre-generated `pip-compile --generate-hashes` output. Не генерируется на деплое.
- `infrastructure/ansible/roles/content-publisher/README.md` — три runbook-раздела + раздел "How to regenerate requirements.locked.txt".
- `infrastructure/ansible/playbooks/deploy.yml` — врезка нового `- role: content-publisher` блока между fabric-services и telegram-ai-agent. `vars:` блок передаёт все sops-секреты, hetzner_volume_id (либо роль резолвит сама), mnemonic_mcp_binary, telegram_forum_chat_id, telegram_ai_agent_allowed_user_ids[0], blogger_prompts_topic_id, publish_callback_hmac_secrets, publish_mode/auto-token/min-score/approval-timeout.
- `work/content-publish-pipeline/decisions.md` — короткий отчёт о выполнении (см. Post-completion).

**Files to read (REFERENCE only):**

- `infrastructure/ansible/roles/fabric-services/tasks/main.yml` — паттерн venv-создания и `pip install` через `ansible.builtin.pip` с `virtualenv:` ключом; общая структура задач.
- `infrastructure/ansible/roles/fabric-services/templates/workspace-manager.service.j2` — shape systemd-юнита для зеркалирования (User, WorkingDirectory, Environment*, EnvironmentFile=-, Restart, StartLimitBurst).
- `infrastructure/ansible/roles/fabric-services/templates/symphony.env.j2` — паттерн sops→env-file рендеринга.
- `infrastructure/ansible/playbooks/deploy.yml` (фокус: строки 252–294) — phase ordering, как соседние фазы передают `vars:` и какие переменные они ожидают на входе.

**Dependencies:**

- Task 1 (sops/secrets template) должен быть выполнен ДО первой попытки рендера `/etc/blogger.env` на реальном деплое — иначе `sops.blogger_telegram_bot_token` и `sops.blogger_prompts_topic_id` будут пустыми. Внутри Wave 1 это OK: задачи независимы по коду, зависимы по деплою.
- Task 2 (Mnemonik MCP role) должна выставить факт `mnemonic_mcp_binary` (либо группа/host var) ДО запуска phase 7.5. Если факт не выставлен — `blogger.mcp.json.j2` сфейлит рендеринг (`UndefinedError`), что соответствует fail-loud-стратегии.

**Edge cases:**

- `ansible_facts.mounts | selectattr('device', 'equalto', '/dev/sdb') | list` пустой (VM без persistent volume) → `fail: msg="hetzner_volume_id not resolved; /dev/sdb is not mounted"`. Деплой обязан падать громко.
- `git clone` падает на закреплённом SHA (потёрт rebase'ом в апстриме) → стандартная ошибка `ansible.builtin.git`; на VM ничего не меняется (idempotent fail).
- `pip install --require-hashes` падает на mismatched hash → fail-loud; роль не доходит до import-assert.
- Import-assert падает (апстрим сдвинул сигнатуру) → понятный stderr с дельтой ключей; деплой не доходит до врубания юнита.
- Повторный прогон роли (re-deploy без изменений) — все шаги idempotent, нет лишних `Restart` (юнит `state: stopped`, не `started`).

**Implementation hints:**

- Не использовать `ansible.builtin.shell` там, где есть `ansible.builtin.git`/`ansible.builtin.pip`/`ansible.builtin.template`/`ansible.builtin.systemd`/`ansible.builtin.file` — это нужно для idempotency-tracking и clean diff'ов.
- При резолве `hetzner_volume_id` сначала `set_fact` в дочернюю переменную, потом проверка `when: hetzner_volume_id is defined and hetzner_volume_id | length > 0` — иначе Jinja может тихо сложить пустое значение в путь `/mnt/HC_Volume_/content-publisher`.
- Не врубать `state: started` для `content-publisher.service` на Wave 1 — Python-сервис не существует. Это обязанность Task 7.
- Для in-process publish hardening (см. Risks): убедиться, что excepthook-скраббинг `article_text` из tracebacks — это уже задача Wave 2 (Python-код); на Ansible-уровне обеспечиваем только `LimitCORE=0` (нет core dumps на диске) + `ProtectSystem=strict` (нет записи в системные пути) + `NoNewPrivileges=true` (нет setuid escalation).
- В README роли пометить `requirements.locked.txt` как "regenerate offline only" — иначе CI runner с интернетом может затянуть свежие пакеты вместо pinned.
- Для `blogger.env.j2` — НЕ ставить дефолты для секретов (`blogger_telegram_bot_token`, `publish_callback_hmac_secrets`, `publish_auto_mode_token`). Пусть Jinja падает с UndefinedError, если оператор забыл положить секрет в sops. Дефолты — только для нечувствительных полей (`blogger_telegram_channel`, `publish_mode`, `publish_min_score`, `publish_approval_timeout_min`, `MNEMONIK_DRY_RUN`).

## Reviewers

- **code-reviewer** → `work/content-publish-pipeline/logs/working/task-3/code-reviewer-{round}.json`
- **security-auditor** → `work/content-publish-pipeline/logs/working/task-3/security-auditor-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в `work/content-publish-pipeline/decisions.md`: Summary (1–3 предложения, что именно поставлено и какие именно SHA закреплены), ссылки на JSON-отчёты обоих ревьюеров по раундам, упоминание `requirements.locked.txt` (как сгенерирован).
- [ ] Если отклонились от спека (например: пришлось ослабить `ProtectSystem=strict` до `full`) — описать отклонение и причину в decisions.md.
- [ ] Обновить tech-spec/user-spec, если в процессе всплыли новые ограничения апстрима blogger или mnemonik-mcp (например: сигнатура `run_campaign_from_article` уже сдвинулась — записать новые kwargs).
