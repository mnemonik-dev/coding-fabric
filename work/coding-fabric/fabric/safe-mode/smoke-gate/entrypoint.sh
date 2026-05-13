#!/bin/bash
#
# entrypoint.sh — candidate fabric service container entry point
#

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-unknown}"
FABRIC_MODE="${FABRIC_MODE:-smoke-gate}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

log() {
  echo "[${SERVICE_NAME}] [${LOG_LEVEL}] $(date +'%Y-%m-%d %H:%M:%S') — $*"
}

log "Starting fabric service: $SERVICE_NAME in $FABRIC_MODE mode"

case "$SERVICE_NAME" in
  workspace-manager)
    log "Starting workspace-manager HTTP API..."
    # Mock HTTP server that responds to health checks and task endpoints
    python3 << 'PYTHON'
import http.server
import json
import socketserver
import threading
import time

class TaskHandler(http.server.BaseHTTPRequestHandler):
    tasks = {}
    task_counter = 0

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        elif self.path.startswith('/api/v1/task/'):
            task_id = self.path.split('/')[-1]
            if task_id in self.tasks:
                task = self.tasks[task_id]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                # Simulate task completion
                if time.time() - task['created'] > 5:
                    task['status'] = 'completed'
                self.wfile.write(json.dumps(task).encode())
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/v1/task':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            request = json.loads(body)

            TaskHandler.task_counter += 1
            task_id = f"task-{TaskHandler.task_counter}"
            task = {
                'task_id': task_id,
                'status': 'running',
                'created': time.time(),
                'prompt': request.get('prompt', ''),
                'topic': request.get('topic', ''),
            }
            self.tasks[task_id] = task

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(task).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging

with socketserver.TCPServer(("0.0.0.0", 8080), TaskHandler) as httpd:
    print("[workspace-manager] Listening on port 8080", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[workspace-manager] Shutting down", flush=True)
PYTHON
    ;;

  mnemonic-mcp)
    log "Starting mnemonic-mcp..."
    # Mock MCP service
    log "mnemonic-mcp ready for connections"
    sleep infinity
    ;;

  fabric-watchdog)
    log "Starting fabric-watchdog..."
    # Mock watchdog service
    log "fabric-watchdog monitoring services"
    sleep infinity
    ;;

  *)
    log "ERROR: unknown service: $SERVICE_NAME"
    exit 1
    ;;
esac
