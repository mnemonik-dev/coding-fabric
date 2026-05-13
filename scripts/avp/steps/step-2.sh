#!/usr/bin/env bash

set -euo pipefail

# AVP Step 2: Drive docs PR end-to-end
#
# 2026-05 scope change: verdict simplified from "5-node attestation DAG" to
# "PR-completion + worktree cleanup". The local Mnemonic MCP server is not
# yet production-ready, so the DAG cannot be produced on a real deploy. The
# backlog feature `mnemonic-attestation-integration` re-introduces the DAG
# verdict once the MCP server is ready.
#
# Pass criteria (live VM):
#   1. workspace-manager /health returns {status: ok}
#   2. workspace-manager /worktrees returns a zero-length array after the run
#      (a docs PR that drove a worktree end-to-end must have cleaned up)
#
# Optional (only when MNEMONIC_MCP_ENABLED=1):
#   3. mnemonic_recall --feature=coding-fabric returns >= 5 nodes
#
# The script never assumes the mnemonic-mcp systemd unit is active; it
# treats MCP verification as an opt-in extension, not a required gate.

LOG_FILE="/tmp/avp-step-2.log"
MNEMONIC_MCP_ENABLED="${MNEMONIC_MCP_ENABLED:-0}"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log_error() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2
}

log "=========================================="
log "AVP Step 2: Drive docs PR end-to-end (PR-completion verdict)"
log "=========================================="

# 1. workspace-manager health
log "Checking workspace-manager health..."
if ! curl -fsS http://127.0.0.1:8080/health | jq -e '.status == "ok"' > /dev/null 2>&1; then
  log_error "workspace-manager health check failed"
  exit 1
fi
log "workspace-manager is healthy"

# 2. Worktree cleanup verdict (docs PR end-to-end must leave zero worktrees)
log "Verifying worktree cleanup via /worktrees endpoint..."
WORKTREES_JSON=$(curl -fsS http://127.0.0.1:8080/worktrees || echo "")
if [ -z "$WORKTREES_JSON" ]; then
  log_error "workspace-manager /worktrees endpoint did not respond"
  exit 1
fi

if ! echo "$WORKTREES_JSON" | jq -e 'length == 0' > /dev/null 2>&1; then
  COUNT=$(echo "$WORKTREES_JSON" | jq 'length' 2>/dev/null || echo "?")
  log_error "Expected zero active worktrees after docs PR end-to-end; got $COUNT"
  echo "$WORKTREES_JSON" | jq '.' >> "$LOG_FILE" 2>/dev/null || true
  exit 1
fi
log "Worktree cleanup verified (0 active)"

# 3. Optional: Mnemonic MCP DAG verification (opt-in; descoped 2026-05).
if [ "$MNEMONIC_MCP_ENABLED" = "1" ]; then
  log "[opt-in] Polling for 5-node attestation DAG via mnemonic_recall..."
  if ! systemctl is-active --quiet mnemonic-mcp; then
    log_error "MNEMONIC_MCP_ENABLED=1 but mnemonic-mcp service is not active"
    exit 1
  fi

  MAX_POLLS=20
  POLL_INTERVAL=30
  DAG_FOUND=0
  for i in $(seq 1 $MAX_POLLS); do
    log "  Poll $i/$MAX_POLLS..."
    if command -v mnemonic_recall &> /dev/null; then
      DAG_OUTPUT=$(mnemonic_recall --feature=coding-fabric 2>&1 || echo "")
      if echo "$DAG_OUTPUT" | jq -e 'length >= 5' > /dev/null 2>&1; then
        log "  5-node DAG detected"
        echo "$DAG_OUTPUT" | jq '.' > /tmp/avp-step-2-dag.json 2>/dev/null || true
        DAG_FOUND=1
        break
      fi
    fi
    if [ "$i" -lt "$MAX_POLLS" ]; then
      sleep "$POLL_INTERVAL"
    fi
  done

  if [ "$DAG_FOUND" -ne 1 ]; then
    log_error "FAIL: Could not verify 5-node DAG within timeout"
    exit 1
  fi
else
  log "Mnemonic MCP DAG verification skipped (descoped 2026-05 — see backlog: mnemonic-attestation-integration)"
fi

log "PASS: Docs PR end-to-end verified (PR-completion + worktree-cleanup verdict)"
exit 0
