# workspace-manager

Per-task git worktree lifecycle service for coding-fabric.

FastAPI service that manages up to 10 concurrent git worktrees, materialises
per-worktree `.env` files from Vaultwarden, and exposes a health endpoint.
Binds to the tailnet IP only (never `0.0.0.0`).

## API

```
POST   /worktree              create worktree + .env
DELETE /worktree/{task_id}    cleanup worktree + .env
GET    /worktree/{task_id}    lookup single worktree
GET    /worktrees             list all active worktrees
GET    /health                service introspection
```

## Environment variables (production, set by Ansible)

| Variable | Required | Default |
|---|---|---|
| `BIND_IP` | yes | `127.0.0.1` |
| `BIND_PORT` | no | `8080` |
| `WORKTREES_ROOT` | no | `~/code/mnemonic-workspaces` |
| `REPOS_ROOT` | no | `~/code/mnemonic-masters` |
| `SCCACHE_DIR` | no | `~/.cache/sccache` |
| `STATE_FILE` | no | `~/.fabric/workspace-manager/state.json` |
| `LOG_FILE` | no | `~/.fabric/logs/sanitized/workspace-manager.log` |
| `LOG_LEVEL` | no | `INFO` |

The Bitwarden session token is loaded via systemd `LoadCredential` at
`/run/credentials/workspace-manager.service/bw-session`.  It is never
stored as an environment variable on disk.

## Development

```bash
cd fabric/workspace-manager
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

## Running locally (smoke)

```bash
BIND_IP=127.0.0.1 uvicorn main:app --port 8080
curl -s http://127.0.0.1:8080/health | python -m json.tool
```

## Security notes

- `task_id` enforced as `^[A-Z0-9-]{3,40}$` — no path traversal possible.
- `.env` written with mode `0600`, owner `op`.
- Bitwarden session token loaded via `LoadCredential`, never via env var on disk.
- All log output routed through `fabric.logs.sanitizer.SanitizedFileHandler`.
- `vault.py` logs only structured metadata (`topic`, `key_count`), never secret values.
- Service binds to tailnet IP only; systemd unit rejects wildcard via `BIND_IP` guard.
