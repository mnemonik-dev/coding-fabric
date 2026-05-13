#!/usr/bin/env bash

set -euo pipefail

LOG_FILE="/tmp/avp-step-9.log"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log_error() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2
}

log "=========================================="
log "AVP Step 9: Services health summary"
log "=========================================="

PASS_COUNT=0
FAIL_COUNT=0

SERVICES=(
  "workspace-manager.service"
  "mnemonic-mcp.service"
  "fabric-watchdog.service"
  "telegram-ai-agent.service"
  "tailscaled.service"
)

log "Checking critical services..."

for service in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$service" 2>/dev/null; then
    log "OK: $service is active"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    log "WARNING: $service is not active"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
done

# Check HTTP endpoints
log "Checking HTTP endpoints..."

ENDPOINTS=(
  "http://127.0.0.1:8080/health"
  "http://127.0.0.1:3000"
)

for endpoint in "${ENDPOINTS[@]}"; do
  if curl -s "$endpoint" > /dev/null 2>&1; then
    log "OK: $endpoint is reachable"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    log "WARNING: $endpoint is not reachable"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
done

log ""
log "Services health: $PASS_COUNT healthy, $FAIL_COUNT unhealthy"

cat > /tmp/avp-step-9-summary.json <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "healthy_services": $PASS_COUNT,
  "unhealthy_services": $FAIL_COUNT,
  "total_services": $((PASS_COUNT + FAIL_COUNT))
}
EOF

if [ "$FAIL_COUNT" -le 1 ]; then
  log "PASS: Most critical services are healthy"
  exit 0
else
  log_error "FAIL: Too many service failures"
  exit 1
fi
