#!/usr/bin/env python3
"""Extract, chunk, embed, and upsert configured source documents into Qdrant.

This worker owns the write path for the separate document collection. It
prepares the desired points before changing Qdrant, checkpoints expensive
embedding work for retries, and never edits source documents.
"""

import argparse
import fcntl
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_DIR))
sys.path.insert(0, str(MCP_DIR / "helpers"))
from rag_config import load_rag_config
from chunk_md import chunk_md
from chunk_pdf import chunk_pdf
from chunk_text import chunk_text
from chunk_csv import chunk_csv
from chunk_json import chunk_json
from chunk_yaml import chunk_yaml
from chunk_html import chunk_html
from chunk_max_chars import Section

NAMESPACE = uuid.UUID("37637c75-c087-4b3b-8e32-a610c5aed125")


def config():
    """Load source, chunk, endpoint, and collection settings from rag.conf."""
    return load_rag_config()


def request_json(url, method="GET", body=None, api_key="", timeout=60):
    """Make one JSON REST call and include bounded server details on failures."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc}") from exc


def embed(endpoint, texts):
    """Request vectors in input order; fail before Qdrant writes on bad responses."""
    result = request_json(endpoint, "POST", {"input": texts}, timeout=180)
    data = result.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError(f"Unexpected embeddings response for batch of {len(texts)}: {str(result)[:500]}")
    data.sort(key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding") for item in data]
    if any(not isinstance(vector, list) or not vector for vector in vectors):
        raise RuntimeError("Embedding response contains a missing vector")
    return [[float(value) for value in vector] for vector in vectors]


# Dispatch table: file suffix → chunker callable.
# chunk_pdf returns (sections, empty_pages); all others return sections only.
_CHUNKERS = {
    ".md":   ("text", chunk_md),
    ".txt":  ("text", chunk_text),
    ".csv":  ("text", chunk_csv),
    ".json": ("text", chunk_json),
    ".yaml": ("text", chunk_yaml),
    ".yml":  ("text", chunk_yaml),
    ".html": ("text", chunk_html),
    ".pdf":  ("pdf",  chunk_pdf),
}


def file_hash(path):
    """Hash source bytes so each payload identifies the exact source revision."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(path, max_chars, overlap):
    """Dispatch to the correct format chunker and return (kind, sections, empty_pages)."""
    suffix = path.suffix.lower()
    entry = _CHUNKERS.get(suffix)
    if entry is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    kind, chunker = entry
    if kind == "pdf":
        sections, empty = chunker(path, max_chars, overlap)
        return "pdf", sections, empty
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = chunker(text, max_chars, overlap)
    return suffix.lstrip("."), sections, []


def acquire_lock(path):
    """Prevent concurrent rebuilds from interleaving checkpoints and collection writes."""
    stream = path.open("a", encoding="utf-8")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        stream.close()
        raise RuntimeError("Another document index build is already running") from exc
    return stream


