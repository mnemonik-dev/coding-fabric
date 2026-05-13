#!/usr/bin/env bash

set -euo pipefail

LOG_FILE="/tmp/avp-step-6.log"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log_error() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2
}

log "=========================================="
log "AVP Step 6: last-known-good tag advancement"
log "=========================================="

# Check if code repository is available
if [ ! -d /home/op/code/mnemonic-masters/mnemonic-core ]; then
  log_error "mnemonic-core repo not found"
  exit 1
fi

cd /home/op/code/mnemonic-masters/mnemonic-core

# Get current last-known-good tag
log "Checking last-known-good tag..."

CURRENT_TAG=$(git describe --tags --match "last-known-good" 2>/dev/null || echo "none")
log "Current last-known-good tag: $CURRENT_TAG"

# Get current HEAD
CURRENT_SHA=$(git rev-parse HEAD)
log "Current HEAD: $CURRENT_SHA"

# Verify tag exists and points to a valid commit
if git rev-parse "last-known-good^{commit}" > /dev/null 2>&1; then
  TAG_SHA=$(git rev-list -n 1 "last-known-good")
  log "Last-known-good points to: $TAG_SHA"

  # Should be from a recent successful task (not this step)
  if [ -n "$TAG_SHA" ]; then
    log "PASS: last-known-good tag is in place and points to valid commit"
    exit 0
  fi
fi

log_error "FAIL: last-known-good tag not found or invalid"
exit 1
