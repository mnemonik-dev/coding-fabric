---
status: planned
depends_on: [1, 2, 3, 4, 5, 6, 7, 8]
wave: 4
skills: [code-reviewing]
verify: []
reviewers: []
teammate_name:
---

# Task 9: Code Audit (Audit Wave)

## Required Skills

Before starting, load:
- `/skill:code-reviewing` — [skills/code-reviewing/SKILL.md](~/.claude/skills/code-reviewing/SKILL.md)

## Description

Full-feature code-quality audit covering everything produced across Wave 1–3 (Tasks 1–8) of the `content-publish-pipeline` feature. This is the **Audit Wave** — a single auditor passes over the complete, integrated implementation (Ansible roles + Python package + bot handler additions) to find issues that only become visible when the pieces are viewed together.

Auditor produces a structured report only — no code changes, no fixes applied, no per-task reviewer round. Findings flow back into the loop only if Wave 4 surfaces blockers that the orchestrator decides to fix before Pre-deploy QA (Task 12).

Focus areas chosen because they are **cross-cutting** by nature and therefore are systematically missed by per-task reviewers operating on a single file slice:

1. **Cross-process consistency of the shared CAS primitive** — `content_publisher.queue.cas_status` must be imported and used by BOTH the worker process (`fabric/content-publisher/src/content_publisher/`, mostly Tasks 5/6) AND the bot process (`mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py`, Task 8). If either side reimplements the lock/atomic-write, the queue corruption story collapses.
2. **Subprocess invocation uniformity** — every `claude` / `analyze_blog.py` / `mnemonik-mcp` spawn (across Tasks 5 and 6) must use `stdin=` for payload + `env=restricted_env(<kind>)`. Argv must never carry bulk content.
3. **In-process publish hardening** — `run_campaign_from_article` runs inside the worker process; if it raises, the traceback can carry `article_text`, the publisher bot token, sops-loaded env, or callback HMAC secrets. There must be a `sys.excepthook` (and asyncio loop exception handler) that scrubs these from any logged or written traceback. Plus systemd unit hardening (`LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`) verified in the rendered unit.
4. **Error-handling consistency across modules** — fail-loud vs. swallow-and-continue policy must be consistent between `state.py`, `queue.py`, `worker.py`, `publish.py`, `attest.py`, `deadline.py`, `cleanup.py`, `recovery.py`, `main.py`, and the bot's `publish.py`.
5. **Shared resources match Architecture table** — `queue.jsonl`, `/etc/blogger.env`, `/etc/blogger.mcp.json`, `mnemonic-mcp.service` ownership/consumer matrix must match Tech-Spec §Architecture · Shared resources. In particular: exactly ONE writer creates the queue file (Ansible role), exactly ONE shared Python module (`content_publisher.queue`) is the only path either process uses for reads/writes.
6. **Architectural drift between implementation and tech-spec** — any deviation from the decisions in §Decisions (1–13), state machine, JSONL schema, or the end-to-end flow diagram must be flagged. Drift that is actually an improvement is still flagged so it can be reflected back into the spec.

## What to do

Produce a structured JSON audit report at `work/content-publish-pipeline/logs/audit/code-audit.json`. The orchestrator reads this file to decide remediation. Findings must be actionable and reference exact files + line ranges.

Concretely:

1. Re-read the tech-spec (`work/content-publish-pipeline/tech-spec.md`) and the decisions log (`work/content-publish-pipeline/decisions.md`) so the audit is grounded in the approved design — not in the auditor's assumptions.
2. Re-read every source file listed in §Context Files below. For each file, build a brief mental model: what it exposes, who imports it, what invariants it asserts.
3. Run the 6 cross-cutting checks listed in §Description. For each, scan the relevant files end-to-end (not by snippet) and record findings.
4. For each finding, fill: `severity` (`blocker` | `major` | `minor` | `nit`), `area` (one of the 6 focus areas, or `other`), `file` + `line_range`, `issue` (what is wrong, in 1–3 sentences), `fix` (concrete suggestion, 1–3 sentences). No code dumps inside the JSON — file paths + line ranges only.
5. Write the report file. Create the parent dir if absent (`mkdir -p work/content-publish-pipeline/logs/audit/`).
6. Append a single Summary line to `work/content-publish-pipeline/decisions.md` with a relative link to the report. No findings table inlined.

## Acceptance Criteria

