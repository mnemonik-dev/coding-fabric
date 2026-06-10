---
status: done
depends_on: [1, 2, 3, 4, 5, 6, 7, 8]
wave: 4
skills: [test-master]
verify: []
reviewers: []
teammate_name:
---

# Task 11: Test Audit

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:test-master` — [skills/test-master/SKILL.md](~/.claude/skills/test-master/SKILL.md)

## Description

Full-feature test-quality audit across all tests written in Waves 1–3 (Tasks 1–8). Goal: prove that the test suite actually defends the user-spec (AC1–AC15) and tech-spec (AC-T1–AC-T13) acceptance criteria — not just that pytest goes green. The pipeline ships TG-channel posts to @mnemonik and records attestations into the Mnemonik MCP; any AC slipping through untested = a live channel incident waiting to happen.

The audit walks the test inventory in `fabric/content-publisher/tests/` and `mnemonik-bridge-workspace/telegram-ai-agent/tests/` against the spec ACs and against the explicit risk surfaces called out in this task. It is a paper-review pass: read tests, read sources, check mappings. No code is modified. Output is a structured findings report at `work/content-publish-pipeline/logs/audit/test-audit.json` that downstream waves (Task 12 pre-deploy QA, plus any patch round on Tasks 4–8) consume to know what to fix before merge.

This is one of three audit-wave passes (Task 9 = code audit, Task 10 = security audit, Task 11 = test audit) running in parallel after the code is written. Each focuses on a different review dimension; together they form the quality gate before pre-deploy QA.

## What to do

1. Build the test inventory.
   - Enumerate every test file created by Tasks 4–8 in `fabric/content-publisher/tests/` (unit + integration subdirs) and in `mnemonik-bridge-workspace/telegram-ai-agent/tests/`.
   - For each file, list test functions and one-line intent.

2. Build the AC matrix.
   - Rows: `AC1`…`AC15` (user-spec) + `AC-T1`…`AC-T13` (tech-spec).
   - Columns: AC text · expected test target(s) named in tech-spec `Testing Strategy` · actual matching test(s) found in the inventory · gap/note.
   - Mark every cell: `covered` (test exists and the assertion clearly maps to the AC), `weak` (test exists but does not actually assert the AC — e.g. only checks happy path), `missing` (no test).
   - Every AC must end up in at least one `covered` cell. Any `weak` / `missing` is a finding.

3. Run the nine targeted checks below. Each is a specific failure mode that the spec author flagged. For each check, open the named test file, read the assertions, and confirm the check holds. If it fails — record a finding with the exact symptom and the suggested fix:

   - **(1) User-spec AC1–AC15 coverage.** Every AC has at least one test target. AC1 is the topic-handler registration check (per Decision 13), not slash-command; verify the test actually asserts the `F.chat.id == TELEGRAM_FORUM_CHAT_ID & F.message_thread_id == BLOGGER_PROMPTS_TOPIC_ID & F.from_user.id == OPERATOR_USER_ID` filter rather than a literal `/publish_content` registration.

   - **(2) Tech-spec AC-T1–AC-T13 coverage.** Same matrix, tech-level. Pay attention to AC-T7 (`tentative_publish_started_at` written before `run_campaign_from_article` call), AC-T8 (MCP-stdio argv hygiene + handshake), AC-T10 (auto-mode two-location token binding), AC-T12 (topic filter 3 negative dims), AC-T13 (just-retry crash recovery) — these are the ones easiest to "test in name only".

   - **(3) Preview-render cross-comparison is real.** Open `fabric/content-publisher/tests/test_preview_render.py`. Confirm the byte-equality test does NOT compare `render(...).segments` against itself. The correct shape: (a) compute `rendered = render(Platform.TELEGRAM, ingest_article(path)).segments`; (b) monkey-patch the platform-specific publisher seam inside `mnemonik_blogger.agent` (e.g. the Telegram sender call inside `_publish_post`) to capture the segments actually handed to the network call; (c) call `run_campaign_from_article(article=Path(path), platforms=[Platform.TELEGRAM], settings=Settings(dry_run=False, ...), attest=False)`; (d) assert captured-sent-segments == `rendered_segments` byte-for-byte. If the test only asserts `render(...) == render(...)`, it proves nothing and is a finding (severity: high — AC5 is the contract for "preview byte-identical with final post").

   - **(4) Centralized mock fixtures.** Confirm `fabric/content-publisher/tests/conftest.py` exists and defines (at minimum) `mock_analyze_blog`, `mock_blogger_run_campaign_from_article`, `mock_mnemonic_mcp_stdio`, `mock_claude_subprocess`. Any test file that re-implements these mocks locally instead of importing the conftest fixture = finding (severity: medium — drift between mocks across test files is exactly how regressions hide).

   - **(5) HMAC: replay + rotation window + foreign user.** Open `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_publish_handlers.py`. Confirm four distinct cases for the HMAC layer: (a) callback signed with current secret → accepted; (b) callback signed with previous secret (rotation window) → accepted; (c) callback signed with neither → rejected with "Invalid signature"; (d) replay — same valid callback fired twice; second one sees `status != preview-sent` and is answered "too late, status: <X>". Also confirm `from_user.id != OPERATOR_USER_ID` is rejected BEFORE the HMAC compare (Decision 9 ordering). Missing any of (a)–(d) or the ordering = finding (severity: high — AC-T6).

   - **(6) cleanup-gc terminal-only deletion.** Open `fabric/content-publisher/tests/test_cleanup_gc.py`. Confirm the test covers both branches: (a) job in terminal status (`done` / `attest-failed` / `failed` / `rejected` / `publish-failed`) with `cleanup_at <= now` → worktree removed, archive record written; (b) job in non-terminal status (`writing` / `scoring` / `preview-sent` / `publishing` / `attest-pending`) with `cleanup_at <= now` → worktree NOT removed. If only branch (a) is tested, the worker can GC live work; that is a finding (severity: high).

   - **(7) Just-retry crash recovery — AC-T13.** Open `fabric/content-publisher/tests/test_publishing_retry_on_crash.py`. Confirm the test (a) sets up a job at `status=publishing`, (b) restarts the worker, (c) asserts the normal publish path runs (mocked `run_campaign_from_article` called exactly once on the retry), (d) asserts `tentative_publish_started_at` was set before the original publish call (so the recovery has a forensic timestamp). Crucially: confirm the test mentions NO `recovery-needed` state, NO `/recovery_mark_published` slash, NO `find_post_by_id`-style channel scanning. Decision 8 explicitly removed those; a test that still references them = finding (severity: high — that test was not updated to the round-5 decision).

   - **(8) Topic filter 3 negative dimensions.** Open `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_topic_filter.py`. Confirm three distinct negative cases: (a) operator writes in the correct chat but WRONG topic (e.g. another forum topic id) → message ignored, no enqueue; (b) operator writes in the correct topic but WRONG chat (e.g. operator's main DM with the bot) → ignored; (c) WRONG user writes in the correct (chat, topic) tuple → ignored. Plus the positive case: correct (chat, topic, user) tuple → `content_publisher.queue.append_job` called once. Missing any of the three negatives = finding (severity: high — AC-T12 explicitly lists all 3 dimensions).

   - **(9) MCP-stdio handshake — argv hygiene + receipt parsing.** Open `fabric/content-publisher/tests/test_attest_envelope.py`. Confirm: (a) the subprocess argv asserted to be exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]` — no payload in argv; (b) the test feeds a mocked stdout containing both the `initialize` response and the `tools/call mnemonic_sign_memory` response; (c) the test asserts the worker writes `initialize` first, then `tools/call` (ordering), and that the receipt fields (`attestation_hash`, `content_sha256`) are parsed off the second response. Missing argv assertion or receipt parsing = finding (severity: high — AC-T8).

