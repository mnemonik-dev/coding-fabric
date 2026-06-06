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
