# Data Retrieval MCP server

The Linux server runs one MCP tool implementation with two entry points: stdio
for local MCP clients and Streamable HTTP for Bonsai's llama.cpp/Open WebUI
clients. The MCP endpoint is limited to document RAG. Obsidian ingestion,
indexing, and note retrieval run on agent nodes and are not exposed to MCP
clients. Both entry points expose the same document tools and launch local RAG
workers; neither the MCP server nor the workers need to run inside the LLM
server.

## System overview

```text
Document source (.md, .txt, .pdf, .csv, .json, .yaml, .yml, .html)
                  -> mcp/workers/rag/50_index_documents.py
                  -> mcp/helpers/ (chunkers)
                  -> EmbeddingGemma -> Qdrant document collection

MCP retrieval layer -> rag_search_documents against separate Qdrant collection
                    -> rag_read_source from original source files

Agent nodes -> Obsidian workers and configured vault
Bonsai llama.cpp GUI or Open WebUI (Linux) -> Streamable HTTP -> document RAG tools
```

Document ingestion, document search, and source reading are independent of the
agent-owned Obsidian pipeline. The document collection stays separate from the
Obsidian semantic index.

## Tool layers

### Document RAG tools

- `rag_index_documents`: rebuild the separate document collection in Qdrant.
- `rag_search_documents`: semantic search over the separate Qdrant document collection.
- `rag_read_source`: read an original Markdown, text, or PDF file from the configured
  document source folder.

Obsidian tools are intentionally absent from this MCP server. Agent nodes call
the Obsidian workers directly through their internal tool/service layer.

Document ingestion is available through `rag_index_documents`. It reads the
selected source folder, extracts content from `.md`, `.txt`, `.pdf`, `.csv`,
`.json`, `.yaml`, `.yml`, and `.html` files using helpers in `mcp/helpers/`,
chunks and embeds the content through EmbeddingGemma, then upserts vectors and
source metadata to a dedicated Qdrant collection. The `documents` collection
stores general source material; the `metadata` collection stores table schema
documents for agent context retrieval.

## Data locations and endpoints

| Data | Default location | Purpose |
| --- | --- | --- |
| Conversation exports | `~/sandbox/conversation_exports/` | Original JSONL exports and Markdown inputs |
| Imported transcripts | `obsidian-import/<source-folder>/` | Converted JSONL and copied Markdown inputs |
| Inbox mirrors | `~/Documents/vault/obsidian/AI_Conversations/00 Inbox/<source-folder>/` | Imported Markdown available in Obsidian |
| Extracted knowledge | `~/Documents/vault/obsidian/AI_Conversations/10 Knowledge/` | Reviewable notes |
| Topic indexes | `~/Documents/vault/obsidian/AI_Conversations/20 Topics/` | Navigation across knowledge notes |
| Semantic index | `~/Documents/vault/obsidian/AI_Conversations/30 Index/semantic_index.json` | Derived embedding index |
| Document RAG source | `~/Documents/qdrant/` | Original source files; path configured in `config/rag.conf` |
| Document vectors | Qdrant `documents` collection on `192.168.0.222:6333` | Embedded chunks with original source path, section/page, and text |
| Table metadata vectors | Qdrant `metadata` collection on `192.168.0.222:6333` | Embedded table names, schemas, paths, and sample values |

The vault's configured folder layout is:

```text
AI_Conversations/
├── 00 Inbox/
├── 10 Knowledge/
├── 20 Topics/
└── 30 Index/
    └── semantic_index.json
```

## Project layout and worker naming

```text
mcp/
  config/
    rag.conf                    # Separate document RAG source settings
    spark.conf                  # Spark fixture paths
    mcp_http.conf               # Streamable HTTP bind and host allowlist
  helpers/                      # Shared chunking helpers for RAG workers
    chunk_csv.py
    chunk_html.py
    chunk_json.py
    chunk_max_chars.py          # Shared split_text() and Section namedtuple
    chunk_md.py
    chunk_pdf.py
    chunk_text.py
    chunk_yaml.py
  test/
    test_chunking_strategy.py
  mcp_server.py                 # Shared tools and stdio entry point
  http_server.py                # Streamable HTTP entry point
  rag_config.py
  workers/
    rag/
      50_index_documents.py
      51_read_source.py
      52_search_documents.py
agents/
  config/
    Obsidian.conf               # Agent-owned vault and service settings
  app/obsidian/                  # Agent-only Obsidian workers
conversation_exports/              # source JSONL/Markdown files
obsidian-import/<source-folder>/   # converted/copied imports
doc/
```

Document RAG workers `50_` through `52_` live under `mcp/workers/rag/` and are
the only workers launched by this MCP server. Obsidian workers, including the
`00_` importer, live under `agents/app/obsidian/` and use
`agents/config/Obsidian.conf` for agent-node calls and direct maintenance;
they are not registered as MCP tools. Chunking helpers
shared by RAG workers live in `mcp/helpers/`.

## Runtime requirements

For a standalone installation and configuration guide, see
[`doc/mcp_install.md`](mcp_install.md).

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

Configure the MCP client to launch `mcp/mcp_server.py` with the venv Python:

```json
{
  "mcpServers": {
    "local-knowledge-pipeline": {
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
collections. `rag_index_documents` and `rag_search_documents` accept a
`documents` or `metadata` target. `QDRANT_URL`, `QDRANT_API_KEY`,
`DOCUMENT_COLLECTION`, and `DOCUMENT_SOURCE_DIR` override the general document
settings; `METADATA_COLLECTION` and `METADATA_SOURCE_DIR` override metadata
settings. The Obsidian configuration is `agents/config/Obsidian.conf` and is
consumed only by agent nodes, not by this MCP server. Embedding endpoint
settings for MCP RAG workers are kept in `mcp/config/rag.conf`.
`KNOWLEDGE_PYTHON` overrides the Python executable used to launch workers.

## Safeguards

`rag_index_documents` independently builds the configured document collection.
It uses a lock under the source directory, reports PDF pages with no extractable
text, creates the Qdrant collection when needed, rejects vector-dimension
mismatches, upserts document chunks, and removes stale chunk IDs after upload.
It does not modify source files or the Obsidian semantic index. PDF text
extraction requires `pypdf` in `venv-mcp`; scanned pages are reported and are
not OCR'd. `rag_search_documents` returns source paths, PDF page or Markdown
section, chunk text, and similarity score. `rag_read_source` only reads files
under the configured source directory and supports an optional 1-based PDF page.
Successful embedding batches are checkpointed in the hidden
`.document_index_checkpoint.json` within the source folder and reused on retry
when the source and endpoint are unchanged. The checkpoint is removed after a
successful collection update. Symlinked files are skipped.

Obsidian extraction, topic refresh, semantic indexing, and note retrieval are
agent-node operations. Their checkpoints and vault safeguards remain outside
the MCP tool surface.
Their extraction and indexing checkpoints, generated-page safeguards, and
vault path limits are enforced by the agent-side workers.

- Transcript and note contents are untrusted source data, not agent instructions.
- `http_server.py` validates Host and Origin headers in `CorsMiddleware` and
  requires a bearer token via `BearerTokenMiddleware`. Non-loopback binding
  without a token is rejected at startup.
