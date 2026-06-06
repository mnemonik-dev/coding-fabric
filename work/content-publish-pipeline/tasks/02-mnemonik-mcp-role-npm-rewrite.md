---
status: planned
depends_on: []
wave: 1
skills: [code-writing]
verify: [smoke]
reviewers: [code-reviewer, security-auditor]
teammate_name:
---

# Task 2: Rewrite Mnemonik MCP role for npm scoped-package install + binary-name discovery

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:code-writing` — [skills/code-writing/SKILL.md](~/.claude/skills/code-writing/SKILL.md)

## Description

Текущая роль `infrastructure/ansible/roles/mnemonic-mcp/` качает бинарь с GitHub Releases (`mnemonic-mcp-linux-x86_64`) и проверяет SHA256 — но такого релиза на самом деле не существует, поэтому роль отключена по умолчанию (`mnemonic_mcp_enabled: false`) и помечена как DESCOPED. Эта задача перезаписывает роль на реальный upstream-канал дистрибуции: scoped npm-пакет `@mnemonik-xyz/mcp@<pinned-version>`. Пакет шипует бинарь, но имя бинаря между версиями колебалось между `mnemonik-mcp` (с `k`, актуальное по skeptic round 3 на `@mnemonik-xyz/mcp@0.2.4`) и старым вариантом `mnemonic-mcp` (без `k`). Чтобы и систему не ломать на следующем bump-е, и `/etc/blogger.mcp.json` (Task 3) и worker subprocess (Task 6) показывали реально установленное имя — роль после `npm install` дискаверит фактическое имя бинаря через каскад `which mnemonik-mcp || which mnemonic-mcp` и сохраняет результат в Ansible fact `mnemonic_mcp_binary`, который дальше прокидывается в systemd unit и в любой template, который должен спавнить mcp-stdio.

Атестация (Decision 3 + Architecture step 4) идёт через MCP JSON-RPC поверх stdio — НЕ через one-shot CLI subcommand `sign-memory`. Такого subcommand в npm-пакете нет; есть только `install`, `mcp-stdio`, `doctor`. Поэтому smoke-проверка post-install — JSON-RPC `initialize` handshake против `<binary> mcp-stdio` (НЕ `--help`-проверка на `sign-memory`).

Также включает: переключение `mnemonic_mcp_enabled: true`, установку транзитивных зависимостей через `npm ci` из checked-in `files/package-lock.json` (security R2-8 — supply-chain hardening: pin не только корневой пакет, но и всё дерево), и два runbook'а в README — (а) ротация publisher-bot токена и (б) bump версии mnemonik-mcp с проверкой имени бинаря.

## What to do

1. **defaults/main.yml** — переключить `mnemonic_mcp_enabled: true`; ввести `mnemonik_mcp_npm_package: "@mnemonik-xyz/mcp"` + `mnemonik_mcp_npm_version: "0.2.4"`; ввести `mnemonik_mcp_install_dir: /opt/mnemonik-mcp`; удалить `mnemonic_mcp_version`, `mnemonic_mcp_binary_url`, `mnemonic_mcp_binary_sha256` (старый distribution channel мёртв). Оставить `mnemonic_mcp_systemd_unit`, `mnemonic_mcp_service_user/group`. Удалить vault/bw-related переменные (`mnemonic_mcp_vault_item`, `mnemonic_mcp_credential_dir`, `mnemonic_mcp_credential_file`) — новый install-path не использует тот же signing-key вход.
2. **tasks/main.yml** — переписать:
   - Удалить весь блок `when: not (mnemonic_mcp_enabled | bool)` (DESCOPED no-op stubs).
   - Удалить `get_url` mnemonic-mcp binary + SHA assertion.
   - Удалить bw materialise signing-key task + cleanup.
   - Удалить config.yml render, protocol-qa.env render, molyanov hooks render, hook scripts loop, hook scripts assertion.
   - Добавить блок установки node+npm (паттерн из `roles/telegram-ai-agent/tasks/main.yml:63-88` — `node --version` check, NodeSource setup_20.x, `ansible.builtin.apt nodejs`). Строки 89-112 того же файла относятся к Claude CLI install — не копировать.
   - Скопировать `files/package-lock.json` → `{{ mnemonik_mcp_install_dir }}/package-lock.json` (mode 0644).
   - Прогнать `npm ci` или `npm install -g @mnemonik-xyz/mcp@{{ mnemonik_mcp_npm_version }}` global=true (точный вариант — см. Implementation Hints; контракт — pin виден в lockfile, root и транзитивы фиксированы).
   - Дискавер бинаря: `command: bash -c 'which mnemonik-mcp || which mnemonic-mcp'` с `register: mcp_which`, `failed_when: mcp_which.rc != 0`, `changed_when: false`. Затем `ansible.builtin.set_fact: mnemonic_mcp_binary: "{{ mcp_which.stdout | trim }}"` — fact доступен всем последующим ролям в этом play (включая Task 3's content-publisher role).
   - Рендер `templates/mnemonic-mcp.service.j2` → `/etc/systemd/system/{{ mnemonic_mcp_systemd_unit }}` + reload + enable + wait_for active.
   - Smoke MCP-stdio handshake (см. Verification Steps → Smoke).
3. **templates/mnemonic-mcp.service.j2** — `ExecStart={{ mnemonic_mcp_binary }} mcp-stdio` (или `mcp-server` если npm-пакет переименовал subcommand — исполнитель сверится с реальной доку после установки, единственный source of truth — bin name discovery + `<binary> --help`). Type=simple (не notify). Снять `LoadCredential`. Сохранить hardening блок (`NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, etc.). PATH в `Environment="PATH=..."` должен включать тот префикс, в который npm global ставит бинарь (обычно `/usr/local/bin` или `/usr/bin`).
4. **files/package-lock.json (NEW)** — checked-in lockfile, сгенерированный offline-командой `npm install @mnemonik-xyz/mcp@0.2.4 --package-lock-only --no-audit --no-fund` в пустой папке. Артефакт supply-chain pin'а; security-auditor читает его как часть task review.
5. **README.md** — переписать целиком:
   - Убрать DESCOPED-блок наверху.
   - Задокументировать новый install-path (`npm install -g @mnemonik-xyz/mcp@<version>` + lockfile-based pin транзитивов).
   - Bump-runbook: обновить `mnemonik_mcp_npm_version` в defaults → пере-генерировать `files/package-lock.json` (offline-команда) → коммит → деплой → post-deploy sanity: `journalctl -u mnemonic-mcp --since "5 minutes ago"` + ssh `<binary> doctor`. Если новый релиз поменял имя бинаря — discovery каскад сам подберёт; никакого manual edit'а в templates.
   - Publisher-bot token rotation runbook (entry-point для оператора): @BotFather revoke → новый токен → `sops edit secrets.sops.yml` → bump `blogger_telegram_bot_token` → деплой → удалить старый токен у @BotFather. (Содержательно — operator-side task, но runbook упоминается тут для дискаверабельности; Task 3's content-publisher README ссылается обратно сюда.)
6. **handlers/main.yml** — оставить только `reload mnemonic-mcp systemd` и `restart mnemonic-mcp service`; убрать handlers связанные с config.yml.
7. **Удалить** артефакты descoped-схемы: `templates/config.yml.j2`, `templates/protocol-qa.env.j2`, `templates/molyanov-mnemonic-hooks.yml.j2`, `templates/hooks/` (вся подпапка). Меньше поверхности для security review.
8. **molecule/** — две части с разным статусом:
   - **GATING (обязательно):** если в директории `molecule/default/` есть сценарий и он запускается в текущем CI — обновить `verify.yml` под новые ассерты (см. TDD Anchor); если сценария нет — пропускаем этот пункт (gate уже покрыт обязательными `ansible-lint` + `pytest` из Automated).
   - **INFORMATIONAL (не блокирующее):** если сценарий есть, но не запускается в текущем CI — оставить как есть, добавить пометку TODO в README с указанием на task ID для follow-up'а. Не блокирует merge.

## TDD Anchor

Тесты пишем ДО реализации. Каждый из перечисленных — **runnable + mandatory** (gate для merge). Все запускаются без живой VM, в локальной dev/CI среде. Сценарии molecule (если есть) — separate informational layer, см. шаг 8 в What to do.

- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_defaults_pin_npm_package_and_version` — pytest читает `defaults/main.yml` через PyYAML и assert'ит наличие ключей `mnemonik_mcp_npm_package == "@mnemonik-xyz/mcp"`, непустого `mnemonik_mcp_npm_version`, `mnemonic_mcp_enabled is True`, отсутствия `mnemonic_mcp_binary_url` и `mnemonic_mcp_binary_sha256`.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_tasks_set_fact_for_binary_discovery` — pytest читает `tasks/main.yml` через PyYAML, находит task с `set_fact: mnemonic_mcp_binary: ...` и проверяет что register'нутая команда содержит и `mnemonik-mcp` и `mnemonic-mcp` (каскад) и имеет `failed_when` на rc != 0.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_no_sign_memory_anywhere` — pytest grep'ает по всей роли (`tasks/`, `templates/`, `defaults/`, `handlers/`, `README.md`) на подстроку `sign-memory` и assert'ит 0 совпадений. Защита от регрессии.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_systemd_template_uses_binary_fact` — pytest читает `templates/mnemonic-mcp.service.j2` как текст и assert'ит наличие подстроки `{{ mnemonic_mcp_binary }} mcp-stdio` в строке `ExecStart=`, отсутствия `LoadCredential=`.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_package_lock_checked_in_and_valid_json` — pytest assert'ит существование `files/package-lock.json` (NEW артефакт), что это валидный JSON, что `lockfileVersion >= 2`, и что в `packages` (или `dependencies`) есть запись с `@mnemonik-xyz/mcp` в ключе или value.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_descoped_artifacts_removed` — pytest assert'ит несуществование `templates/config.yml.j2`, `templates/protocol-qa.env.j2`, `templates/molyanov-mnemonic-hooks.yml.j2`, директории `templates/hooks/`.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_ansible_lint_clean` — pytest вызывает `subprocess.run(["ansible-lint", "infrastructure/ansible/roles/mnemonic-mcp/"])` и assert'ит rc == 0.
- `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py::test_ansible_syntax_check` — pytest вызывает `subprocess.run(["ansible-playbook", "--syntax-check", "infrastructure/ansible/playbooks/deploy.yml"])` и assert'ит rc == 0.

Все вышеперечисленные тесты пишутся в один новый файл `infrastructure/ansible/roles/mnemonic-mcp/tests/test_role_contract.py`. Запуск: `pytest infrastructure/ansible/roles/mnemonic-mcp/tests/ -v`. Прохождение всех 8 тестов — обязательное условие готовности задачи.

## Acceptance Criteria

- [ ] `npm install -g @mnemonik-xyz/mcp@{{ mnemonik_mcp_npm_version }}` отрабатывает без ошибок на чистой VM (с предварительным `apt install nodejs` через тот же play).
- [ ] Транзитивные зависимости установлены через `npm ci` из checked-in `infrastructure/ansible/roles/mnemonic-mcp/files/package-lock.json` (security R2-8 — pin не только верхнего пакета, но и всё дерево).
- [ ] Ansible fact `mnemonic_mcp_binary` установлен через `which mnemonik-mcp || which mnemonic-mcp` (trim'нутый stdout), доступен всем последующим ролям в playbook'е без `cacheable: true` / `hostvars[...]` (тот же play, тот же host).
- [ ] systemd unit `templates/mnemonic-mcp.service.j2` использует `{{ mnemonic_mcp_binary }}` в `ExecStart`.
- [ ] `/etc/blogger.mcp.json` template (рендерится Task 3, но контракт фиксируется этой задачей через cross-role propagation) использует `{{ mnemonic_mcp_binary }}` в `mcpServers.mnemonik.command`.
- [ ] `mnemonic_mcp_enabled: true` в `defaults/main.yml`.
- [ ] `mnemonic-mcp.service` `active (running)` после деплоя.
- [ ] One-shot MCP-stdio handshake: spawn `<mnemonic_mcp_binary> mcp-stdio`, в stdin JSON-RPC `initialize` (protocolVersion `2024-11-05`, capabilities `{}`, clientInfo name=`task2-smoke`), из stdout читается ответ с `result.protocolVersion`. stdin close → процесс exit 0.
- [ ] **НЕТ** probe-а на subcommand `sign-memory` нигде в роли (tasks / smokes / docs / README) — этот subcommand не существует; атестация делается через `tools/call mnemonic_sign_memory` в MCP JSON-RPC (контракт Task 6).
- [ ] Role README содержит два runbook'а: (a) bump mnemonik-mcp version (включая ре-генерацию package-lock.json и post-deploy sanity); (b) rotate publisher-bot token (entry-point для оператора).
- [ ] Старые артефакты descoped-схемы удалены: `templates/config.yml.j2`, `templates/protocol-qa.env.j2`, `templates/molyanov-mnemonic-hooks.yml.j2`, `templates/hooks/*`, no-op stub block в tasks/main.yml.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md) — Architecture step 4, Decision 3, Implementation Tasks Task 2
- [decisions.md](../decisions.md)
- [CLAUDE.md](../../../CLAUDE.md) — **project context (PK substitute)**: components map, pipeline, infrastructure (Hetzner VM, persistent volume, tofu state), bus protocol, operational truths (VM persistence #24/#25, direct-to-trunk, NO `Co-Authored-By` trailer). Этот репо не содержит `.claude/skills/project-knowledge/project.md` или `architecture.md` — CLAUDE.md выполняет обе роли.
- [infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml](../../../infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml) — текущая роль (descoped), переписать
- [infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml](../../../infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml) — текущие defaults, заменить distribution-channel переменные
- [infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2](../../../infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2) — текущий unit, переписать ExecStart, снять LoadCredential
- [infrastructure/ansible/roles/mnemonic-mcp/README.md](../../../infrastructure/ansible/roles/mnemonic-mcp/README.md) — текущий README с DESCOPED notice, переписать
- [infrastructure/ansible/roles/telegram-ai-agent/tasks/main.yml](../../../infrastructure/ansible/roles/telegram-ai-agent/tasks/main.yml) — паттерн node+npm install (строки 63-88), `community.general.npm` global install
- [infrastructure/ansible/roles/fabric-services/tasks/main.yml](../../../infrastructure/ansible/roles/fabric-services/tasks/main.yml) — общий стиль ролей в fabric
- [infrastructure/ansible/playbooks/deploy.yml](../../../infrastructure/ansible/playbooks/deploy.yml) — где роль вызывается в pipeline (для понимания где `mnemonic_mcp_binary` fact будет потребляться downstream)

## Verification Steps

### Automated

- `pytest infrastructure/ansible/roles/mnemonic-mcp/tests/ -v` → все 8 contract-тестов (см. TDD Anchor) зелёные. **Gating.**
- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` → exit 0. **Gating** (также покрыт pytest-обёрткой).
- `ansible-lint infrastructure/ansible/roles/mnemonic-mcp/` → no errors. **Gating** (также покрыт pytest-обёрткой).
- `molecule test -s default` (если сценарий запускается в текущем CI) → все assertions в verify.yml зелёные. **Informational** — см. What to do шаг 8.
- Negative test (manual one-off): удалить локально строку `mnemonic_mcp_binary` set_fact и прогнать downstream Task 3 render — должен валиться с понятным `'mnemonic_mcp_binary' is undefined`. Затем восстановить (контракт защищает от silent fallback). **Informational.**

### Smoke

Прогоняется на пост-деплой VM (или molecule-сценарием с реальным npm-пакетом):

- `ssh root@<vm> systemctl is-active mnemonic-mcp` → `active`.
- `ssh root@<vm> "$mnemonic_mcp_binary --help"` listит subcommand `mcp-stdio` (заодно — НЕ листит `sign-memory`).
- One-shot JSON-RPC handshake (bash one-liner):
  ```
  printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"task2-smoke","version":"0.1"}}}\n' \
    | "$mnemonic_mcp_binary" mcp-stdio \
    | head -1 \
    | python3 -c 'import json,sys;r=json.loads(sys.stdin.read());assert "protocolVersion" in r.get("result",{}),r'
  ```
  Exit 0 → handshake ok.
- `sha256sum /opt/mnemonik-mcp/package-lock.json` совпадает с локальным `files/package-lock.json` (lockfile прибыл на VM без drift).

## Details

**Files:**
- `infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml` — заменить distribution-channel переменные (см. What to do шаг 1).
- `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml` — переписать (см. What to do шаг 2). Текущий файл — 310 строк, descoped-блок и legacy binary-download составляют 95% объёма. Новый объём ожидается ~120-150 строк.
- `infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2` — ExecStart на `{{ mnemonic_mcp_binary }} mcp-stdio`, Type=simple, без LoadCredential, hardening сохранён.
- `infrastructure/ansible/roles/mnemonic-mcp/files/package-lock.json` *(NEW)* — checked-in lockfile.
- `infrastructure/ansible/roles/mnemonic-mcp/README.md` — переписать целиком (см. What to do шаг 5).
- `infrastructure/ansible/roles/mnemonic-mcp/handlers/main.yml` — два handler'а, релевантных новой схеме.
- УДАЛИТЬ: `templates/config.yml.j2`, `templates/protocol-qa.env.j2`, `templates/molyanov-mnemonic-hooks.yml.j2`, `templates/hooks/` (вся подпапка).

**Dependencies:**
- В play предшествующих: нет (Wave 1, no depends_on).
- В play последующих: Task 3 (`content-publisher` role) — потребляет `mnemonic_mcp_binary` fact в `/etc/blogger.mcp.json` рендере. Это межролевая контрактная зависимость (отсюда AC на cross-template propagation).
- Runtime: Task 6's worker spawn'ит `<mnemonic_mcp_binary> mcp-stdio` для атестации.
- External: `@mnemonik-xyz/mcp@0.2.4` на npmjs.org (per tech-spec skeptic round 3); `nodejs` apt-package (NodeSource setup_20.x).

**Edge cases:**
- npm install не находит пакет (scope `@mnemonik-xyz` отсутствует, версия retracted) → fail loud в `community.general.npm` step.
- Оба `which mnemonik-mcp` И `which mnemonic-mcp` → пусто → `failed_when: mcp_which.rc != 0` валит роль с понятным сообщением.
- Будущая версия пакета именует бинарь третьим вариантом (`mnemonik-mcp-server` и т.п.) → задача упадёт на discovery. Runbook в README указывает: обновить каскад в tasks/main.yml. Альтернатива (follow-up): парсить `npm list -g @mnemonik-xyz/mcp --json` для извлечения `bin` поля.
- VM уже имеет старого `op` user'а от прошлого descoped-install'а → `user` task должен быть идемпотентным.
- На VM остались артефакты descoped-инстал'а под `/opt/mnemonic-mcp/` (без k) → допустимо оставить (идемпотентно); новая роль работает в `/opt/mnemonik-mcp/` (с k). Опционально — `state: absent` на `/opt/mnemonic-mcp/bin/mnemonic-mcp` чтобы убрать неоднозначность для `which`.
- npm global prefix варьируется (`/usr/local/bin` vs `/usr/bin` vs `/usr/lib/node_modules/.bin`) → исполнитель проверяет реальный prefix через `npm config get prefix` на target и подстраивает `PATH` в systemd unit.

**Implementation hints:**

- Ansible facts cross-task propagation: `set_fact` без `cacheable: true` живёт в пределах play. Поскольку content-publisher role вызывается в том же play на том же host'е, fact `mnemonic_mcp_binary` доступен в templates через `{{ mnemonic_mcp_binary }}`. НЕ нужно `hostvars[inventory_hostname]['mnemonic_mcp_binary']` — Jinja автоматически разрешает по обычному lookup'у.
- `community.general.npm` модуль автоматически добавляет `--global`. `state: present` идемпотентен. Pin версии — параметр `version: "{{ mnemonik_mcp_npm_version }}"`.
- Дилемма `npm ci` vs `npm install -g`: scoped-пакет шипует бинарь в `bin/` своего packed payload, который npm global устанавливает на PATH. Транзитивы лежат в `node_modules` внутри global prefix'a. `npm ci` обычно для local project'а с lockfile. Для global install с pin транзитивов один рабочий путь: установить локально в `{{ mnemonik_mcp_install_dir }}` через `npm ci`, затем сделать symlink `bin/mnemonik-mcp` в `/usr/local/bin/`. Исполнитель проверяет реальный layout `@mnemonik-xyz/mcp@0.2.4` после первой установки на тест-VM и выбирает рабочий вариант. README документирует выбор.
- Skeptic round 3 проверка: `@mnemonik-xyz/mcp@0.2.4` шипает binary `mnemonik-mcp` (с K) — base case для первой ветки discovery каскада. Cascade нужен защитой от прошлых/будущих версий с разным spelling'ом.
- `--help` бинаря должен листить subcommands `install`, `mcp-stdio`, `doctor` (per tech-spec Decision 3). НЕ ищем `sign-memory` ни в каком виде.
- Smoke MCP handshake на Ansible-стороне: `ansible.builtin.shell` с pipeline'ом `printf '...' | <binary> mcp-stdio | head -1 | python3 -c '...'`, `register` + `failed_when` на rc + stdout-парсинге.
- Генерация package-lock.json (offline): на dev-машине в новой пустой папке `npm install @mnemonik-xyz/mcp@0.2.4 --package-lock-only --no-audit --no-fund`, копировать получившийся `package-lock.json` в `infrastructure/ansible/roles/mnemonic-mcp/files/`. Чек его в README как часть bump-runbook'а.
- Coexistence с descoped-артефактами: предыдущая роль ставила `/opt/mnemonic-mcp/bin/mnemonic-mcp` (downloaded). Новая может работать рядом, но для уменьшения двусмысленности `which` — опционально удалить старый бинарь через `ansible.builtin.file: path=/opt/mnemonic-mcp/bin state=absent`.
- PATH в systemd unit: после первого install'а на тест-VM проверить `npm config get prefix` (вероятнее всего `/usr/local`), тогда binary в `/usr/local/bin/`. `Environment="PATH=/usr/local/bin:/usr/bin:/bin"`.

**Honored constraints:**
- VM persistence (#24/#25): имя бинаря дискаверится каждый деплой; новый VM / переезд не страшен; нет state, привязанного к machine-id. Согласуется с переходом на persistent VM model (#24/#25 stable SSH key уже на VM).
- Direct-to-trunk: одна ветка `claude/review-coding-fabric-spec-uUvMK`, коммиты прямо туда.
- NO `Co-Authored-By` trailer в коммитах (CLAUDE.md rule).

## Reviewers

- **code-reviewer** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-2/code-reviewer-{round}.json`
- **security-auditor** → `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/working/task-2/security-auditor-{round}.json`

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону (Summary: 1-3 предложения, ревью со ссылками на JSON, без таблиц файндингов и дампов)
- [ ] Если отклонились от спека — описать отклонение и причину (особенно: если фактическая subcommand-схема npm-пакета отличается от `mcp-stdio` — задокументировать обнаруженное и обновить tech-spec Decision 3)
- [ ] Обновить user-spec/tech-spec если что-то изменилось (особенно: точное имя binary + version `@mnemonik-xyz/mcp`; контракт `/etc/blogger.mcp.json` template)
