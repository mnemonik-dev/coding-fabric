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

# Format and send message
FORMATTED_MSG="[${TIMESTAMP}] ${SEVERITY^^}: ${MESSAGE}"
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d "chat_id=${TOPIC_ID}" \
  -d "text=${FORMATTED_MSG}" \
  -d "parse_mode=HTML" > /dev/null 2>&1 || \
  logger -t molyanov-ops-notify -p user.err "Failed to post to ops topic"

exit 0
