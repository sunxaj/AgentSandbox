#!/usr/bin/env python3
"""Build hierarchical, validated topic indexes from Knowledge notes."""
import argparse
import atexit
import fcntl
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

# Keep shared configuration in the MCP package while locating this agent-owned
# worker from its new directory rather than relying on the current directory.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = PROJECT_ROOT / "mcp"
sys.path.insert(0, str(MCP_DIR))
from runtime_config import get_server_url, get_vault_folder

BASE_URL = get_server_url("bonsai_base_url")
KNOWLEDGE_DIR = get_vault_folder("knowledge")
TOPICS_DIR = get_vault_folder("topics")
REQUEST_TIMEOUT = 300
MAX_REDUCTION_LEVELS = 8
CACHE_FILENAME = ".topic_index_build_cache.json"
CACHE_VERSION = 1


def acquire_lock(path, label):
    """Prevent overlapping topic builds from racing on generated pages and cache state."""
    lock_file = path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError(f"Another {label} is already running.") from exc
    atexit.register(lock_file.close)
    return lock_file

SYSTEM_PROMPT = """
You organize a personal AI engineering knowledge base.

Group the supplied items into a small number of useful, non-overlapping topic
categories. Use only the supplied item IDs, titles, summaries, and member
titles. Do not invent topics or claim that notes are duplicates without evidence.

Return valid JSON only:
{
  "topics": [
    {
      "name": "Agents and MCP",
      "description": "Short description of this topic",
      "item_ids": ["exact-id-1", "exact-id-2"]
    }
  ]
}

Rules:
- Every item ID must match a supplied ID exactly.
- Assign each item to at most one topic; the caller will preserve omitted items.
- Prefer around 4-12 topics for a group of knowledge notes.
- Do not merge or delete source notes.
"""


def get_model():
    """Use the active Bonsai model ID for every generation request in this build."""
    req = urllib.request.Request(BASE_URL + "/models")
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.load(response)
    models = data.get("data", [])
    if not models or not models[0].get("id"):
        raise RuntimeError("Bonsai /models did not return a model id")
    return models[0]["id"]


def extract_title(text, fallback):
    """Prefer a Markdown H1 as the visible note title, falling back to its filename."""
    match = re.search(r"(?m)^# (.+)$", text)
    return match.group(1).strip() if match else fallback


def extract_summary(text):
    """Prepare a bounded opening excerpt so grouping requests stay small."""
    lines = text.splitlines()
    start = next(
        (i + 1 for i, line in enumerate(lines)
         if line.startswith("# ") and not line.startswith("## ")),
        0,
    )
    excerpt = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip() and not line.startswith("---"):
            excerpt.append(line.strip().lstrip("> "))
        if len(" ".join(excerpt)) > 350:
            break
    return " ".join(excerpt)[:350]


