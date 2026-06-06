---
status: planned
depends_on: [1, 2, 3, 4, 5, 6, 7, 8]
wave: 4
skills: [security-auditor]
verify: []
reviewers: []
teammate_name:
---

# Task 10: Security Audit

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:security-auditor` — [skills/security-auditor/SKILL.md](~/.claude/skills/security-auditor/SKILL.md)

## Description

Full-feature OWASP Top 10 security audit across all source files created or modified in Tasks 1–8 of the `content-publish-pipeline` feature. The audit is read-only — no code edits — and produces a structured findings report at `work/content-publish-pipeline/logs/audit/security-audit.json`. The audit must verify that the security decisions hardened during user-spec / tech-spec review rounds (round-1/2/3 security findings) actually landed in the implementation: subprocess argv/env hygiene, path traversal containment, HMAC + replay protection on callback flows, sops secret containment, topic-filter correctness, publisher bot token leakage controls, attestation content binding, auto-mode two-location integrity, and supply-chain pinning.

The audit is the second of two audit-wave tasks (Task 9 = code audit, this = security audit, Task 11 = test audit). It runs after the entire implementation is integrated and before pre-deploy QA (Task 12). It has NO reviewers — its output IS the review.

## What to do

1. Read the implementation surface from Tasks 1–8 — all files listed under "Files" below. Build a mental map of trust boundaries (operator → bot → queue → worker → claude/MCP subprocesses → mnemonik_blogger in-process publish → @mnemonik channel).
2. Walk the OWASP Top 10 (2021+) categories against this map. For every category, decide if there is a relevant attack surface in this feature, and if so, audit it concretely.
3. Apply the focused hit list (carries the security findings raised during tech-spec rounds — every item below MUST be checked explicitly and have a verdict in the report):
   - **(1) Subprocess argv / env hygiene** — verify ALL bulk content goes via stdin (not argv); verify `restricted_env(kind)` returns exactly the allowlisted variables for each `kind` (`claude`, `analyze_blog`, `mnemonik-mcp`, `blogger_inproc`) per the tech-spec table; verify `mnemonik-mcp` argv is exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]` with no payload.
   - **(2) Path traversal** — worktree paths are constructed from sanitized UUID v4 only; reject control chars, `..`, slashes, etc.; `test_uuid_validation.py` covers the negative cases; no user-controlled string ever joined into a worktree or article path without UUID validation.
   - **(3) Callback HMAC + replay + rotation** — Decision 9 implementation: format `publish:<action>:<job_id[:12]>:<hmac8>` is enforced; HMAC computed with `HMAC-SHA256`, compared in constant time; TWO secrets accepted via `CALLBACK_HMAC_SECRETS=<current>,<previous>`; `from_user.id == OPERATOR_USER_ID` validated BEFORE HMAC compare; replay protection comes from the state-machine CAS (`status != preview-sent` → "too late") — verify it actually rejects a second click on the same job.
   - **(4) Sops env leakage** — `/etc/blogger.env` permissions 0600 root:root; `TELEGRAM_BOT_TOKEN` (publisher) is NOT exported to claude / analyze_blog / mnemonik-mcp subprocess envs (only to the in-process `blogger_inproc` kind); no `os.environ` global leak path; no logging of token values.
   - **(5) Topic-filter correctness** — bot REJECTS messages NOT from operator OR NOT in the correct `(chat_id, message_thread_id)` tuple (AC-T12). All 3 negative dimensions (wrong chat, wrong thread, wrong sender) must be rejected; positive case enqueues. Verify the handler decorator filters AND has belt-and-suspenders runtime checks (defense in depth).
   - **(6) Publisher bot token leakage** — excepthook scrubs the token from any traceback that may include `article_text` / `prompt` / env; coredumps disabled (`LimitCORE=0` in the systemd unit); `ProtectSystem=strict`, `NoNewPrivileges=true` present; token never written to logs or queue records.
   - **(7) Attestation content-sha256 binding** — `mnemonic_sign_memory` MCP `tools/call` payload includes BOTH `content` and `content_sha256` (recomputed by the worker from the on-disk article bytes); worker also stores `content_sha256` in the queue record so future tamper detection is possible; `post_url`, `score`, `prompt` are bound into the attestation metadata.
   - **(8) Auto-mode two-location integrity** — Decision 7: worker startup checks `PUBLISH_MODE=auto` against `PUBLISH_AUTO_MODE_TOKEN` matching sops `publish_auto_mode_token`; mismatch aborts loud (no silent downgrade, no partial-mode); the env-flip-alone single-point attack is genuinely closed.
   - **(9) Supply chain** — `pip install --require-hashes -r requirements.locked.txt` is the actual install command (not `pip install .`); the locked file has hashes for every package; `npm ci` (not `npm install`) is used when installing `@mnemonik-xyz/mcp` with a checked-in `package-lock.json`; pinned commit SHAs for `mnemonik-dev/blogger` and `mnemonik-dev/claude-blog`.
   - **(10) Posts-per-hour rate ceiling in worker** — security finding R2-9 (cited in tech-spec Task 6): the worker enforces a posts-per-hour rate ceiling on the publish path; verify the implementation actually rejects publish on burst (e.g., when the per-hour counter is at/over the configured cap). Verify the counter window is correctly bounded (sliding or fixed-hour), is process-restart safe (or explicitly documented as best-effort across restarts), and the rejection path leaves the job in a recoverable state with a clear log line — not silently dropped.
   - **(11) Bot's 10-minute sliding-window HMAC-failure counter** — Task 8: bot tracks HMAC-validation failures in a 10-minute sliding window and emits an alert when the threshold is crossed; verify (a) the sliding window actually slides (entries older than 10 minutes are evicted, not accumulated forever), (b) the counter resets correctly after the window passes with no new failures, (c) the alert message is actually delivered to the operator (not lost on send failure), (d) the counter is per-process and does not crash on bot restart.
