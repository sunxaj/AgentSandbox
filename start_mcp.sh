# Launch Inspector with an explicit local MCP server process for this project.
set -euo pipefail
cd ~/sandbox
# Use the project's MCP dependencies rather than a global Python installation.
source ./venv-mcp/bin/activate
# Invoke the package entry point directly. Some npm versions materialize the
# .bin launcher as a standalone file, which breaks its relative import of
# parse-launcher-argv.js; the packaged entry point keeps those imports together.
INSPECTOR_ENTRY="$PWD/node_modules/@modelcontextprotocol/inspector/clients/launcher/build/index.js"
if [[ ! -f "$INSPECTOR_ENTRY" ]]; then
  echo "MCP Inspector is not installed. Run npm install in $PWD first." >&2
  exit 1
fi
# start_all_services.sh supplies this path so the companion stop script can
# terminate the foreground Inspector and its child MCP server precisely.
if [[ -n "${MCP_INSPECTOR_PID_FILE:-}" ]]; then
  mkdir -p "$(dirname "$MCP_INSPECTOR_PID_FILE")"
  printf '%s\n' "$$" > "$MCP_INSPECTOR_PID_FILE"
fi
exec node "$INSPECTOR_ENTRY" \
  "$PWD/venv-mcp/bin/python" "$PWD/mcp/mcp_server.py"
