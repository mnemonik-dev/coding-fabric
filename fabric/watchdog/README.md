# fabric-watchdog

Operational alert watchdog for the coding-fabric loop VM.

Runs 9 alert checks every 5 minutes via a systemd timer. Alerts post to
the ops Telegram topic. Operators can convert any alert into a Kaneo task
card with the `/turn-into-task` bot command.

---

## Alert classes and thresholds

| Class | Severity | Threshold / Condition |
|---|---|---|
| `orphaned_worktrees` | WARNING | Disk dirs under `worktrees_root` not tracked in workspace-manager `state.json` |
| `hung_tmux` | WARNING | tmux session idle > 12 hours (configurable via `tmux_idle_hours`) |
| `solana_rpc` | CRITICAL | Devnet RPC does not respond to `getHealth` within 10 s, or returns `result != "ok"` |
| `irys_balance` | WARNING | Irys testnet wallet balance below `irys_balance_min` (default 1 000 000 units) |
| `stale_swarms` | WARNING | ruflo swarm age > `swarm_ttl_hours` (default 48 h) |
| `disk_pressure` | WARNING / CRITICAL | Worktrees partition used fraction vs 50 GB budget: 75% = WARNING, 85% = WARNING, 90% = CRITICAL |
| `master_drift` | WARNING | Local `main` HEAD differs from `origin/main` after `git fetch` |
| `stale_prs` | WARNING | Open GitHub PR not updated in > 7 days (configurable via `stale_pr_days`) |
| `failed_attestation` | CRITICAL | Mnemonic MCP pending queue contains entries with `status: failed/error` |
| `stale_lkg` | WARNING | `last-known-good` tag in `mnemonic-loop` repo older than 7 days |

## Opt-in disable per check

Add the check module name (without `.py`) to `disabled_checks` in
`config.json` to silence individual checks. Natural-owner alternatives
(e.g. ruflo health hook for `stale_swarms`) can set this once they have
been deployed.

```json
{
  "disabled_checks": ["stale_swarms", "hung_tmux"]
}
```

---

## /turn-into-task usage

From the ops Telegram topic:

```
/turn-into-task <alert_id>
```

`alert_id` is the UUID printed in the alert message. It is valid for 24 hours
after the alert fires. The command creates a Kaneo card in the `watchdog`
project with the alert details. Duplicate calls for the same `alert_id` are
idempotent — no second card is created.

**Example:**

```
[WARNING] disk_pressure
Disk usage at 87.3% (43.7/50 GiB) — exceeded 85% threshold on '/home/op/code/mnemonic-workspaces'
alert_id: 550e8400-e29b-41d4-a716-446655440000

/turn-into-task 550e8400-e29b-41d4-a716-446655440000
# -> Kaneo card created: [[WARNING] disk_pressure] id=card-128
```

---

## Ops topic message format

Each alert message has the format:

```
[SEVERITY] alert_class
<evidence line>
alert_id: <uuid4>
```

When more than 3 alerts fire in a single tick, they are batched into one
digest message:

```
[DIGEST] 5 alerts fired in one tick:
  [WARNING] orphaned_worktrees: 2 orphaned worktree dir(s): TASK-007, TASK-008
  [CRITICAL] solana_rpc: Solana RPC 'https://api.devnet.solana.com' unreachable: ...
  ...
alert_ids: <uuid1>, <uuid2>, ...
```

---

## Configuration reference

Configuration is loaded from `WATCHDOG_CONFIG_FILE` (default
`/home/op/.fabric/watchdog/config.json`). All keys are optional; missing
keys fall back to the defaults listed below.

| Key | Default | Description |
|---|---|---|
| `worktrees_root` | `/home/op/code/mnemonic-workspaces` | Root dir for worktrees |
| `state_file` | `/home/op/.fabric/workspace-manager/state.json` | workspace-manager state |
| `tmux_idle_hours` | `12` | Hung tmux threshold |
| `solana_rpc_url` | `https://api.devnet.solana.com` | Devnet RPC endpoint |
| `solana_rpc_timeout` | `10` | HTTP timeout (seconds) |
| `irys_node_url` | `https://devnet.irys.xyz` | Irys node URL |
| `irys_address` | — | Wallet address to check (required for irys_balance check) |
| `irys_balance_min` | `1000000` | Minimum acceptable balance |
| `ruflo_state_file` | `/home/op/.fabric/ruflo/swarms.json` | ruflo swarm registry |
| `swarm_ttl_hours` | `48` | Stale swarm threshold |
| `disk_path` | `/home/op/code/mnemonic-workspaces` | Path to check |
| `disk_budget_gb` | `50` | Budget in GiB |
| `disk_warn_threshold` | `0.75` | First warning level |
| `disk_warn2_threshold` | `0.85` | Second warning level |
| `disk_crit_threshold` | `0.90` | Critical level |
| `master_repos` | 4 mnemonic-* clones | List of repo absolute paths |
| `master_branch` | `main` | Branch name |
| `github_repos` | `[]` | List of `owner/repo` strings |
| `github_token` | — | GitHub PAT (set via LoadCredential) |
| `stale_pr_days` | `7` | Stale PR threshold |
| `mnemonic_mcp_url` | `http://127.0.0.1:4000` | Mnemonic MCP server |
| `loop_repo_path` | `/home/op/code/mnemonic-masters/mnemonic-loop` | mnemonic-loop clone |
| `lkg_tag_name` | `last-known-good` | LKG tag name |
| `stale_lkg_days` | `7` | Stale LKG threshold |
| `alert_state_file` | `/home/op/.fabric/watchdog/state/alerts.json` | 24h alert cache |
| `disabled_checks` | `[]` | Check module names to skip |

---

## Deployment

```bash
# Install
python -m venv /home/op/.fabric/watchdog/.venv
/home/op/.fabric/watchdog/.venv/bin/pip install -e fabric/watchdog/

# Install sanitizer dep (hard requirement)
/home/op/.fabric/watchdog/.venv/bin/pip install -e fabric/logs/sanitizer/

# Enable timer
sudo cp fabric/watchdog/systemd/fabric-watchdog.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fabric-watchdog.timer
sudo systemctl status fabric-watchdog.timer
```

---

## Running tests

```bash
cd fabric/watchdog
python -m pytest tests/ -v
```