4. Write the audit report.
   - Path: `work/content-publish-pipeline/logs/audit/test-audit.json`.
   - Schema below in **Details**.
   - Severity rubric: `high` = AC unprovable from the current test suite (ship-blocker); `medium` = test exists but is brittle / mock drift / partial coverage; `low` = nit (naming, docstring missing, etc.).
   - Include a summary count and a verdict: `pass` (zero high findings, ≤ 3 medium) or `fail` (any high, or > 3 medium).

5. Do NOT modify any test or source file. This is a read-only audit. Findings drive a follow-up patch wave on Tasks 4–8 if the verdict is `fail`.

## Acceptance Criteria

- [ ] Every user-spec AC (AC1–AC15) appears in the AC matrix with at least one `covered` test target.
- [ ] Every tech-spec AC (AC-T1–AC-T13) appears in the AC matrix with at least one `covered` test target.
- [ ] All nine targeted checks (1)–(9) above are executed and recorded in the report with a per-check status (`pass` / `fail`) and, when failing, a finding entry.
- [ ] AC1 is verified as the topic-handler registration check, NOT a slash-command registration (per Decision 13).
- [ ] Preview-render byte-equality test is confirmed to intercept the platform-specific publisher inside `mnemonik_blogger.agent`, not to compare `render()` against itself.
- [ ] Mock fixtures are confirmed centralized in `fabric/content-publisher/tests/conftest.py`; any local re-implementation in test files is flagged.
- [ ] HMAC tests cover all four cases: current secret, previous secret, foreign signature, replay.
- [ ] `test_cleanup_gc.py` is confirmed to assert non-terminal jobs are NOT GC'd.
- [ ] `test_publishing_retry_on_crash.py` is confirmed to reference NO `recovery-needed` state or recovery slash commands (Decision 8 cleanup).
- [ ] `test_topic_filter.py` is confirmed to cover all 3 negative dimensions (wrong topic, wrong chat, wrong user) plus the positive case.
- [ ] `test_attest_envelope.py` is confirmed to assert subprocess argv is exactly `[<mnemonic_mcp_binary>, "mcp-stdio"]` and to parse the receipt off the second JSON-RPC response.
- [ ] Report written to `work/content-publish-pipeline/logs/audit/test-audit.json` with summary counts, severity-tagged findings, and a `pass` / `fail` verdict.
- [ ] No source or test files are modified during the audit.

