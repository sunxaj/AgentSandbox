#!/usr/bin/env bash
# Serve the document RAG MCP tools over Streamable HTTP for Bonsai's GUI.
export MCP_HTTP_BEARER_TOKEN=test
set -euo pipefail
echo "MCP_HTTP_BEARER_TOKEN=test"
cd "$(dirname "$0")"
exec ./venv-mcp/bin/python mcp/http_server.py
