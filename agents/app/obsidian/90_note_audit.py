"""Report simple maintenance signals for generated Knowledge Markdown notes.

This is a read-only audit helper: it counts files, duplicate H1 titles, large
notes, and notes with little content without changing the vault.
"""

import sys
from pathlib import Path
from collections import Counter

# This audit runs on the agent side; load the sibling runtime module explicitly
# so direct execution does not depend on the current directory.
OBSIDIAN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OBSIDIAN_DIR))
from runtime_config import get_vault_folder

knowledge = get_vault_folder("knowledge")

# Audit only top-level Knowledge notes, matching the extraction/indexing convention.
files = sorted(knowledge.glob("*.md"))
print("Knowledge notes:", len(files))

titles = Counter()
# Normalize titles for comparison so capitalization differences still surface.
for path in files:
    text = path.read_text(encoding="utf-8")
    title = next(
        (line[2:].strip() for line in text.splitlines()
         if line.startswith("# ") and not line.startswith("## ")),
        path.stem,
    )
    titles[title.lower()] += 1

print("\nDuplicate titles:")
duplicates = [(title, count) for title, count in titles.items() if count > 1]
if duplicates:
    for title, count in duplicates:
        print(f"  {count}x  {title}")
else:
    print("  None found")

# File size is a cheap review signal; it does not imply that a long note is wrong.
print("\nLargest notes:")
for path in sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:10]:
    print(f"  {path.stat().st_size:>8} bytes  {path.name}")

# Strip YAML frontmatter before applying the body-length heuristic.
print("\nNotes with little content:")
for path in files:
    text = path.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
    if len(body) < 250:
        print(" ", path.name)