- [ ] Report file exists at `work/content-publish-pipeline/logs/audit/code-audit.json` and is valid JSON.
- [ ] Report root has keys: `task_number: 9`, `feature: "content-publish-pipeline"`, `audited_at` (ISO-8601 UTC), `summary` (1–3 sentences), `findings` (array).
- [ ] Each finding object has: `severity`, `area`, `file`, `line_range`, `issue`, `fix`.
- [ ] Severity counts (`blockers`, `majors`, `minors`, `nits`) reported in the summary header object.
- [ ] All 6 focus areas have been explicitly covered — if no findings in an area, an empty stub entry `{"area": "<name>", "checked": true, "findings": 0}` appears under a `coverage` array so the orchestrator can see the check was actually performed.
- [ ] Cross-process CAS check explicitly identifies WHICH module the bot's `publish.py` imports `cas_status` from — and asserts it is `content_publisher.queue` (not a local re-implementation).
- [ ] Subprocess uniformity check enumerates every `asyncio.create_subprocess_exec` / `subprocess.Popen` call site across `spawn.py`, `score.py`, `attest.py`, `mcp_client.py` and reports whether each uses `stdin=` for payload + `env=restricted_env(...)`.
- [ ] Excepthook scrub check confirms presence of a top-level `sys.excepthook` (and asyncio loop exception handler) in `main.py` that scrubs `article_text`, `TELEGRAM_BOT_TOKEN`, `CALLBACK_HMAC_SECRETS`, `PUBLISH_AUTO_MODE_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` from any logged traceback.
- [ ] Shared-resources matrix is reproduced in the report and each row marked `matches_arch: true|false` with a 1-line note on drift if any.
- [ ] Decisions log appended with a 1-line summary linking to the report.
- [ ] No code modifications made by the auditor (verify via `git status` after the run — only added files: the report + decisions.md append).

## Context Files

**Feature-specific:**
- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md)
- [code-research.md](../code-research.md)

**Project context:**
- [CLAUDE.md](../../../CLAUDE.md) — project map, infra layout, Rules, Co-Authored-By policy
- [code-reviewing SKILL.md](~/.claude/skills/code-reviewing/SKILL.md) — 11 review dimensions and methodology

**Code under audit — Wave 1 (Ansible / Infra):**
- `infrastructure/secrets/secrets.sops.yml.template` — 9 new keys (Task 1)
- `infrastructure/ansible/roles/mnemonic-mcp/defaults/main.yml` (Task 2)
- `infrastructure/ansible/roles/mnemonic-mcp/tasks/main.yml` (Task 2)
- `infrastructure/ansible/roles/mnemonic-mcp/templates/mnemonic-mcp.service.j2` (Task 2)
- `infrastructure/ansible/roles/mnemonic-mcp/files/package-lock.json` (Task 2)
- `infrastructure/ansible/roles/mnemonic-mcp/README.md` (Task 2)
- `infrastructure/ansible/roles/content-publisher/tasks/main.yml` (Tasks 3 + 7)
- `infrastructure/ansible/roles/content-publisher/defaults/main.yml` (Task 3)
- `infrastructure/ansible/roles/content-publisher/templates/blogger.env.j2` (Task 3)
- `infrastructure/ansible/roles/content-publisher/templates/blogger.mcp.json.j2` (Task 3)
- `infrastructure/ansible/roles/content-publisher/templates/content-publisher.service.j2` (Task 3 + hardening)
- `infrastructure/ansible/roles/content-publisher/requirements.locked.txt` (Task 3)
- `infrastructure/ansible/roles/content-publisher/handlers/main.yml` (Task 7)
- `infrastructure/ansible/roles/content-publisher/README.md` (Task 3 — HMAC + auto-mode + publisher-token rotation runbooks)
- `infrastructure/ansible/playbooks/deploy.yml` (Task 3)

**Code under audit — Wave 2 (Python worker, `fabric/content-publisher/`):**
- `fabric/content-publisher/pyproject.toml` (Task 4)
- `fabric/content-publisher/src/content_publisher/__init__.py` (Task 4)
- `fabric/content-publisher/src/content_publisher/state.py` — atomic-write + flock helpers (Task 4; copied pattern from `fabric/workspace-manager/state.py:_write_raw,_acquire,_release`)
- `fabric/content-publisher/src/content_publisher/queue.py` — `append_job`, `load`, `cas_status` — **the shared CAS primitive imported by both processes** (Task 4)
- `fabric/content-publisher/src/content_publisher/models.py` — Pydantic Job + status enum (Task 4)
- `fabric/content-publisher/src/content_publisher/env.py` — `restricted_env(kind)` allowlist helper (Task 4)
- `fabric/content-publisher/src/content_publisher/worker.py` — writing → scoring → preview-sent (Task 5)
- `fabric/content-publisher/src/content_publisher/spawn.py` — claude subprocess (Task 5)
- `fabric/content-publisher/src/content_publisher/score.py` — analyze_blog subprocess + issue truncation (Task 5)
- `fabric/content-publisher/src/content_publisher/render.py` — in-process `render(Platform.TELEGRAM, ingest_article(...))` (Task 5)
- `fabric/content-publisher/src/content_publisher/publish.py` — `tentative_publish_started_at` ordering + in-process `run_campaign_from_article` (Task 6)
- `fabric/content-publisher/src/content_publisher/deadline.py` — sub-loop b (Task 6)
- `fabric/content-publisher/src/content_publisher/cleanup.py` — sub-loop c (Task 6)
- `fabric/content-publisher/src/content_publisher/attest.py` — MCP-stdio attestation logic (Task 6)
- `fabric/content-publisher/src/content_publisher/mcp_client.py` — JSON-RPC over stdio (Task 6)
- `fabric/content-publisher/src/content_publisher/recovery.py` — startup just-retry for `publishing` jobs (Task 6, Decision 8)
- `fabric/content-publisher/src/content_publisher/main.py` — FastAPI lifespan + 3 sub-loops + **`sys.excepthook` scrub** + auto-mode token check (Task 6)

