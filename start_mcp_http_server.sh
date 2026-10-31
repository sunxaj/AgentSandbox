#!/usr/bin/env bash
# Serve the existing registered MCP tools over Streamable HTTP for Bonsai's GUI.
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv-mcp/bin/python mcp/http_server.py
