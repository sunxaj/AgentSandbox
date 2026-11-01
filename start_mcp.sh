# Launch Inspector with an explicit local MCP server process for this project.
cd ~/sandbox
# Use the project's MCP dependencies rather than a global Python installation.
source ./venv-mcp/bin/activate
npx -y @modelcontextprotocol/inspector \
  "$PWD/venv-mcp/bin/python" "$PWD/mcp/mcp_server.py"
