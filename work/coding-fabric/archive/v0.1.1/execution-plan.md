# Execution Plan — coding-fabric

**Feature:** coding-fabric (Mnemonic Protocol Agent-Driven Development Loop v0.1.1)
**Total tasks:** 21 across 8 waves
**Status:** PENDING USER DECISION — environment mismatch detected

---

## Environment reality check

Before spawning the team, three honest facts about this execution environment that
do not match what the skill / tech-spec assume:

### 1. Most tasks describe work on a remote cloud VM that doesn't exist

The tech-spec is for a fabric that lives on a single VPN-fronted cloud VM. From this
shell I cannot:

- Provision a cloud VM (Task 01)
- Install Vaultwarden / Kaneo / Telegram bot (Task 02)
- Clone repos to `~/code/mnemonic-masters/` (Task 03 partial)
- Install ruflo (Task 06), molyanov (Task 07), Mnemonic MCP (Task 08)
- Patch `telegram-ai-agent` upstream OSS repo (Task 05)
- Run end-to-end smoke against the live fabric (Tasks 09, 21)
- Deploy via the fabric (Task 20)

An agent spawned to do these tasks would either fail at `apt install ...` / `gcloud
compute instances create ...` or, worse, **fabricate** the work by writing dummy
configs to non-existent paths. Both outcomes are bad.

### 2. The skill assumes `TeamCreate` for persistent teammates

The skill prescribes named teammates with SendMessage-based review loops over up to 3
rounds. In this environment I have only one-shot `Agent` calls — no persistent team
with bidirectional messaging across rounds.

Workaround if we proceed: each task → one-shot `Agent` call (general-purpose, opus)
that does implementation **and** self-review against an in-prompt review rubric.
Lower fidelity than the prescribed loop, but real.

### 3. Code-artefact tasks ARE executable here

Several tasks produce code that can be written and unit-tested locally without the
VM. These are real artefacts the operator will later copy to the VM:

- Task 03 — sanitizer module + tests (Python)
- Task 04 — workspace-manager FastAPI service + tests
- Task 10 — swarm-bridge adapter (Python, against ruflo MCP surface — needs interface stubs)
- Task 11 — validator wrappers (Python)
- Task 12 — PR template + GitHub Actions workflow (YAML/MD)
- Task 13 — conformance harnesses (multi-language; partial — Rust/WASM fixtures depend on mnemonic-core which isn't here)
- Task 14 — fabric-watchdog + /turn-into-task (Python)
- Task 15 — last-known-good hook + safe-mode script + smoke-gate workflow (bash + YAML)

Suggested local layout: write artefacts under `fabric/` at repo root, mirroring the
`~/.fabric/` layout on the future VM. Operator does `rsync fabric/ op@vm:~/.fabric/`
when the VM is provisioned.

---

## Three execution paths (pick one)

### Path A — full skill execution (NOT RECOMMENDED)

Spawn all 21 task teammates per skill. ~9 will fail or fabricate; the rest will
produce real artefacts. Wastes opus tokens on operator-required tasks. Audit /
QA / deploy waves run against an incomplete state.

### Path B — code-only subset, written locally (RECOMMENDED DEFAULT)

Execute the code-artefact tasks (03, 04, 10, 11, 12, 14, 15) — and as much of 13 as
possible — writing to `fabric/` under repo root. Skip 01, 02, 05, 06, 07, 08, 09,
20, 21 as operator-driven (mark `status: deferred-to-operator`). Run audits (16, 17,
18) and pre-deploy QA (19) over the code that was produced.

What you get: real, testable code modules ready to ship to the VM. ~7 working
days of agent work compressed; ~$ spent on opus calls.

Skill deviations to call out:
- No `TeamCreate` — one-shot agents with self-review-against-rubric
- Reviewers consolidated: each task gets ONE reviewer agent (sonnet) instead of 2-3
- Max 1 review round (one-shot model) instead of 3
- Operator tasks marked `deferred-to-operator` in frontmatter, not `done`

### Path C — three-task proof (LOW RISK)

Pick three high-value, fully-self-contained code tasks (03 sanitizer, 04
workspace-manager, 14 watchdog), execute them as in Path B. Stop, you review the
artefacts, then decide whether to continue with Path B for the rest.

---

## Waves (if we proceed with Path B)

| Wave | Tasks | Mode |
|------|-------|------|
| 1 | 01, 02 | DEFER (operator) |
| 1 | 03 | CODE — produce `fabric/logs/sanitizer/` |
| 2 | 04 | CODE — produce `fabric/workspace-manager/` |
| 2 | 05 | DEFER (operator — upstream OSS patch) |
| 3 | 06, 07, 08 | DEFER (operator — installs) |
| 3 | 09 | DEFER (operator — live e2e) |
| 4 | 10 | CODE — produce `fabric/integrations/swarm-bridge.py` |
| 4 | 11 | CODE — produce `fabric/molyanov/validators/` wrappers |
| 5 | 12 | CODE — produce `fabric/github-templates/pr-conformance/` |
| 5 | 13 | CODE PARTIAL — harness scaffolding only (Rust/WASM fixtures need real upstream code) |
| 6 | 14 | CODE — produce `fabric/watchdog/` |
| 6 | 15 | CODE — produce `fabric/safe-mode/` + smoke-gate workflow |
| 7 | 16, 17, 18 | RUN — audits over code produced in waves 1–6 |
| 8 | 19 | RUN — pre-deploy QA over local code artefacts |
| 8 | 20, 21 | DEFER (operator — deploy and post-deploy on VM) |

**Executable in Path B:** 9 code tasks + 3 audits + 1 pre-deploy QA = 13 agent dispatches.
**Deferred to operator:** 8 tasks (01, 02, 05, 06, 07, 08, 09, 20, 21).

Estimated wall-clock with parallel waves: ~30–60 minutes of agent runtime, depending
on retries.

---

## What I need from you

Pick one:

- **A** — full skill execution (not recommended; will waste tokens on fabricated work)
- **B** — code-only subset to `fabric/` (recommended; ~13 agent dispatches)
- **C** — three-task proof (sanitizer + workspace-manager + watchdog) then decide
- **D** — something else (please specify; e.g., "skip /do-feature, treat the 21 tasks as a manual checklist for VM setup")

Once you choose, I'll initialize checkpoint.yml, mark deferred tasks, and start
Wave 1.
