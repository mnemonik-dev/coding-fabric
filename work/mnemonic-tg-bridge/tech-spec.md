---
feature: mnemonic-tg-bridge
work_type: feature
size: S
status: approved
created: 2026-05-13
last_updated: 2026-05-13
branch: dev
source_user_spec: work/mnemonic-tg-bridge/user-spec.md
upstream_repo: https://github.com/pavel-molyanov/telegram-ai-agent
fork_repo: https://github.com/mnemonik-dev/telegram-ai-agent
fork_branch: feat/cwd-dynamic-resolver
upstream_license: MIT
parent_feature: coding-fabric (consumes the merged feature via Ansible role T09)
---

# Tech Spec — mnemonic-tg-bridge (cwd:"DYNAMIC" HTTP resolver upstream PR)

## 1. Goal & Context

Add a single small generic feature to `pavel-molyanov/telegram-ai-agent` (Python bot):
support `cwd: "DYNAMIC"` sentinel in per-topic config that resolves at engine-spawn
time via an external HTTP resolver. Delivery: PR upstream. Fallback: maintain fork
`mnemonik-dev/telegram-ai-agent` with the patch.

Why generic: the bot becomes useful to anyone running per-task sandboxes
(coding-fabric, CI runners, ephemeral environments). No Mnemonic-specific code paths.

Why upstream: 80+ Python files of features (tmux mode, `/tui`, voice, MCP server,
session resume, media batching) — reuse instead of rewriting.

## 2. Architecture

### 2.1 Upstream code surface (read-only, mapped from fork main HEAD)

- `src/telegram_bot/core/services/topic_config.py` — `TopicSettings` dataclass and
  JSON validation. Today `cwd: str | None` where `None` means "use defaults.cwd".
- `src/telegram_bot/core/services/topic_runtime.py` — `resolve_topic_runtime_config()`
  (sync) computes the concrete runtime cwd from settings+defaults.
- `src/telegram_bot/core/services/{claude.py,codex_mcp.py,tmux_spawn.py}` — engine
  spawn sites; receive a resolved Path cwd.
- `src/telegram_bot/core/config.py` — `Settings` (env-driven).

### 2.2 New + modified files

| File | Mode | Purpose |
|------|------|---------|
| `src/telegram_bot/core/services/workspace_resolver.py` | NEW | Async httpx client: `lease(task_id, repo, base_ref, topic) -> Path` (POST `/worktree`), `release(task_id) -> None` (DELETE `/worktree/{task_id}`). Retry on 5xx/timeout (3x, exp backoff). 409 = capacity → raise `WorkspaceCapacityError`. |
| `src/telegram_bot/core/services/topic_config.py` | MODIFY | Accept `"DYNAMIC"` as `cwd` value. New `TopicSettings.dynamic_cwd: bool` derived field. Cwd literal `"DYNAMIC"` mapped to `dynamic_cwd=True, cwd=None`. Existing static-cwd back-compat. |
| `src/telegram_bot/core/services/topic_runtime.py` | MODIFY | `TopicRuntimeConfig` gets `dynamic_cwd: bool` field. `resolve_topic_runtime_config()` stays sync; cwd is None when dynamic_cwd=True. Caller (engine spawn) resolves via workspace_resolver. |
| `src/telegram_bot/core/handlers/text.py` (or wherever engine spawn happens) | MODIFY | Wrap engine spawn call site. If `runtime_config.dynamic_cwd`: derive `task_id` from `(chat_id, thread_id, message_id)`, call `workspace_resolver.lease(...)`, pass returned path to engine, register `finally` that calls `release(task_id)`. |
| `src/telegram_bot/core/config.py` | MODIFY | Add `Settings.workspace_resolver_url: str | None` from env `TELEGRAM_AI_AGENT_CWD_RESOLVER_URL`. |
| `tests/services/test_workspace_resolver.py` | NEW | Unit tests with respx mock (httpx mocking). |
| `tests/services/test_topic_config_dynamic.py` | NEW | Validates DYNAMIC accepted, back-compat for None/string. |
| `tests/integration/test_dynamic_cwd_flow.py` | NEW | End-to-end with mock workspace-manager + mock engine spawn. |
| `README.md` | MODIFY | Document new env var + config option + example. |
| `README.ru.md` | MODIFY | Russian translation. |
| `.claude/skills/project-knowledge/references/architecture.md` | MODIFY | Add resolver to architecture doc. |
| `.claude/skills/project-knowledge/references/configuration.md` | MODIFY | Document env var + topic config option. |

