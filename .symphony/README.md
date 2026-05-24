# Symphony per-repo configuration

This directory tells Symphony how to drive autonomous coding sessions
against this repo. Symphony (the orchestrator daemon, an extension of
`fabric/workspace-manager/`) reads it on each poll tick. Changes land
without a Symphony restart — files are mtime-cached.

## Layout

```
.symphony/
├── policy.yml                # Merge policy + workspace cleanup
└── workflows/
    ├── code.md               # Coder stage (default — claude)
    ├── review.md             # Reviewer stage (codex — cross-engine)
    └── qa.md                 # QA stage (claude + browser MCP)
```

## Workflow files

Each `<stage>.md` has YAML front matter + a markdown body:

- **front matter** — `engine`, `model`, `allowed_tools`, `poll_filters`,
  `cleanup_on`. See `fabric/workspace-manager/workflow_loader.py` for
  the exact schema.
- **body** — the system prompt the engine reads. Reference Molyanov
  skills (`/tech-spec-planning`, `/do-task`, etc.) and Kaneo MCP tools
  (`mcp__kaneo__create_task_comment`).

## Stage routing

Symphony's `dispatch.stage_for(ticket)` picks the workflow per ticket
label:

- Label `stage:code`   → `code.md`
- Label `stage:review` → `review.md`
- Label `stage:qa`     → `qa.md`
- No `stage:*` label   → defaults to `code.md`

Stages are independent tickets in Kaneo. The coding agent opens a new
"review TASK-N" ticket when it finishes; the reviewer agent opens a
"qa TASK-N" ticket when it approves.

## Cross-engine review

`review.md` deliberately specifies a different engine than `code.md`
(codex vs claude). Adversarial diversity — the model that wrote the
code should not be the one approving it. Change per repo if you want
single-engine review.

## Auto-merge

`policy.yml` → `merge_policy`:

- `auto-with-veto` — after `qa` passes, Symphony posts a notice in
  `veto_topic`. If the operator types `/reject <TASK-ID>` within
  `veto_window_hours`, the merge is cancelled. Otherwise Symphony
  runs `gh pr merge`. Default for this repo.
- `always-human-merge` — never auto-merge. Operator types `/approve
  <TASK-ID>` to ship.
- `full-auto` — merge as soon as QA passes; skip the veto window.

## Adding a new stage

1. Create `.symphony/workflows/<stage>.md` with proper front matter.
2. Tickets gain a `stage:<stage>` label as they enter that stage.
3. The workflow body tells the engine how to drive the work and how
   to transition the ticket on completion.

No Symphony restart needed.
