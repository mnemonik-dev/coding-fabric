# Role: mnemonic-mcp

Installs the **Mnemonik MCP server** from the upstream-supported npm
distribution channel (`@mnemonik-xyz/mcp`), discovers the actual binary
name shipped by the pinned version, and runs the server under systemd
as a `mcp-stdio` daemon. The discovered binary name is exposed as the
Ansible fact `mnemonic_mcp_binary` and consumed downstream by the
`content-publisher` role (`/etc/blogger.mcp.json` template) and by the
content-publisher worker for attestation calls.

Anchored to `work/content-publish-pipeline/tech-spec.md` Decision 3 +
Architecture step 4.

## Install path

1. **Node.js prerequisite**: NodeSource setup_20.x → `apt install nodejs`
   (pattern shared with the `telegram-ai-agent` role).
2. **Local lockfile install** (supply-chain pin for the full tree,
   per security R2-8): `npm ci` consumes the checked-in
   `files/package-lock.json` in `{{ mnemonik_mcp_install_dir }}` (default
   `/opt/mnemonik-mcp/`). Every transitive integrity hash is fixed.
3. **Global install for PATH**: `community.general.npm` installs
   `@mnemonik-xyz/mcp@{{ mnemonik_mcp_npm_version }}` globally, which
   drops the bin at the npm global prefix (`/usr/local/bin/` on the
   target VM).
4. **Binary discovery**: `which mnemonik-mcp || which mnemonic-mcp`.
   Upstream has shipped the bin under both spellings (current
   `@mnemonik-xyz/mcp@0.2.4` ships `mnemonik-mcp` with K; older builds
   shipped `mnemonic-mcp` without K). The cascade resolves whichever
   spelling the pinned version actually shipped; the result lands in the
   `mnemonic_mcp_binary` fact.
5. **systemd unit**: `templates/mnemonic-mcp.service.j2` renders
   `ExecStart={{ mnemonic_mcp_binary }} mcp-stdio` and inherits the
   hardening block (`NoNewPrivileges`, `ProtectSystem=strict`,
   `ProtectHome`, etc.).
6. **Post-install smoke**: spawn `<mnemonic_mcp_binary> mcp-stdio`, feed
   a JSON-RPC `initialize` envelope on stdin, assert `protocolVersion`
   in the response. Replaces the bogus `--selftest` probe; this is the
   real surface (see tech-spec Decision 3).

## What it does *not* install

- No Vaultwarden signing-key materialisation. The previous flow pulled
  a key out of `bw`, mode-0600'd it under `/run/credentials/`, then
  systemd `LoadCredential=`'d it. The new npm distribution channel
  doesn't use the same on-disk signing-key contract; signing flows
  through MCP JSON-RPC tools, not a bound-at-startup credential. The
  `LoadCredential=` directive is gone from the unit.
- No `--selftest` probe and no one-shot signing CLI subcommand.
  Neither exists on the npm-shipped binary; both are bygone artefacts of
  the descoped binary-download era. The npm-shipped binary only exposes
  `install`, `mcp-stdio`, `doctor`; signing flows are reached via
  JSON-RPC `tools/call`, not a one-shot CLI verb. The role asserts via
  real MCP-stdio handshake instead.
- No molyanov hook scripts, no `config.yml`, no `protocol-qa.env`. The
  old role was attestation-DAG-hooks-shaped; this role is
  long-running-stdio-server-shaped.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `mnemonic_mcp_enabled` | `true` | Master switch (re-enabled after descope cleanup). |
| `mnemonik_mcp_npm_package` | `@mnemonik-xyz/mcp` | Scoped package name. |
| `mnemonik_mcp_npm_version` | `0.2.4` | Pinned root version. Bumping requires re-generating `files/package-lock.json`. |
| `mnemonik_mcp_install_dir` | `/opt/mnemonik-mcp` | Where `package.json` + lockfile live; not the binary path. |
| `mnemonic_mcp_systemd_unit` | `mnemonic-mcp.service` | systemd unit filename. |
| `mnemonic_mcp_service_user` | `op` | User runs the daemon. |
| `mnemonic_mcp_service_group` | `op` | Group. |

## Facts exposed to downstream roles

- `mnemonic_mcp_binary` (str): absolute path to the resolved binary
  (e.g. `/usr/local/bin/mnemonik-mcp`). Set via `set_fact` without
  `cacheable:`; lives for the rest of the same play on the same host —
  no `hostvars[…]` lookup needed in downstream Jinja templates.

## Runbooks

