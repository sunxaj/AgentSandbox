"""Chunk JSON documents into retrieval-ready sections.

Strategy:
  Raw JSON syntax (braces, quotes, colons) is noise for an embedding model.
  Each top-level item is serialised to a readable "key: value" string so the
  embedding captures content rather than structure.

  - Array of objects: each object becomes one unit; its keys become field labels.
  - Array of scalars: each value becomes one unit.
  - Single object: each top-level key becomes one unit.
  - Any other shape: the whole document is serialised as a single unit.

  Units are batched up to max_chars before embedding; oversized units are
  passed through split_text's sliding window.
"""

import json

from chunk_max_chars import Section, split_text


def _item_to_text(item) -> str:
    """Serialise one JSON value to a readable string."""
    if isinstance(item, dict):
        return "\n".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "", [], {}))
    return str(item)


def chunk_json(text: str, max_chars: int, overlap: int) -> list[Section]:
    """Return Section list for one JSON document."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if isinstance(data, list):
        items = [_item_to_text(item) for item in data]
    elif isinstance(data, dict):
        items = [f"{k}:\n{_item_to_text(v)}" for k, v in data.items()]
    else:
        items = [str(data)]

    sections: list[Section] = []
    current_parts: list[str] = []
    current_len = 0

    for item_text in items:
        if not item_text.strip():
            continue
        if current_parts and current_len + len(item_text) + 2 > max_chars:
            batch = "\n\n".join(current_parts)
            for chunk in split_text(batch, max_chars, overlap):
                sections.append(Section(page=None, section="", body=chunk))
            current_parts = []
            current_len = 0
        current_parts.append(item_text)
        current_len += len(item_text) + 2

    if current_parts:
        batch = "\n\n".join(current_parts)
        for chunk in split_text(batch, max_chars, overlap):
            sections.append(Section(page=None, section="", body=chunk))

    return sections
