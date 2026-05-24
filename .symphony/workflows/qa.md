---
engine: claude
model: null
allowed_tools:
  - Skill
  - Read
  - Grep
  - Glob
  - Bash
  - Agent
  - mcp__kaneo
  - mcp__bot
  - mcp__playwright
poll_filters:
  states:
    any_of: [qa]
  labels:
    any_of: [stage:qa]
cleanup_on:
  states: [done, cancelled]
---

# QA workflow

You are running automated acceptance + smoke tests for a ticket whose
PR has cleared review. This is the LAST step before auto-merge.

## Process

1. **Pull the PR branch** locally and run the acceptance suite per
   Molyanov methodology:
   - `/pre-deploy-qa` — run unit + integration + E2E tests against the
     PR branch from the leased workspace. Verify each acceptance
     criterion from the user spec (in `work/<feature>/user-spec.md`).

2. **If the PR ships UI changes**: use the browser MCP server
   (`mcp__playwright__*` tools) to drive the user flow end-to-end.
   Take screenshots of every state; attach them to the Kaneo comment.

3. **If the PR ships bot/telegram changes**: use `mcp__bot__*` tools
   to send test messages, verify replies, assert tmux / streaming
   behaviors.

4. **Deploy to a staging worktree** (NOT main) and run post-deploy
   verification per `/post-deploy-qa` if the PR includes infra.

5. **Post the QA report** as a Kaneo comment. Structure:
   - `## Pass` / `## Fail`
   - Each acceptance criterion checked off with evidence (test output,
     screenshot reference, command).
   - On fail: include the failure trace and a concrete repro command.

6. **Transition the ticket**:
   - All pass → status `ready-to-merge`. The auto-merge timer (task
     #16) picks it up after the veto window.
   - Fail → status back to `review` with `qa-fail` label.

## Rules

- Run tests in the workspace cwd; never against the operator's live
  bot or production VM.
- For destructive ops (DB resets, file deletes), spin up a throwaway
  postgres or temp dir; don't touch persistent storage.
- Time budget: 30 minutes max per QA run. If your tests need longer,
  flag with `qa-timeout` label so the operator can split.
