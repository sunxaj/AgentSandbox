"""Chunk YAML documents into retrieval-ready sections.

Strategy:
  Mirrors chunk_json: YAML is parsed and each top-level item is serialised to a
  readable "key: value" string. The same batching and sliding-window logic
  applies so max_chars / overlap_chars config values are respected.

  - Mapping: each top-level key becomes one unit.
  - Sequence: each item becomes one unit.
  - Scalar or other: the whole document is one unit.

  PyYAML is used for parsing; it is not in requirements-mcp.txt by default.
  If it is absent the worker falls back to treating the file as plain text via
  chunk_text so indexing does not hard-fail on a missing optional dependency.
"""

from chunk_max_chars import Section, split_text


def _item_to_text(item) -> str:
    """Serialise one YAML value to a readable string."""
    if isinstance(item, dict):
        return "\n".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "", [], {}))
    return str(item)


def chunk_yaml(text: str, max_chars: int, overlap: int) -> list[Section]:
    """Return Section list for one YAML document.

    Falls back to plain-text chunking if PyYAML is not installed so the
    pipeline degrades gracefully rather than refusing to index the file.
    """
    try:
        import yaml  # optional dependency
        data = yaml.safe_load(text)
    except ImportError:
        # PyYAML absent: treat as plain text so indexing still works.
        from chunk_text import chunk_text
        return chunk_text(text, max_chars, overlap)
    except Exception as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if isinstance(data, list):
        items = [_item_to_text(item) for item in data]
    elif isinstance(data, dict):
        items = [f"{k}:\n{_item_to_text(v)}" for k, v in data.items()]
    else:
        items = [str(data)] if data is not None else []

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
