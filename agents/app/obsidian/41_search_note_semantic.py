#!/usr/bin/env python3
"""Search the JSON semantic index created by `30_build_semantic_index.py`.

This worker embeds one query, scores it against the local stored vectors, and
returns the highest-scoring note chunks. It does not contact Qdrant; that is a
separate document collection and uses the RAG workers.
"""

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Preserve direct CLI execution after the worker move by locating the shared
# runtime configuration relative to the repository, not the working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = PROJECT_ROOT / "mcp"
sys.path.insert(0, str(MCP_DIR))
from runtime_config import get_path, get_server_url, get_value

VAULT_DEFAULT = get_path("vault")
ENDPOINT_DEFAULT = get_server_url("embeddings_url")


def embed(endpoint, query):
    """Embed a single query using the same configured service as index building."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"input": query}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:800]
        raise RuntimeError(f"Embedding API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach {endpoint}: {exc}") from exc

    data = result.get("data")
    if not data or not isinstance(data[0].get("embedding"), list):
        raise RuntimeError(f"Unexpected embeddings response: {str(result)[:800]}")
    return [float(value) for value in data[0]["embedding"]]


def cosine(a, b):
    """Compute cosine similarity and reject vectors from incompatible models."""
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: query={len(a)}, index={len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    # Zero vectors have no direction, so treat them as non-matches.
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def main():
    """Resolve the configured index, rank matching chunks, and format the results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="+", help="Natural-language query")
    parser.add_argument("--vault", type=Path, default=VAULT_DEFAULT)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--endpoint", default=ENDPOINT_DEFAULT)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--context", type=int, default=300)
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    vault = args.vault.expanduser().resolve()
    configured_index = Path(get_value("paths", "semantic_index")).expanduser()
    index_path = (
        args.index or (configured_index if configured_index.is_absolute()
                       else vault / configured_index)
    ).resolve()
    if not index_path.is_file():
        parser.error(f"Index not found: {index_path}. Run 30_build_semantic_index.py first.")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    records = index.get("records", [])
    if not records:
        parser.error("Index contains no records.")

    query = " ".join(args.query)
    query_vector = embed(args.endpoint, query)
    expected_dimensions = index.get("embedding_dimensions")
    if expected_dimensions and len(query_vector) != expected_dimensions:
        parser.error(
            "Embedding dimensions differ. Use the same model and rebuild the index "
            "if the model changed."
        )

    results = []
    for record in records:
        score = cosine(query_vector, record["embedding"])
        if score >= args.min_score:
            results.append({
                "score": score,
                "path": record["path"],
                "title": record["title"],
                "chunk": record["chunk"],
                "text": record["text"],
            })

    # Stable source order is unnecessary here; semantic score alone defines rank.
    results.sort(key=lambda result: result["score"], reverse=True)
    results = results[:args.limit]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f"Semantic search: {query}\nResults: {len(results)}\n")
    for rank, result in enumerate(results, 1):
        snippet = re.sub(r"\s+", " ", result["text"]).strip()
        if args.context > 0 and len(snippet) > args.context:
            snippet = snippet[:args.context].rsplit(" ", 1)[0] + "…"
        print(
            f"{rank}. [{result['score']:.4f}] {result['title']} — chunk {result['chunk']}\n"
            f"   {vault / result['path']}\n   {snippet}\n"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
