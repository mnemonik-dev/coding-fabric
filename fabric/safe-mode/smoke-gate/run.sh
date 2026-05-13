#!/bin/bash
#
# run.sh — orchestrates end-to-end smoke test on candidate fabric
# Runs a trivial docs-task through workspace-manager HTTP API.
#

set -euo pipefail

TIMEOUT=600  # 10 minutes
WORKSPACE_MANAGER_URL="${WORKSPACE_MANAGER_URL:-http://localhost:8080}"
FABRIC_MODE="${FABRIC_MODE:-smoke-gate}"

log() {
  echo "[smoke-gate] $(date +'%Y-%m-%d %H:%M:%S') — $*"
}

check_endpoint_ready() {
  local url="$1"
  local timeout="$2"
  local elapsed=0

  while [[ $elapsed -lt $timeout ]]; do
    if curl -sf "$url/health" >/dev/null 2>&1; then
      log "Endpoint ready: $url"
      return 0
    fi
    elapsed=$((elapsed + 2))
    sleep 2
  done

  log "ERROR: endpoint not ready after $timeout sec"
  return 1
}

run_smoke_task() {
  log "Starting smoke task: trivial docs-task"

  local task_body=$(cat <<'EOF'
{
  "task_name": "smoke-gate-docs",
  "prompt": "Write a test plan for a trivial smoke test in the docs-topic",
  "topic": "docs",
  "timeout_sec": 300
}
EOF
  )

  log "POST /api/v1/task to $WORKSPACE_MANAGER_URL"
  local response
  response=$(curl -sf -X POST \
    -H "Content-Type: application/json" \
    -d "$task_body" \
    "$WORKSPACE_MANAGER_URL/api/v1/task")

  log "Response: $response"

  local task_id
  task_id=$(echo "$response" | jq -r '.task_id // empty')

  if [[ -z "$task_id" ]]; then
    log "ERROR: no task_id in response"
    return 1
  fi

  log "Task submitted: $task_id"

  # Poll for task completion
  local start_time=$(date +%s)
  local poll_interval=5

  while true; do
    local current_time=$(date +%s)
    local elapsed=$((current_time - start_time))

    if [[ $elapsed -gt $TIMEOUT ]]; then
      log "ERROR: task timed out after $TIMEOUT sec"
      return 1
    fi

    local status_response
    status_response=$(curl -sf "$WORKSPACE_MANAGER_URL/api/v1/task/$task_id" 2>/dev/null || echo '{"status":"error"}')

    local status
    status=$(echo "$status_response" | jq -r '.status // "unknown"')

    log "Task status: $status (elapsed: ${elapsed}s)"

    case "$status" in
      completed)
        log "Task completed successfully"
        touch /tmp/smoke-task-complete
        return 0
        ;;
      failed|error)
        log "Task failed: $status_response"
        return 1
        ;;
      pending|running)
        sleep "$poll_interval"
        ;;
      *)
        log "Unknown status: $status"
        sleep "$poll_interval"
        ;;
    esac
  done
}

main() {
  log "smoke-gate orchestrator starting"
  log "Fabric mode: $FABRIC_MODE"
  log "Workspace manager: $WORKSPACE_MANAGER_URL"

  if ! check_endpoint_ready "$WORKSPACE_MANAGER_URL" 30; then
    log "ERROR: workspace-manager not ready"
    return 1
  fi

  if ! run_smoke_task; then
    log "ERROR: smoke task failed"
    return 1
  fi

  log "smoke-gate complete"
  return 0
}

main "$@"