### 2.3 HTTP contract (matches coding-fabric workspace-manager §2.5)

```
POST {resolver_url}/worktree
  body: {"task_id": str, "repo": str, "base_ref": str, "topic": str}
  200 → {"cwd": str}            // resolved absolute path
  409 → {"error": "capacity"}   // pool full
  5xx → retry up to 3 (1s, 2s, 4s)
  400 → fail-fast (bad request)

DELETE {resolver_url}/worktree/{task_id}
  204 → success
  404 → idempotent ok (already released)
```

### 2.4 task_id derivation

`task_id` is constructed deterministically per Telegram message as
`{chat_id}-{thread_id or 'main'}-{message_id}`. Matches `^[A-Z0-9-]{3,40}$` regex
required by workspace-manager (we'll uppercase numeric components — they're
already digits + `-`, no letters need upper-casing; just ensure the regex pattern
matches; if not, base32-encode the message_id).

Decision verification: max chat_id is 13 digits, thread_id 10, message_id 10 ≈
35 chars max with hyphens. Within `{3,40}`. Pure digits/hyphens → regex matches.

### 2.5 Failure modes

| Condition | Behavior |
|-----------|----------|
| `cwd="DYNAMIC"` + `TELEGRAM_AI_AGENT_CWD_RESOLVER_URL` unset | Fail at config load with `ConfigError("DYNAMIC cwd requires TELEGRAM_AI_AGENT_CWD_RESOLVER_URL")` |
| Resolver returns 409 | Bot posts friendly message to originating topic via existing `messages.py` infrastructure: "Workspace pool busy. Please try again in a moment."; engine NOT spawned; no resource leak |
| Resolver returns 5xx/timeout | 3 retries with exp backoff (1s, 2s, 4s); on persistent failure → log + post diagnostic to topic + engine NOT spawned |
| Resolver returns 400 (bad task_id/repo) | Fail immediately; log full request for debugging; post generic "internal error" to topic |
| Engine spawn fails after lease | `finally` block calls `release(task_id)` — no orphan worktree |
| Bot crash mid-spawn | workspace-manager has its own orphan-cleanup watchdog (coding-fabric T18); this is acceptable resource leak |

### 2.6 Logging hygiene

- All HTTP requests/responses logged at DEBUG only
- Token-free URLs (resolver_url is local/tailnet — no token expected, but be defensive)
- task_id, status codes logged at INFO
- 409 surfaces as INFO (expected business condition, not error)
- 5xx/timeout after retries → ERROR
- All log lines safe by default — generic upstream feature, no Mnemonic-specific sanitization

## 3. Decisions

| # | Decision | Justification | Anchors |
|---|----------|---------------|---------|
| D1 | One generic feature, no Mnemonic-specific code | Maximize upstream merge chance | user-spec §2 |
| D2 | Strict opt-in via per-topic `cwd: "DYNAMIC"` + env var | Back-compat: existing topics unchanged | AC1 |
| D3 | Sync resolve_topic_runtime_config stays sync; resolution moves to caller | Minimize blast radius — `topic_runtime.py` is touched by many call sites | AC1, AC2 |
| D4 | New `workspace_resolver.py` module — async via httpx (already a dep) | Aligns with rest of upstream async style | AC2, AC5 |
| D5 | Retry policy: 3 attempts, 1s/2s/4s backoff | Conservative; matches coding-fabric watchdog patterns | AC5 |
| D6 | `task_id = "{chat_id}-{thread_id}-{message_id}"` | Deterministic, no state needed, matches workspace-manager regex | AC2, AC3 |
| D7 | 409 capacity surfaces as user-friendly Telegram message via existing `messages.py` | Operator UX | AC4 |
| D8 | `cwd: "DYNAMIC"` + missing env var = fail-fast at config load | Loud, early failure | AC6 |
| D9 | Cleanup in `finally` of engine spawn block | No orphans on happy/error paths | AC3 |
| D10 | Idempotent DELETE — 404 from workspace-manager treated as success | Standard REST pattern | AC8 |
| D11 | Trust-by-network for resolver auth (no token between bot and resolver) | Loopback / tailnet usage; matches user-spec §11 | user-spec §11 |
| D12 | `[TECHNICAL]` PR strategy: fork-branch `feat/cwd-dynamic-resolver` on `mnemonik-dev/telegram-ai-agent`; PR opened against `pavel-molyanov/telegram-ai-agent:main` | Standard upstream contribution | AC11–AC14 |

