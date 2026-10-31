# Agent Installation

## Prerequisites

- Mac with Python 3.11+
- Linux server at `192.168.0.222` running Bonsai (port 8081), MCP HTTP server (port 8000), and Qdrant (port 6333)
- `MCP_HTTP_BEARER_TOKEN` set on the Linux server

## 1 — Start the Linux server stack

```bash
ssh xs@192.168.0.222
cd ~/sandbox
bash start_all.sh
```

Confirm the MCP HTTP server is up:

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer test" \
  http://192.168.0.222:8000/mcp
# expect 200 or 405
```

## 2 — Verify the model name (first time only)

```bash
curl -s http://192.168.0.222:8081/v1/models | python3 -m json.tool
```

Copy the `id` value and set it as `model` in `agents/config/agent.conf`.

## 3 — Configure the agent (first time only)

Edit `agents/config/agent.conf`:

```ini
[llm]
base_url    = http://192.168.0.222:8081/v1
model       = <model-id-from-step-2>
api_key     = none
temperature = 0.1
max_tokens  = 4096

[mcp]
url          = http://192.168.0.222:8000/mcp
bearer_token = test

[agent]
upload_dir = ~/sandbox/uploads
```

## 4 — Create the Mac venv (first time only)

```bash
cd ~/sandbox
python3 -m venv venv-agent
source venv-agent/bin/activate
pip install -r requirements-agent.txt
```

## 5 — Launch the agent

```bash
cd ~/sandbox
source venv-agent/bin/activate

# The agent entry point will be added during Phase 1 implementation.
# Run it here once agents/app/main.py exists.
# python -m agents.app.main
```

The browser UI is not available until the Phase 3 frontend is implemented.
Phase 1 is tested through the development CLI instead.

## Subsequent runs

```bash
# Terminal 1 — keep the Linux server running
ssh xs@192.168.0.222
cd ~/sandbox && bash start_all.sh

# Terminal 2 — Mac
cd ~/sandbox
source venv-agent/bin/activate
# Start the implemented CLI or agent entry point from the current phase.
```

## MCP server setup (Linux — first time only)

See `doc/mcp.md` for full details. Quick reference:

```bash
ssh xs@192.168.0.222
cd ~/sandbox
python3 -m venv venv-mcp
source venv-mcp/bin/activate
pip install -r requirements-mcp.txt
```

Set `MCP_HTTP_BEARER_TOKEN` and run `./start_mcp_http_server.sh` to expose MCP over HTTP.
The server binds to `192.168.0.222:8000`; restrict port 8000 to the LAN at the firewall.

## Spark MCP and development fixtures

The agent uses a separate PySpark MCP server for executing approved code. Install
it on the Linux server in its own environment:

```bash
ssh xs@192.168.0.222
cd ~/sandbox
python3 -m venv venv-spark-mcp
./venv-spark-mcp/bin/pip install "pyspark-mcp==0.0.6"
```

Start it on the server so the Mac agent can reach it:

```bash
cd ~/sandbox
./venv-spark-mcp/bin/pyspark-mcp \
  --master "local[*]" \
  --host 192.168.0.222 \
  --port 8090
```

Set the agent's Spark URL to:

```ini
[spark]
url = http://192.168.0.222:8090/mcp
```

Create the synthetic Parquet tables and their metadata documents:

```bash
cd ~/sandbox
./venv-spark-mcp/bin/python agents/app/create_dummy_parquet.py
```

The command writes the three tables from `agents/references/data.md` under
`/home/xs/Documents/data/parquet/` and writes searchable metadata documents
under `/home/xs/Documents/data/metadata/`.

Index the metadata into its separate Qdrant collection:

```bash
cd ~/sandbox
./venv-mcp/bin/python mcp/workers/rag/50_index_documents.py \
  --collection metadata
```

Test metadata retrieval:

```bash
./venv-mcp/bin/python mcp/workers/rag/52_search_documents.py \
  "Which table contains customer product relationships?" \
  --collection metadata
```
