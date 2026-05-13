# Role: mnemonic-mcp

> **STATUS: DISABLED BY DEFAULT (descoped 2026-05).**
> The local Mnemonic MCP server is not yet production-ready. coding-fabric
> deploys cleanly without it. To re-enable, set `mnemonic_mcp_enabled: true`
> in inventory once the MCP server reaches v1.0+ and a signing-key generation
> procedure is in place. Re-introduction work is tracked in the backlog
> feature `work/mnemonic-attestation-integration/`.
>
> When disabled, the role installs no-op stub hooks (5 scripts under
> `/etc/mnemonic-mcp/hooks/`) that exit 0 silently and log `attestation
> skipped (mnemonic-mcp disabled — see backlog)` to journald so downstream
> code paths invoking the hooks do not fail.

Installs and configures the local Mnemonic MCP server with 5 trigger-point hook scripts that produce the per-feature DAG attestation lineage. Anchors user-spec AC11-AC16 and tech-spec §2.3 role 9, §2.6 D14.

## Responsibilities

1. **Binary Installation**: Downloads and verifies mnemonic-mcp server binary from GitHub releases (pinned version, SHA256 verified).
2. **systemd Unit**: Renders `/etc/systemd/system/mnemonic-mcp.service` with `LoadCredential` mechanism to load signing key from Vaultwarden vault (never writes key to disk).
3. **Configuration**: Renders `/etc/mnemonic-mcp/config.yml` binding to `127.0.0.1:7777` in local mode.
4. **Protocol QA**: Renders `/etc/mnemonic-mcp/protocol-qa.env` with `MNEMONIC_MODE=full` and Solana devnet + Arweave testnet credentials (mode 0600, owner `op`).
5. **Hook Scripts**: Installs 5 trigger-point hook scripts under `/etc/mnemonic-mcp/hooks/`:
   - `user-spec.sh` (root node)
   - `tech-spec.sh` (parent: user-spec)
   - `task-complete.sh` (parent: tech-spec)
   - `pre-deploy.sh` (parent: task-complete)
   - `post-deploy.sh` (parent: pre-deploy, final node)
6. **molyanov Integration**: Configures molyanov success paths to invoke hooks at phase completion.
7. **Healthcheck**: Smoke test via stub invocation of all 5 hooks; verifies DAG construction via `mnemonic_recall`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `mnemonic_mcp_enabled` | `false` | Master switch (descoped 2026-05). When false, only no-op stub hooks are installed. |
| `mnemonic_mcp_version` | `0.1.0` | Binary version tag |
| `mnemonic_mcp_binary_sha256` | `changeme...` | SHA256 checksum for binary verification |
| `mnemonic_mcp_install_dir` | `/opt/mnemonic-mcp` | Installation directory |
| `mnemonic_mcp_config_dir` | `/etc/mnemonic-mcp` | Configuration directory |
| `mnemonic_mcp_service_user` | `op` | systemd service user |
| `mnemonic_mcp_mode` | `local` | Operation mode: `local` or `full` |
| `solana_devnet_rpc_url` | `https://api.devnet.solana.com` | Solana RPC endpoint (protocol-qa only) |
| `arweave_testnet_url` | `https://testnet.arweave.net` | Arweave testnet URL (protocol-qa only) |

## Dependencies

- `base` (OS user management)
- `vaultwarden` (signing key source via systemd vault:// URI)
- `molyanov` (skill success path integration)

## Security

- Signing key never written to disk; loaded by systemd `LoadCredential` at unit startup
- protocol-qa.env mode 0600, owner `op`
- Service runs with `NoNewPrivileges=yes`, `ProtectSystem=strict`
- All hook scripts: `set -euo pipefail`, proper error handling

## Hook Trigger Points & DAG Construction

Each hook is invoked by molyanov at phase completion:

| Hook | Phase | Parent | Output |
|------|-------|--------|--------|
| user-spec.sh | user-spec approved | none (root) | `work/<feature>/attestations.yml` |
| tech-spec.sh | tech-spec approved | user-spec attestation_id | appends to attestations.yml |
| task-complete.sh | all tasks passing | tech-spec attestation_id | appends (with optional Solana/Arweave txids in full mode) |
| pre-deploy.sh | pre-deploy QA pass | task-complete attestation_id | appends to attestations.yml |
| post-deploy.sh | deploy + verify | pre-deploy attestation_id | appends (DAG complete) |

In `MNEMONIC_MODE=full` (protocol-qa topic only), task-complete and post-deploy hooks record Solana devnet + Arweave testnet transaction IDs.

## Full Mode vs Local Mode

- **Local** (default): Signs in-process, no network calls. All hooks work offline.
- **Full** (protocol-qa topic only): Anchors attestations to Solana devnet and Arweave testnet. If RPC outage, hook queues for retry; molyanov skill success is blocked until receipt.

## Key Rotation

To rotate the signing key:

1. Update the secret in Vaultwarden at path `mnemonic/mcp-signing-key`
2. Reload systemd: `systemctl daemon-reload`
3. Restart service: `systemctl restart mnemonic-mcp`
4. Verify: `systemctl is-active mnemonic-mcp`

No ansible re-run needed; systemd will load the new credential on next startup.

## Healthcheck

Post-deployment, the role runs:

1. `systemctl is-active mnemonic-mcp` → must be `active`
2. Synthetic invocation of all 5 hooks with stubbed parents
3. `mnemonic_recall --feature=stub` → must return 5-node DAG

If any check fails, task exits non-zero; ansible halt prevents broken deployments.

## Testing (Molecule)

```bash
molecule test -s mnemonic-mcp
```

Molecule scenario mocks the Mnemonic MCP server and validates:
- All hooks installed and executable
- Configuration templates rendered correctly
- systemd unit loads
- DAG construction smoke test

## Files

- `tasks/main.yml` — Installation, configuration, healthcheck
- `defaults/main.yml` — Variable defaults
- `handlers/main.yml` — systemd reload/restart handlers
- `templates/`:
  - `mnemonic-mcp.service.j2` — systemd unit
  - `config.yml.j2` — Server config
  - `protocol-qa.env.j2` — Full-mode env vars
  - `molyanov-mnemonic-hooks.yml.j2` — molyanov integration
  - `hooks/{user-spec,tech-spec,task-complete,pre-deploy,post-deploy}.sh.j2` — 5 hook scripts

## Cross-Role Notes

- molyanov role: Installs molyanov skills; this role registers hooks with success paths
- vaultwarden role: Provides secret store; this role only references vault URI (never requests the key)
- ruflo role: Provides hook runner machinery; this role plugs into it via molyanov config
