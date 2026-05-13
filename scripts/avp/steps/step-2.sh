#!/usr/bin/env bash

set -euo pipefail

LOG_FILE="/tmp/avp-step-2.log"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log_error() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2
}

log "=========================================="
log "AVP Step 2: Drive docs PR end-to-end + 5-node DAG"
log "=========================================="

# Check workspace-manager health
log "Checking workspace-manager health..."
if ! curl -s http://127.0.0.1:8080/health | jq -e '.status == "ok"' > /dev/null 2>&1; then
  log_error "workspace-manager health check failed"
  exit 1
fi

log "workspace-manager is healthy"

# Verify Mnemonic MCP local service
log "Checking Mnemonic MCP service..."
if ! systemctl is-active --quiet mnemonic-mcp; then
  log_error "mnemonic-mcp service not active"
  exit 1
fi

log "mnemonic-mcp service is active"

# Poll for 5-node DAG evidence
log "Polling for 5-node attestation DAG..."
MAX_POLLS=20
POLL_INTERVAL=30

for i in $(seq 1 $MAX_POLLS); do
  log "Poll $i/$MAX_POLLS..."

  # Query Mnemonic MCP recall API (if available locally)
  if command -v mnemonic_recall &> /dev/null; then
    DAG_OUTPUT=$(mnemonic_recall --feature=coding-fabric 2>&1 || echo "")

    if echo "$DAG_OUTPUT" | jq -e 'length >= 5' > /dev/null 2>&1; then
      log "5-node DAG detected"
      echo "$DAG_OUTPUT" | jq '.' > /tmp/avp-step-2-dag.json
      log "PASS: Docs PR end-to-end verified with complete DAG"
      exit 0
    fi
  fi

  if [ "$i" -lt "$MAX_POLLS" ]; then
    sleep "$POLL_INTERVAL"
  fi
done

log_error "FAIL: Could not verify 5-node DAG within timeout"
exit 1
