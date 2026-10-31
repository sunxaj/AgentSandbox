#!/usr/bin/env bash
#
# start_all.sh — start the full knowledge stack on the Linux server.
#
#   1. Qdrant              (docker compose, detached)
#   2. Embedding server    (llama-server, background)
#   3. MCP HTTP server     (http_server.py, background)
#   4. MCP stdio server    (inspector + mcp_server.py, foreground)
#
# Usage:  bash start_all.sh
#
# Notes:
#   - The embedding server blocks in the foreground, so it is started in the
#     background (nohup) and its output is written to embedding_server.log.
#   - The MCP HTTP server is started in the background before the inspector.
#     MCP_HTTP_BEARER_TOKEN must be set in the environment or the HTTP server
#     will refuse to start.
#   - The MCP inspector is an interactive TUI, so it runs last, in the
#     foreground, and takes over the terminal.

set -euo pipefail

# Always operate from the project root (this script's directory).
# start_embedding_server.sh uses the relative path ../llama/..., so it must
# be run with the project root as the working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ---------------------------------------------------------------------------
# 1. Qdrant
# ---------------------------------------------------------------------------
log "Starting Qdrant..."
(cd "$HOME/services/qdrant" && docker compose up -d)
log "Qdrant started."

# ---------------------------------------------------------------------------
# 2. Embedding server (background — llama-server blocks in the foreground)
# ---------------------------------------------------------------------------
log "Starting embedding server (background)..."
nohup bash "$SCRIPT_DIR/start_embedding_server.sh" \
    > "$SCRIPT_DIR/embedding_server.log" 2>&1 &
EMBED_PID=$!
log "Embedding server started (PID $EMBED_PID, log: $SCRIPT_DIR/embedding_server.log)"

# ---------------------------------------------------------------------------
# 3. MCP HTTP server (background — serves Streamable HTTP for Bonsai GUI)
# ---------------------------------------------------------------------------
export MCP_HTTP_BEARER_TOKEN="test"
log "Starting MCP HTTP server (background)..."
nohup bash "$SCRIPT_DIR/start_mcp_http_server.sh" \
    > "$SCRIPT_DIR/mcp_http_server.log" 2>&1 &
MCP_HTTP_PID=$!
log "MCP HTTP server started (PID $MCP_HTTP_PID, log: $SCRIPT_DIR/mcp_http_server.log)"

# ---------------------------------------------------------------------------
# 4. MCP stdio server (foreground — inspector is an interactive TUI)
# ---------------------------------------------------------------------------
log "Starting MCP server..."
bash "$SCRIPT_DIR/start_mcp.sh"
