#!/usr/bin/env bash
# Check the services started by start_all_services.sh on the Linux server.
set -uo pipefail

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}/knowledge-stack"
FAILED=0

report() {
    local name="$1"
    local state="$2"
    printf '%-24s %s\n' "$name" "$state"
    [[ "$state" == "RUNNING" ]] || FAILED=1
}

check_pid_file() {
    local name="$1"
    local expected="$2"
    local pid_file="$RUNTIME_DIR/$name.pid"
    local pid command

    if [[ ! -f "$pid_file" ]]; then
        report "$name" "STOPPED (no PID file)"
        return
    fi
    pid="$(<"$pid_file")"
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$command" == *"$expected"* ]]; then
        report "$name" "RUNNING (PID $pid)"
    else
        report "$name" "STOPPED (stale PID file)"
    fi
}

check_http() {
    local name="$1"
    local url="$2"
    local status

    # Any HTTP response proves that the listener is reachable. Authentication
    # and MCP protocol semantics are intentionally left to the service tests.
    status="$(curl --silent --show-error --output /dev/null \
        --write-out '%{http_code}' --max-time 5 "$url" 2>/dev/null || true)"
    if [[ "$status" =~ ^[1-5][0-9][0-9]$ ]]; then
        report "$name" "RUNNING (HTTP $status)"
    else
        report "$name" "STOPPED (no HTTP response)"
    fi
}

if (cd "$HOME/services/qdrant" && \
    docker compose ps --status running --quiet 2>/dev/null | grep -q .); then
    report "Qdrant" "RUNNING"
else
    report "Qdrant" "STOPPED"
fi

check_pid_file "embedding_server" "llama-server"
check_http "Embedding HTTP" "http://127.0.0.1:8082/health"

check_pid_file "mcp_http_server" "mcp/http_server.py"
check_http "MCP HTTP" "http://192.168.0.222:8000/mcp"

# Inspector is interactive and has no health endpoint, so its recorded process
# is the authoritative check. Its child MCP server is included in its process
# tree and is stopped with it by stop_all_services.sh.
check_pid_file "mcp_inspector" "clients/launcher/build/index.js"

if (( FAILED )); then
    printf '\nOne or more services are not running.\n' >&2
    exit 1
fi
printf '\nAll services are running.\n'
