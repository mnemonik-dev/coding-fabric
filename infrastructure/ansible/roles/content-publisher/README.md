# content-publisher (Ansible role)

Install scaffold for the Mnemonik content-publish-pipeline. Lays down the
filesystem, Python venv, env-files, and systemd unit that the Python
worker package (`fabric/content-publisher/`) plugs into during Task 7.

**Wave:** 1 (foundation) — after this role runs, the unit is `enabled`
but `inactive`. Wave 2 adds the worker package and flips it to `started`.

**Tech-spec:** `work/content-publish-pipeline/tech-spec.md`
(Task 3, Decisions 10–12, Risks).

---

## What it installs

| Artifact | Path | Owner / mode |
|----------|------|--------------|
| Blogger clone | `/opt/blogger/` | `op:op` |
| Claude-blog clone | `/opt/claude-blog/` | `op:op` |
| Blogger venv | `/opt/blogger/venv/` | `op:op` |
| Env-file | `/etc/blogger.env` | `root:root 0600` |
| MCP config | `/etc/blogger.mcp.json` | `root:op 0640` |
| systemd unit | `/etc/systemd/system/content-publisher.service` | `root:root 0644` |
| Worktree root | `/var/lib/content-publisher/work/` | `op:op 0750` |
| Persistent queue dir | `/mnt/HC_Volume_<id>/content-publisher/` | `op:op 0750` |
| Archive dir | `/mnt/HC_Volume_<id>/content-publisher/archive/` | `op:op 0750` |

---

## Required vars

These come from `playbooks/deploy.yml` via the sops snapshot. The role
deliberately has NO defaults for secrets — Jinja must fail with
`UndefinedError` if the operator forgot to set the sops key (Decision 12,
fail-loud supply-chain strategy).

| Var | Source | Default |
|-----|--------|---------|
| `blogger_repo_ref` | sops `blogger_repo_ref` | — (required, 40-char SHA) |
| `claude_blog_repo_ref` | sops `claude_blog_repo_ref` | — (required, 40-char SHA) |
| `blogger_telegram_bot_token` | sops `blogger_telegram_bot_token` | — (required) |
| `blogger_prompts_topic_id` | sops `blogger_prompts_topic_id` | — (required) |
| `telegram_forum_chat_id` | sops `telegram_forum_chat_id` | — (required) |
| `telegram_ai_agent_allowed_user_ids` | sops, first element used as `OPERATOR_USER_ID` | — (required) |
| `publish_callback_hmac_secrets` | sops `publish_callback_hmac_secrets` | — (required) |
| `publish_mode` | sops `publish_mode` | `"approval"` |
| `publish_auto_mode_token` | sops `publish_auto_mode_token` | `""` |
| `publish_min_score` | sops `publish_min_score` | `80` |
| `publish_approval_timeout_min` | sops `publish_approval_timeout_min` | `5` |
| `mnemonic_mcp_binary` | fact from `mnemonic-mcp` role (Task 2) | — (required) |

---

## Runbook: rotate HMAC callback secret

Callback inline-button data is signed with HMAC-SHA256 (Decision 9). The
service accepts TWO secrets at any time in `CALLBACK_HMAC_SECRETS=<new>,<old>`
to keep live previews working across rotation.

1. Generate a new secret:
   ```
   openssl rand -hex 32
   ```
2. Edit `infrastructure/secrets/secrets.sops.yml` (`sops` opens an editor).
   Prepend the new secret:
   ```
   publish_callback_hmac_secrets: "<new>,<current>"
   ```
3. Commit + deploy. Both secrets are now valid on the VM.
4. Wait at least `PUBLISH_APPROVAL_TIMEOUT_MIN` minutes (default 5) plus a
   safety margin — any preview message older than this can no longer have
   its button validly clicked, so the old secret is no longer needed.
5. Edit sops again, drop the old secret:
   ```
   publish_callback_hmac_secrets: "<new>"
   ```
6. Commit + deploy.

Rollback at any step is "edit sops + deploy" — the two-secret window
contains no destructive operations.

---

## Runbook: switch `PUBLISH_MODE` to `auto`

`PUBLISH_MODE=auto` skips the operator-approval step and publishes scored
articles directly. Decision 7 binds the mode change to TWO sops values so
a single env-flip cannot enable auto-publish silently — the worker
aborts on startup if the values disagree.

1. Generate a binding token:
   ```
   openssl rand -hex 32
   ```
2. `sops infrastructure/secrets/secrets.sops.yml` — set BOTH keys:
   ```
   publish_mode: "auto"
   publish_auto_mode_token: "<the-token>"
   ```
3. Commit + deploy. On startup the worker reads `PUBLISH_MODE` from
   `/etc/blogger.env` AND compares `PUBLISH_AUTO_MODE_TOKEN` against the
   sops-bound value passed through the role's env-file render. Mismatch
   → loud abort in journald, service stays inactive.
