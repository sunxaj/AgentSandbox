# Launch Inspector using its saved catalog, which contains the MCP server entries.
# Activate the same environment configured for the MCP server command.
source venv-mcp/bin/activate
# `--catalog` lets Inspector read connection settings from its own catalog file.
npx -y @modelcontextprotocol/inspector --catalog ~/.mcp-inspector/mcp.json
