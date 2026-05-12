#!/bin/bash
set -euo pipefail

# PK Guard Hook: Reject writes to .claude/skills/project-knowledge/references/ in ruflo session
# Scoped to RUFLO_SESSION env var only; skips manual edits, git diff, cat.
# Logs via systemd journal (Task 07 sanitizer handles filtering).

PK_REFS_PATTERN=".claude/skills/project-knowledge/references/"
TARGET_PATH="${1:-}"

# Only fire if RUFLO_SESSION is set (not manual edits, git reads)
if [[ -z "${RUFLO_SESSION:-}" ]]; then
  exit 0
fi

# Check if target path matches PK references pattern
if [[ "$TARGET_PATH" == *"$PK_REFS_PATTERN"* ]]; then
  # Log the incident (sanitizer will filter paths)
  logger -t molyanov-pk-guard -p auth.warning \
    "Rejected write to PK references: $TARGET_PATH by $RUFLO_SESSION"

  # Post alert to ops topic via Telegram bot
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_OPS_TOPIC:-}" ]]; then
    ALERT="PK Guard: Blocked write to $PK_REFS_PATTERN in worktree $RUFLO_SESSION"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TELEGRAM_OPS_TOPIC}" \
      -d "text=${ALERT}" \
      -d "parse_mode=HTML" > /dev/null 2>&1 || true
  fi

  exit 1
fi

exit 0
