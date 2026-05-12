# fabric/integrations

Adapters connecting molyanov skill commands to external service APIs.

## Components

### `swarm_bridge.py` — `/do-feature` to ruflo swarm MCP

`SwarmBridge` adapts molyanov `/do-feature` feature plans to ruflo swarm MCP calls:

1. Calls `swarm_init` once per feature.
2. For each wave: for each task, leases a worktree via `POST http://localhost:8080/worktree`,
   then calls `agent_spawn` with `cwd=<resolved_path>` and the task brief.
3. Worktree lifetime is managed by a context-manager pattern (auto `DELETE` in `finally`).
4. Partial failure safety: if any spawn fails, all successfully-created worktrees in that
   wave are still deleted.

### `workspace_client.py` — workspace-manager HTTP wrapper

Thin `httpx.AsyncClient` wrapper around the workspace-manager REST API. Retries on 5xx
(max 3, exponential back-off); raises immediately on 4xx. Raises `CapacityError` on 409.

### `ruflo_client.py` — ruflo MCP wrapper

Async facade over the `swarm_init` and `agent_spawn` MCP tools. Tool callables are
injected at construction time (dependency injection pattern) for testability.

## Logging

All log emission uses `fabric.logs.sanitizer.handler.SanitizedFileHandler`.
An `ImportError` from that module is a hard fail — install `fabric/logs/sanitizer`
before using this package.

## Tests

```
cd fabric/integrations
python -m pytest tests/ -v
```

All tests are fully isolated — no real workspace-manager or ruflo process is required.

## Deploy-time note

Update `~/.fabric/molyanov/skills/feature-execution/SKILL.md` to invoke `SwarmBridge`
via the `fabric-services` Ansible role (do not edit the SKILL.md manually before deploy).