4. For every OWASP category and every focus item, record a verdict: PASS, FAIL, or N/A with one-line rationale. For FAIL findings, capture: file:line, attack vector, severity (Critical/High/Medium/Low), suggested fix.
5. Write the structured report to `work/content-publish-pipeline/logs/audit/security-audit.json` (see format below).
6. If any Critical or High finding is present, return a clear summary at the top of the JSON — pre-deploy QA (Task 12) MUST gate on Critical/High count == 0.

## Acceptance Criteria

- [ ] All files listed under "Files" have been read and audited.
- [ ] All 10 OWASP categories have a recorded verdict (PASS / FAIL / N/A with rationale).
- [ ] All 11 focus items from the hit list have a recorded verdict.
- [ ] Every FAIL finding includes: file path, line number(s) where applicable, attack vector, severity, suggested fix.
- [ ] AC-T6 (operator authorization + HMAC) explicitly verified — foreign `from_user.id` rejected, tampered HMAC rejected.
- [ ] AC-T8 (mnemonik-mcp argv hygiene) explicitly verified — argv is exactly `[<binary>, "mcp-stdio"]`.
- [ ] AC-T9 (env isolation) explicitly verified — per-subprocess allowlist enforced.
- [ ] AC-T10 (auto-mode two-location binding) explicitly verified — mismatched token aborts.
- [ ] AC-T12 (topic filter correctness) explicitly verified — all 3 negative dimensions rejected.
- [ ] Report file written to `work/content-publish-pipeline/logs/audit/security-audit.json` and is valid JSON.
- [ ] Report top-level fields include: `summary`, `owasp_findings` (one entry per category), `focus_findings` (one entry per hit-list item), `critical_high_count`, `passed`.

## Context Files

**Feature-specific:**
- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md)

**Project context:**
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — project architecture, infrastructure layout, security rules, secrets handling conventions
- [handoff-bot-mcp-debug-2026-05-23.md](/Users/syi/src/sessions/coding-fabric/.claude/skills/project-knowledge/references/handoff-bot-mcp-debug-2026-05-23.md) — reference notes on bot MCP integration patterns

**Implementation surface (files to audit — from Tasks 1–8):**

Task 1 (sops + secrets):
- `infrastructure/secrets/secrets.sops.yml.template`
- `infrastructure/secrets/secrets.sops.yml`
- `infrastructure/ansible/roles/fabric-services/tasks/main.yml` (sops load)

