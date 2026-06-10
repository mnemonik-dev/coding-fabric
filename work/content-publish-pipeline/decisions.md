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
**Amendment:** `blogger_prompts_topic_id` is now legacy fallback only. The deploy pipeline creates the `blogger-prompts` forum topic through `telegram-init` and passes the generated `telegram_topics['blogger-prompts']` thread id to content-publisher; the operator must not create that topic manually.
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

## Task 5: `content-publisher` — claude spawn + analyze_blog + renderer

**Status:** Done
**Commits:** 2d8eef3 (impl), adc63d9 (review-fix round 1), d5ccf91 (review reports)
**Agent:** task-5-spawn-render
**Summary:** Added the writing→scoring→preview-sent step of the worker. Four new modules under `fabric/content-publisher/src/content_publisher/`: `spawn.py` runs `claude` via `asyncio.create_subprocess_exec` with a fixed argv tuple (`--print --mcp-config /etc/blogger.mcp.json --strict-mcp-config --skill claude-blog/blog-writer`) and the operator-controlled prompt piped through stdin via `proc.communicate(input=...)` — never on argv (Decision 1, AC12); `score.py` runs `analyze_blog.py` from `$MNEMONIK_CLAUDE_BLOG_PATH/scripts/`, parses JSON, truncates issues to the first three INSIDE the module so the preview cap is enforced once; `render.py` calls the Decision 4 three-line preview render (`Platform`/`ingest_article`/`render`) — imports live inside the function so dev-venvs without the fully-landed upstream snapshot can still collect tests, with Decision 11's deploy-time `inspect.signature` assertion as the production gate; `worker.py` ties them together as one coroutine `run_writing_to_preview(job)` that validates the UUID v4 (using the same `UUID4_RE` regex as the `Job.id` Pydantic validator), enforces worktree containment, CAS `queued→writing→scoring→preview-sent` with all preview metadata (`score`, `issues`, `preview_segments`, `approval_deadline`, `preview_pending=True`, `cleanup_at`) written in one CAS so the Task 6 deadline-checker cannot race the writer. Any step-level failure CAS's to `failed` with `notify_pending=True` and an `error_message` containing the stderr tail; worktree is intentionally preserved for cleanup-gc to reclaim on `cleanup_at`. Five new test files (test_preview_render, test_analyze_gate, test_spawn_failure_path, test_separate_bot_token, test_required_mounts_for) covering all 10 TDD Anchor items plus three additional anchors (high-score-with-issues, prompt-via-stdin adversarial, missing-article-after-success).
**Deviations:**
  1. `mnemonik_blogger` imports in `render.py` are inside the function rather than at module top. The exact Decision 4 import strings still appear verbatim (regression-guarded by `test_render_uses_config_platform_import` + `test_render_uses_documented_ingest_and_dispatcher_imports`), but deferring them inside the function lets test modules that import `render_preview` be collected on a dev-venv whose `mnemonik_blogger` snapshot is missing `ingest_article` (the local snapshot at `/Users/syi/src/sessions/blogger` lacks both `ingest_article` and `run_campaign_from_article`). Decision 11's install-time signature assertion is the production gate; the in-function imports do not change runtime behaviour or the error-loud-on-missing-upstream contract.
  2. The byte-equality test `test_preview_segments_byte_equal_to_publish_segments` is currently **skipped** under `pytest.importorskip` because the upstream snapshot installed in the local dev-venv does not yet ship `mnemonik_blogger.content.ingest.ingest_article` or `mnemonik_blogger.agent.run_campaign_from_article`. The verified seam is `mnemonik_blogger/publish/telegram.py::TelegramPublisher.publish` (NOT the speculative `mnemonik_blogger.agent._publish_post` mentioned in the tech-spec's Testing Strategy note — that seam does not exist in the upstream code). The test is structured to lift the skip automatically once the pinned `blogger_repo_ref` SHA exposes the documented API; the skip is loud (records the missing module name in the pytest summary). The Smoke check #2 from the task spec (`from content_publisher.render import render_preview; ... render_preview(short_article.md)`) is **deferred** to Task 7's on-VM verification for the same reason — the dev-venv smoke would just re-trigger the deploy-gate failure that Decision 11 was designed to surface.
  3. Added `MNEMONIK_MIN_SCORE` as a module-level env-driven constant in `worker.py` so tests can assert that the high-score path actually crosses the gate (`refreshed.score >= worker.MNEMONIK_MIN_SCORE`) — without this, a bug that ignored the threshold entirely would still pass test_high_score_no_retry_marker.
  4. `score.py` resolves the analyze_blog.py path from `MNEMONIK_CLAUDE_BLOG_PATH` at call time (security-auditor round 1 fix). Tech-spec text mentioned a hardcoded `/opt/claude-blog/scripts/analyze_blog.py` constant; the env-driven form keeps the runtime path in sync with the env var that `restricted_env('analyze_blog')` already passes to the subprocess, so operator sops changes take effect without a code change.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_minor — T5-1 worktree.mkdir before CAS leaked dir on race, T5-2 no subprocess cleanup on CancelledError, T5-3 uuid.UUID() laxer than model regex → [logs/working/task-5/code-reviewer-round1.json](logs/working/task-5/code-reviewer-round1.json)
- test-reviewer: needs_improvement — F1 high-score test didn't pin threshold (medium), F2 Settings(dry_run=False) would ValidationError when upstream lands (medium), F3 no time-bracket on deadlines (low) → [logs/working/task-5/test-reviewer-round1.json](logs/working/task-5/test-reviewer-round1.json)
- security-auditor: APPROVED — T5-SEC-001 hardcoded analyze_blog path ignored MNEMONIK_CLAUDE_BLOG_PATH (low), plus PASS/INFO on argv hygiene, path containment, AC12 env isolation, fail-loud auth, JSON parsing → [logs/working/task-5/security-auditor-round1.json](logs/working/task-5/security-auditor-round1.json)

*Round 2 (after fixes):*
- code-reviewer: approved — all three findings resolved; ANALYZE_BLOG_PATH dynamicization noted as bonus → [logs/working/task-5/code-reviewer-round2.json](logs/working/task-5/code-reviewer-round2.json)
- test-reviewer: passed — F1/F2/F3 resolved; the two implementation-side fixes (orphan SIGTERM cleanup + worktree.mkdir reorder) noted as sound and not needing new tests → [logs/working/task-5/test-reviewer-round2.json](logs/working/task-5/test-reviewer-round2.json)
- security-auditor: APPROVED — CLOSED — T5-SEC-001 fixed exactly as recommended; the orphan-subprocess and worktree-mkdir-after-CAS fixes also noted as carrying security-adjacent benefit → [logs/working/task-5/security-auditor-round2.json](logs/working/task-5/security-auditor-round2.json)

**Verification:**
- `pytest fabric/content-publisher/tests/ -v` → 48 passed, 1 skipped (the byte-equality test pending upstream `ingest_article` / `run_campaign_from_article` via Decision 11)
- `ruff check fabric/content-publisher/src/ fabric/content-publisher/tests/` → clean
- `mypy fabric/content-publisher/src/` → clean (strict mode, with `ignore_missing_imports = true` override for `mnemonik_blogger.*` since upstream lacks py.typed)
- Smoke check #1 (`Platform.TELEGRAM.value == 'telegram'`) → exit 0 against the local blogger snapshot
- Smoke check #3 (`restricted_env('claude')` allowlist) → exit 0
- Smoke check #2 (`render_preview` on the fixture) → deferred to Task 7 on-VM verification (local dev-venv lacks `ingest_article`)

## Task 6: `content-publisher` — publish step + 3 sub-loops + MCP-stdio attestation + just-retry recovery

**Status:** Done
**Commits:** cce889f (impl), 0372985 (review-fix round 1 / code-reviewer), 477e157 (review-fix round 1 / test-reviewer + security-auditor)
**Agent:** task-6-publish-attest
**Summary:** Added the publish→done leg of the worker plus its bootstrap glue: `publish.py` writes `tentative_publish_started_at` BEFORE the strictly keyword-only in-process call to `run_campaign_from_article(article=..., platforms=[Platform.TELEGRAM], settings=..., attest=False, min_score=...)` and CAS-es `publishing→published` on `pr.ok` (storing `pr.primary_url`, `pr.ids`, sha256 of `article.md`) or `publishing→publish-failed` with `pr.error + notify_pending=true` otherwise; before any of that it runs a posts-per-hour rate-ceiling pre-check (security R2-9) that CAS-es to `publish-failed` without an in-process call when `>N` posts have landed in the last 60 minutes. `deadline.py` runs the 30s sub-loop: `preview-sent AND approval_deadline<=now AND preview_pending==false` → CAS `publishing`, and `attest-pending` due (5min cadence from `attest_pending_since`) → `attest_once`. `cleanup.py` is the 5min sub-loop: terminal jobs with `cleanup_at<=now` get archived to `queue.archive.jsonl` (same flock + atomic-rename pattern), then their worktree is `rmtree`'d (`FileNotFoundError` treated as success), then the queue row is dropped — archive-before-rewrite so a crash replays as a re-archive, not a lost job. `attest.py` orchestrates: first failure CAS `published→attest-pending` (stickying `attest_pending_since` via the auto-set in `queue.cas_status`), subsequent failures bump `attest_attempts` in-place (NOT a graph transition — direct rewrite under the same flock), and on `now - attest_pending_since >= 24h` CAS `attest-pending → attest-failed` with `notify_pending=true`. `mcp_client.py` speaks MCP JSON-RPC over `<binary> mcp-stdio` with argv strictly two elements (no payload), `env=restricted_env('mnemonik-mcp')` (PATH only), newline-delimited frames (Task 2 fixture-confirmed): `initialize` → `tools/call mnemonic_sign_memory` with `{content, content_sha256, post_url, score, prompt}` → close stdin → drain stderr → return `attestation_hash` from `result.content[0].text`. `recovery.py` is invoked once on startup: `writing`/`scoring` are reset to `queued` via direct rewrite (NOT a graph transition — explicit recovery move under flock); `preview-sent`/`publishing`/`attest-pending` and all terminals are left alone (Decision 8 — `publishing` is JUST RETRIED by the normal publish-step on the next tick; `tentative_publish_started_at` is the operator's forensic timestamp for the rare double-post). `main.py` enforces auto-mode two-location binding (`PUBLISH_AUTO_MODE_TOKEN` vs `PUBLISH_AUTO_MODE_TOKEN_CONFIRM` via `hmac.compare_digest`; empty/missing/mismatch → `SystemExit(2)`), runs `recover_in_flight`, then spawns three asyncio tasks each emitting the exact start line Task 7 smoke greps for (`queue-poll started`, `deadline-checker started`, `cleanup-gc started`). `models.py` got one new state-graph edge (`PUBLISHED→DONE`) so the happy-path attest-on-first-try CAS is legal (the spec text "на успехе CAS published → done" is unambiguous; the original graph only allowed `published→attest-pending`). `worker.py` was extended to handle auto-mode: after `scoring→preview-sent` (with `preview_pending=False`), do an immediate `preview-sent→publishing` CAS so the preview gate is skipped; the deadline-checker can race this second CAS, so the loser's StaleStateError is logged at DEBUG (the race is by-design, both paths lead to the same outcome). 12 new test files (8 unit + 3 integration + 1 publish handlers); all TDD Anchor items covered.

