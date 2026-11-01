# Launch Inspector using its saved catalog, which contains the MCP server entries.
# Activate the same environment configured for the MCP server command.
set -euo pipefail
source venv-mcp/bin/activate
# `--catalog` lets Inspector read connection settings from its own catalog file.
# Invoke the package entry point directly because some npm versions create a
# broken standalone .bin shim for Inspector's relative launcher imports.
INSPECTOR_ENTRY="$PWD/node_modules/@modelcontextprotocol/inspector/clients/launcher/build/index.js"
if [[ ! -f "$INSPECTOR_ENTRY" ]]; then
  echo "MCP Inspector is not installed. Run npm install in $PWD first." >&2
  exit 1
fi
exec node "$INSPECTOR_ENTRY" --catalog "$HOME/.mcp-inspector/mcp.json"
