#!/usr/bin/env bash
# Stop the services started by start_all_services.sh on the Linux server.
set -euo pipefail

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}/knowledge-stack"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

stop_process_tree() {
    local pid="$1"
    local child
    # Stop children first so an Inspector-spawned MCP server cannot outlive its
    # parent and keep the stack's stdio process running in the background.
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        stop_process_tree "$child"
    done
    kill "$pid" 2>/dev/null || true
}

stop_pid_file() {
    local name="$1"
    local expected="$2"
    local pid_file="$RUNTIME_DIR/$name.pid"
    local pid command

    if [[ ! -f "$pid_file" ]]; then
        log "$name is not recorded as running."
        return
    fi
    pid="$(<"$pid_file")"
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ ! "$pid" =~ ^[0-9]+$ ]] || [[ "$command" != *"$expected"* ]]; then
        log "Ignoring stale or mismatched $name PID file."
        rm -f "$pid_file"
        return
    fi

    log "Stopping $name (PID $pid)..."
    stop_process_tree "$pid"
    for _ in {1..20}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 0.25
    done
    rm -f "$pid_file"
}

# The PID and command checks keep this script scoped to processes started by
# this project, even if other Python, Node, or llama services are running.
stop_pid_file "mcp_inspector" "clients/launcher/build/index.js"
stop_pid_file "mcp_http_server" "mcp/http_server.py"
stop_pid_file "embedding_server" "llama-server"

log "Stopping Qdrant..."
(cd "$HOME/services/qdrant" && docker compose stop)
log "All services stopped."
