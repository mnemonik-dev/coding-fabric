---
status: planned
depends_on: [12]
wave: 6
skills: [deploy-pipeline]
verify: [smoke]
reviewers: []
teammate_name:
---

# Task 13: Deploy content-publish-pipeline to fabric VM

## Required Skills

Перед выполнением задачи загрузи:
- `/skill:deploy-pipeline` — [skills/deploy-pipeline/SKILL.md](~/.claude/skills/deploy-pipeline/SKILL.md)

## Description

Ship the content-publish-pipeline feature to the fabric Hetzner VM via the existing `deploy-fabric.yml` GitHub Actions workflow. This is the canonical deploy path for this repo (CLAUDE.md: "CI deploy is the canonical path — don't edit `.env` files or systemd units directly on the VM").

The deploy must succeed with the **persistent-VM** semantics that landed in tasks #24/#25: VM uptime MUST INCREASE across the deploy (proving no destroy-recreate). After Tofu apply and the Ansible playbook complete green, all roles must be applied — including two new/changed roles introduced by this feature:
- `content-publisher` (NEW role from Tasks 3 / 7) — installs the Python worker package and its systemd unit.
- `mnemonic-mcp` (REWRITTEN in Task 2, now toggled `mnemonic_mcp_enabled: true`) — installs `@mnemonik-xyz/mcp` via npm and registers its systemd unit.

The gate for this task is: workflow run `completed:success` AND VM `/proc/uptime` strictly greater post-deploy than pre-deploy AND the two new/changed systemd units (`content-publisher`, `mnemonic-mcp`) report `active (running)` on the VM. Failing any of these three checks means the deploy did not actually succeed regardless of GitHub Actions' green tick.

**Sidecar jobs awareness:** `deploy-fabric.yml` chains downstream jobs `e2e-smoke` and `post-deploy-avp` after successful Tofu/Ansible apply. These are feature-orthogonal infrastructure smoke checks owned elsewhere — Task 13's gate MUST NOT block on their pass/fail. Task 13 ends when `tofu-apply` and `ansible-deploy` jobs are green and the three local checks above pass. Treat sidecar job results as informational; surface them in `decisions.md` but do not fail Task 13 on a sidecar failure (those are reported separately).

## What to do

1. Capture pre-deploy VM uptime baseline via `ssh op@<vm-host> 'cat /proc/uptime'` (first number, seconds since boot). Record it for the post-deploy comparison.
2. Ensure the working tree is committed and pushed: `git push origin claude/review-coding-fabric-spec-uUvMK`.
3. Dispatch the deploy workflow: `gh workflow run deploy-fabric.yml -f action=apply --ref claude/review-coding-fabric-spec-uUvMK`.
4. Start a background watcher that polls workflow run status until `completed:success` (or fail-out on `completed:failure` / `completed:cancelled`). Use the proven session pattern — `gh run list --workflow=deploy-fabric.yml --limit 1 --json status,conclusion,databaseId` polled at a reasonable cadence. NOTE on the race: a bare `gh run list --limit 1` can return a different workflow's run if another workflow fires simultaneously; ALWAYS scope with `--workflow=deploy-fabric.yml`. Preferred (race-free) alternative: capture the run URL/id directly from `gh workflow run ... --json` output where supported, or call `gh api /repos/{owner}/{repo}/actions/workflows/deploy-fabric.yml/runs --jq '.workflow_runs[0]'` immediately after dispatch and pin to that `databaseId` for the rest of the watch loop. Do NOT re-resolve the run id on every poll iteration — pin once, then watch.
5. While the workflow runs, surface progress (current job name, latest annotation) so the operator can see where it is — but do NOT block on operator input.
6. On `completed:success`, immediately capture post-deploy VM uptime via `ssh op@<vm-host> 'cat /proc/uptime'`.
7. Assert post-deploy uptime > pre-deploy uptime. If post-deploy uptime is LOWER (VM was rebooted/recreated), report failure even though the workflow succeeded — this is the #24/#25 regression signal.
8. SSH to the VM and verify the two new/changed systemd units are `active (running)`:
   - `systemctl is-active content-publisher` → `active`
   - `systemctl is-active mnemonic-mcp` → `active`