**Deviations:**
  1. **State-graph edge `PUBLISHED→DONE` added.** Tech-spec Data Models diagram only listed `PUBLISHED→ATTEST_PENDING`, but task spec section 4 + AC-T8 verification require the happy-path success CAS to be `published→done` (with `attestation_hash` stored). Forcing the success path through `attest-pending` would flag a healthy attestation as "pending retry" for at least one tick, fire `notify_pending=true` twice, and inflate `attest_attempts` to 1 on every successful job. The added edge is the cleanest expression of the spec and was confirmed safe by both code-reviewer and security-auditor.
  2. **Two-location auto-mode binding rendered from TWO sops keys.** Decision 7 reads "compare `/etc/blogger.env` `PUBLISH_AUTO_MODE_TOKEN` to sops `publish_auto_mode_token`" — there is no in-process sops reader at runtime, so the only durable interpretation is rendering both halves of the comparison from sops at deploy time. Added a SECOND sops key `publish_auto_mode_token_confirm` (Task 1 scope creep, minimum invasion: one new key, one new flat-fact mapping, one new template render line in `blogger.env.j2`). Operator must set both sops keys to the same value via two separate `sops edit` operations; a single stale or partial edit cannot silently re-enable posting. Both reviewers preferred this over rendering the same key into both env vars (which would defeat the integrity check).
  3. **In-place rewrite under flock for non-graph mutations.** Three places need to update queue-line fields without a state transition: `publish._mark_tentative` (tentative timestamp on `PUBLISHING`), `attest._bump_retry_attempts` (retry counter + error_message on `ATTEST_PENDING`), `recovery.recover_in_flight` (writing/scoring → queued reset). Each takes the same `_acquire/_lock_path/_read_lines/_write_raw/_release` flock pattern as `cas_status`. The underscored helpers were exported via `__all__` in `queue.py` so mypy --strict accepts the sibling-module imports. This is consistent with the Task 4 convention but worth flagging as an explicit cross-module use of underscored names.
  4. **notify_pending NOT set on `publishing→published`.** Initial impl set it there (per literal task spec wording "AC (e)" symmetry), but code-reviewer round 1 (T6-2) correctly observed that user-spec step 9 wants ONE consolidated `Опубликовано. Receipt: <hash>.` message after attestation — setting `notify_pending` at publish would fire an incomplete notify. attest.py now owns the only notify-firing CAS-es (`→DONE` / `→ATTEST_PENDING` / `→ATTEST_FAILED`).

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_minor — T6-1 PUBLISH_AUTO_MODE_TOKEN_CONFIRM never rendered (auto-mode broken), T6-2 premature notify_pending on publishing→published (would send notify without receipt), T6-3 informational note on _append_archive duplicate-on-replay → [logs/working/task-6/code-reviewer-round1.json](logs/working/task-6/code-reviewer-round1.json)
- test-reviewer: needs_improvement — F1 auto-mode test missing notify_pending=True assertion at terminal (AC11 regression risk), F2 queue_path kwarg not pinned in deadline-loop attest call, F3 test_tentative_write_ordering setup over-engineered → [logs/working/task-6/test-reviewer-round1.json](logs/working/task-6/test-reviewer-round1.json)
- security-auditor: APPROVED — T6-SEC-001 (LOW) auto-mode CAS-2 logs WARNING for the by-design deadline-checker race, T6-SEC-002 (INFO) PUBLISH_AUTO_MODE_TOKEN_CONFIRM missing from rendered env (same root cause as T6-1), plus PASS on argv hygiene, env restriction, tentative ordering, rate ceiling, constant-time compare, sticky pending_since, recovery just-retry, archive-before-rewrite, PUBLISHED→DONE invariant preservation → [logs/working/task-6/security-auditor-round1.json](logs/working/task-6/security-auditor-round1.json)