### A. Bump `mnemonik-mcp` to a new version

1. **Off-host: regenerate the lockfile.** In a clean throwaway dir on
   your dev machine (so the lockfile inherits no host noise):
   ```bash
   mkdir /tmp/mnemonik-mcp-bump && cd /tmp/mnemonik-mcp-bump
   npm init -y >/dev/null
   npm install @mnemonik-xyz/mcp@<NEW_VERSION> \
       --package-lock-only --no-audit --no-fund
   ```
2. **Strict-pin the root dep** in the new lockfile (replace `^X.Y.Z`
   with `X.Y.Z` in `packages[""].dependencies`).
3. **Copy** the new `package-lock.json` over
   `infrastructure/ansible/roles/mnemonic-mcp/files/package-lock.json`.
4. **Edit defaults**: bump `mnemonik_mcp_npm_version` in
   `defaults/main.yml`.
5. **Commit + deploy.**
6. **Post-deploy sanity** (no edit needed even if upstream renamed the
   binary; the discovery cascade handles `mnemonik-mcp` and
   `mnemonic-mcp`):
   ```bash
   ssh root@<vm> journalctl -u mnemonic-mcp --since '5 minutes ago'
   ssh root@<vm> "$mnemonic_mcp_binary doctor"
   ```
7. **If upstream introduces a THIRD binary spelling** (e.g.
   `mnemonik-mcp-server`): extend the cascade in
   `tasks/main.yml` → "Discover installed Mnemonik MCP binary name".
   Alternative future-proofing: parse
   `npm list -g @mnemonik-xyz/mcp --json` for the `bin` field.

### B. Rotate the publisher-bot Telegram token

Operator-side procedure; the content-publisher role consumes the token
from sops. This runbook is reproduced here so it's discoverable from
the role most directly responsible for the publishing path's runtime
(content-publisher's README links back here).

1. In `@BotFather` (Telegram): `/mybots` → `@mnemonik_publisher_bot`
   → `API Token` → `Revoke current token`.
2. `@BotFather` returns a fresh token. Copy it.
3. On your dev machine:
   ```bash
   sops edit infrastructure/secrets/secrets.sops.yml
   # bump the value of `blogger_telegram_bot_token`
   ```
4. Commit + deploy.
5. `@BotFather` again: delete any orphan tokens still listed.

A rotation does **not** affect `mnemonic_mcp_binary` or the MCP server —
those are independent.

## Smoke (manual, post-deploy)

```bash
ssh root@<vm> systemctl is-active mnemonic-mcp           # active
ssh root@<vm> "$mnemonic_mcp_binary --help"              # lists mcp-stdio
ssh root@<vm> 'printf "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"manual\",\"version\":\"0.1\"}}}\n" | "$mnemonic_mcp_binary" mcp-stdio | head -1 | python3 -c "import json,sys; r=json.loads(sys.stdin.read()); assert \"protocolVersion\" in r.get(\"result\",{}), r"'
```

Lockfile drift check:

```bash
sha256sum /opt/mnemonik-mcp/package-lock.json
# Compare to local files/package-lock.json sha256.
```

## Testing

Unit-style contract tests (no live VM, no docker):

```bash
pytest infrastructure/ansible/roles/mnemonic-mcp/tests/ -v
```

These assert: npm package + version pinned; binary discovery cascade
present with `failed_when rc != 0`; systemd unit uses the fact;
`files/package-lock.json` is valid JSON with `lockfileVersion >= 2` and
contains `@mnemonik-xyz/mcp`; descoped artefacts removed; no
the descoped one-shot signing subcommand string anywhere; `ansible-lint` + playbook
`--syntax-check` exit 0.

Molecule (local-only, not wired into CI as of 2026-06):

```bash
cd infrastructure/ansible/roles/mnemonic-mcp
molecule test -s default
```

**TODO** (follow-up, non-blocking): wire molecule into the
`deploy-fabric.yml` workflow's pre-deploy stage. Tracked under the
content-publish-pipeline feature dir.

## Files

- `tasks/main.yml` — install, discover binary, render unit, smoke.
- `defaults/main.yml` — variable defaults.
- `handlers/main.yml` — `reload mnemonic-mcp systemd`, `restart
  mnemonic-mcp service`.
- `templates/mnemonic-mcp.service.j2` — systemd unit.
- `files/package-lock.json` — supply-chain pin for the npm transitive
  tree (consumed by `npm ci`).
- `tests/test_role_contract.py` — pytest contract tests.
- `molecule/default/` — local-only docker scenario.