4. Verify on the VM:
   ```
   ssh op@<vm> 'systemctl is-active content-publisher.service'
   ssh op@<vm> 'sudo grep -E "^PUBLISH_(MODE|AUTO_MODE_TOKEN)=" /etc/blogger.env'
   ```

To revert: set both back to `approval` / `""` and redeploy. NEVER toggle
only `PUBLISH_MODE` without also setting (or clearing) the token — the
two-location binding is the audit gate.

---

## Runbook: rotate publisher bot token

The publisher bot (`@mnemonik_publisher_bot`) has a token separate from
the operator-facing bot (Decision 5). If the token leaks, only the
@mnemonik channel publishing path is at risk.

1. In Telegram, talk to `@BotFather`:
   - `/revoke` → select `@mnemonik_publisher_bot` → confirm.
   - `@BotFather` issues a fresh token immediately.
2. `sops infrastructure/secrets/secrets.sops.yml`:
   ```
   blogger_telegram_bot_token: "<new-token>"
   ```
3. Commit + deploy. The role re-renders `/etc/blogger.env` (mode `0600`
   `root:root`) and restarts `content-publisher.service`.
4. Verify the service can post:
   ```
   ssh op@<vm> 'journalctl -u content-publisher --since "2 minutes ago"'
   ```
   No `401 Unauthorized` from the Telegram Bot API in the last 30s of
   logs = success.
5. The old token is invalidated by `@BotFather` at step 1 — no separate
   "remove old token" step is needed.

> `/etc/blogger.env` has mode `0600 root:root` — only `root` and the
> service (via `EnvironmentFile=`) can read it. The publisher bot's
> token never appears in `ps`, in `/proc/<pid>/environ` for non-root,
> or in any per-user file.

---

## Runbook: bump pinned upstream SHAs

Both `blogger_repo_ref` and `claude_blog_repo_ref` are 40-char commit
SHAs. Tags and branches are rejected at preflight (`assert`).

1. Pick the new SHAs from upstream:
   ```
   git ls-remote https://github.com/mnemonik-dev/blogger.git HEAD
   git ls-remote https://github.com/mnemonik-dev/claude-blog.git HEAD
   ```
2. Update `defaults/main.yml`:
   ```yaml
   blogger_repo_ref: "<40-char SHA>"
   claude_blog_repo_ref: "<40-char SHA>"
   ```
   (or set both via sops if the operator prefers rotating without a code
   commit.)
3. Regenerate `requirements.locked.txt` (see next runbook).
4. Commit + deploy. The role re-clones at the new SHA and re-runs the
   import-surface assertion. If `mnemonik_blogger.agent.run_campaign_from_article`
   has drifted (signature change, missing kwarg) the role fails BEFORE
   touching the systemd unit.

---

## How to regenerate `requirements.locked.txt`

`requirements.locked.txt` is a **compile-time artifact** — generated by
the role author, NOT by `ansible-playbook`. Generating it from CI would
silently bind to newer transitives than what the operator reviewed,
defeating the supply-chain pin (Decision 12).

1. Clone blogger at the SHA you intend to deploy:
   ```
   git clone https://github.com/mnemonik-dev/blogger.git /tmp/blogger
   cd /tmp/blogger
   git checkout <blogger_repo_ref>
   ```
2. Materialise blogger's declared dependencies into a `requirements.in`:
   ```
   pip install pip-tools
   pip-compile --generate-hashes \
       --output-file=requirements.locked.txt \
       pyproject.toml
   ```
3. Inspect the diff against the previous lockfile. Every change should
   either be (a) a new direct dep blogger declared, or (b) a transitive
   bump pulled in by a direct upgrade. Anything else (new top-level
   package, sudden vendor swap) is a supply-chain signal — investigate
   before committing.
4. Copy the lockfile back into the role:
   ```
   cp requirements.locked.txt \
      <coding-fabric>/infrastructure/ansible/roles/content-publisher/requirements.locked.txt
   ```
5. Commit `defaults/main.yml` + `requirements.locked.txt` in the SAME
   commit. A drift between SHA and lockfile causes
   `pip install --require-hashes` to fail loud on the next deploy.

> Run pip-compile OFFLINE (in a clean venv on the operator machine) —
> never let the production CI runner generate the lockfile on the fly.

---

## Wave-1 acceptance check

After this role runs successfully:

```
ssh op@<vm> 'systemctl is-enabled content-publisher.service'   # → enabled
ssh op@<vm> 'systemctl is-active content-publisher.service'    # → inactive (expected)
ssh op@<vm> 'stat -c "%a %U:%G" /etc/blogger.env'              # → 600 root:root
ssh op@<vm> 'stat -c "%a %U:%G" /etc/blogger.mcp.json'         # → 640 root:op
ssh op@<vm> 'test -f /opt/blogger/venv/bin/python && echo OK'  # → OK
ssh op@<vm> 'grep -E "^RequiresMountsFor=" /etc/systemd/system/content-publisher.service'
# → RequiresMountsFor=/mnt/HC_Volume_<int>/content-publisher  (no literal *)
```

Task 7 (Wave 2) installs the Python entrypoint and flips the unit to
`active`.
