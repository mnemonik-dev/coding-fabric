# Handoff — coding-fabric Telegram bot debugging (2026-05-23)

Long debug session ran out of context cleanly. This is the resume note.

## Environment

- **Project**: `/Users/syi/src/sessions/coding-fabric`
- **Branch**: `claude/review-coding-fabric-spec-uUvMK`
- **VM**: Hetzner CCX33
  - Public IP: `178.105.207.172`
  - Tailnet IP: `100.109.210.5`
  - SSH user: `op` (NOPASSWD sudo via cloud-init)
  - SSH access: public port 22 temporarily opened to `194.87.227.58/32` for operator debug (rule added in `infrastructure/tofu/hetzner/main.tf` — remove after debug)

## What works (green)

- **Deploy pipeline GREEN** on run `26289095629`. All 10 Ansible roles applied. tofu-state persisted via artifact (filter `size_in_bytes > 800` excludes empty placeholder states uploaded by failed Applies).
- **VM lifecycle**: every CI run destroys + recreates `hcloud_server.fabric` so cloud-init re-injects this run's freshly minted ephemeral SSH pubkey. `hcloud_volume.fabric_data` survives — Vaultwarden/restic data preserved.
- **CI SSH key plumbing**: Plan job generates ephemeral keypair, encrypts privkey under age (SOPS recipient), exports pubkey via `gen_ci_key` step's `$GITHUB_OUTPUT` so Apply (separate runner) consumes via `needs.tofu-plan.outputs.ci_ssh_pubkey`.
- **Bot service** `telegram-ai-agent.service` is `active (running)`, polling Telegram as `@mnemonik_fabric_bot` (id 8903720516).
- **Allowlist works** — operator user `206475911` added to `ALLOWED_USER_IDS=[206475911]` in `/etc/telegram-ai-agent/.env` (live edit). Bot logs `Starting bot, allowed users: 1`.
- **`.env` token format fixed live + committed** — `{%- if %}` with Ansible's `trim_blocks: True` was eating the newline before `CLAUDE_CODE_OAUTH_TOKEN=...`, producing `# scope ...quota burn.CLAUDE_CODE_OAUTH_TOKEN=sk-ant-...` (one line, parsed as comment). Fixed in `roles/telegram-ai-agent/templates/.env.j2` by removing the `-` from `{%- if` / `{%- endif`. Live `.env` on VM was hand-edited to split lines.
- **claude CLI** installed via `sudo npm install -g @anthropic-ai/claude-code`. Confirmed working standalone — `claude -p "say hi"` returns `Hi!` instantly.
- **Symlink `.env`**: `/opt/telegram-ai-agent/.env -> /etc/telegram-ai-agent/.env` (live + committed in `roles/telegram-ai-agent/tasks/main.yml`). Needed because `mcp-servers/bot/start.sh` reads `$PROJECT_DIR/.env` from `/opt/telegram-ai-agent/.env`.
- **`.venv` is healthy**: `pyvenv.cfg` present, `sys.prefix = /opt/telegram-ai-agent/.venv`, `mcp` package importable (`/opt/telegram-ai-agent/.venv/lib/python3.12/site-packages/mcp/__init__.py`). `.venv/bin/python -> /usr/bin/python3` is normal for uv-managed venvs.

## Open bug — exact symptom

- Every Telegram message → bot replies `Added to queue (#1)` immediately, then "Thinking..." forever, no Claude answer.
- Bot logs show `Running agent stream: provider=claude resume=False, mode=free, cwd=., session_id=None` then nothing.
- Multiple zombie `claude --output-format stream-json ... --strict-mcp-config -p ...` processes accumulate over time, all idle (low CPU, just sitting).
- Manual test (`scripts/test-mcp.sh`-style with same MCP config) shows claude reports `"mcp_servers":[{"name":"bot","status":"failed"}]` in its init JSON. The bot's MCP server (`mcp-servers/bot/server.py`) fails to start.
- But claude itself works — returns text in <2s.
- strace on zombie shows single `read(8, ..., 8) = 8` on the MCP pipe — claude waiting on MCP that doesn't respond.

## Next step (run on VM as `op`)

Need server.py traceback. Save this script to a file and run it:

```bash
cat > ~/test-server.sh <<'EOF'
#!/bin/bash
export PROJECT_DIR=/opt/telegram-ai-agent
export TELEGRAM_CHAT_ID=-1003450829353
export TELEGRAM_THREAD_ID=463
export TELEGRAM_CONTEXT_LOCK=1
export BOT_TOKEN=test
/opt/telegram-ai-agent/.venv/bin/python /opt/telegram-ai-agent/mcp-servers/bot/server.py < /dev/null 2>&1 | head -30
EOF
chmod +x ~/test-server.sh
~/test-server.sh
```

