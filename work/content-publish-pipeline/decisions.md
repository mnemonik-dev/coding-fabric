# Decisions Log: content-publish-pipeline

Agent reports on completed tasks. Each entry is written by the agent that executed the task.

---

<!-- Entries are added by agents as tasks are completed.

Format is strict — use only these sections, do not add others.
Do not include: file lists, findings tables, JSON reports, step-by-step logs.
Review details — in JSON files via links. QA report — in logs/working/.

## Task N: [title]

**Status:** Done
**Commit:** abc1234
**Agent:** [teammate name or "main agent"]
**Summary:** 1-3 sentences: what was done, key decisions. Not a file list.
**Deviations:** None / Deviated from spec: [reason], did [what].

**Reviews:**

*Round 1:*
- code-reviewer: 2 findings → [logs/working/task-N/code-reviewer-1.json]
- security-auditor: OK → [logs/working/task-N/security-auditor-1.json]

*Round 2 (after fixes):*
- code-reviewer: OK → [logs/working/task-N/code-reviewer-2.json]

**Verification:**
- `npm test` → 42 passed
- Manual check → OK

-->

## Task 1: Sops + secrets template extension

**Status:** Done
**Commit:** 2bdef02 (impl), 66b2684 (review-fix), 2decc7f (reports)
**Agent:** task-1-sops
**Summary:** Added 10 content-publisher secrets/configs (blogger_telegram_bot_token, blogger_telegram_channel, blogger_prompts_topic_id, publish_min_score, publish_approval_timeout_min, publish_mode, publish_auto_mode_token, blogger_repo_ref, claude_blog_repo_ref, publish_callback_hmac_secrets) to `infrastructure/secrets/secrets.sops.yml.template` (documented section `# === Content Publisher ===`) and the encrypted `secrets.sops.yml` (placeholder values via `sops set` + editor mode for proper int types). Extended the existing `set_fact` "Expose sops keys as flat vault_* facts" block in `playbooks/deploy.yml` `pre_tasks` with 10 new `vault_*` mappings (all with safe `default(...)`) under the existing `no_log: true`. Key decisions during review: re-encrypted numeric keys via sops editor to coerce envelope from `type:float` to `type:int` (avoids `int("80.0")` worker parse error); added explicit template comments documenting the always-edit-via-sops invariant for `blogger_telegram_bot_token` and the ≥32-byte HMAC key length contract for `publish_callback_hmac_secrets`.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_minor — 2 minor (type:float→int; doc note on unencrypted empty bot token) → [logs/working/task-1/code-reviewer-round1.json](logs/working/task-1/code-reviewer-round1.json)
- security-auditor: APPROVED — 2 LOW (sops-edit warning, HMAC min-length), 3 INFO, 1 LOW deferred to Task 8 → [logs/working/task-1/security-auditor-round1.json](logs/working/task-1/security-auditor-round1.json)

*Round 2 (after fixes):*
- code-reviewer: approved — all round 1 findings resolved → [logs/working/task-1/code-reviewer-round2.json](logs/working/task-1/code-reviewer-round2.json)
- security-auditor: APPROVED — CLOSED, no new findings → [logs/working/task-1/security-auditor-round2.json](logs/working/task-1/security-auditor-round2.json)

**Verification:**
- `sops -d secrets.sops.yml` → all 10 keys present, correct types (str/int/str)
- `grep -c -E '^(blogger_…|publish_…):' template` → 10 matches
- `ansible-playbook --syntax-check playbooks/deploy.yml` → no syntax errors
- `git diff` confirms only additions; existing 20+ `vault_*` facts untouched

## Task 3: Ansible role `content-publisher` — install scaffold + upstream verification + README runbooks

**Status:** Done
**Commits:** 25ec80f (impl), bb0e0e2 (review-fix round 1), c22acff (review-fix round 2), then review reports
**Agent:** task-3-content-publisher-scaffold
**Summary:** Created `infrastructure/ansible/roles/content-publisher/` (tasks/defaults/handlers/meta + 3 j2 templates + requirements.locked.txt + README) and inserted it as Phase 7.5 in `playbooks/deploy.yml` between fabric-services and telegram-ai-agent. Role pins `blogger_repo_ref` + `claude_blog_repo_ref` as 40-char SHAs via sops (defaults empty, preflight assert rejects tags/branches), resolves `hetzner_volume_id` through `ansible_facts.mounts` selectattr on `/dev/sdb` (Decision 10 — no `*` literal reaches the unit), installs blogger into `/opt/blogger/venv/` via `pip install --require-hashes` (Decision 12), runs the Decision 11 import + `inspect.signature` assertion (Platform, Settings, ingest_article, render, run_campaign_from_article keyword-only), and renders three templates: `/etc/blogger.env` (0600 root:root), `/etc/blogger.mcp.json` (0640 root:op), and `content-publisher.service` (with `RequiresMountsFor=`, `ConditionPathIsMountPoint=`, `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`). Unit enabled but stopped per Wave 1 contract. README ships four runbooks (HMAC rotation, PUBLISH_MODE=auto switch, publisher-token rotation, SHA bump + lockfile regen). Both `blogger_repo_ref` and `claude_blog_repo_ref` are sourced from sops (Task 1 facts) — concrete SHA values must be set in sops before first deploy.
**Deviations:** `requirements.locked.txt` ships a fail-closed PLACEHOLDER entry (bogus sha256) instead of real pinned hashes — blogger is a private upstream repo and pre-generating real hashes from this scaffold would bind to whatever transitives a CI runner happened to resolve, defeating Decision 12. README's "How to regenerate requirements.locked.txt" runbook is the explicit gate. Added `ConditionPathIsMountPoint=` to the systemd unit in addition to `RequiresMountsFor=` (round 1 code-reviewer T3-2) — user-spec line 92 / AC15 / Риск 9 require both, and the latter alone allows silent root-disk fallback if a regular directory exists at the mount path.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_minor — 3 minor (sops.* → vault_* in deploy.yml Phase 7.5; missing ConditionPathIsMountPoint=; handler restart guard conflated state vars) → [logs/working/task-3/code-reviewer-1.json](logs/working/task-3/code-reviewer-1.json)
- security-auditor: APPROVED with MEDIUM blocking — 1 MEDIUM (default('') defeats fail-loud for bot token + HMAC), 2 LOW (auto-mode token binding; EnvironmentFile=- dash), 1 INFO (quote token values) → [logs/working/task-3/security-auditor-1.json](logs/working/task-3/security-auditor-1.json)

*Round 2 (after fixes):*
- code-reviewer: approved — all 3 findings resolved; one informational ordering-comment note added in c22acff → [logs/working/task-3/code-reviewer-2.json](logs/working/task-3/code-reviewer-2.json)
- security-auditor: APPROVED — CLOSED, no new findings → [logs/working/task-3/security-auditor-2.json](logs/working/task-3/security-auditor-2.json)

**Verification:**
- `ansible-lint infrastructure/ansible/roles/content-publisher/` → Passed (production profile)
- `ansible-playbook infrastructure/ansible/playbooks/deploy.yml --syntax-check` → exit 0
- Smoke verification (real test deploy — `systemctl is-enabled/active`, file modes, grep `RequiresMountsFor=` for concrete path, import-test exit 0) deferred to Task 7 / 13 when the unit actually starts.