## 4. Implementation Tasks

Total: 10 tasks (5 implementation + 3 audit + 2 final). Within S-cap.

### Wave 1 — Implementation (sequential — they touch shared files but in clear order)

**T01 — Extend `topic_config.py` to accept "DYNAMIC" cwd sentinel**
- Skill: `python-alchemist`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files modify: `src/telegram_bot/core/services/topic_config.py`, `tests/services/test_topic_config_dynamic.py` (new)
- Scope: regex/validation that `cwd: "DYNAMIC"` is allowed; surface as `TopicSettings.dynamic_cwd: bool` derived; static-cwd back-compat preserved. Unit tests.
- Verify-smoke: `uv run pytest tests/services/test_topic_config_dynamic.py -v`

**T02 — New `workspace_resolver.py` module**
- Skill: `python-alchemist`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files modify: `src/telegram_bot/core/services/workspace_resolver.py` (new), `tests/services/test_workspace_resolver.py` (new), `src/telegram_bot/core/config.py` (add Settings.workspace_resolver_url)
- Scope: async httpx client with `lease()` + `release()`, retry/backoff, error hierarchy (`WorkspaceCapacityError`, `WorkspaceUnreachableError`, `WorkspaceConfigError`). Unit tests via `respx` mock.
- Verify-smoke: `uv run pytest tests/services/test_workspace_resolver.py -v`

**T03 — Wire DYNAMIC resolution into engine spawn**
- Skill: `python-alchemist`
- Reviewers: `code-reviewer`, `reliability-engineer`, `test-reviewer`
- Files modify: `src/telegram_bot/core/services/topic_runtime.py` (add dynamic_cwd field), `src/telegram_bot/core/handlers/text.py` or `core/services/claude.py` / `codex_mcp.py` (whichever wraps engine spawn — agent decides based on code-research), `tests/integration/test_dynamic_cwd_flow.py` (new)
- Scope: When config.dynamic_cwd is True, derive task_id from Telegram context, lease(), pass cwd to engine spawn, release() in finally. 409 → user-friendly message. End-to-end integration test with mock resolver + mock engine.
- Verify-smoke: `uv run pytest tests/integration/test_dynamic_cwd_flow.py -v`

**T04 — Documentation: README + architecture + configuration**
- Skill: `documentation-writing`
- Reviewers: `code-reviewer`
- Files modify: `README.md`, `README.ru.md`, `.claude/skills/project-knowledge/references/{architecture,configuration}.md`
- Scope: explain the new env var, new topic config option, example use cases (sandboxing, CI runners, ephemeral envs), failure-mode reference, link to spec.

**T05 — Local pre-PR quality gate**
- Skill: `pre-deploy-qa`
- Reviewers: (none — this is the gate)
- Files modify: none (read-only verification)
- Scope: run `uv sync`, `uv run ruff check`, `uv run mypy src/`, `uv run pytest tests/ -v` (all upstream tests + new ones). Confirm CI configuration matches upstream's `.github/workflows/ci.yml`. Produce `work/mnemonic-tg-bridge/logs/tasks/pre-pr-qa.json` with pass/fail per check.
- Verify-smoke: all four green or documented exceptions.

### Wave 2 — Audit (parallel, no reviewers)

**T06 — Code Audit**
- Skill: `code-reviewing`
- Reviewers: none
- Scope: holistic review of T01–T04 deliverables (≤ 200 LOC of patch + ~300 LOC of tests). Read full files. Output: `work/mnemonic-tg-bridge/logs/working/audit/code-audit.json`.

**T07 — Security Audit**
- Skill: `security-auditor`
- Reviewers: none
- Scope: HTTP-client OWASP pass — SSRF (resolver_url must be loopback/tailnet only?), URL-construction safety, response-size limits, JSON parsing safety, task_id-via-Telegram-IDs injection vectors. Logging of secrets (none expected). Output: `work/mnemonic-tg-bridge/logs/working/audit/security-audit.json`.