Task 2 (mnemonik-mcp role):
- `infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml`
- `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml`
- `infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2`
- `infrastructure/ansible/roles/mnemonic-mcp/files/package-lock.json`
- `infrastructure/ansible/roles/mnemonic-mcp/README.md`

Task 3 (content-publisher Ansible role):
- `infrastructure/ansible/roles/content-publisher/tasks/main.yml`
- `infrastructure/ansible/roles/content-publisher/defaults/main.yml`
- `infrastructure/ansible/roles/content-publisher/templates/blogger.env.j2`
- `infrastructure/ansible/roles/content-publisher/templates/blogger.mcp.json.j2`
- `infrastructure/ansible/roles/content-publisher/templates/content-publisher.service.j2`
- `infrastructure/ansible/roles/content-publisher/requirements.locked.txt`
- `infrastructure/ansible/roles/content-publisher/README.md`
- `infrastructure/ansible/playbooks/deploy.yml`

Task 4 (queue + state + env):
- `fabric/content-publisher/pyproject.toml`
- `fabric/content-publisher/src/content_publisher/state.py`
- `fabric/content-publisher/src/content_publisher/queue.py`
- `fabric/content-publisher/src/content_publisher/models.py`
- `fabric/content-publisher/src/content_publisher/env.py`
- `fabric/content-publisher/src/content_publisher/__init__.py`

Task 5 (spawn + score + render):
- `fabric/content-publisher/src/content_publisher/worker.py`
- `fabric/content-publisher/src/content_publisher/spawn.py`
- `fabric/content-publisher/src/content_publisher/score.py`
- `fabric/content-publisher/src/content_publisher/render.py`

Task 6 (publish + sub-loops + attestation + recovery):
- `fabric/content-publisher/src/content_publisher/publish.py`
- `fabric/content-publisher/src/content_publisher/deadline.py`
- `fabric/content-publisher/src/content_publisher/cleanup.py`
- `fabric/content-publisher/src/content_publisher/attest.py`
- `fabric/content-publisher/src/content_publisher/recovery.py`
- `fabric/content-publisher/src/content_publisher/main.py`
- `fabric/content-publisher/src/content_publisher/mcp_client.py`

Task 7 (wire service):
- `infrastructure/ansible/roles/content-publisher/tasks/main.yml` (deltas)
- `infrastructure/ansible/roles/content-publisher/handlers/main.yml`

Task 8 (bot handlers):
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py`
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py`

## Verification Steps

### Automated

- `python -c "import json; json.load(open('work/content-publish-pipeline/logs/audit/security-audit.json'))"` → exits 0 (report is valid JSON).
- `python -c "import json; r=json.load(open('work/content-publish-pipeline/logs/audit/security-audit.json')); assert len(r['owasp_findings'])==10; assert len(r['focus_findings'])==11; assert 'critical_high_count' in r; assert 'passed' in r"` → exits 0 (all 10 OWASP + 11 focus items recorded; required fields present).

## Details

