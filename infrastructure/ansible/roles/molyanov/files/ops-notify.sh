#!/bin/bash
set -euo pipefail

# Ops-Notify Wrapper: Post messages to ops topic via Telegram bot
# Uses TELEGRAM_BOT_TOKEN from sops/Vaultwarden
# Message format: severity level, timestamp, content

SEVERITY="${1:-info}"
MESSAGE="${2:-}"
TIMESTAMP=$(date -u +'%Y-%m-%dT%H:%M:%SZ')

if [[ -z "$MESSAGE" ]]; then
  echo "Usage: $0 <severity> <message>" >&2
  exit 1
fi

# Resolve bot token from sops or environment
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TOPIC_ID="${TELEGRAM_OPS_TOPIC:-}"

if [[ -z "$BOT_TOKEN" || -z "$TOPIC_ID" ]]; then
  logger -t molyanov-ops-notify -p user.warning \
    "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_OPS_TOPIC; cannot send alert"
  exit 1
fi

# Format and send message.
# NOTE: parse_mode=HTML was removed because $MESSAGE is user-controlled
# content (callers pass arbitrary strings, including paths that may contain
# `<` or `&`). Plain text avoids both Telegram parse errors and HTML
# injection. Use --data-urlencode so special characters in the message body
# don't break the curl POST encoding.
FORMATTED_MSG="[${TIMESTAMP}] ${SEVERITY^^}: ${MESSAGE}"

# Build the request URL inside the script so the literal token never appears
# in this script's argv (audit Finding F-004 in T21).  The token lives in env
# until curl is exec'd; curl's argv does briefly contain the URL, but the
# wider exposure window via /proc/<this_pid>/cmdline is eliminated.
TG_URL="https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"
curl -s -X POST "$TG_URL" \
  --data-urlencode "chat_id=${TOPIC_ID}" \
  --data-urlencode "text=${FORMATTED_MSG}" \
  > /dev/null 2>&1 || \
  logger -t molyanov-ops-notify -p user.err "Failed to post to ops topic"

exit 0