EOF on stdin makes the MCP server attempt JSON-RPC read, fail, and exit with Python traceback. That traceback names the actual import/runtime error.

Also useful: `bash -x` the launcher to see env values at exec time:
```bash
PROJECT_DIR=/opt/telegram-ai-agent TELEGRAM_CHAT_ID=-1003450829353 \
  TELEGRAM_THREAD_ID=463 TELEGRAM_CONTEXT_LOCK=1 \
  bash -x /opt/telegram-ai-agent/mcp-servers/bot/start.sh < /dev/null 2>&1 | head -40
```

## Once the bug is identified

1. Fix the actual cause (likely env-export quoting in start.sh, or missing system dep, or python deps mismatch — unknowable until traceback shown).
2. Clean restart:
   ```bash
   sudo systemctl stop telegram-ai-agent
   sudo pkill -9 -f "claude --output-format stream-json"
   sudo pkill -9 -f "mcp-servers/bot"
   sudo systemctl start telegram-ai-agent
   ```
3. Test from Telegram. Expect actual claude answer (not "Added to queue").

## Backlog (do not touch until bot proven working)

| Item | Action | Why |
|------|--------|-----|
| Close public SSH 22 | Remove TEMP rule from `infrastructure/tofu/hetzner/main.tf` (the `194.87.227.58/32` block under the firewall) and redeploy | Tailnet-only is the invariant — public SSH was operator debug shortcut |
| Rotate `restic_password` | Generate new password, encrypt into `secrets.sops.yml`, redeploy | Was briefly in public CI logs during early iterations |
| Nuke ~200 duplicate Telegram topics | Run `scripts/nuke-telegram-topics.py --max-thread-id 1000` (bot token + forum chat id from sops), then re-run `telegram-init` Ansible role to recreate the 8 canonical topics | CI loop created duplicates during 50+ deploys; `telegram-init` is now idempotent via VM-local `/etc/fabric/telegram-topics.yml` state file |
| Persistent allowlist | Add `telegram_allowed_user_ids: [206475911]` to `infrastructure/ansible/inventory/group_vars/all/secrets.sops.yml`. Plumbing already in `deploy.yml` (`vault_telegram_allowed_user_ids` set_fact) and `.env.j2` (`ALLOWED_USER_IDS=...`) | So redeploy doesn't lose the allowlist again |
| E2E smoke green | Set up Tailscale SSH ACL with `tag:fabric` allowing operator's runner OR rewrite smoke to use the ephemeral CI SSH key | Currently `continue-on-error: true` to not block deploy on this; structural gap |
| Tofu firewall rule cleanup | Delete commented "TEMP debug SSH" rule in `main.tf` after close | Hygiene |

## Key code paths to know

- Bot source: `mnemonik-bridge-workspace/telegram-ai-agent/src/telegram_bot/` (forked from pavel-molyanov, pinned tag `v0.1.0+mnemonik.1`).
- MCP server: `mnemonik-bridge-workspace/telegram-ai-agent/mcp-servers/bot/server.py`, launcher `start.sh` next to it.
- Ansible role for bot: `infrastructure/ansible/roles/telegram-ai-agent/`. Defaults set every topic `exec_mode: "subprocess"` (no tmux); switch per-topic to `tmux` for persistent `/tui` access.
- Topic config rendered into `/etc/telegram-ai-agent/config.yml` from `templates/config.yml.j2` using `telegram_ai_agent_topics_with_ids` (computed by merging `defaults/main.yml` topics with `inventory/telegram-topics.yml` IDs).
- Systemd unit: `templates/telegram-ai-agent.service.j2`. **`ProtectHome=true`** — service cannot read/write `/home/op/.claude/`. Bot relies on `CLAUDE_CODE_OAUTH_TOKEN` env var from `.env`, not on `claude /login`-saved credentials at `~/.claude/.credentials.json`.

## Recent commits (this debug session)

- `322fefa` — Symlink `.env` into project root for MCP server
- `4d6afa2` — Fix .env.j2 newline-eat that commented out OAuth/API keys
- `f188919` — Plumb telegram_allowed_user_ids through sops -> .env
- `acfc50b` — Forward CI SSH pubkey from Plan to Apply via job output
- `1450a0d` — Move destroy-server step from Plan to Apply job
- `8108f19` — Explicit destroy server before plan (8-core quota workaround)
- `8a3ce8e` — Pick most recent NON-EMPTY tofu-state artifact
- `82ba80c` — Restore prior tofu-state via gh CLI (not dawidd6 action)
- `2c25b57` — Make e2e-smoke best-effort + ghost-resolve in tailnet validate

All on branch `claude/review-coding-fabric-spec-uUvMK`. Not yet pushed to main.
