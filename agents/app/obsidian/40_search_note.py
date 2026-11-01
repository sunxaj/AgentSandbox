#!/usr/bin/env python3
"""
Phase A: keyword search across the local knowledge base.

Deterministic, local, no LLM. Searches configured knowledge and topic folders,
the staged Markdown imports, and the vault Inbox. Optionally searches raw JSONL
exports from the configured conversation export directory (--jsonl).

Usage examples:
  python3 40_search_knowledge.py "mcp server"
  python3 40_search_knowledge.py "llama server" --any
  python3 40_search_knowledge.py "llama server" --all
  python3 40_search_knowledge.py "context" --limit 3 --context 2
  python3 40_search_knowledge.py "mcp" --jsonl --quiet
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Resolve the agent-owned runtime configuration after moving this read-only
# Obsidian worker into the agent application.
OBSIDIAN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OBSIDIAN_DIR))
from runtime_config import get_path, get_vault_folder

VAULT = get_path("vault")
SOURCE_JSONL_DIR = get_path("conversation_exports")
IMPORT_DIR = get_path("import_output")

# (label, directory) pairs. Display order follows this list.
SEARCH_DIRS = [
    ("Knowledge", get_vault_folder("knowledge")),
    ("Topics", get_vault_folder("topics")),
    ("Transcripts", IMPORT_DIR),
    ("Vault Inbox", get_vault_folder("inbox")),
]


def extract_title(path):
    """Return the note title from YAML frontmatter, then '# ' heading, then stem."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path.stem

    # Only consider the YAML frontmatter block (between the first two '---').
    fm = ""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            fm = text[3:end]

    m = re.search(r"^title:\s*(\"[^\"]*\"|'[^']*'|[^#\n]+)", fm, re.M)
    if m:
        val = m.group(1).strip().strip("\"'")
        if val:
            return val

    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()

    return path.stem


def jsonl_title(path):
    """Best-effort conversation name from the first JSONL metadata record."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            first = f.readline()
        data = json.loads(first)
        name = data.get("name")
        if name:
            return str(name)
    except Exception:
        pass
    return path.stem


def line_matches(line, query, mode):
    """Apply one of the public matching modes to a single line."""
    low = line.lower()
    if mode == "phrase":
        return query.lower() in low
    words = [w for w in query.split() if w]
    if not words:
        return False
    if mode == "any":
        return any(w.lower() in low for w in words)
    return all(w.lower() in low for w in words)


def search_file(path, query, mode, context, limit):
    """Return (match_count, lines, shown_line_numbers, chosen_match_numbers)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    match_indices = [i for i, line in enumerate(lines) if line_matches(line, query, mode)]
    if not match_indices:
        return 0, lines, [], set()

    chosen = match_indices[:limit]
    shown = set()
    for i in chosen:
        shown.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    return len(match_indices), lines, sorted(shown), set(chosen)

def relevance_score(path, query, lines):
    """Calculate a deterministic relevance score for a matching file."""
    phrase = query.strip().casefold()
    words = list(dict.fromkeys(
        word.casefold() for word in query.split() if word
    ))

    title = extract_title(path).casefold()
    headings = [
        line.lstrip("#").strip().casefold()
        for line in lines
        if re.match(r"^\s{0,3}#{1,6}\s+", line)
    ]

    text = "\n".join(lines).casefold()

    score = 0.0

    # Titles/headings are stronger evidence of relevance than an incidental body hit.
    if phrase and phrase in title:
        score += 10

    if phrase and any(phrase in heading for heading in headings):
        score += 6

    # Exact phrase in the document body.
    body_lines = [
        line.casefold()
        for line in lines
        if not re.match(r"^\s{0,3}#{1,6}\s+", line)
    ]
    phrase_occurrences = sum(
        line.count(phrase) for line in body_lines
    ) if phrase else 0

    if phrase_occurrences:
        score += 4

    # Bonus when all query words occur somewhere in the document.
    if words and all(word in text for word in words):
        score += 2

    # Small capped bonus for repeated phrase occurrences.
    score += min(max(phrase_occurrences - 1, 0), 5) * 0.5

    return round(score, 1)

