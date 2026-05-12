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
| `kaneo_image` | `kaneo/kaneo:latest` | Docker image tag. Operator should override to pin a release (e.g., `kaneo/kaneo:v0.2.0`). See note below. |
| `kaneo_tailnet_domain` | `kaneo.mnemonic-fabric.ts` | Caddy vhost domain (tailnet only). |
| `kaneo_http_port` | `3030` | Internal HTTP port. Caddy reverse-proxies TLS on this. |
| `kaneo_database_type` | `sqlite` | Database backend; currently SQLite only. |
| `kaneo_database_path` | `/opt/kaneo/data/kaneo.db` | SQLite database file location. |
| `kaneo_default_projects` | List of 6 dicts | Projects to seed (name + description). |
| `kaneo_health_endpoint` | `/api/health` | Health check path. |
| `kaneo_projects_endpoint` | `/api/projects` | Projects API path. |
| `kaneo_project_create_delay` | `0.5` | Seconds between project creates (rate-limit safety). |

### Encrypted Variables (from sops)

The following variable **must** be passed by the playbook via sops decryption. It is **not** defined in defaults and will cause the role to fail clearly if missing:

| Variable | Source | Purpose |
|----------|--------|---------|
| `kaneo_api_token` | `infrastructure/secrets/secrets.sops.yml` | Admin API token for project creation. Treated as a secret (`no_log: true` on all HTTP calls). |

Example sops structure:
```yaml
---
kaneo:
  api_token: "sk-kaneo-admin-token-..."
```

Playbook invocation should decrypt and pass:
```yaml
- name: Deploy Kaneo
  hosts: fabric
  vars:
    kaneo_api_token: "{{ sops_kaneo_api_token }}"
  roles:
    - kaneo
```

## Task Flow

1. **Create directories** (`/opt/kaneo`, `/opt/kaneo/data`) with appropriate ownership
2. **Template docker-compose.yml** — Kaneo service with SQLite, health check, network isolation
3. **Template Caddyfile snippet** — `kaneo.mnemonic-fabric.ts` reverse-proxy (Caddy reload handler)
4. **Compose up** — Pull latest image, start Kaneo container
5. **Wait for health** — Retry `GET /api/health` up to 60s; fail clearly if timeout
6. **GET existing projects** — Fetch list of already-created projects (idempotency gate)
7. **POST missing projects** — For each of six defaults not in the list, POST to `/api/projects`; include 0.5s delay between requests
8. **Verify final count** — GET projects again, assert exactly 6 exist
9. **Verify container running** — Docker ps check

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

Kaneo must respond to `GET /api/health` with a 200 status code. The role retries up to 60 times with 1-second delays.

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
- **Reload caddy for kaneo vhost:** Triggers Caddy config reload in the vaultwarden-caddy-1 container (does not restart Kaneo)

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
