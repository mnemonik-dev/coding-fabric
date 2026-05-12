# Execution Plan — coding-fabric v0.2.0

**Feature:** coding-fabric (Mnemonic Protocol Agent-Driven Development Loop)
**Total tasks:** 25 across 8 waves
**Status:** PENDING USER APPROVAL — env constraints documented below
**Fresh start:** no prior checkpoint or decisions.md

---

## Environment constraints (deviations from skill)

1. **No `TeamCreate` tool available.** The skill prescribes persistent named
   teammates with SendMessage-based 3-round review loops. Workaround for this
   environment:
   - One `Agent` call per task (foreground, `subagent_type` chosen from agent
     catalogue, `model: opus`).
   - The teammate's prompt embeds a **self-review rubric** built from the task's
     `reviewers` field (code-reviewer / security-auditor / etc.). Agent does
     impl + self-review in a single shot.
   - No multi-round SendMessage loop. If self-review surfaces unresolved issues,
     teammate writes them to its decisions.md entry as `unresolved findings`,
     and I escalate to you.
   - Cost-saving vs full 3-round loop is significant; quality loss is real but
     bounded by quality of the rubric.

2. **External-service smoke verifications are deferred to operator.**
   Tasks have smoke steps that require real Hetzner, Telegram, Tailscale, Vault,
   Solana devnet, etc. Agents cannot execute those here. Each such smoke step
   gets recorded in the task's decisions.md entry as `smoke: deferred-to-operator`
   along with the exact command to run during bootstrap-checklist execution.
   Verifications that CAN run locally (pytest, ansible-lint, actionlint,
   `tofu validate`, Molecule against ephemeral Docker if Docker is available) DO run.

3. **Path mapping.** Tasks reference paths like `fabric/...`,
   `infrastructure/tofu/...`. These don't exist yet — agents create them as they
   work. Each agent commits the files it produces with proper `feat: task N — …`
   messages.

4. **Skill names in task frontmatter are this-harness agent types.** I'll route:
   - `terraform-master` → `terraform-master` agent
   - `ansible-automation` → `ansible-automation` agent
   - `python-alchemist` → `python-alchemist` agent
   - `fastapi-expert` → `fastapi-expert` agent
   - `github-actions-pro` → `github-actions-pro` agent
   - `playwright-pro` → `playwright-pro` agent
   - `code-reviewing` → `general-purpose` w/ self-review rubric
   - `security-auditor` → `security-auditor` agent
   - `test-master` → `general-purpose` w/ test-quality rubric
   - `pre-deploy-qa`, `post-deploy-qa` → `general-purpose` agents

---

## Wave plan

| Wave | Tasks | Parallel | Skills routed | Notes |
|------|-------|----------|---------------|-------|
| 1 | 01-06 | 6 | terraform-master, ansible-automation × 5 | IaC foundation |
| 2 | 07-09 | 3 | python-alchemist, fastapi-expert, ansible-automation | Sanitizer, workspace-manager, tg-agent role |
| 3 | 10-13 | 4 | ansible × 3, github-actions-pro | ruflo, molyanov, mcp, e2e-smoke CI |
| 4 | 14-15 | 2 | python-alchemist × 2 | swarm-bridge, validator wrappers |
| 5 | 16-17 | 2 | github-actions-pro, playwright-pro | PR conformance, harnesses |
| 6 | 18-19 | 2 | python-alchemist, github-actions-pro | watchdog, safe-mode |
| 7 | 20-22 | 3 | general-purpose × 2, security-auditor | Audit wave (no reviewers per skill) |
| 8 | 23-25 | 3 | general-purpose, github-actions-pro × 2 | pre-deploy QA, deploy CI, post-deploy AVP |

Within each wave: agents dispatched **in parallel** (single message, multiple Agent
tool calls). Lead waits for ALL agents to complete before committing wave + advancing.

---

## Estimated cost & wall-clock

- **Token cost (rough):** opus model, ~25 agents × ~60–120K tokens each (incl. spec
  reads + code writes + self-review) = ~2–3M tokens total. Significant but bounded.
- **Wall-clock:** parallel within waves; each agent ~5–15 min. Total ~2-4 hours
  if no escalations / retries; longer if rounds happen.
- **Output volume:** ~50–80 new files, ~3,000–6,000 lines of code across
  `fabric/` and `infrastructure/`.

---

## What gets produced (concrete file list expected)

After all 8 waves complete:

```
fabric/
  logs/sanitizer/{filter,handler,tg_middleware}.py + tests/
  workspace-manager/{main,worktree,vault,state}.py + tests/ + systemd/
  integrations/swarm-bridge.py + tests/
  molyanov/validators/{skeptic,security_auditor,post_deploy_qa}_wrapper.py + _common + tests/
  watchdog/{checks/*.py,scheduler,telegram,kaneo,turn_into_task}.py + systemd/ + tests/
  safe-mode/{last-known-good.sh,smoke-gate/,tests/}
  github-templates/{pull_request_template.md,workflows/pr-conformance.yml,composite-action/}
  harnesses/{byte-equivalence,mcp-compat,wasm-browser,full-mode}/{Makefile,README,fixtures/,tests/}
  harnesses/actions/*/action.yml

infrastructure/
  tofu/hetzner/{main,variables,outputs,versions}.tf + tests/
  ansible/
    inventory/{hosts.yml.j2,telegram-topics.yml}
    playbooks/{deploy.yml,safe-mode-rollback.yml}
    roles/{base,tailscale,vaultwarden,kaneo,telegram-init,restic-backups,ruflo,molyanov,mnemonic-mcp,telegram-ai-agent,fabric-services}/
  secrets/{.sops.yaml,secrets.sops.yml (template)}
  scripts/bootstrap.sh

.github/workflows/
  deploy-fabric.yml
  e2e-smoke.yml
  post-deploy-avp.yml
  smoke-gate.yml
  pr-conformance.yml
```

After this, the operator executes `work/coding-fabric/bootstrap-checklist.md`
(once, ~30 min) and triggers `deploy-fabric.yml` → live fabric on Hetzner VM.

---

## What WON'T happen here (deferred to operator)

- Real `tofu apply` against Hetzner (no credentials)
- Real Ansible run on a VM (no VM)
- Real bot creation, real Telegram messages, real Solana devnet tx
- Real GitHub Actions runs against the produced workflows
- Live e2e-smoke (T13), live deploy (T24), live post-deploy AVP (T25) — these
  produce the WORKFLOW FILES and DEPLOYMENT LOGIC, but execution against a live
  environment happens during operator bootstrap.

The audit (T20-22), pre-deploy QA (T23), and the produced AVP-runner script
(T25) ARE all runnable locally and they run.

---

## Per-wave commit cadence

After each wave: `git commit -m "chore: complete wave N — N tasks, statuses updated"`
(orchestration commit by lead). Code commits happen inside each teammate Agent
session.

---

## Decision points where I'll pause and ask you

I'll halt and ask explicit confirmation if:
- Any wave produces zero output (all agents fail) — likely env / infra issue
- Audit wave (W7) surfaces critical findings — your call to remediate vs ship
- Pre-deploy QA (W8 / T23) FAILS — you decide whether to fix before bootstrap

---

## What I need from you to start

Pick one of:

- **GO** — proceed with Wave 1, all 6 agents in parallel, opus model
- **GO + dry-run-first** — first dispatch only Wave 1 task 01 (single agent) as a
  proof; review output; then resume full parallelism
- **PAUSE** — adjust constraints, change scope, or back out

After your confirmation I'll initialize `checkpoint.yml`, mark Wave 1 tasks
`in_progress`, and dispatch.