**Files to audit:** see Context Files → Implementation surface (Tasks 1–8 — 30+ files spanning Ansible roles, secrets templates, the new `fabric/content-publisher/` Python package, and the bot's new `publish.py` handler).

**Output file:** `work/content-publish-pipeline/logs/audit/security-audit.json`. Create the parent directory if missing (`mkdir -p work/content-publish-pipeline/logs/audit/`).

**Report JSON shape:**

```json
{
  "summary": "1-3 sentence verdict — pass/fail, key issues.",
  "passed": true,
  "critical_high_count": 0,
  "owasp_findings": [
    {
      "category": "A01_broken_access_control",
      "verdict": "PASS|FAIL|N/A",
      "rationale": "...",
      "findings": [
        {
          "severity": "Critical|High|Medium|Low",
          "file": "path/to/file.py",
          "line": 42,
          "issue": "...",
          "attack_vector": "...",
          "suggested_fix": "..."
        }
      ]
    }
  ],
  "focus_findings": [
    {
      "focus": "subprocess_argv_env_hygiene",
      "verdict": "PASS|FAIL|N/A",
      "rationale": "...",
      "findings": [ ]
    }
  ]
}
```

The 10 OWASP categories to record (2021+): `A01_broken_access_control`, `A02_cryptographic_failures`, `A03_injection`, `A04_insecure_design`, `A05_security_misconfiguration`, `A06_vulnerable_outdated_components`, `A07_identification_authentication_failures`, `A08_software_data_integrity_failures`, `A09_security_logging_monitoring_failures`, `A10_ssrf`.

The 11 focus items: `subprocess_argv_env_hygiene`, `path_traversal_worktree`, `callback_hmac_replay_rotation`, `sops_env_leakage`, `topic_filter_correctness`, `publisher_token_leakage`, `attestation_content_sha256_binding`, `auto_mode_two_location_integrity`, `supply_chain_pinning`, `worker_posts_per_hour_rate_ceiling`, `bot_hmac_failure_sliding_window_alert`.

**Dependencies:** Tasks 1–8 must be done. No package install needed for the audit itself — it is read-only.

**Edge cases to probe:**
- Token rotation window: an in-flight preview signed with `<previous>` secret must still validate after a rotation that puts `<current>,<previous>` in the env.
- Constant-time HMAC compare (`hmac.compare_digest`) — NOT `==`.
- The bot writes to the queue directly (Decision 13); verify the bot process actually has matching uid/gid to satisfy `0600 op:op` on `queue.jsonl`.
- `LimitCORE=0` is in the unit BUT also verify no Python `faulthandler` writes a tb dump to a world-readable path.
- Verify `excepthook` is installed in `main.py` startup (not just defined somewhere unreachable).
- The bot's preview poll reads the queue — ensure it does not log job records containing `prompt` text at a level that ends up in journalctl by default.
- `package-lock.json` must be checked in (Task 2 deliverable) AND `npm ci` (not `npm install`) used.
- `requirements.locked.txt` must contain `--hash=sha256:...` lines for every dep including transitive.
- Empty/missing `hetzner_volume_id` Ansible fact: verify Decision 10 fail-loud actually fires (no silent fallback to root disk that could leak via different perms).

**Implementation hints:**
- Use `grep -rn "subprocess" fabric/content-publisher/src/` to enumerate every subprocess invocation, then audit each one's `args=`, `env=`, `stdin=` arguments.
- Use `grep -rn "TELEGRAM_BOT_TOKEN\|publisher.*token\|blogger_telegram" --include="*.py" --include="*.j2"` to trace token flow.
- Use `grep -rn "hmac\|HMAC\|compare_digest\|callback_data" mnemonik-bridge-workspace/telegram-ai-agent/src/` to find HMAC implementation.
- Use `grep -rn "uuid\|UUID\|worktree" fabric/content-publisher/src/` for path-traversal audit.
- Use `grep -n "mode:\|owner:\|group:" infrastructure/ansible/roles/content-publisher/tasks/main.yml` to verify file permissions.
- Use `grep -n "RequiresMountsFor\|LimitCORE\|ProtectSystem\|NoNewPrivileges" infrastructure/ansible/roles/content-publisher/templates/content-publisher.service.j2` to verify systemd hardening.
- Cross-check the `restricted_env(kind)` allowlist in `env.py` against the tech-spec table.
- Tests in `test_env_isolation.py`, `test_publish_handlers.py`, `test_topic_filter.py`, `test_attest_envelope.py`, `test_auto_mode_token_binding.py` show the intended invariants — use them as a checklist of what the production code must enforce.

## Reviewers

(none — this task IS the security review; its output is the report at `logs/audit/security-audit.json`)

## Post-completion

- [ ] Записать краткий отчёт в `work/content-publish-pipeline/decisions.md`: 1–3 предложения с вердиктом (passed: true/false), счётчиком Critical/High, ссылкой на `logs/audit/security-audit.json`.
- [ ] Если найдены Critical или High — пометить Task 12 (Pre-deploy QA) как заблокированный до устранения; перечислить блокеры в decisions.md.
- [ ] Если отклонились от спека — описать отклонение и причину.
- [ ] Обновить user-spec/tech-spec если что-то изменилось (например, новое security-решение, которое нужно зафиксировать как Decision).
