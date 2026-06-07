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

## Task 2: Rewrite Mnemonik MCP role for npm scoped-package install + binary-name discovery

**Status:** Done
**Commits:** 44011b0 (impl), 7057e43 (review-fix round 1), 6ba3140 (reports)
**Agent:** task-2-mnemonik-mcp
**Summary:** Replaced the dead binary-download distribution channel in `infrastructure/ansible/roles/mnemonic-mcp/` with the upstream-supported npm scoped package `@mnemonik-xyz/mcp@0.2.4`. Single-step install via `npm ci` against the checked-in `files/package-lock.json` (lockfileVersion 3, root strict-pinned, every transitive integrity hash fixed) is the sole audited supply-chain anchor — the binary is exposed system-wide via a `/usr/local/bin/mnemonik-mcp` symlink into `node_modules/.bin/`, so the byte sequence on PATH is exactly what the lockfile installed (no second registry resolution). Binary-name discovery cascade (`which mnemonik-mcp || which mnemonic-mcp`) writes Ansible fact `mnemonic_mcp_binary`, consumed by the systemd unit (`ExecStart={{ mnemonic_mcp_binary }} mcp-stdio`, `Type=simple`) and downstream by Task 3's `/etc/blogger.mcp.json`. Post-install smoke runs an MCP JSON-RPC `initialize` handshake over `mcp-stdio` — payload via stdin, nothing of substance in argv. Vaultwarden `LoadCredential` flow, `config.yml`, `protocol-qa.env`, `molyanov-mnemonic-hooks.yml.j2`, and the 5 hook scripts removed (descoped-era artefacts; new role is long-running-stdio-server-shaped). Eight pytest contract tests under `tests/test_role_contract.py` enforce: npm pin, discovery cascade with `failed_when rc != 0`, systemd unit consumes the fact + no `LoadCredential`, lockfile valid + contains `@mnemonik-xyz/mcp`, descoped artefacts gone, no `sign-memory` substring anywhere, `ansible-lint .` rc=0, `ansible-playbook --syntax-check` rc=0. Role-local `.ansible-lint` skips `role-name` (pre-existing kebab-case convention across the repo's nine roles) and excludes `molecule/default/converge.yml` (molecule injects `ANSIBLE_ROLES_PATH` at runtime; not in CI as of 2026-06).
**Deviations:** None. The skeptic-round-3 hint that the binary was `mnemonik-mcp` (with K) on `@mnemonik-xyz/mcp@0.2.4` was confirmed offline before commit (`npm ci` + `--help` listing `install / mcp-stdio / doctor`). The two-step install originally drafted (local `npm ci` + `community.general.npm` global) was collapsed to single-step + symlink during round 1 review per security-auditor T2-SEC-002 — the original two-step would have resolved transitives twice (once from the lockfile, once fresh from the registry), defeating the supply-chain pin. Molecule scenario kept as local-only with a README TODO note since it isn't wired into CI.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_minor — 3 minor (T2-1 binary fact undefined when role disabled; T2-2 `npm ci` always marked changed; T2-3 stale descoped binary may shadow discovery cascade) → [logs/working/task-2/code-reviewer-1.json](logs/working/task-2/code-reviewer-1.json)
- security-auditor: APPROVED — 2 LOW (T2-SEC-001 `--no-audit` suppresses CVE info; T2-SEC-002 two-step install creates lockfile/binary divergence), 6 PASS/INFO → [logs/working/task-2/security-auditor-1.json](logs/working/task-2/security-auditor-1.json)

*Round 2 (after fixes):*
- code-reviewer: approved — all 3 findings resolved; one informational note (test coverage of new symlink/single-step structure) deferred → [logs/working/task-2/code-reviewer-2.json](logs/working/task-2/code-reviewer-2.json)
- security-auditor: APPROVED — CLOSED, no new findings → [logs/working/task-2/security-auditor-2.json](logs/working/task-2/security-auditor-2.json)