9. SSH to the VM and verify the existing services were not broken by the deploy:
   - `systemctl is-active telegram-ai-agent` → `active`
   - `systemctl is-active workspace-manager` → `active`
   - `docker compose -f /opt/kaneo/docker-compose.yml ps` — all containers `Up`
   - `docker compose -f /opt/vaultwarden/docker-compose.yml ps` — all containers `Up`
10. Record the workflow run URL, the pre/post uptime numbers, and the systemctl results in `work/content-publish-pipeline/decisions.md` under "Task 13".
11. Do NOT run the live post-deploy verification flow (operator brief in the Telegram topic, MCP recall, etc.) — that is Task 14's responsibility. Task 13 ends when infrastructure is green and the new units are running.

## Acceptance Criteria

- [ ] Branch `claude/review-coding-fabric-spec-uUvMK` pushed to `origin` before dispatch.
- [ ] `gh workflow run deploy-fabric.yml -f action=apply --ref claude/review-coding-fabric-spec-uUvMK` dispatched successfully (workflow run id captured).
- [ ] Workflow run reaches `completed:success` (tofu-plan, tofu-apply, ansible-deploy jobs all green).
- [ ] Tofu apply log contains no `destroy` actions on `hcloud_server.fabric` (persistent-VM mode confirmed).
- [ ] VM uptime post-deploy > VM uptime pre-deploy (uptime preserved — proves no implicit reboot/recreate).
- [ ] Ansible play recap: all roles applied, `failed=0`, including the new `content-publisher` role and the rewritten `mnemonic-mcp` role.
- [ ] `systemctl is-active content-publisher` → `active` on the VM.
- [ ] `systemctl is-active mnemonic-mcp` → `active` on the VM (`mnemonic_mcp_enabled: true` now in effect).
- [ ] `systemctl is-active telegram-ai-agent` → `active` (no regression).
- [ ] `systemctl is-active workspace-manager` → `active` (no regression).
- [ ] kaneo and vaultwarden docker compose stacks: all containers `Up` (no regression).
- [ ] Task 13 entry written to `work/content-publish-pipeline/decisions.md` with workflow URL, pre/post uptime values, and systemctl results.

## Context Files