## Context Files

- [user-spec.md](../user-spec.md) — ACs AC1–AC15 plus the operator-eye-as-safety-net intent that the tests must defend.
- [tech-spec.md](../tech-spec.md) — `Testing Strategy` (canonical list of test files), `Acceptance Criteria` (AC-T1–AC-T13), Decision 8 (just-retry), Decision 9 (HMAC + rotation), Decision 13 (topic-based dispatch).
- [decisions.md](../decisions.md) — round-by-round decision log; useful when a test references a stale decision.
- [code-research.md](../code-research.md) — upstream API signatures (e.g. real shape of `run_campaign_from_article`) — used to confirm test mocks track the real surface.
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — project rules, pipeline overview, "Kaneo is the bus" architecture.
- [01-sops-secrets-template-extension.md](./01-sops-secrets-template-extension.md) through [08-bot-topic-handler-callbacks-preview-poll.md](./08-bot-topic-handler-callbacks-preview-poll.md) — sibling task files; gives the exact list of tests each task was supposed to produce.
- [test-master SKILL.md](~/.claude/skills/test-master/SKILL.md) — testing methodology and review dimensions for the audit.

Code under audit (read-only):
- `fabric/content-publisher/tests/conftest.py` — centralized mock fixtures.
- `fabric/content-publisher/tests/test_queue.py`, `test_state_machine.py`, `test_uuid_validation.py`, `test_env_isolation.py` (Task 4).
- `fabric/content-publisher/tests/test_preview_render.py`, `test_analyze_gate.py`, `test_spawn_failure_path.py`, `test_separate_bot_token.py`, `test_required_mounts_for.py` (Task 5).
- `fabric/content-publisher/tests/test_deadline_checker.py`, `test_cleanup_gc.py`, `test_attest_retry.py`, `test_attest_envelope.py`, `test_auto_mode.py`, `test_auto_mode_token_binding.py`, `test_tentative_write_ordering.py`, `test_publishing_retry_on_crash.py`, `test_retry_linkage.py` (Task 6).
- `fabric/content-publisher/tests/integration/test_crash_recovery.py`, `test_concurrent_publish.py`, `test_attestation_pipeline.py`, `test_queue_persist.py` (Task 6).
- `mnemonik-bridge-workspace/telegram-ai-agent/tests/test_publish_handlers.py`, `test_topic_filter.py`, `test_retry_linkage.py` (Task 8).
- Source files referenced by the tests, on demand — only to verify a test's assertion is structurally honest (not to review the source itself; that is Task 9).