def main():
    """Search configured folders locally, rank matching files, and print snippets."""
    parser = argparse.ArgumentParser(
        description="Keyword search across knowledge notes, topic indexes, and raw transcripts."
    )
    parser.add_argument("query", help="Search text (case-insensitive).")
    # Phrase matching is the default; these mutually exclusive flags select broader
    # OR/AND matching without making the search depend on an LLM.
    match_group = parser.add_mutually_exclusive_group()
    match_group.add_argument(
        "--any", action="store_true",
        help="Match if ANY query word appears (OR)."
    )
    match_group.add_argument(
        "--all", action="store_true",
        help="Match if ALL query words appear (AND)."
    )
    parser.add_argument("--limit", type=int, default=5,
                        help="Max matching lines to show per file (default 5).")
    parser.add_argument("--context", type=int, default=1,
                        help="Context lines around each match (default 1).")
    parser.add_argument("--jsonl", action="store_true",
                        help="Also search raw JSONL source files.")
    parser.add_argument("--include", nargs="*", default=[],
                        help="Only search these labels (Knowledge, Topics, Transcripts, Source JSONL).")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Skip these labels.")
    parser.add_argument("--quiet", action="store_true",
                        help="Print only matching file paths.")
    parser.add_argument("--count", action="store_true",
                        help="Print only per-file match counts.")
    args = parser.parse_args()
    if not args.query.strip():
        parser.error("query cannot be empty")

    if args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.context < 0:
        parser.error("--context cannot be negative")

    mode = "any" if args.any else ("all" if args.all else "phrase")

    dirs = list(SEARCH_DIRS)
    if args.jsonl:
        dirs.append(("Source JSONL", SOURCE_JSONL_DIR))

    include = {x.lower() for x in args.include}
    exclude = {x.lower() for x in args.exclude}

    results = []
    total_files = 0
    total_matches = 0

    for label, directory in dirs:
        if include and label.lower() not in include:
            continue
        if label.lower() in exclude:
            continue
        if not directory.exists():
            continue

        pattern = "*.jsonl" if label == "Source JSONL" else "*.md"

        # Recursive search matches the nested structure used by conversation imports.
        for path in sorted(directory.rglob(pattern)):
            total_files += 1

            try:
                count, lines, shown, chosen = search_file(
                    path, args.query, mode, args.context, args.limit
                )
            except OSError as exc:
                print(f"Warning: cannot read {path}: {exc}")
                continue

            if count == 0:
                continue

            total_matches += count

            score = relevance_score(path, args.query, lines)

            title = (
                jsonl_title(path)
                if label == "Source JSONL"
                else extract_title(path)
            )

            results.append({
                "label": label,
                "path": path,
                "title": title,
                "count": count,
                "lines": lines,
                "shown": shown,
                "chosen": chosen,
                "score": score,
            })

    # Stable tie-breakers keep results reproducible when relevance scores are equal.
    results.sort(
        key=lambda r: (
            -r["score"],
            r["label"].casefold(),
            str(r["path"]).casefold(),
        )
    )

    if not results:
        print(f"No matches for {args.query!r} across {total_files} files.")
        return

    for result in results:
        path = result["path"]

        if args.quiet:
            print(path)
            continue

        if args.count:
            print(
                f'{result["score"]:>5.1f}  '
                f'{result["count"]:>4}  '
                f'{result["label"]}: {path.name}'
            )
            continue

        print(f'[{result["label"]}] {path.name}')
        print(f'  title: {result["title"]}')
        print(f'  relevance: {result["score"]:.1f}')
        print(f'  matches: {result["count"]}')

        for i in result["shown"]:
            marker = "  > " if i in result["chosen"] else "     "
            print(f"{marker}{i + 1:>5}: {result['lines'][i][:120]}")

        print()

    print(
        f"Done. {len(results)} matching files; "
        f"{total_matches} matching lines across "
        f"{total_files} files searched."
    )


if __name__ == "__main__":
    main()
