#!/usr/bin/env python3
"""Local stdio MCP server that exposes document RAG tools.

The server validates public tool arguments, starts the matching RAG worker, and
returns its structured result. Obsidian workers are intentionally excluded:
agent nodes own vault access and invoke those workers through their internal
tool/service layer instead of exposing them to remote MCP clients.
"""

import os
import json
import subprocess
import sys

from mcp.server.fastmcp import FastMCP
from runtime_config import PROJECT_ROOT, get_path

ROOT = PROJECT_ROOT
WORKERS = ROOT / "mcp" / "workers"
# Document RAG workers are the only workers exposed through this MCP server.
# Obsidian workers are agent-node responsibilities and are intentionally not
# registered here, preventing remote MCP clients from accessing the vault.
DOCUMENT_WORKERS = WORKERS / "rag"
VAULT = get_path("vault")
PYTHON = os.getenv("KNOWLEDGE_PYTHON", sys.executable)

mcp = FastMCP("local-knowledge-pipeline")


def _refresh_paths():
    """Reload machine-specific paths before a tool runs, so config edits take effect."""
    global VAULT
    VAULT = get_path("vault")


def _run_document_tool(script, *args, timeout=7200):
    """Run a document RAG worker; its configuration is loaded from config/rag.conf."""
    _refresh_paths()
    env = os.environ.copy()
    env["KNOWLEDGE_VAULT"] = str(VAULT)
    # RAG workers are isolated from the Obsidian worker directory by design.
    command = [PYTHON, str(DOCUMENT_WORKERS / script), *map(str, args)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=timeout)
    output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
    if result.returncode:
        raise RuntimeError(f"{script} failed (exit {result.returncode}): {output[-5000:]}")
    return output


@mcp.tool()
def rag_index_documents(collection: str = "documents") -> dict:
    """Index either the general document corpus or the separate table metadata corpus."""
    if collection not in {"documents", "metadata"}:
        raise ValueError("collection must be 'documents' or 'metadata'.")
    output = _run_document_tool("50_index_documents.py", "--collection", collection,
                                timeout=7200)
    # The worker reports progress first and emits one JSON summary as its final record.
    lines = [line for line in output.splitlines() if line.startswith("{")]
    if not lines:
        return {"success": True, "output": output[-5000:]}
    return json.loads(lines[-1])


@mcp.tool()
def rag_search_documents(query: str, limit: int = 5, min_score: float = 0.0,
                         collection: str = "documents") -> dict:
    """Search a selected Qdrant collection and return source/page/section references."""
    if not query.strip() or len(query) > 1000:
        raise ValueError("query must contain 1–1000 characters.")
    if not 1 <= limit <= 20 or not 0.0 <= min_score <= 1.0:
        raise ValueError("limit must be 1–20 and min_score must be between 0 and 1.")
    if collection not in {"documents", "metadata"}:
        raise ValueError("collection must be 'documents' or 'metadata'.")
    output = _run_document_tool("52_search_documents.py", query, "--limit", limit,
                                "--min-score", min_score, "--collection", collection,
                                timeout=300)
    return json.loads(output)


@mcp.tool()
def rag_read_source(source_path: str, page: int | None = None,
                max_chars: int = 12000) -> dict:
    """Read an original Markdown, text, or PDF source by path relative to the configured document folder."""
    if not source_path or len(source_path) > 1000:
        raise ValueError("source_path must contain 1–1000 characters.")
    if page is not None and page < 1:
        raise ValueError("PDF page numbers start at 1.")
    if not 1 <= max_chars <= 20000:
        raise ValueError("max_chars must be between 1 and 20000.")
    # Path confinement and PDF page bounds are checked again in the worker.
    args = [source_path, "--max-chars", max_chars]
    if page is not None:
        args.extend(["--page", page])
    return json.loads(_run_document_tool("51_read_source.py", *args, timeout=300))


if __name__ == "__main__":
    mcp.run(transport="stdio")
