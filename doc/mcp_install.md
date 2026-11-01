# MCP installation and configuration

## Runtime requirements

`mcp/http_server.py` serves the shared tool implementation over Streamable HTTP.
It binds to `192.168.0.222` on the Linux server's LAN interface;
`MCP_HTTP_BEARER_TOKEN` is required and the server firewall should restrict
port 8000 to the LAN. The Python workers are ordinary scripts launched by the
MCP server and do not run inside Bonsai. See `doc/services.md` for startup
and stop commands.

## Setup

```bash
ssh xs@192.168.0.222
cd ~/sandbox
python3 -m venv venv-mcp
source venv-mcp/bin/activate
python -m pip install -r requirements-mcp.txt
```

The requirements include the document-RAG MCP server dependencies and the
`pyspark-mcp` package used by the agent's separate Spark execution server.

Configure the MCP client to launch `mcp/mcp_server.py` with the venv Python:

```json
{
  "mcpServers": {
    "local-data-pipeline": {
      "command": "/home/xs/sandbox/venv-mcp/bin/python",
      "args": ["/home/xs/sandbox/mcp/mcp_server.py"]
    }
  }
}
```

To expose MCP over HTTP, set `MCP_HTTP_BEARER_TOKEN` and run
`./start_mcp_http_server.sh`; the server binds to `192.168.0.222:8000`.
Follow `doc/services.md` for the Bonsai client header, firewall guidance, and
service shutdown commands.

## Configuration

The MCP server uses `mcp/config/rag.conf` for the document source, metadata
source, accepted formats, chunking, embedding batch size, Qdrant URL, and
collections. Use `rag_index_documents(collection="metadata")` to index table
schema documents separately from general documents. `QDRANT_URL`,
`QDRANT_API_KEY`, `DOCUMENT_COLLECTION`, and `DOCUMENT_SOURCE_DIR` override the
general document settings; `METADATA_COLLECTION` and `METADATA_SOURCE_DIR`
override the metadata settings. Obsidian configuration is stored at
`agents/config/Obsidian.conf` and is consumed by agent nodes, not by this MCP
server. MCP RAG embedding settings are stored in `mcp/config/rag.conf`.
`KNOWLEDGE_PYTHON` overrides the Python executable used to launch workers.
Spark fixture output paths are configured separately in `mcp/config/spark.conf`.
