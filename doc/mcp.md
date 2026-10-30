# Data Retrival MCP server

The Linux server runs one MCP tool implementation with two entry points: stdio
for local MCP clients and Streamable HTTP for Bonsai's llama.cpp/Open WebUI
clients. Both expose the same tools and launch local Python workers; neither
the MCP server nor workers need to run inside the LLM server.

## Tool layers

### Layer 1: pipeline

- `obsidian_import_conversations`: import one `.jsonl` or `.md` file, or recursively scan
  the configured export folder and its subfolders.
- `obsidian_extract_knowledge`: create reviewable notes through Bonsai.
- `obsidian_build_topic_index`: refresh generated topic pages.
- `obsidian_build_semantic_index`: rebuild the semantic index through EmbeddingGemma.
- `obsidian_run_knowledge_pipeline`: run import, extraction, topic refresh, and semantic
  rebuild in order, stopping on failure.
- `rag_index_documents`: rebuild the separate document collection in Qdrant.

### Layer 2: retrieval

- `obsidian_search_knowledge`: keyword search across notes, topics, and
  transcripts; optionally search raw JSONL exports.
- `obsidian_search_semantic`: meaning-based search over the built semantic index.
- `obsidian_read_note`: read a Markdown note by vault-relative path under `00 Inbox`,
  `10 Knowledge`, or `20 Topics`.
- `obsidian_list_topics`: list topic page titles and paths.
- `rag_search_documents`: semantic search over the separate Qdrant document collection.
- `rag_read_source`: read an original Markdown, text, or PDF file from the configured
  document source folder.

Document ingestion is available through `rag_index_documents`. It reads the
configured source folder, extracts content from `.md`, `.txt`, `.pdf`, `.csv`,
`.json`, `.yaml`, `.yml`, and `.html` files using helpers in `mcp/helpers/`,
chunks and embeds the content through EmbeddingGemma, then upserts vectors and
source metadata to a dedicated Qdrant collection. The document collection
remains separate from the Obsidian semantic index. Qdrant is reachable directly
at `192.168.0.222:6333` over the LAN; no SSH tunnel is required.

## Project folders and worker names

```text
mcp/
  config/
    Obsidian.conf               # Vault paths, service URLs
    rag.conf                    # Separate document RAG source settings
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
  runtime_config.py
  rag_config.py
  workers/
    00_import_conversations.py
    rag/
      50_index_documents.py
      51_read_source.py
      52_search_documents.py
    obsidian/
      10_extract_knowledge.py
      20_build_topic_index.py
      30_build_semantic_index.py
      40_search_knowledge.py
      41_search_semantic.py
      90_knowledge_audit.py
conversation_exports/              # source JSONL/Markdown files
obsidian-import/<source-folder>/   # converted/copied imports
doc/
```

Worker filename prefixes indicate workflow order: `00_` imports source files,
`10_` extracts knowledge, `20_` refreshes topics, and `30_` rebuilds the
semantic index. `40_` and `41_` retrieve Obsidian content. Document RAG workers
`50_` through `52_` live under `mcp/workers/rag/`; `90_` is an audit utility.
Obsidian-reading and -writing workers live under `mcp/workers/obsidian/`.
Chunking helpers shared by RAG workers live in `mcp/helpers/`.

## Runtime requirements

`mcp/http_server.py` serves the shared tool implementation over Streamable HTTP.
It binds to `192.168.0.222` on the Linux server's LAN interface;
`MCP_HTTP_BEARER_TOKEN` is required and the server firewall should restrict
port 8000 to the LAN. The Python workers are ordinary scripts launched by the
MCP server and do not run inside Bonsai. See `doc/services.md` for startup and
stop commands.

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
    "data-retrival-pipeline": {
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

The server re-reads `mcp/config/Obsidian.conf` when each tool is called. It
contains the conversation export and import output paths, vault path, vault
folder names, semantic index path, and Bonsai and embeddings URLs. Relative
paths resolve from the repository root, except a relative `semantic_index`
path, which resolves from the vault root. `OBSIDIAN_CONFIG` selects another
config file. The settings can also be overridden with `CHATGPT_EXPORT_DIR`,
`CONVERSATION_IMPORT_DIR`, `KNOWLEDGE_VAULT`, `BONSAI_BASE_URL`, and
`EMBEDDINGS_URL` environment variables. `mcp/config/rag.conf` configures the
document source, accepted formats, chunking, embedding batch size, Qdrant URL,
and collection. `QDRANT_URL`, `QDRANT_API_KEY`, `DOCUMENT_COLLECTION`, and
`DOCUMENT_SOURCE_DIR` can override the corresponding settings at runtime.
`KNOWLEDGE_PYTHON` overrides the Python executable used to launch workers.

## Import safeguards

Call `obsidian_import_conversations` with either an explicit `source_file` or a
`source_directory`, or omit both to recursively scan `CHATGPT_EXPORT_DIR`.
The importer converts `.jsonl` files and copies `.md` files unchanged. Results
go under `obsidian-import/`, in subfolders matching the source folder path.
Each output is also mirrored into the configured Inbox folder (`00 Inbox`),
preserving the same relative subfolder path. Existing staging and Inbox files
are skipped, never overwritten; a repeat run can fill a missing Inbox copy from
an existing staged file. The result contains staging import counts, Inbox
copy/skip counts, and parse/file errors. The pipeline stops at import if errors
are reported.

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

Extraction marks notes `needs-review`, never overwrites existing note files,
and checkpoints successful transcript chunks by source content hash. Unchanged
chunks are skipped on later runs. Modified transcripts produce versioned note
filenames; previous notes remain for review. Successful chunks are saved after
each model response, so failed chunks can be retried without repeating
successful requests. A failed chunk makes the extraction tool fail, and only
one extraction worker may run at a time. Checkpoint data is stored in the
hidden `.knowledge_extraction_state.json` file under the configured import
staging directory. Keep this file to preserve incremental behavior.

Topic refresh replaces only existing pages marked as generated
`type: "topic-index"`. Semantic indexing rebuilds the configured index at
`30 Index/semantic_index.json` in the vault. `obsidian_read_note` limits reads
to the three Markdown folders above and caps returned content length.

Topic indexing groups at most 20 note summaries per Bonsai request by default,
then consolidates those groups hierarchically. If a grouping request fails,
completed requests are cached in the hidden `.topic_index_build_cache.json`
file under Topics; retrying reuses them. After a successful refresh, generated
topic pages that no longer appear are reported as stale and left in place for
review. Unmarked pages are still protected. An exclusive lock rejects a second
topic build while one is already running.

Semantic-index embedding progress is cached beside the configured index in a
hidden `<index filename>.checkpoint.json` file. A failed rebuild can reuse
completed batches if the source set and endpoint are unchanged. The existing
complete index remains available until the replacement is written atomically.
The checkpoint is removed after a successful rebuild. An exclusive lock rejects
overlapping semantic-index rebuilds.
