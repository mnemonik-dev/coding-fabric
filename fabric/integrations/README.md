# fabric/integrations

Adapters connecting molyanov skill commands to external service APIs.

## Components

### `swarm_bridge.py` — `/do-feature` to ruflo swarm MCP

`SwarmBridge` adapts molyanov `/do-feature` feature plans to ruflo swarm MCP calls.

#### Worktree ownership contract

Worktree lifetime is a **swarm-level responsibility**, not a spawn-level one.
A spawned agent is still running and actively using its cwd directory after
`agent_spawn` returns.  Deleting the directory immediately would corrupt the agent.

The caller (molyanov skill) must therefore:

1. Call `dispatch(plan)` — returns a `DispatchResult` with `leased_worktrees` (a
   `dict[task_id, path]` for every live worktree).
2. Wait until agents finish (via ruflo swarm signal, webhook, or poll).
3. Call `cleanup_worktrees(result.leased_worktrees)` to release all worktrees.

For synchronous / test workflows where agents complete before `dispatch` returns,
pass `auto_cleanup=True` and worktrees are deleted before the call returns.

If `dispatch` raises mid-wave, it cleans up all worktrees it owns before propagating
the exception — no manual cleanup needed on the failure path.

#### Protocol

1. Calls `swarm_init` once per feature.
2. For each wave: for each task, leases a worktree via `POST /worktree`,
   then calls `agent_spawn` with `cwd=<resolved_path>` and the task brief.
3. Tasks in a wave are spawned concurrently with `asyncio.gather(return_exceptions=True)`;
   a single failure does not cancel sibling coroutines mid-flight.
4. Capacity handling: on HTTP 409 the bridge sleeps with jitter
   (`capacity_wait_s + random.uniform(0, capacity_wait_s * 0.5)`) then retries once.
5. A `Semaphore(max_concurrent_creates)` limits simultaneous `create_worktree` calls
   to prevent thundering herd on the workspace-manager.

### `workspace_client.py` — workspace-manager HTTP wrapper

Thin `httpx.AsyncClient` wrapper around the workspace-manager REST API.
Retries on 5xx (max 3, exponential back-off); raises immediately on 4xx.

Exception hierarchy:
- `WorkspaceError` — base
  - `WorkspaceCapacityError` (alias: `CapacityError`) — HTTP 409
  - `WorkspaceUnreachableError` — connection failure / timeout
  - `WorkspaceClientError` — non-retryable 4xx (other than 409)

### `ruflo_client.py` — ruflo MCP wrapper

Async facade over the `swarm_init` and `agent_spawn` MCP tools.
Tool callables are injected at construction time (dependency injection) for testability.

Exception hierarchy:
- `RufloError` — base
  - `RufloSpawnError` — wraps any exception from `agent_spawn_fn` with structured
    context (`task_id`, `swarm_id`, `cause`).

### `swarm_bridge.py` exceptions

- `SwarmError` — raised on `swarm_init` failure; carries `system="ruflo"` and a
  `context` dict with `feature_id`.

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