**Verification:**
- `pytest infrastructure/ansible/roles/mnemonic-mcp/tests/ -v` → 8/8 pass
- `ansible-lint .` (in role dir, picks up role-local config) → 0 failures
- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` (via test wrapper) → rc=0
- Offline-verified upstream surface: `npm ci` from generated lockfile installs cleanly; `node_modules/.bin/mnemonik-mcp --help` lists `install / mcp-stdio / doctor` — NO `sign-memory` subcommand on the npm-shipped binary (tech-spec Decision 3 anchor preserved)
- Live VM smoke (post-deploy `systemctl is-active mnemonic-mcp`, real `mcp-stdio` handshake, lockfile sha drift check) deferred to Task 13 / 14

## Task 4: `fabric/content-publisher/` — queue + state machine + CAS + env isolation

**Status:** Done
**Commits:** 59b9289 (impl), 3027ed3 (review-fix round 1), then review reports
**Agent:** task-4-queue-cas
**Summary:** Created new Python package `fabric/content-publisher/` with the SHARED CAS primitive both the long-running worker (Tasks 5–6) and the Telegram bot (Task 8) will import — single source of truth for every mutation of `queue.jsonl` on the Hetzner persistent volume. `state.py` carries the ~30 LOC flock/atomic-rename pattern from `workspace-manager/state.py` (module-level functions taking path parameters — copied, NOT imported, per AC10). `queue.py` exposes four functions — `append_job`, `load`, `cas_status`, `find_by_prefix` — implemented under `fcntl.flock(LOCK_EX)` + read-modify-write + `os.replace()` (tech-spec Decision 2). `cas_status` auto-sets `published_at` on `publishing→published` and `attest_pending_since` on `published→attest-pending` (caller-supplied `updates` apply after auto-set and may override). `find_by_prefix` accepts the 12-character SLICE form `job.id[:12]` (8 hex + `-` + 3 hex — what the bot actually stores in Telegram callback_data, capped at 64 bytes), fail-loud on ambiguous prefix to prevent publishing the wrong operator's content from a misrouted callback. `models.Job` has 31 fields including the three new lifecycle fields (`published_at`, `attest_pending_since`, `error_message`), `extra="forbid"` to catch typos in `updates` dicts, and a strict regex UUID v4 validator that rejects path traversal, control chars, uppercase hex, and non-v4 UUIDs at the model boundary. `env.py` builds child-process env from an allowlist per kind (claude / analyze_blog / mnemonik-mcp / blogger_inproc), fail-loud if neither `CLAUDE_CODE_OAUTH_TOKEN` nor `ANTHROPIC_API_KEY` is set for `claude`. Centralized `tests/conftest.py` carries `tmp_queue_dir` / `tmp_queue_path` / `make_job` factory + four placeholder mock fixtures (documented return shapes) used by Tasks 5/6/8. The race-safety invariant is proved by `test_cas_status_atomic_under_concurrent_clicks` using `threading.Barrier(2)` (exactly one CAS succeeds, the other gets `StaleStateError`). Round 1 review also surfaced a data-loss hazard: the round-1 `cas_status` silently dropped malformed JSONL lines on rewrite, so a half-written line from a previous kill-9 would be permanently deleted on the next CAS; fixed by preserving unparseable lines verbatim as opaque pass-through entries (`entries: list[Job | str]`), with `test_cas_status_preserves_unknown_lines` pinning the invariant.
**Deviations:** Two minor variances from the TDD Anchor wording:
  1. `find_by_prefix` accepts the 12-char SLICE form (`job.id[:12]` = 8 hex + dash + 3 hex), not 12 hex characters. The TDD Anchor tests themselves use `j.id[:12]` directly, so this matches what Task 8 (bot callback resolver) will actually pass at the call site. Spec text saying "12 hex chars" was inconsistent with the spec's own tests; chose the test-driven form.
  2. `test_illegal_transitions_raise` was collapsed to assert against the static `Job.allowed_transitions()` map for the 8 ILLEGAL_TRANSITIONS pairs, plus a separate `test_cas_status_raises_on_illegal_transition` that pins the cas_status → `IllegalTransitionError` wiring with a pre/post file-immutability check. Per-edge end-to-end CAS tests would have required walking every job through a multi-step legal path to each src state (and many illegal-source states like `done` / `rejected` are not reachable from `queued` without first taking another illegal transition); the collapsed form is strictly stronger and avoids the bootstrap problem.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_minor — T4-1 SCORING→FAILED missing (FALSE POSITIVE, mapping already at models.py:119), T4-2 cas_status drops malformed lines on rewrite (data-loss hazard), T4-3 static-vs-locked confusion (comment only) → [logs/working/task-4/code-reviewer-round1.json](logs/working/task-4/code-reviewer-round1.json)
- test-reviewer: passed — F1 weak caplog assertion (medium), F2 no file-immutability after IllegalTransitionError (low), F3 conftest placeholder fixtures undocumented (low) → [logs/working/task-4/test-reviewer-round1.json](logs/working/task-4/test-reviewer-round1.json)

*Round 2 (after fixes):*
- code-reviewer: approved — T4-1 confirmed false positive (mapping was present in original commit); T4-2 elegantly fixed with `list[Job | str]` + isinstance guard; T4-3 comment applied; all test-reviewer findings fixed → [logs/working/task-4/code-reviewer-round2.json](logs/working/task-4/code-reviewer-round2.json)
- test-reviewer: passed — all three findings resolved correctly; new test_cas_status_preserves_unknown_lines well-structured → [logs/working/task-4/test-reviewer-round2.json](logs/working/task-4/test-reviewer-round2.json)

**Verification:**
- `pytest fabric/content-publisher/tests/ -v` → 34/34 pass (including the critical `test_cas_status_atomic_under_concurrent_clicks`)
- `ruff check fabric/content-publisher/` → clean
- `mypy fabric/content-publisher/src/` → clean (strict mode)
- `grep -rE "from workspace_manager|import workspace_manager" fabric/content-publisher/src/` → empty (AC10 decoupling)
