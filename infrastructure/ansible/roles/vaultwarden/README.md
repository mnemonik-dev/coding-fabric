# Ansible Role: vaultwarden

Deploy Vaultwarden (open-source password manager) via docker-compose with Caddy reverse proxy, bound to tailnet IP only.

## Overview

This role installs Docker, deploys Vaultwarden and Caddy in a docker-compose stack, configures TLS via Caddy's internal CA (for tailnet domains), and provides health checks. All network bindings are restricted to the Tailscale IP — never to 0.0.0.0.

## Requirements

- Base role: OS hardening, base directories (Task 02)
- Tailscale role: Tailnet membership, `tailscale_ip` fact (Task 02)
- Ansible collections: `community.docker` (auto-installed from galaxy)
- Docker daemon running on target host
- Secrets decryption: sops-encrypted `secrets.sops.yml` decrypted by playbook (Task 24)

## Role Variables

All variables are prefixed with `vaultwarden_`.

### Defaults (in defaults/main.yml)

| Variable | Default | Purpose |
|----------|---------|---------|
| `vaultwarden_image` | `vaultwarden/server:1.32.0` | Vaultwarden container image (pinned to exact version) |
| `vaultwarden_caddy_image` | `caddy:2.9.1` | Caddy reverse proxy image |
| `vaultwarden_tailnet_domain` | `vault.mnemonic-fabric.ts` | Tailnet domain (internal-CA TLS) |
| `vaultwarden_data_dir` | `/opt/vaultwarden/data` | Data volume mount point (mode 0700) |
| `vaultwarden_https_port` | `8443` | HTTPS port (bound to tailnet IP only) |

### Required Secrets (from sops, passed by deploy.yml)

These variables **MUST** be provided by the playbook via sops decryption. They are **NOT** defined in defaults/main.yml to prevent accidental defaults:

| Variable | Source | Purpose |
|----------|--------|---------|
| `vaultwarden_initial_admin_password` | `infrastructure/secrets/secrets.sops.yml` → `VAULTWARDEN_INITIAL_ADMIN_PASSWORD` | Seeds admin password on first install only (uses `creates:` guard) |
| `vaultwarden_admin_token` | `infrastructure/secrets/secrets.sops.yml` → `VAULTWARDEN_ADMIN_TOKEN` | Admin API token (stored in .env, mode 0600) |

### Required Facts

- `tailscale_ip`: Set by tailscale role; used to bind Caddy port
- `ansible_os_family`: Ubuntu (tested on 20.04, 22.04, 24.04)

## Sops Integration

The role **does not decrypt sops itself**. The playbook (Task 24 `deploy.yml`) is responsible for:

1. Decrypting `infrastructure/secrets/secrets.sops.yml` with the age private key
2. Passing decrypted variables to this role via `vars:`

Example (in deploy.yml):

```yaml
- name: Decrypt secrets
  ansible.builtin.include_vars:
    file: infrastructure/secrets/secrets.sops.yml
    name: sops_vars

- name: Deploy vaultwarden role
  ansible.builtin.include_role:
    name: vaultwarden
  vars:
    vaultwarden_initial_admin_password: "{{ sops_vars.VAULTWARDEN_INITIAL_ADMIN_PASSWORD }}"
    vaultwarden_admin_token: "{{ sops_vars.VAULTWARDEN_ADMIN_TOKEN }}"
```

## Role Tasks

1. **Install Docker**: Fetch from get.docker.com (idempotent via docker --version check) or community.docker apt method
2. **Add op user to docker group**: Allows op user to manage containers
3. **Create directories**: /opt/vaultwarden (0700) and /opt/vaultwarden/data (0700), owner op:op
4. **Render docker-compose.yml**: Template with exact image versions, port bindings to tailnet IP only
5. **Render Caddyfile**: TLS via internal CA, reverse proxy to Vaultwarden
6. **Generate .env file**: admin token and initial password from sops
7. **Docker compose up**: Start Vaultwarden and Caddy containers
8. **Health check**: Wait up to 60s for /api/health to return 200; fail loudly if timeout
9. **Verify containers**: Assert both vaultwarden and caddy containers are running

## Idempotency

- **docker_compose_v2 with state: present**: Idempotent; only recreates containers if config changes
- **Admin password seed**: Uses `creates:` guard (a shell script marker) so initial password only sets on first install
- **Caddyfile changes**: Trigger `reload caddy` handler (no full vaultwarden restart needed)
- **Molecule verify**: Second run includes idempotence check; zero changes expected

## Security Notes

- **Network binding**: Caddy port ALWAYS bound to `{{ tailscale_ip }}:8443`, never to 0.0.0.0
- **Image pinning**: Vaultwarden and Caddy versions pinned to exact tags (e.g. 1.32.0, 2.9.1), never `:latest`
- **Secrets**: Admin token and initial password sourced from sops only (never hardcoded); .env file mode 0600
- **Data volume**: Mode 0700, owner op:op (no world-readable secrets at rest)
- **TLS**: Caddy internal-CA mode (not Let's Encrypt) — tailnet domains are not publicly resolvable
- **Handlers**: Caddy reload does not restart Vaultwarden; Caddyfile config errors don't mask application state

## Handlers

| Handler | Trigger | Action |
|---------|---------|--------|
| `Restart vaultwarden` | docker-compose.yml or .env change | Restart vaultwarden service via docker-compose |
| `Reload caddy` | Caddyfile change | Caddy reload (zero-downtime; preserves connections) |

## Testing

Run Molecule tests to verify idempotency and health checks:

```bash
molecule test -s vaultwarden
```

Verify role lint compliance:

```bash
ansible-lint infrastructure/ansible/roles/vaultwarden
```

## Acceptance Criteria (from Task 03)

- [x] Role is idempotent (second run: 0 changed)
- [x] Vaultwarden binds to tailnet IP only (never 0.0.0.0)
- [x] Caddy reverse-proxy with internal-CA TLS (tailnet domain)
- [x] Admin password seeded from sops (first install only, `creates:` guard)
- [x] Health check waits up to 60s, fails loudly on timeout
- [x] ansible-lint passes (lint-clean code)

## Troubleshooting

### Vaultwarden fails to start
Check docker-compose logs:
```bash
docker compose -f /opt/vaultwarden/docker-compose.yml logs vaultwarden
```

### Health check timeout
Ensure Vaultwarden container is running and /api/health endpoint is responding:
```bash
docker exec vaultwarden-vaultwarden-1 curl http://localhost:80/api/health
```

### Port already in use
Verify port 8443 is not bound on tailnet IP:
```bash
ss -tlnp | grep 8443
```

### Admin token not working
Verify .env file contains ADMIN_TOKEN:
```bash
grep ADMIN_TOKEN /opt/vaultwarden/.env
```

## Dependencies

- **base role** (Task 02): OS hardening, ufw rules, op user creation
- **tailscale role** (Task 02): tailscale_ip fact, tailnet membership

## Backward Compatibility

Role is idempotent and safe to re-apply. Existing Vaultwarden data persists in `/opt/vaultwarden/data` across role runs.

## Author

ansible-vaultwarden (Mnemonic Protocol coding-fabric Task 03)