*Round 2 (after fixes):*
- code-reviewer: approved — all three findings resolved; non-blocking informational follow-up noted: Task 3 README runbook should document the two-sops-key procedure → [logs/working/task-6/code-reviewer-round2.json](logs/working/task-6/code-reviewer-round2.json)
- test-reviewer: passed — F1/F2/F3 resolved; two-assertion design (`notify_pending=False at PUBLISHED, True at DONE`) noted as stronger than the original suggestion → [logs/working/task-6/test-reviewer-round2.json](logs/working/task-6/test-reviewer-round2.json)
- security-auditor: APPROVED — CLOSED — T6-SEC-001 fixed by lowering log level + clarifying message; T6-SEC-002 fixed with two separate sops keys (noted as architecturally stronger than the original suggestion of rendering one key into two vars) → [logs/working/task-6/security-auditor-round2.json](logs/working/task-6/security-auditor-round2.json)

**Verification:**
- `pytest fabric/content-publisher/tests/ -v` → 95 passed, 1 skipped (the byte-equality test from Task 5, still pending upstream `ingest_article` / `run_campaign_from_article`)
- `ruff check fabric/content-publisher/` → clean
- `mypy fabric/content-publisher/src/` → clean (strict mode)
- `grep -RIn "recovery-needed\|recovery_needed\|RECOVERY_NEEDED" fabric/content-publisher/src/` → only literal mentions in module docstrings explicitly noting the absence; the JobStatus enum check inside `test_publishing_retry_on_crash.py::test_recovery_needed_state_does_not_exist` is the hard gate (Decision 8)
- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` (with ANSIBLE_ROLES_PATH=./roles) → rc=0 after adding the new sops key + flat-fact mapping

**Follow-up (non-blocking, surfaced by code-reviewer round 2):**
- Task 3's `infrastructure/ansible/roles/content-publisher/README.md` operator runbook for "Switching to PUBLISH_MODE=auto" should be updated to call out the new two-sops-key procedure (`publish_auto_mode_token` + `publish_auto_mode_token_confirm` both set to the same value via separate `sops edit` calls). Not blocking Task 6 — operators reading the `blogger.env.j2` comment will still get the right info — but the runbook is the canonical reference.

## Task 8: Bot topic-handler + callbacks + preview dispatch

**Status:** Done
**Agent:** codex
**Summary:** Added the operator-facing Telegram integration for content-publish-pipeline. The nested `telegram-ai-agent` repo now has `core/handlers/publish.py` with a `(chat, thread, operator)` topic filter, plain-text brief enqueue through shared `content_publisher.queue.append_job`, HMAC-signed approve/reject/retry callbacks, retry linkage through `regenerate_job`, a sliding invalid-callback security note, and a background preview/notify dispatch loop registered from `__main__.py` before the catch-all text router. The outer repo adds shared queue support for non-status field updates (`update_job_fields`) so bot bookkeeping can clear `preview_pending` / `notify_pending` without illegal self-transitions, plus deploy wiring so `telegram-ai-agent` gets only the publisher-control env vars it needs, installs the shared `content_publisher` package into its own venv, and has systemd write access to the queue directory without loading `/etc/blogger.env` or overriding the operator bot token.
**Deviations:** The bot does not source `/etc/blogger.env` directly because that file contains `TELEGRAM_BOT_TOKEN` for the separate publisher bot. Instead the deploy renders the required control vars into `/etc/telegram-ai-agent/.env` under non-conflicting names.

**Verification:**
- `fabric/content-publisher/.venv/bin/python -m pytest fabric/content-publisher/tests -q` → 99 passed, 1 skipped.
- `fabric/content-publisher/.venv/bin/python -m ruff check fabric/content-publisher/src/content_publisher/queue.py fabric/content-publisher/tests/test_retry_linkage.py` → clean.
- `mnemonik-bridge-workspace/telegram-ai-agent/.venv/bin/python -m pytest -q` → 79 passed.
- `mnemonik-bridge-workspace/telegram-ai-agent/.venv/bin/python -m ruff check src/telegram_bot/core/handlers/publish.py src/telegram_bot/__main__.py tests/test_publish_handlers.py tests/test_public_runtime.py` → clean.
- `ansible-playbook --syntax-check infrastructure/ansible/playbooks/deploy.yml` → rc=0.

## Task 9: Code Audit

Task 9 (Code Audit): Ruflo was started for an external review, but produced no artifact before this report was finalized; local audit found shared queue/subprocess handling mostly aligned, with one blocker in missing Python traceback redaction hooks, one major stuck-job error path, and one minor queue-helper drift; full report → [logs/audit/code-audit.json](logs/audit/code-audit.json). Counts: blockers=1, majors=1, minors=1, nits=0.

Task 9 remediation: Fixed the audit blocker/major/minor in a follow-up implementation pass: `main.py` now installs neutral traceback redaction hooks for process + asyncio loop failures, `publish_step` transitions missing-article jobs to `publish-failed` with `notify_pending=true`, and tentative publish timestamps now use `queue.update_job_fields` instead of a private local rewrite. Verification: content-publisher pytest 102 passed, 1 skipped; touched-file ruff clean.

## Task 10: Security Audit

Task 10 (Security Audit): passed=true with Critical/High=0; report found two Medium follow-ups (per-secret callback HMAC validation, in-handler topic guard) and one Low monitoring hardening item (alert-send failure handling); full report -> [logs/audit/security-audit.json](logs/audit/security-audit.json).
