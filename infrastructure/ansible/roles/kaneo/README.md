# Ansible Role: kaneo

Deploy Kaneo, an open-source project management system, via docker-compose. Binds to the Tailscale private network only and reverse-proxies through Caddy. Seeds six default projects idempotently via the Kaneo HTTP API.

## Overview

This role:
1. Deploys Kaneo container via docker-compose v2 with SQLite database
2. Exposes HTTP on `{{ tailscale_ip }}:3030` (Caddy terminates TLS)
3. Registers Kaneo vhost `kaneo.mnemonic-fabric.ts` in Caddy
4. Waits for `/api/health` endpoint to return 200
5. Idempotently seeds six default Mnemonic Protocol projects via HTTP API:
   - `mnemonic-core`
   - `mnemonic-mcp`
   - `mnemonic-wasm`
   - `mnemonic-demo-client`
   - `mnemonic-docs`
   - `mnemonic-loop`
6. Verifies all six projects exist post-run (healthcheck)

## Requirements

- Ansible >= 2.15
- `community.docker` collection (for `docker_compose_v2` module)
- Docker and Docker Compose plugin installed on target host
- `tailscale` role executed first (provides `tailscale_ip` fact)
- `base` role executed first (creates `op` OS user, hardens system)

## Variables

All role variables are prefixed with `kaneo_`.

### Default Variables

See `defaults/main.yml`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `kaneo_image` | `ghcr.io/usekaneo/kaneo:latest` | Docker image tag. Override to pin a release. |
| `kaneo_tailnet_domain` | `kaneo.mnemonic-fabric.ts` | Caddy vhost domain (tailnet only). |
| `kaneo_http_port` | `5173` | Internal HTTP port (matches upstream compose.yml). Caddy reverse-proxies TLS to this. |
| `kaneo_postgres_db` | `kaneo` | Postgres database name. |
| `kaneo_postgres_user` | `kaneo` | Postgres user. |
| `kaneo_data_dir` | `/opt/kaneo/data` | Host bind-mount root; postgres data under `{kaneo_data_dir}/postgres`. |
| `kaneo_health_endpoint` | `/api/health` | Health check path. |

### Encrypted Variables (from sops)

Both **must** be passed by the playbook via sops decryption. Neither is
defined in defaults, so the role fails loudly if missing:

| Variable | Source | Purpose |
|----------|--------|---------|
| `kaneo_auth_secret` | `infrastructure/secrets/secrets.sops.yml` | JWT signing key, `>=32` chars. Without a stable value, sessions are invalidated on every container restart. Generate: `openssl rand -hex 32`. |
| `kaneo_postgres_password` | `infrastructure/secrets/secrets.sops.yml` | Postgres password, `>=12` chars. Generate: `openssl rand -base64 24`. |

## Task Flow

