#!/bin/bash
set -euo pipefail

# PK Guard Hook: Reject writes to .claude/skills/project-knowledge/references/.
#
# Fail-closed semantics (audit Finding F-005, T21 security-audit):
#   Earlier versions exited 0 unless a session env var was set, so any agent that
#   simply unset the var could bypass the guard.  This version inverts the
#   gate: the guard ALWAYS rejects writes to the PK references directory unless
#   the operator explicitly opts out via PK_GUARD_BYPASS=1.  PK_GUARD_BYPASS must
#   only be set in a deliberate operator-interactive shell; every bypass is
#   logged to auth.notice and Telegram-ops so post-hoc review is possible.
#   PK_GUARD_SESSION is an optional informational label written to logs.
#
# Telegram delivery (audit Finding F-004):
#   The bot token is composed into a TG_URL env var BEFORE curl runs, so the
#   token does not appear in this shell's argv listing or job history.  curl
#   itself does receive the URL as argv during exec, but the exposure window
#   is bounded to the lifetime of the curl process and is mitigated by the
#   `User=op` + systemd hardening on the units that invoke this hook.

PK_REFS_PATTERN=".claude/skills/project-knowledge/references/"
TARGET_PATH="${1:-}"
SESSION_LABEL="${PK_GUARD_SESSION:-unknown-session}"

# Only fire on writes that target the PK references tree.
if [[ "$TARGET_PATH" != *"$PK_REFS_PATTERN"* ]]; then
  exit 0
fi

# Operator-controlled bypass.  Setting PK_GUARD_BYPASS=1 in an interactive
# operator shell (never in any agent or CI environment) allows the write to
# proceed but emits an auditable log line + ops alert so the bypass is
# reviewable after the fact.
if [[ "${PK_GUARD_BYPASS:-0}" == "1" ]]; then
  logger -t molyanov-pk-guard -p auth.notice \
    "PK guard bypassed by operator (PK_GUARD_BYPASS=1): target=$TARGET_PATH session=$SESSION_LABEL"

  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_OPS_TOPIC:-}" ]]; then
    BYPASS_MSG="PK Guard BYPASSED (operator) for write to $PK_REFS_PATTERN; session=$SESSION_LABEL target=$TARGET_PATH"
    TG_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
    curl -s -X POST "$TG_URL" \
      --data-urlencode "chat_id=${TELEGRAM_OPS_TOPIC}" \
      --data-urlencode "text=${BYPASS_MSG}" \
      > /dev/null 2>&1 || true
  fi
  exit 0
fi

# Default: reject the write.
logger -t molyanov-pk-guard -p auth.warning \
  "Rejected write to PK references: target=$TARGET_PATH session=$SESSION_LABEL"

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_OPS_TOPIC:-}" ]]; then
  ALERT="PK Guard: Blocked write to $PK_REFS_PATTERN in session $SESSION_LABEL"
  # parse_mode intentionally omitted (default plain text) to prevent HTML/Markdown
  # injection from untrusted path/session values (Finding F-006).
  TG_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
  curl -s -X POST "$TG_URL" \
    --data-urlencode "chat_id=${TELEGRAM_OPS_TOPIC}" \
    --data-urlencode "text=${ALERT}" \
    > /dev/null 2>&1 || true
fi

exit 1