**T08 — Test Audit**
- Skill: `test-master`
- Reviewers: none
- Scope: 9 AC traceability + suite coverage assessment (unit + integration). Output: `work/mnemonic-tg-bridge/logs/working/audit/test-audit.json`.

### Wave 3 — Final

**T09 — Pre-deploy QA (re-run after audit fixes)**
- Skill: `pre-deploy-qa`
- Reviewers: none
- Scope: re-run lint + typecheck + tests after audit fixes land. Produce `work/mnemonic-tg-bridge/logs/tasks/pre-deploy-qa.json`.

**T10 — Open upstream PR**
- Skill: `github-actions-pro` (best fit for GitHub PR flow + CI)
- Reviewers: none
- Verify-user: user reviews PR body before publication; user pings Pavel via GitHub issue/Telegram (optional but recommended)
- Scope: push fork branch `feat/cwd-dynamic-resolver` to `mnemonik-dev/telegram-ai-agent`; open PR against `pavel-molyanov/telegram-ai-agent:main` with structured body (Motivation, Design summary, Tests, Back-compat note, Acceptance criteria). Iterate review comments (up to 3 rounds). Track PR URL + status in decisions.md. If 30 days without merge → fallback path triggered (tag fork release `v<base>+mnemonik.1` and inform coding-fabric T09 to pin to this tag).
- Files modify: none in this repo; PR body in upstream

## 5. Testing Strategy (size S)

- **Unit**: `test_topic_config_dynamic.py` — 6-8 tests (DYNAMIC accepted, back-compat for None/static, derived field correctness). `test_workspace_resolver.py` — 10-15 tests (positive path, 409, 5xx retry path, timeout, idempotent DELETE, URL-construction sanity).
- **Integration**: `test_dynamic_cwd_flow.py` — 4-5 tests (full handler flow with respx-mocked resolver: happy path, 409 user message, capacity-recovery, no-resolver fail-fast, cleanup verified).
- **CI gate**: upstream's existing `.github/workflows/ci.yml` runs on the fork branch — must remain green.
- **Manual smoke (deferred to operator)**: live workspace-manager + bot deployed via coding-fabric T13 e2e-smoke covers the full DYNAMIC path on the real VM.

No new external dependencies (respx is dev-only, may already be in upstream's dev-deps — check).

## 6. Agent Verification Plan (post-merge in upstream OR post-fork-release)

Coding-fabric T13 (e2e-smoke.yml) is the AVP — verifies DYNAMIC sentinel works end-to-end after deploy. No separate AVP for this feature.

## 7. User-Spec Deviations

None.

The user-spec stated "delivery_strategy: upstream PR (preferred); fallback fork
to mnemonic-org/telegram-ai-agent". This tech-spec uses `mnemonik-dev/telegram-ai-agent`
(operator's actual GitHub user) instead of `mnemonic-org` — naming detail, not
structural. `[PENDING USER APPROVAL]` implicit; operator confirmed this fork already
exists with push access.

## 8. Risks

| # | Risk | Mitigation |
|---|------|------------|
| TR1 | Engine-spawn site is in multiple places (claude.py, codex_mcp.py, tmux_spawn.py) — patches multiply | T03 agent does code-research first to identify single point of integration (likely `handlers/text.py` or `cc_modes.py` before engine dispatch) |
| TR2 | task_id collision across topics if message_ids overlap | task_id encodes chat_id + thread_id + message_id — guaranteed unique across telegram |
| TR3 | upstream's existing ruff/mypy/CI is strict; new code fails lint | T05 catches before PR; iterate locally |
| TR4 | Pavel rejects feature as too-niche | Pre-PR issue discussion (operator's manual step); fallback path documented |
| TR5 | `respx` not in upstream dev-deps | Use `httpx.MockTransport` instead (stdlib-ish via httpx) — no new dep |
| TR6 | Tmux execution mode (persistent sessions) re-uses cwd across messages | DYNAMIC requires fresh cwd per message → DYNAMIC + tmux mode is incompatible; document this in topic config validation as warning at config-load + error at runtime if both set |

## 9. Out of Scope

Inherited from user-spec §4.2 + §11:
- Rust rewrite
- tmux/`/tui`/voice/MCP-server-side bot integrations
- Per-topic resolver override (1 global URL suffices)
- Auth between bot and resolver
- WebSocket / long-poll resolver
