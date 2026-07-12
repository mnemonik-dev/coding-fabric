# Role: mnemonik-server

Deploys the **server side** of the Mnemonic protocol, co-located on the fabric
VM: the Rust MCP HTTP server (GHCR image `ghcr.io/mnemonik-xyz/mnemonic-mcp`)
plus a local Ollama for `/chat`. TLS + public ingress are handled by the
**shared Caddy** (vaultwarden role), not the monorepo's own nginx/certbot.

> **Not** the `mnemonic-mcp` role. That one installs the *client* binary
> (`@mnemonik-xyz/mcp`, per-spawn stdio) that content-publisher spawns. This
> role hosts the *server* those clients connect to. Two sides of one protocol.

## What it does

1. **Swap** — idempotent `{{ mnemonik_server_swap_size_mb }}`MB swapfile
   (ollama + fastembed headroom). Toggle with `mnemonik_server_swap_enabled`.
2. **State on the persistent volume** — creates `<volume>/mnemonik/{data,keypair,ollama}`
   and bind-mounts them into the containers so server identity (`id.json` in
   `/keypair`), the attestation DB (`/data`), and the pulled model survive a
   VM rebuild.
3. **GHCR pull** — optional `docker login` (private package), then
   `docker compose pull` — the box never compiles Rust.
4. **Ollama** — stock `ollama/ollama` image + a post-up `ollama pull` of
   `{{ mnemonik_ollama_model }}` (replicates the monorepo's custom image
   without needing its build context) + a non-fatal warm-up.
5. **Caddy vhost** — drops `mnemonik-mcp.conf` into the shared conf.d and
   restarts Caddy; `{{ mnemonik_public_domain }}` gets a real Let's Encrypt
   cert on first request.
6. **Health gate** — polls `https://{{ mnemonik_public_domain }}/health` the
   same way an external client would.

## Boundary with the monorepo pipeline

- **Build stays upstream.** `mnemonik-xyz/monorepo` → `build-mcp-image.yml`
  builds + pushes the image to GHCR. Untouched by this role.
- **Deploy is fabric's.** This role does what `deploy-mcp.yml`'s deploy half
  does (pull + compose up + health gate), inside fabric's existing deploy that
  already has authenticated access to this box — instead of maintaining the
  monorepo's separate SSH path into a box whose port 22 is firewalled.
- **Webapp is elsewhere.** The frontend lives on Cloudflare Pages; only the
  MCP host is served here.

## Enabling (first deploy)

1. **DNS** — A record `mcp.mnemonik.xyz` → VM public IPv4.
2. **sops** — `sops edit infrastructure/secrets/secrets.sops.yml`, set
   `mnemonik_mcp_jwt_secret` + `mnemonik_mcp_refresh_salt`
   (`openssl rand -base64 32` each, keep stable). Also confirm
   `caddy_acme_email` is set so Let's Encrypt can issue.
3. **GHCR** — make the package public, or rely on the sops github PAT wired in
   `deploy.yml` (`mnemonik_ghcr_token`).
4. **Enable** — set `mnemonik_server_enabled: true` (group_vars or `-e`) and
   deploy.

## Key variables

| Variable | Default | Notes |
|----------|---------|-------|
| `mnemonik_server_enabled` | `false` | Master switch. |
| `mnemonik_mcp_image_tag` | `latest` | Pin a real tag for reproducible redeploys. |
| `mnemonik_ollama_model` | `qwen2.5:3b` | Pulled once, cached on the volume. |
| `mnemonik_public_domain` | `mcp.mnemonik.xyz` | Caddy public vhost. |
| `mnemonik_server_swap_size_mb` | `4096` | Swap cushion. |

`mnemonik_mcp_jwt_secret` / `mnemonik_mcp_refresh_salt` have no default — the
role fails loudly if enabled without them.

## Cross-role contract

Depends on the **vaultwarden** role for the shared Caddy container
(`{{ vaultwarden_caddy_container_name }}`) and its external docker network
(`{{ mnemonik_shared_caddy_network }}`). The mcp container joins that network
with alias `mnemonik-mcp`; Caddy reverse-proxies to it by name. Must run after
vaultwarden + kaneo in the `deploy.yml` roles list.

## Smoke (manual, post-deploy)

```bash
curl -fsS https://mcp.mnemonik.xyz/health
ssh <vm> 'docker compose -f /opt/mnemonik-server/docker-compose.yml ps'
ssh <vm> 'docker exec mnemonik-ollama-1 ollama list'   # model present
```
