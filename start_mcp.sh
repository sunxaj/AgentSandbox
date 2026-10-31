# Launch Inspector with an explicit local MCP server process for this project.
cd ~/sandbox
# Use project dependencies and the project Obsidian config rather than a global Python install.
source ./venv-mcp/bin/activate
OBSIDIAN_CONFIG="$PWD/mcp/config/Obsidian.conf" \
  npx -y @modelcontextprotocol/inspector \
  "$PWD/venv-mcp/bin/python" "$PWD/mcp/mcp_server.py"
