# Service start and stop commands

This file covers the MCP server and Inspector on the Linux server
(192.168.0.222), plus Qdrant and the EmbeddingGemma endpoint on the same host.

## Qdrant Docker service

Qdrant runs in Docker Compose under `~/services/qdrant` on the Linux server.

Start locally (when already SSH'd in):

```bash
bash start_qdrant.sh
# equivalent: cd "$HOME/services/qdrant" && docker compose up -d
```

Start remotely via SSH from another machine:

```bash
bash start_docker.sh xs@192.168.0.222
# or: QDRANT_SSH_TARGET=xs@192.168.0.222 bash start_docker.sh
```

Stop (keep data):

```bash
ssh xs@192.168.0.222 'cd "$HOME/services/qdrant" && docker compose stop'
```

Stop and remove container (data in `storage/` and `snapshots/` is preserved):

```bash
ssh xs@192.168.0.222 'cd "$HOME/services/qdrant" && docker compose down'
```

Qdrant's container port is bound to `0.0.0.0:6333` on the Linux server.
The configured Qdrant URL is `http://192.168.0.222:6333`
(`http://192.168.0.222:6333/dashboard`) in `mcp/config/rag.conf`.

Keep the Linux server firewall configured to allow inbound connections on
port 6333 from trusted LAN addresses only.

## EmbeddingGemma server

The configured embedding endpoint is `http://192.168.0.222:8082/v1/embeddings`.
The server is started via `start_embedding_server.sh`, which calls
`../llama/build/bin/llama-server` relative to the project root.

```bash
bash start_embedding_server.sh
```

Or from another machine:

```bash
ssh xs@192.168.0.222 'cd "$HOME/sandbox" && bash start_embedding_server.sh'
```

Check that the endpoint is responding:

```bash
curl --fail --silent --show-error \
  -o /dev/null -w 'HTTP %{http_code}\n' \
  http://192.168.0.222:8082/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":"health check"}'
```

An HTTP 200 response confirms the embedding request succeeded.

## MCP server (stdio)

The MCP server uses stdio. MCP Inspector or an MCP-capable agent host launches
it as a child process; it is not a standalone network service.

To run the server directly (e.g. for scripted testing):

```bash
cd ~/sandbox
./venv-mcp/bin/python mcp/mcp_server.py
```

**Explicit server launch** (`start_mcp.sh`) — passes the venv Python and
`mcp/mcp_server.py` directly to Inspector, overriding any catalog entry. The
script invokes Inspector's packaged launcher entry directly to avoid a broken
npm `.bin` shim on installations where relative launcher imports are not
preserved. Use this to test the server without relying on a saved catalog:

```bash
cd ~/sandbox
bash ./start_mcp.sh
```

Stop it with **Ctrl+C**.

## Streamable HTTP MCP endpoint for Bonsai

The MCP HTTP server binds to `192.168.0.222` on the Linux server's LAN
interface, so Bonsai's GUI can reach it directly. Start the server with a
bearer token:

```bash
cd ~/sandbox
export MCP_HTTP_BEARER_TOKEN=test
bash ./start_mcp_http_server.sh
```

Or via `start_all_services.sh`, which starts Qdrant, the embedding server, the HTTP MCP
server, and MCP Inspector in sequence:

```bash
cd ~/sandbox
MCP_HTTP_BEARER_TOKEN=test bash start_all_services.sh
```

Stop the services started by that stack launcher with:

```bash
cd ~/sandbox
bash stop_all_services.sh
```

Check the status of all four services without changing them:

```bash
cd ~/sandbox
bash check_all_services.sh
```

The check returns exit code `1` if Qdrant, the embedding server, the MCP HTTP
server, or the Inspector is unavailable.

The endpoint is `http://192.168.0.222:8000/mcp`. In Bonsai's built-in
llama.cpp chat UI, open **Settings → MCP Client**, add that URL, enable the
MCP server for a conversation, and set the custom `Authorization` header to
`Bearer <token>`. Do not commit the token.

Stop the server with **Ctrl+C**. Keep the Linux server firewall configured to
allow inbound connections on port 8000 from trusted LAN addresses only.

## MCP Inspector

Two launcher scripts are provided depending on how Inspector is configured.

**Explicit launch** (`start_mcp.sh`) — passes the venv Python and
`mcp/mcp_server.py` directly to Inspector. The script uses Inspector's
packaged launcher entry directly so npm `.bin` shim issues do not prevent
relative launcher imports:

```bash
cd ~/sandbox
bash ./start_mcp.sh
```

**Catalog-based launch** (`start_mcps.sh`) — uses the Inspector v2 saved
catalog at `~/.mcp-inspector/mcp.json`, which holds the server connection
settings. It uses the same packaged launcher entry. Use this when the catalog
is already configured:

```bash
cd ~/sandbox
bash ./start_mcps.sh
```

Both scripts activate `venv-mcp`; they do not load the agent-owned Obsidian
configuration. Keep the terminal open while using Inspector. Stop Inspector
and its child MCP server with **Ctrl+C** in that terminal. See the official
[Inspector README](https://github.com/modelcontextprotocol/inspector/blob/main/README.md)
for catalog setup and connection options.
