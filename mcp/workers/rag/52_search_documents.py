#!/usr/bin/env python3
"""Search the configured Qdrant document collection by semantic similarity.

The embedding query uses the same configured endpoint as indexing. Search hits
return stored provenance fields so an agent can open the original source next.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_DIR))
from rag_config import load_rag_config


def request_json(url, method="GET", body=None, api_key="", timeout=60):
    """Call a JSON API and turn network errors into readable worker failures."""
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


def main():
    """Embed one bounded query, ask Qdrant for ranked points, and shape citations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument(
        "--collection",
        choices=("documents", "metadata"),
        default="documents",
        help="Search the general document corpus or the table metadata corpus.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=0.0)
    args = parser.parse_args()
    if not args.query.strip() or len(args.query) > 1000:
        raise ValueError("query must contain 1–1000 characters")
    if not 1 <= args.limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if not 0 <= args.min_score <= 1:
        raise ValueError("min_score must be between 0 and 1")

    config = load_rag_config(args.collection)
    # Query vectors must come from the same endpoint/model as stored document vectors.
    embedded = request_json(config["embeddings_url"], "POST", {"input": [args.query]}, timeout=180)
    data = embedded.get("data")
    if not isinstance(data, list) or len(data) != 1 or not data[0].get("embedding"):
        raise RuntimeError(f"Unexpected embeddings response: {str(embedded)[:500]}")
    vector = [float(value) for value in data[0]["embedding"]]
    collection = urllib.parse.quote(config["collection"], safe="")
    # Request payload but not vectors: payload contains the source citation and chunk text.
    result = request_json(
        f"{config['qdrant_url']}/collections/{collection}/points/search",
        "POST",
        {"vector": vector, "limit": args.limit, "score_threshold": args.min_score,
         "with_payload": True, "with_vectors": False},
        config["qdrant_api_key"], timeout=120)
    hits = result.get("result")
    if not isinstance(hits, list):
        raise RuntimeError(f"Unexpected Qdrant search response: {str(result)[:500]}")
    results = []
    for hit in hits:
        payload = hit.get("payload") or {}
        results.append({
            "score": hit.get("score"),
            "source_path": payload.get("source_path"),
            "file_name": payload.get("file_name"),
            "file_type": payload.get("file_type"),
            "title": payload.get("title"),
            "page": payload.get("page"),
            "section": payload.get("section"),
            "chunk": payload.get("chunk"),
            "text": payload.get("text", ""),
        })
    print(json.dumps({"results": results, "count": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