## Verification Steps

### Automated

- `test -f work/content-publish-pipeline/logs/audit/test-audit.json` → file exists.
- `python -c "import json; r=json.load(open('work/content-publish-pipeline/logs/audit/test-audit.json')); assert 'verdict' in r and r['verdict'] in ('pass','fail'); assert 'findings' in r and isinstance(r['findings'], list); assert 'ac_matrix' in r"` → report is valid JSON with the expected top-level keys.
- `python -c "import json; r=json.load(open('work/content-publish-pipeline/logs/audit/test-audit.json')); user_acs={c['ac'] for c in r['ac_matrix'] if c['ac'].startswith('AC') and not c['ac'].startswith('AC-T')}; tech_acs={c['ac'] for c in r['ac_matrix'] if c['ac'].startswith('AC-T')}; assert len(user_acs)==15, user_acs; assert len(tech_acs)==13, tech_acs"` → matrix covers all 28 ACs.
- `python -c "import json; r=json.load(open('work/content-publish-pipeline/logs/audit/test-audit.json')); checks={c['id'] for c in r['targeted_checks']}; assert checks=={'check_1','check_2','check_3','check_4','check_5','check_6','check_7','check_8','check_9'}, checks"` → all nine targeted checks present.

## Details

**Report schema** — `work/content-publish-pipeline/logs/audit/test-audit.json`:

```json
{
  "feature": "content-publish-pipeline",
  "task": 11,
  "auditor": "test-master",
  "generated_at": "<ISO-8601 UTC>",
  "verdict": "pass | fail",
  "summary": {
    "total_findings": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "ac_covered": 28,
    "ac_weak": 0,
    "ac_missing": 0
  },
  "ac_matrix": [
    {
      "ac": "AC1",
      "source": "user-spec",
      "text": "topic-handler registered (per Decision 13)",
      "expected_tests": ["tests/test_topic_filter.py", "tests/test_publish_handlers.py"],
      "actual_tests": ["..."],
      "status": "covered | weak | missing",
      "note": ""
    }
  ],
  "targeted_checks": [
    {
      "id": "check_1",
      "name": "User-spec AC1–AC15 coverage",
      "status": "pass | fail",
      "findings_ids": []
    }
  ],
  "findings": [
    {
      "id": "F-001",
      "severity": "high | medium | low",
      "check_id": "check_3",
      "ac_refs": ["AC5"],
      "file": "fabric/content-publisher/tests/test_preview_render.py",
      "issue": "Test asserts render(...) == render(...) — self-equality. Does not prove preview-vs-publish byte-equality.",
      "fix": "Monkey-patch the Telegram sender seam inside mnemonik_blogger.agent._publish_post to capture sent segments; assert captured == render(Platform.TELEGRAM, ingest_article(path)).segments.",
      "spec_anchor": "tech-spec.md Decision 4 + AC5"
    }
  ]
}
```

**Verdict rubric:**
- `pass`: zero `high` findings AND ≤ 3 `medium` findings AND every AC is `covered`.
- `fail`: any `high` finding OR > 3 `medium` findings OR any AC is `weak` or `missing`.

**Severity rubric:**
- `high` — an AC is unprovable from the current test suite (ship-blocker). Examples: missing test entirely, self-equality assertion in byte-equality test, missing replay-protection branch in HMAC test, missing non-terminal-not-GC'd branch in cleanup-gc test.
- `medium` — test exists, structurally honest, but partial coverage or mock drift. Examples: local mock instead of importing conftest fixture, only one of three negative dimensions covered, missing ordering assertion.
- `low` — naming, docstring, weak parametrization, etc. Cosmetic.