def main():
    """Scan the configured corpus, embed chunks, sync Qdrant, and report provenance stats."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collection",
        choices=("documents", "metadata"),
        default="documents",
        help="Index the general document corpus or the table metadata corpus.",
    )
    args = parser.parse_args()
    settings = load_rag_config(args.collection)
    source = settings["source"]
    if not source.is_dir():
        raise FileNotFoundError(f"Document source folder does not exist: {source}")
    if settings["max_chars"] < 1 or not 0 <= settings["overlap"] < settings["max_chars"]:
        raise ValueError("Chunk settings need max_chars > overlap_chars >= 0")
    if settings["batch_size"] < 1:
        raise ValueError("embedding_batch_size must be at least 1")
    collection = settings["collection"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", collection):
        raise ValueError("Collection name may contain only letters, digits, underscore, or hyphen")

    # The hidden lock is placed beside the source so operators can find it without
    # letting the recursive file scan treat it as an input document.
    lock = acquire_lock(source / ".document_index.lock")
    try:
        # Ignore hidden state, unsupported suffixes, and symlinks that could escape the source root.
        files = sorted(path for path in source.rglob("*")
                       if path.is_file() and not path.is_symlink()
                       and not path.name.startswith(".")
                       and path.suffix.lower() in settings["extensions"])
        if not files:
            raise RuntimeError(f"No supported documents found under {source}")

        records, texts, empty_pages, errors = [], [], [], []
        for path in files:
            relative = path.relative_to(source).as_posix()
            try:
                kind, sections, empty = extract(path, settings["max_chars"], settings["overlap"])
                digest = file_hash(path)
                for number, sec in enumerate(sections, 1):
                    if not sec.body:
                        continue
                    point_id = str(uuid.uuid5(NAMESPACE, f"{relative}\0{sec.page}\0{sec.section}\0{number}"))
                    payload = {
                        "source_path": relative, "file_name": path.name,
                        "file_type": kind, "title": path.stem,
                        "page": sec.page, "section": sec.section or None,
                        "chunk": number, "sha256": digest, "text": sec.body,
                    }
                    records.append({"id": point_id, "payload": payload})
                    texts.append(f"Title: {path.stem}\nSource: {relative}\n" +
                                 (f"Page: {sec.page}\n" if sec.page else "") +
                                 (f"Section: {sec.section}\n" if sec.section else "") + f"\n{sec.body}")
                empty_pages.extend({"file": relative, "page": page} for page in empty)
            except Exception as exc:
                errors.append(f"{relative}: {exc}")
        if errors:
            raise RuntimeError("Some documents could not be read; Qdrant was not changed: " + "; ".join(errors[:20]))
        if not records:
            raise RuntimeError("No extractable text found; Qdrant was not changed")

        # A fingerprint ties reusable vectors to exact chunk text and the endpoint.
        checkpoint_path = source / ".document_index_checkpoint.json"
        fingerprint = hashlib.sha256(json.dumps({
            "endpoint": settings["embeddings_url"],
            "records": [{"id": record["id"], "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
                        for record, text in zip(records, texts)],
        }, sort_keys=True).encode("utf-8")).hexdigest()
        vectors = [None] * len(texts)
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            saved = checkpoint.get("vectors")
            if (checkpoint.get("fingerprint") == fingerprint
                    and isinstance(saved, list) and len(saved) == len(vectors)):
                vectors = saved
                print("Resuming document embeddings from checkpoint.", flush=True)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

        def save_checkpoint():
            """Atomically save embedded vectors so interrupted builds can resume."""
            temporary = checkpoint_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fingerprint": fingerprint, "vectors": vectors}), encoding="utf-8")
            temporary.replace(checkpoint_path)

        # Persist every completed batch so transient service failures do not repeat inference.
        for start in range(0, len(texts), settings["batch_size"]):
            stop = min(start + settings["batch_size"], len(texts))
            missing = [index for index in range(start, stop)
                       if not isinstance(vectors[index], list) or not vectors[index]]
            if missing:
                batch_vectors = embed(settings["embeddings_url"], [texts[index] for index in missing])
                for index, vector in zip(missing, batch_vectors):
                    vectors[index] = vector
                save_checkpoint()
            print(f"Embedded {stop}/{len(texts)} chunks", flush=True)
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or len(vectors) != len(records):
            raise RuntimeError("Embedding dimensions/count are inconsistent")

        qdrant = settings["qdrant_url"]
        api_key = settings["qdrant_api_key"]
        encoded = urllib.parse.quote(collection, safe="")
        collection_url = f"{qdrant}/collections/{encoded}"
        try:
            current = request_json(collection_url, api_key=api_key)
            current_size = current.get("result", {}).get("config", {}).get("params", {}).get("vectors", {}).get("size")
            if current_size != len(vectors[0]):
                raise RuntimeError(f"Qdrant collection {collection!r} has vector size {current_size}; embeddings have {len(vectors[0])}. Choose a new collection or resolve the mismatch.")
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
            request_json(collection_url, "PUT", {"vectors": {"size": len(vectors[0]), "distance": "Cosine"}}, api_key)

        # Upload current IDs before pruning stale ones; failed preparation therefore
        # cannot leave the collection empty or remove points it still needs.
        for start in range(0, len(records), 128):
            stop = min(start + 128, len(records))
            points = [{**record, "vector": vector} for record, vector in zip(records[start:stop], vectors[start:stop])]
            request_json(f"{collection_url}/points?wait=true", "PUT", {"points": points}, api_key, timeout=180)
            print(f"Uploaded {stop}/{len(records)} chunks", flush=True)

        # Remove obsolete chunks only after Qdrant has acknowledged all current chunks.
        desired = {record["id"] for record in records}
        stale = []
        offset = None
        while True:
            body = {"limit": 256, "with_payload": False, "with_vectors": False}
            if offset is not None:
                body["offset"] = offset
            page = request_json(f"{collection_url}/points/scroll", "POST", body, api_key)
            result = page.get("result", {})
            points = result.get("points", [])
            stale.extend(point["id"] for point in points if str(point["id"]) not in desired)
            offset = result.get("next_page_offset")
            if offset is None or not points:
                break
        for start in range(0, len(stale), 256):
            request_json(f"{collection_url}/points/delete?wait=true", "POST",
                         {"points": stale[start:start + 256]}, api_key, timeout=180)

        checkpoint_path.unlink(missing_ok=True)

        print(json.dumps({"success": True, "collection": collection, "source": str(source),
                          "files": len(files), "chunks": len(records), "embedding_dimensions": len(vectors[0]),
                          "empty_pdf_pages": empty_pages, "stale_chunks_removed": len(stale)}, ensure_ascii=False))
    finally:
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
