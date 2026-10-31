"""Runtime configuration for the separate document RAG collection."""

import configparser
import os
from pathlib import Path

from runtime_config import get_server_url

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
RAG_CONFIG = MCP_DIR / "config" / "rag.conf"


def load_rag_config(target="documents"):
    """Load one RAG target while keeping document and table metadata indexes separate."""
    parser = configparser.ConfigParser()
    if not RAG_CONFIG.is_file():
        raise FileNotFoundError(f"RAG configuration not found: {RAG_CONFIG}")
    parser.read(RAG_CONFIG, encoding="utf-8")
    # Keep source location, collection, and Qdrant credentials independent of code.
    if target not in {"documents", "metadata"}:
        raise ValueError("RAG target must be 'documents' or 'metadata'")
    if target == "metadata":
        source_value = os.getenv("METADATA_SOURCE_DIR", parser.get("paths", "metadata_source"))
        collection_value = os.getenv(
            "METADATA_COLLECTION",
            parser.get("document_rag", "metadata_collection", fallback="metadata"),
        )
    else:
        source_value = os.getenv("DOCUMENT_SOURCE_DIR", parser.get("paths", "document_source"))
        collection_value = os.getenv(
            "DOCUMENT_COLLECTION",
            parser.get("document_rag", "collection", fallback="documents"),
        )
    source = Path(source_value).expanduser()
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    extensions = {ext.strip().lower() for ext in parser.get(
        "document_rag", "supported_extensions", fallback=".md, .txt, .pdf").split(",")}
    return {
        "source": source.resolve(),
        "extensions": extensions,
        "max_chars": parser.getint("chunking", "max_chars", fallback=500),
        "overlap": parser.getint("chunking", "overlap_chars", fallback=100),
        "batch_size": parser.getint("chunking", "embedding_batch_size", fallback=8),
        "collection": collection_value,
        "qdrant_url": os.getenv("QDRANT_URL", parser.get("servers", "qdrant_url")).rstrip("/"),
        "qdrant_api_key": os.getenv("QDRANT_API_KEY", parser.get("servers", "qdrant_api_key", fallback="")),
        "embeddings_url": get_server_url("embeddings_url"),
    }