1. **Assert secrets** — both `kaneo_auth_secret` (>=32) and `kaneo_postgres_password` (>=12) are present
2. **Create directories** — `/opt/kaneo`, `{kaneo_data_dir}`, `{kaneo_data_dir}/postgres`
3. **Template docker-compose.yml** — Postgres + Kaneo services, network isolation, healthchecks
4. **Template Caddy vhost** — write `/opt/vaultwarden/caddy_conf_d/kaneo.conf` (visible to the Caddy container via the vaultwarden role's bind mount), reverse-proxy to `kaneo:5173`
5. **Compose up** — start Postgres + Kaneo
6. **Wait for health** — retry `GET https://{tailnet_domain}/api/health` up to 120 times (4-min budget)
7. **Verify containers** — both `kaneo-postgres-1` and `kaneo-kaneo-1` running

Workspaces, projects, and Bearer tokens are created on demand via Kaneo's
built-in MCP server (`apps/api/src/mcp/`) — no static-token seeding. The
first operator who signs in via the UI bootstraps the default workspace.
10. **Verify container running** — Docker ps check

## Idempotency

The role is fully idempotent:

- Docker-compose is re-run each time but converges to "already running" if no template change
- Project seeding uses **explicit GET-before-POST** logic: `when: item.name not in existing_project_names`
- No project is duplicated across runs
- Caddy snippet write only triggers reload handler, never restarts Kaneo

**Test idempotency:** Run `molecule test -s kaneo` twice; second run reports no changes.

## Security

- **Network isolation:** Binds only to `{{ tailscale_ip }}`; no 0.0.0.0 listening
- **Secrets:** API token is never logged (`no_log: true` on HTTP calls)
- **Database:** SQLite file stored in `/opt/kaneo/data` with 0700 perms (operator user only)
- **File ownership:** All files owned by `op:op` user; role runs with `become_user: op` for docker-compose

## Networking

- **Internal:** Kaneo listens on `127.0.0.1:3030` within its docker-compose network
- **Access:** Caddy reverse-proxy on `{{ tailscale_ip }}:8443` (Caddy container bound to tailnet IP)
- **DNS:** Tailnet members resolve `kaneo.mnemonic-fabric.ts` to the tailnet IP via Tailscale DNS
- **TLS:** Caddy terminates TLS; clients connect to `https://kaneo.mnemonic-fabric.ts/`

## Health Check

Kaneo must respond to `GET /api/health` with a 200 status code. The role retries up to 120 times with 2-second delays (4-minute total budget).

If health check fails:
- Check docker logs: `docker compose -f /opt/kaneo/docker-compose.yml logs kaneo`
- Verify network connectivity: `docker network ls`
- Ensure Kaneo image is available locally or can be pulled

## Database

Currently **SQLite only**. The database file is stored at `/opt/kaneo/data/kaneo.db` and is persisted as a docker volume mount.

**Future:** If external PostgreSQL is required, update `kaneo_database_type` and template a `.env` file with a `DATABASE_URL=postgresql://...` connection string.

## Dependencies

- **Roles:** `tailscale` (provides `tailscale_ip` fact), `base` (creates `op` user)
- **Collections:** `community.docker` (install via `ansible-galaxy collection install community.docker`)
- **Binaries:** `docker`, `docker compose` plugin

## Handlers

- **Restart kaneo:** Restarts only the Kaneo service (preserves network, volumes)
- **Reload caddy for kaneo vhost:** Triggers Caddy config reload in the Caddy container (does not restart Kaneo). Uses `vaultwarden_caddy_container_name` variable for cross-role compatibility.

## Cross-Role Contract: Caddy Integration

**This role has a TIGHT COUPLING with the `vaultwarden` role.**

The kaneo role:
1. Generates `/opt/kaneo/Caddyfile.snippet` with the kaneo vhost config
2. Copies the snippet into the Caddy container at `/etc/caddy/conf.d/kaneo.conf`
3. Notifies the Caddy reload handler to apply the new config

**CRITICAL Requirements:**
- The `vaultwarden` role **MUST be applied first** and its Caddyfile **MUST include** `import /etc/caddy/conf.d/*.conf` at the end (added in vaultwarden/templates/Caddyfile.j2)
- The Caddy container name must match `vaultwarden_caddy_container_name` (defaults to `vaultwarden-caddy-1`)
- The Caddy container must have a `/etc/caddy/conf.d` directory (created by vaultwarden role)
- If the vaultwarden Caddyfile does NOT include the import directive, the kaneo vhost snippet will be ignored by Caddy

**Playbook ordering:**
```yaml
- name: Deploy Mnemonic Fabric
  hosts: fabric
  roles:
    - base
    - tailscale
    - vaultwarden     # MUST run first; ensures /etc/caddy/conf.d exists and Caddyfile includes import
    - kaneo           # Runs second; injects vhost into conf.d/kaneo.conf
```

**If you change the Caddy container name or vaultwarden Caddyfile structure:**
- Update `vaultwarden_caddy_container_name` in your playbook or group_vars
- Verify the vaultwarden Caddyfile contains `import /etc/caddy/conf.d/*.conf`
- Ensure the kaneo role is re-run after vaultwarden changes
- Test with: `docker exec vaultwarden-caddy-1 caddy validate -c /etc/caddy/Caddyfile`

## Testing

Run molecule tests:
```bash
molecule test -s kaneo
```

This will:
1. Create a test container
2. Run the converge playbook (install Docker, create `op` user, run kaneo role)
3. Run the verify playbook (health check, container running, idempotence check)
4. Destroy the test environment

## Kaneo Image and Version Pinning

**IMPORTANT:** The default `kaneo_image: "kaneo/kaneo:latest"` is a placeholder because the upstream Kaneo repository (`kaneo-app/kaneo` or equivalent) may not yet be publicly released with stable versioning.

**Action items for operator:**
1. Identify the canonical Kaneo GitHub repo (e.g., https://github.com/kaneo-app/kaneo)
2. Determine the latest stable release tag or commit hash
3. Override `kaneo_image` in your playbook or group_vars:
   ```yaml
   kaneo_image: "kaneo/kaneo:v0.2.0"  # or your preferred tag
   ```
4. If the image is under a different Docker Hub namespace or registry, update accordingly
5. Once pinned, document the reasoning and commit hash in `decisions.md` alongside this deployment

## Troubleshooting

### Kaneo container fails to start
- Check image availability: `docker pull kaneo/kaneo:latest`
- Check logs: `docker compose -f /opt/kaneo/docker-compose.yml logs kaneo`
- Verify port 3030 is not already in use: `docker ps | grep :3030`

### Projects not being created
- Verify API token is correct: check `infrastructure/secrets/secrets.sops.yml`
- Check Kaneo API logs: `docker compose -f /opt/kaneo/docker-compose.yml logs kaneo | grep -i "project\|api"`
- Manually test: `curl -H "Authorization: Bearer <token>" http://127.0.0.1:3030/api/projects`

### Caddy vhost not resolving
- Verify Caddy snippet was templated: `cat /opt/kaneo/Caddyfile.snippet`
- Reload Caddy manually: `docker exec vaultwarden-caddy-1 caddy reload -c /etc/caddy/Caddyfile`
- Check Caddy logs: `docker compose -f /opt/vaultwarden/docker-compose.yml logs caddy`

## References

- Kaneo repository: https://github.com/kaneo-app/kaneo (assumed; operator to verify)
- Ansible docker_compose_v2 module: https://docs.ansible.com/ansible/latest/collections/community/docker/docker_compose_v2_module.html
- Caddy reverse proxy: https://caddyserver.com/docs/quick-start

## License

This Ansible role is part of the Mnemonic Protocol coding-fabric and is provided under the same license as the parent project.