**Code under audit — Wave 3 (Bot integration):**
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py` — topic-handler + 3 callback handlers + preview-dispatch poll (Task 8). **Must import `cas_status` and `append_job` from `content_publisher.queue` — not reimplement.**
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/__main__.py` — `publish_router` registration (Task 8)

**Reference (existing code — READ to compare patterns, do not flag):**
- `fabric/workspace-manager/state.py` — origin of the `_write_raw`/`_acquire`/`_release` pattern
- `fabric/workspace-manager/main.py` — origin of the lifespan + sub-loop pattern
- `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/tail.py:227-228` — origin of the prefix-based callback handler pattern

## Verification Steps

### Automated

- `python -c "import json; json.load(open('work/content-publish-pipeline/logs/audit/code-audit.json'))"` → exits 0 (report is valid JSON).
- `git status --porcelain | grep -v -E '^\?\? work/content-publish-pipeline/logs/audit/|^ M work/content-publish-pipeline/decisions.md$'` → empty output (auditor changed nothing else).

## Details

**Output report path:** `work/content-publish-pipeline/logs/audit/code-audit.json` (absolute: `/Users/syi/src/sessions/coding-fabric/work/content-publish-pipeline/logs/audit/code-audit.json`).

**Report JSON shape (canonical):**

```
{
  "task_number": 9,
  "feature": "content-publish-pipeline",
  "audited_at": "<ISO-8601 UTC>",
  "summary": "<1-3 sentences: overall posture + headline findings>",
  "counts": {"blockers": N, "majors": N, "minors": N, "nits": N},
  "coverage": [
    {"area": "cross_process_cas",          "checked": true, "findings": N},
    {"area": "subprocess_uniformity",      "checked": true, "findings": N},
    {"area": "inprocess_publish_hardening","checked": true, "findings": N},
    {"area": "error_handling_consistency", "checked": true, "findings": N},
    {"area": "shared_resources_matrix",    "checked": true, "findings": N},
    {"area": "architectural_drift",        "checked": true, "findings": N}
  ],
  "shared_resources_matrix": [
    {"resource": "queue.jsonl",              "owner": "...", "consumers": [...], "matches_arch": true, "note": ""},
    {"resource": "content_publisher.queue",  "owner": "...", "consumers": [...], "matches_arch": true, "note": ""},
    {"resource": "/etc/blogger.env",         "owner": "...", "consumers": [...], "matches_arch": true, "note": ""},
    {"resource": "/etc/blogger.mcp.json",    "owner": "...", "consumers": [...], "matches_arch": true, "note": ""},
    {"resource": "mnemonic-mcp.service",     "owner": "...", "consumers": [...], "matches_arch": true, "note": ""}
  ],
  "findings": [
    {
      "severity": "blocker|major|minor|nit",
      "area": "cross_process_cas|subprocess_uniformity|inprocess_publish_hardening|error_handling_consistency|shared_resources_matrix|architectural_drift|other",
      "file": "fabric/content-publisher/src/content_publisher/queue.py",
      "line_range": "42-58",
      "issue": "...",
      "fix": "..."
    }
  ]
}
```

**Severity rubric:**
- `blocker` — runtime correctness or security defect (e.g., bot reimplements CAS without flock → race; argv carries article text → leaks via `ps`; missing excepthook scrub → token leaks into journalctl).
- `major` — non-blocking correctness/maintainability defect (e.g., inconsistent error handling between two siblings; one subprocess uses argv when stdin is the project convention).
- `minor` — code quality (naming, structure, dead branches).
- `nit` — style / doc / comment.

**Cross-process CAS check — what to look for in `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/core/handlers/publish.py`:**
- Import line for `content_publisher.queue` (or `from content_publisher.queue import cas_status, append_job`). If absent → blocker.
- No locally-defined `cas_status`, `_acquire`, `_release`, `flock`, `os.replace` call inside the bot handler (these signal a re-implementation). If present → blocker.
- Bot process must have read+write permission to the same `queue.jsonl` path (env var or constant matches `CONTENT_PUBLISHER_QUEUE` from `blogger.env`). If the bot reads the path from a different source → major + architectural-drift cross-link.