**AC1 reinterpretation note:** AC1 in user-spec text says "slash-command registration". Decision 13 (round 5) replaced this with topic-handler dispatch. The audit must verify that the test for AC1 asserts the topic-handler is registered with the correct `F.chat.id & F.message_thread_id & F.from_user.id` filter — NOT a literal `/publish_content` slash-command registration. A test that still checks `bot_commands.py` for `publish_content` is a finding (the test was not updated to the round-5 decision).

**Decision 8 (just-retry) cleanup check:** when reading `test_publishing_retry_on_crash.py`, grep mentally for any of: `recovery-needed`, `/recovery_mark_published`, `find_post_by_id`, `tentative_post_id`, "manual recovery". Each of these is a residue from rounds 1–4 and is a finding (severity: high — the test exists but tests a decision that was reversed).

**AC14 conflict note (user-spec vs. tech-spec Decision 8):** User-spec AC14 was originally written as "no double-post via blogger CLI check" — a pre-publish lookup of the target channel to detect an already-posted version. Decision 8 (round 5, see tech-spec) intentionally reversed this: the worker just re-runs the normal publish path on crash recovery, accepting a rare double-post as the cost of removing the channel-scan complexity. Tech-spec AC-T13 (`test_publishing_retry_on_crash.py`) verifies this new behaviour. Auditors MUST accept `test_publishing_retry_on_crash` as satisfying both AC14 and AC-T13 (one test, both ACs map to it). Do NOT mark AC14 as `weak` or `missing` on the grounds that there is no channel-scan / no-double-post check — that check was deliberately removed. In the AC matrix, AC14's `note` should explicitly reference Decision 8 and point to the same test target as AC-T13.

**Dependencies:**
- All of Tasks 1–8 must be at least drafted (test files present) for the audit to have anything to read.
- If Tasks 4–8 produced only stubs, the audit still runs — every AC will land in `missing` and the verdict will be `fail`. That is correct behaviour: it tells the orchestrator that the test layer is not ready for pre-deploy QA.

**Edge cases:**
- A test file exists but has no test functions (placeholder) → treat as `missing` for the relevant ACs, not `covered`.
- A test asserts via a mock fixture that itself does not match the real upstream signature (e.g. `mock_blogger_run_campaign_from_article` has wrong kwargs) → finding (severity: high; the test passes but defends nothing).
- An AC is partially covered across two test files (e.g. half in `test_publish_handlers.py`, half in `test_topic_filter.py`) → mark `covered` if the union is complete; record the split in the `note` field.
- Tests use real network/filesystem instead of fixtures → finding (severity: medium); audit notes the flakiness risk.

**Implementation hints (NOT pseudocode):**
- Read tests file-by-file; do not try to run them.
- For each test function, ask one question: "What AC does this assertion actually prove?" Write the answer in the matrix.
- The targeted-check list is not exhaustive — it is the floor. If a test smells wrong while reading it (e.g. `assert True`, swallowed exceptions, fixture-only call with no assertion) — record a finding regardless of which check it falls under.
- The output is consumed by humans and by downstream automation; keep it valid JSON, with stable IDs (`F-001`, `F-002`, …) and explicit `spec_anchor` strings so a patch round can be opened against a precise line.

## Reviewers

No reviewers — this task IS the review. Output is consumed by Task 12 (pre-deploy QA) and by any patch-round on Tasks 4–8.

## Post-completion

- [ ] Записать краткий отчёт в decisions.md: 1–3 предложения summary, вердикт (`pass` / `fail`), ссылка на `work/content-publish-pipeline/logs/audit/test-audit.json`, counts of high/medium/low findings.
- [ ] Если verdict = `fail` — поднять отдельную задачу (или round-2 на затронутых Tasks 4–8) c конкретными finding IDs к исправлению, прежде чем стартует Task 12.
- [ ] Если в ходе аудита нашлась несостыковка между user-spec / tech-spec и реальностью (например, AC ссылается на decision, который уже отменён) — записать в decisions.md как deviation и предложить правку спека, не править тесты молча.