- [user-spec.md](../user-spec.md)
- [tech-spec.md](../tech-spec.md)
- [decisions.md](../decisions.md)
- [12-pre-deploy-qa.md](./12-pre-deploy-qa.md)
- [03-content-publisher-ansible-scaffold.md](./03-content-publisher-ansible-scaffold.md)
- [02-mnemonik-mcp-role-npm-rewrite.md](./02-mnemonik-mcp-role-npm-rewrite.md)
- [07-content-publisher-wire-service-smoke.md](./07-content-publisher-wire-service-smoke.md)
- [CLAUDE.md](/Users/syi/src/sessions/coding-fabric/CLAUDE.md) — pipeline overview, infrastructure section, key constraints (#24/#25)
- [docs/vm-runbook.md](/Users/syi/src/sessions/coding-fabric/docs/vm-runbook.md) — SSH host, paths, secrets map (gitignored; lives on operator's local machine)
- [.github/workflows/deploy-fabric.yml](/Users/syi/src/sessions/coding-fabric/.github/workflows/deploy-fabric.yml) — the workflow being dispatched
- [infrastructure/ansible/playbooks/deploy.yml](/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/playbooks/deploy.yml) — playbook that runs the roles

## Verification Steps

### Smoke

These are the checks the deploying agent runs as part of executing the task — they ARE the task, not a post-hoc audit:

- `git push origin claude/review-coding-fabric-spec-uUvMK` → exit 0 (or "Everything up-to-date").
- `gh workflow run deploy-fabric.yml -f action=apply --ref claude/review-coding-fabric-spec-uUvMK` → exit 0.
- `gh run list --workflow=deploy-fabric.yml --limit 1 --json status,conclusion,databaseId,url` — capture the new run id and watch until `status=completed`.
- Background watcher loop polls `gh run view <id> --json status,conclusion` until `status=completed`; gate on `conclusion=success`.
- Pre-deploy: `ssh op@<vm-host> "awk '{print \$1}' /proc/uptime"` → record as `UPTIME_PRE`.
- Post-deploy (immediately on workflow completion): `ssh op@<vm-host> "awk '{print \$1}' /proc/uptime"` → record as `UPTIME_POST`.
- Assert `UPTIME_POST > UPTIME_PRE` (floats). If `UPTIME_POST < UPTIME_PRE` → VM was rebooted/recreated → FAIL the task even if workflow is green.
- `ssh op@<vm-host> "systemctl is-active content-publisher mnemonic-mcp telegram-ai-agent workspace-manager"` → all `active`.
- `ssh op@<vm-host> "sudo docker compose -f /opt/kaneo/docker-compose.yml ps --format json && sudo docker compose -f /opt/vaultwarden/docker-compose.yml ps --format json"` → no container in non-running state.
- `gh run view <id> --log | grep -E "PLAY RECAP|failed=" | tail -5` → confirm `failed=0` and presence of `content-publisher` and `mnemonic-mcp` in the recap.

## Details

**Files:** No source-tree files are modified by this task. The artifacts are: the dispatched workflow run, the VM state after Ansible converges, and a `decisions.md` entry under "Task 13".

**Dependencies:**
- Task 12 (Pre-deploy QA) must be `done` — all unit + integration tests green, AVP report generated. We do not redeploy work that hasn't passed QA.
- All Wave 1–3 tasks (1–8) and Audit Wave (9–11) committed to `claude/review-coding-fabric-spec-uUvMK`.
- GitHub Actions secrets in place: `HCLOUD_TOKEN`, `OPERATOR_SSH_PUBKEY`, `CI_SSH_PRIVATE_KEY`, `TAILSCALE_AUTH_KEY`, sops age key. These were provisioned long before this feature; no setup needed here.
- VM `op@` user reachable via Tailscale magic DNS (or whatever host the runbook lists). Operator's local SSH config is assumed to resolve `op@<vm-host>`.

**Edge cases / failure modes:**
- **Hetzner capacity error (`resource_unavailable` on ccx33 in fsn1)** — surfaces during `tofu apply`. NOT in scope to retry here — report the failure with a link to the Hetzner status / the `apply.log` snippet and stop. (Tasks #24/#25 reduce but don't eliminate this risk because persistent-VM mode means we rarely need new capacity at all.)
- **Ansible task fails on a specific role** — `gh run view <id> --log-failed` to surface the failing task. Common candidates given this feature:
  - `content-publisher` role: `pip --require-hashes` mismatch (regenerate `requirements.locked.txt` and recommit), `inspect.signature` assertion (upstream blogger API drifted — see Decision 11), `RequiresMountsFor=` fact unresolved (volume not mounted, see Decision 10).
  - `mnemonic-mcp` role: `npm install -g @mnemonik-xyz/mcp@<version>` 404 / version yanked (bump pinned version after verifying with `npm view`); node/npm prerequisite missing.
- **Workflow green but a new systemd unit `inactive` or `failed`** — `ssh op@<vm-host> 'sudo journalctl -u content-publisher --since "10 minutes ago" -n 200'`. Most likely: missing env var in `/etc/blogger.env` (sops keys not all populated — Task 1 sops template extension), or `RequiresMountsFor=` blocking on a volume that isn't mounted.
- **VM uptime decreased post-deploy** — means destroy-recreate happened despite #24/#25. Check `tofu apply` log for `-/+` markers on `hcloud_server.fabric`. This is a regression in the workflow and must be reported as such — do not silently accept.
- **`mnemonic_mcp_enabled` still false** — verify the role's `defaults/main.yml` from Task 2 actually flipped the default OR that `group_vars` overrides it. Symptom: `systemctl is-active mnemonic-mcp` returns `inactive` because the unit was never enabled.
- **Workflow times out before reaching completion** — `ansible-deploy` job has a 45-min timeout in the workflow. If it's still running past that, the watcher will see `status=in_progress` indefinitely; cap the watcher loop at ~60 minutes and fail loud.
- **Pre-deploy uptime SSH fails** — the VM may already be unreachable; do not proceed with dispatch. Report and stop.

**Implementation hints (NOT pseudocode):**
- Use this session's already-proven Bash deploy-watcher pattern. Outline:
  1. `UPTIME_PRE=$(ssh op@<host> "awk '{print \$1}' /proc/uptime")`
  2. `git push origin claude/review-coding-fabric-spec-uUvMK`
  3. `gh workflow run deploy-fabric.yml -f action=apply --ref claude/review-coding-fabric-spec-uUvMK`
  4. Resolve the new run id ONCE, scoped to this workflow (avoids the race where another workflow fires at the same time): `RUN_ID=$(gh run list --workflow=deploy-fabric.yml --limit 1 --json databaseId --jq '.[0].databaseId')`. Even safer: `RUN_ID=$(gh api /repos/{owner}/{repo}/actions/workflows/deploy-fabric.yml/runs --jq '.workflow_runs[0].id')`. Pin this id for the entire watch loop; do not re-resolve.
  5. Poll loop: `gh run view $RUN_ID --json status,conclusion --jq '.status + " " + (.conclusion // "")'` — every 15–30s; break on `completed *`. Using the pinned `$RUN_ID` here means a concurrent workflow run cannot accidentally redirect the watcher.
  6. Gate on `conclusion == success`.
  7. `UPTIME_POST=$(ssh op@<host> "awk '{print \$1}' /proc/uptime")`
  8. Compare floats: `awk -v a="$UPTIME_POST" -v b="$UPTIME_PRE" 'BEGIN{exit !(a>b)}'`.
  9. `ssh op@<host> systemctl is-active content-publisher mnemonic-mcp telegram-ai-agent workspace-manager`
- The watcher can be run via Bash with `run_in_background: true` if it's expected to take >2 minutes. Stream stdout lines to notify on state changes. `ansible-deploy` alone is allowed up to 45 min in the workflow; full pipeline (tofu-plan → tofu-apply → 90s cloud-init grace → ansible-deploy) realistically lands in 15–25 min on a persistent VM.
- VM host: look up in `docs/vm-runbook.md` (gitignored, on the operator's local machine). If absent, ask the operator before running SSH commands. Do NOT hardcode an IP into the task — the runbook is the source of truth.
- This task does NOT run E2E tests. `tests/e2e/test_publish_smoke.sh` and the post-deploy live-publish verification are Task 14's responsibility (`post-deploy-qa` skill).
- If deploy fails, do NOT attempt a fix-and-redeploy from inside this task. Report the failure (which job, which task, log excerpt) and let the operator decide whether to roll back, retry, or escalate.

## Reviewers

No reviewers configured for this task — deploy execution is verified by the smoke gate (workflow completion + uptime check + systemctl checks). Task 14 (post-deploy verification) provides the next layer of validation.

## Post-completion

- [ ] Записать краткий отчёт в decisions.md по шаблону: workflow run URL, `UPTIME_PRE` / `UPTIME_POST`, `systemctl is-active` results for all 4 units, kaneo/vaultwarden container state summary.
- [ ] Если deploy упал — не помечать таску как done; зафиксировать в decisions.md что именно упало и где смотреть логи (`gh run view <id> --log-failed`).
- [ ] Если VM uptime уменьшилась несмотря на зелёный workflow — отдельной строкой в decisions.md отметить это как regression в персистентном-VM режиме (#24/#25) и завести follow-up ticket.
- [ ] Никакие изменения в user-spec / tech-spec тут не ожидаются — это deploy задача, не код.