def save_cache(path, cache):
    """Atomically persist successful grouping calls for recovery after later failures."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def group_batch(model, items, stage, cache, cache_path):
    """Ask Bonsai to group a bounded batch and validate returned item IDs."""
    # Send only the model-facing fields; member paths stay local for validated linking.
    payload_items = [
        {key: item[key] for key in ("id", "title", "summary", "member_titles")
         if key in item}
        for item in items
    ]
    cache_key = hashlib.sha256(json.dumps({
        "version": CACHE_VERSION,
        "model": model,
        "stage": stage,
        "items": payload_items,
        "members": {item["id"]: item.get("members", []) for item in items},
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    # The cache key includes model, stage, summaries, and member IDs so incompatible
    # or stale model output is never reused for a different grouping request.
    if cache_key in cache:
        print(f"Reusing completed grouping: {stage}", flush=True)
        return cache[cache_key]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Group these items ({stage}) into topic indexes:\n"
                    + json.dumps(payload_items, ensure_ascii=False)
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 2500,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # A bounded timeout prevents a stalled local endpoint from holding the build forever.
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        result = json.load(response)

    try:
        content = result["choices"][0]["message"].get("content", "")
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Bonsai returned an invalid topic response ({stage}): {exc}") from exc

    topics = parsed.get("topics")
    if not isinstance(topics, list):
        raise RuntimeError(f"Bonsai topic response has no topics list ({stage})")

    # Accept only exact supplied IDs and assign each at most once; hallucinated IDs
    # and accidental duplicate assignments cannot create broken note links.
    by_id = {item["id"]: item for item in items}
    assigned = set()
    groups = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        name = str(topic.get("name", "")).strip()
        ids = topic.get("item_ids", [])
        if not name or not isinstance(ids, list):
            continue
        selected = []
        for item_id in ids:
            if isinstance(item_id, str) and item_id in by_id and item_id not in assigned:
                selected.append(by_id[item_id])
                assigned.add(item_id)
        if not selected:
            continue
        groups.append({
            "name": name,
            "description": str(topic.get("description", "")).strip(),
            "members": sorted({member for item in selected
                               for member in item.get("members", [])}),
            "member_titles": [title for item in selected
                              for title in item.get("member_titles", [item["title"]])],
        })

    # Never lose an input item if the model omits or mistypes an ID.
    for item in items:
        if item["id"] not in assigned:
            groups.append({
                "name": item["title"],
                "description": item["summary"],
                "members": list(item.get("members", [])),
                "member_titles": list(item.get("member_titles", [item["title"]])),
            })
    cache[cache_key] = groups
    save_cache(cache_path, {"format_version": CACHE_VERSION, "entries": cache})
    return groups


def initial_groups(model, records, batch_size, cache, cache_path):
    """Group note summaries in bounded requests before global consolidation."""
    groups = []
    batches = [records[i:i + batch_size]
               for i in range(0, len(records), batch_size)]
    for batch_number, batch in enumerate(batches, 1):
        print(f"Grouping note batch {batch_number}/{len(batches)} ({len(batch)} notes)",
              flush=True)
        groups.extend(group_batch(model, batch, f"note batch {batch_number}",
                                  cache, cache_path))
    return groups


def reduce_groups(model, groups, batch_size, cache, cache_path):
    """Merge candidate topics in bounded passes until one global set remains."""
    current = groups
    for level in range(1, MAX_REDUCTION_LEVELS + 1):
        if len(current) <= 1:
            break
        batches = [current[i:i + batch_size]
                   for i in range(0, len(current), batch_size)]
        next_groups = []
        for batch_number, batch in enumerate(batches, 1):
            items = []
            for i, group in enumerate(batch, 1):
                items.append({
                    "id": f"level-{level}-batch-{batch_number}-item-{i}",
                    "title": group["name"],
                    "summary": group["description"],
                    "member_titles": group["member_titles"][:40],
                    "members": group["members"],
                })
            print(
                f"Consolidating topic level {level}, batch "
                f"{batch_number}/{len(batches)} ({len(batch)} groups)",
                flush=True,
            )
            next_groups.extend(group_batch(
                model, items, f"topic consolidation level {level}, batch {batch_number}",
                cache, cache_path
            ))

        # Stop if a level does not reduce the candidate count; this prevents an
        # unproductive loop and preserves the best groups already produced.
        if len(next_groups) >= len(current):
            print("Topic consolidation stopped because grouping did not reduce "
                  "the candidate count.")
            break
        current = next_groups
    return current


def merge_slug_collisions(groups):
    """Combine topic names that normalize to the same Obsidian filename."""
    merged = {}
    for group in groups:
        slug = re.sub(r"[^a-z0-9]+", "-", group["name"].lower()).strip("-") or "topic"
        if slug not in merged:
            merged[slug] = dict(group)
            merged[slug]["members"] = list(group["members"])
            merged[slug]["member_titles"] = list(group["member_titles"])
        else:
            merged[slug]["members"] = sorted(set(merged[slug]["members"])
                                               | set(group["members"]))
            merged[slug]["member_titles"] = list(dict.fromkeys(
                merged[slug]["member_titles"] + group["member_titles"]))
    return merged


def main():
    """Read Knowledge notes, build topics hierarchically, and refresh generated pages only."""
    parser = argparse.ArgumentParser(
        description="Build hierarchical topic indexes in bounded Bonsai requests."
    )
    parser.add_argument("--refresh", action="store_true",
                        help="Replace existing pages marked type: topic-index.")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="Maximum notes or candidate groups per model request.")
    args = parser.parse_args()
    if args.batch_size < 2:
        raise SystemExit("--batch-size must be at least 2")
    if not KNOWLEDGE_DIR.exists():
        raise SystemExit(f"Knowledge folder not found: {KNOWLEDGE_DIR}")

    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if not files:
        raise SystemExit("No Markdown knowledge notes found.")

    records = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        title = extract_title(text, path.stem)
        records.append({
            "id": path.name,
            "title": title,
            "summary": extract_summary(text),
            "members": [path.name],
            "member_titles": [title],
        })

    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    acquire_lock(TOPICS_DIR / ".topic_index_build.lock", "topic index build")
    cache_path = TOPICS_DIR / CACHE_FILENAME
    cache = {}
    if cache_path.is_file():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            entries = loaded.get("entries")
            if (loaded.get("format_version") == CACHE_VERSION
                    and isinstance(entries, dict)):
                cache = entries
        except (OSError, json.JSONDecodeError, AttributeError):
            print(f"Ignoring unreadable topic build cache: {cache_path}")
    # Only generated pages participate in stale detection; manually authored pages
    # are never treated as disposable output.
    existing_generated = {
        path.stem for path in TOPICS_DIR.glob("*.md")
        if 'type: "topic-index"' in path.read_text(encoding="utf-8")
    }

    model = get_model()
    groups = initial_groups(model, records, args.batch_size, cache, cache_path)
    if len(records) > args.batch_size:
        groups = reduce_groups(model, groups, args.batch_size, cache, cache_path)
    grouped = merge_slug_collisions(groups)
    titles_by_filename = {record["id"]: record["title"] for record in records}

    written = 0
    generated_slugs = set(grouped)
    for slug, topic in grouped.items():
        name = topic["name"]
        matched = [filename for filename in topic["members"]
                   if filename in titles_by_filename]
        if not matched:
            continue
        destination = TOPICS_DIR / f"{slug}.md"

        if destination.exists():
            # Refresh may replace generated pages but deliberately preserves user pages.
            existing = destination.read_text(encoding="utf-8")
            if not args.refresh or 'type: "topic-index"' not in existing:
                print(f"SKIP existing topic: {destination.name}")
                continue

        lines = [
            "---",
            f"title: {json.dumps(name, ensure_ascii=False)}",
            'type: "topic-index"',
            "tags:",
            "  - topic-index",
            "---",
            "",
            f"# {name}",
            "",
            topic["description"],
            "",
            "## Knowledge notes",
            "",
        ]
        for filename in matched:
            title = titles_by_filename[filename].replace("]", "\\]")
            lines.append(f"- [[{KNOWLEDGE_DIR.name}/{filename}|{title}]]")
        lines.extend([
            "", "## Review", "",
            "- [ ] Check that these notes belong together",
            "- [ ] Add links to related topic indexes", "",
        ])
        destination.write_text("\n".join(lines), encoding="utf-8")
        written += 1
        print(f"CREATED: {destination.name} ({len(matched)} notes)")

    # Stale generated topics are reported for review instead of deleted automatically.
    stale = sorted(existing_generated - generated_slugs)
    for slug in stale:
        print(f"STALE generated topic (not deleted): {slug}.md")

    print(f"\nFinished. Created or refreshed {written} topic index files.")
    print(f"Stale generated topic pages left for review: {len(stale)}")
    print(f"Open this folder in Obsidian: {TOPICS_DIR}")
    cache_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
