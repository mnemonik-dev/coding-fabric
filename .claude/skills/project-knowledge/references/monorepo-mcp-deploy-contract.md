# Contract: monorepo "Deploy MCP" workflow → fabric VM

The `mnemonik-xyz/monorepo` repo deploys the containerized `mnemonic-mcp`
stack (the public `mcp.mnemonik.xyz` API) **onto the coding-fabric Hetzner
VM** (`mnemonic-fabric`). Its workflow
(`monorepo/.github/workflows/deploy-mcp.yml` +
`monorepo/.github/actions/fabric-vm-ssh`) reuses the exact access path this
repo's CI uses, because the Hetzner firewall blocks public TCP/22:

1. Runner joins the tailnet as an ephemeral node (`tailscale/github-action@v3`
   with `TAILSCALE_AUTH_KEY` — the same reusable auth key secret as here).
2. Resolves the ONLINE peer with `HostName == mnemonic-fabric` from
   `tailscale status --json` (ghost-device-safe, same jq filter as
   `deploy-fabric.yml`).
3. Plain SSH over the tailnet as `op@<100.x>` with `CI_SSH_PRIVATE_KEY`
   (the same stable CI key whose pubkey cloud-init stamps into
   `op@VM:~/.ssh/authorized_keys`).

## What the monorepo deploy assumes about this VM (keep true)

- **Tailnet-only SSH invariant** stays: no public 22, Tailscale-SSH stays
  OFF (plain OpenSSH over the tailnet interface).
- **`op` user** exists, has NOPASSWD sudo, and is in the `docker` group
  (docker is installed by the vaultwarden role; the deploy falls back to
  `sudo -n docker` if the group is missing).
- **Monorepo checkout** at `/opt/mnemonik/monorepo` (overridable via the
  monorepo repo variable `MCP_VM_REPO_DIR`) with an operator-created
  `mcp.env` next to `docker-compose.yml`. The deploy bootstrap-clones the
  repo if missing but REFUSES to start without `mcp.env` (it holds secrets).
- **Caddy owns 80/443.** The monorepo deploy starts ONLY the `mcp` compose
  service (`127.0.0.1:3000`, plus profile-gated ollama) — never its
  nginx/certbot services. The `mcp.mnemonik.xyz` vhost that reverse-proxies
  Caddy → `127.0.0.1:3000` is currently **hand-managed on the VM** (not yet
  codified in an Ansible role here). TODO: codify it alongside the kaneo
  Caddyfile snippet pattern so a VM rebuild doesn't silently drop the MCP
  ingress. When codifying, note Caddy runs in a container — it needs a
  host-gateway route (or host networking) to reach `127.0.0.1:3000` on the
  host; check how the live snippet does it before templating.

## Secrets shared across repos

`TAILSCALE_AUTH_KEY` and `CI_SSH_PRIVATE_KEY` must hold the SAME values in
both repos' GitHub secrets. If either is rotated here, rotate it in
`mnemonik-xyz/monorepo` too, or its Deploy MCP workflow starts failing at
the tailnet-join / SSH step.
