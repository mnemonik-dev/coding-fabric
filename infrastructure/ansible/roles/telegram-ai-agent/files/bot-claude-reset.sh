#!/usr/bin/env bash
# bot-claude-reset — reset the telegram-ai-agent bot's Claude engine state.
#
# Use when the bot stops answering because the Claude subscription quota is
# exhausted, the OAuth token expired/was revoked, or stale engine sessions
# keep resuming into a broken state ("Thinking..." forever). Installed to
# /usr/local/bin by the telegram-ai-agent Ansible role.
#
# What it does (in order):
#   1. systemctl stop telegram-ai-agent
#   2. kills stray `claude` engine subprocesses and bot MCP servers
#   3. (--wipe-sessions) removes the bot's session-resume state so the next
#      message starts a FRESH Claude session instead of resuming a dead one
#   4. (--oauth-token / --api-key) rotates the credential in
#      /etc/telegram-ai-agent/.env — this is the "relogin": the bot's claude
#      subprocesses authenticate from these env vars, not from
#      `claude /login` browser credentials (service HOME is isolated)
#   5. systemctl start telegram-ai-agent + prints status
#
# Typical quota-day flows:
#   sudo bot-claude-reset                          # plain reset (stray procs, same creds)
#   sudo bot-claude-reset --wipe-sessions          # also drop session resume state
#   sudo bot-claude-reset --oauth-token sk-ant-oat01-...   # rotate subscription token
#   sudo bot-claude-reset --api-key sk-ant-api03-...       # switch to pay-per-token key
#
# NOTE: a token rotated here survives until the next Ansible deploy, which
# re-renders .env from sops. Copy the new token into
# infrastructure/secrets/secrets.sops.yml (claude_code_oauth_token /
# anthropic_api_key) to make it permanent.

set -euo pipefail

ENV_FILE="/etc/telegram-ai-agent/.env"
BOT_HOME="/opt/telegram-ai-agent"
SERVICE="telegram-ai-agent"

WIPE_SESSIONS=0
NEW_OAUTH_TOKEN=""
NEW_API_KEY=""

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,30p'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wipe-sessions) WIPE_SESSIONS=1; shift ;;
    --oauth-token)   NEW_OAUTH_TOKEN="${2:?--oauth-token requires a value}"; shift 2 ;;
    --api-key)       NEW_API_KEY="${2:?--api-key requires a value}"; shift 2 ;;
    -h|--help)       usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (sudo bot-claude-reset ...)" >&2
  exit 1
fi

# Replace KEY=... in the env file, or append if the key is absent.
# Preserves 0600 by editing in place.
set_env_var() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
  chmod 0600 "$ENV_FILE"
  echo "updated ${key} in ${ENV_FILE}"
}

echo "==> stopping ${SERVICE}"
systemctl stop "$SERVICE"

echo "==> killing stray engine processes"
pkill -9 -f "claude --output-format stream-json" 2>/dev/null || true
pkill -9 -f "mcp-servers/bot" 2>/dev/null || true

if [[ $WIPE_SESSIONS -eq 1 ]]; then
  echo "==> wiping session-resume state"
  for f in channel_sessions.json session_mapping.json; do
    if [[ -f "${BOT_HOME}/${f}" ]]; then
      : > "${BOT_HOME}/${f}"
      echo "cleared ${BOT_HOME}/${f}"
    fi
  done
fi

if [[ -n "$NEW_OAUTH_TOKEN" ]]; then
  set_env_var "CLAUDE_CODE_OAUTH_TOKEN" "$NEW_OAUTH_TOKEN"
fi
if [[ -n "$NEW_API_KEY" ]]; then
  set_env_var "ANTHROPIC_API_KEY" "$NEW_API_KEY"
fi

echo "==> starting ${SERVICE}"
systemctl start "$SERVICE"
sleep 2
systemctl --no-pager --lines=5 status "$SERVICE" || true

echo
echo "Done. Send a message to the bot to verify. If auth still fails, check:"
echo "  journalctl -u ${SERVICE} -n 50 --no-pager"
echo "Remember: tokens rotated here are overwritten by the next Ansible deploy —"
echo "persist them in infrastructure/secrets/secrets.sops.yml."