**Subprocess uniformity check — enumerate every spawn site:**

| Site | Expected payload channel | Expected env |
|------|--------------------------|--------------|
| `spawn.py` — claude | stdin (prompt) | `restricted_env("claude")` |
| `score.py` — analyze_blog | stdin or argv-path is acceptable per tech-spec; flag if argv carries article body | `restricted_env("analyze_blog")` |
| `mcp_client.py` / `attest.py` — `mnemonik-mcp mcp-stdio` | stdin (JSON-RPC) | `restricted_env("mnemonik-mcp")` (PATH only) |

Any deviation → major (subprocess argv leak risk).

**Excepthook check:**
- `main.py` must install a `sys.excepthook` (or equivalent) that filters traceback frames for the following secrets / payloads: `article_text`, `prompt` body, `TELEGRAM_BOT_TOKEN`, `CALLBACK_HMAC_SECRETS`, `PUBLISH_AUTO_MODE_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`.
- Asyncio loop exception handler must apply the same scrub for unhandled task exceptions (because `publish.py`'s `run_campaign_from_article` is awaited).
- Systemd unit (`content-publisher.service.j2`) must set `LimitCORE=0`, `ProtectSystem=strict`, `NoNewPrivileges=true`. Missing any → major.

**Error-handling consistency — patterns to compare across modules:**
- CAS-loser path: does every CAS call site react uniformly when the CAS rejects (return-and-log vs. raise vs. silent)?
- Subprocess non-zero exit: does every spawn site set `notify_pending=true` + transition to `failed`/`publish-failed`/`attest-failed` consistently?
- Filesystem errors during atomic-write: are they propagated or swallowed? Tech-spec implies fail-loud.

**Shared-resources matrix — compare against tech-spec §Architecture · Shared resources verbatim.** Drift (e.g., role creates queue.jsonl with the wrong mode; queue path not from `CONTENT_PUBLISHER_QUEUE` env in one of the consumers; `mnemonic-mcp.service` consumer list wrong) → major.

**Architectural drift — likely hotspots:**
- Decision 4: did the publish path actually pass `attest=False` and use the keyword-only signature?
- Decision 8: is the `publishing`-state recovery actually a plain retry (no special state machine, no slash commands)?
- Decision 11: does the role's `inspect.signature` check run BEFORE service starts?
- Decision 13: does the bot's topic-handler actually filter on all three of (`chat_id`, `thread_id`, `from_user.id`)?

**Dependencies:** Tasks 1–8 must all be in `status: done` before this task runs (wave 4 gate).

**Edge cases the auditor must explicitly think through (and note in `summary` if not found):**
- Bot's preview-dispatch poll and worker's deadline-checker both touching `queue.jsonl` simultaneously — is `cas_status` the only mutation path on both sides?
- `recovery.py` running BEFORE the three sub-loops start in `main.py` lifespan? (otherwise the just-retry can race with the queue-poll sub-loop picking up the same job).
- HMAC failure counter (>5 in 10-min window) — is it reset on restart? Persisted? Either is fine, but it should be intentional.

**Implementation hints (for the auditor, NOT for fixers):**
- When in doubt about an "is this drift" call: re-read the relevant Decision entry verbatim. The spec is the source of truth; if the code is better than the spec, file it as a `minor` drift and let the orchestrator decide whether to update the spec.
- File paths in findings MUST be repo-relative (e.g. `fabric/content-publisher/src/content_publisher/queue.py`), not absolute, so the orchestrator can grep/jump cleanly.
- The auditor is allowed (encouraged) to flag duplicates across Task 1–8 findings if a single root cause produces multiple symptoms — list each occurrence as its own finding but cross-reference via an optional `root_cause_id` finding field.

## Reviewers

None — Audit Wave standard. The auditor's report IS the review artifact; the orchestrator (feature-execution) consumes it directly to schedule any remediation. No `code-reviewer-1.json` round, no nested review chain.

## Post-completion

- [ ] Append a Summary line to `work/content-publish-pipeline/decisions.md`: `Task 9 (Code Audit): <1-3 sentences>; full report → [logs/audit/code-audit.json](logs/audit/code-audit.json). Counts: blockers=N, majors=N, minors=N, nits=N.`
- [ ] If the audit surfaced architectural drift that the team accepts as an improvement over the tech-spec, note it in decisions.md and flag the tech-spec section that should be updated (auditor does NOT edit tech-spec).
- [ ] If any blockers found: the orchestrator (NOT the auditor) decides whether to spawn a fix-cycle before Wave 4's other audits (Tasks 10, 11) or before Pre-deploy QA (Task 12). Auditor stops at "report written".
