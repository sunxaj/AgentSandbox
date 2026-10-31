"""Chunk CSV documents into retrieval-ready sections.

Strategy:
  Raw CSV syntax (commas, quoted fields) is not meaningful to an embedding
  model. Each row is serialised to a "field: value" natural-language string so
  the embedding captures the actual content rather than punctuation patterns.

  Short rows are batched together up to max_chars so the embedding model sees
  enough context per chunk. Rows that exceed max_chars on their own are passed
  through split_text's sliding window.

  The header row is prepended to every batch so each chunk is self-contained
  and a search hit can be understood without the surrounding rows.
"""

import csv
import io

from chunk_max_chars import Section, split_text


def _row_to_text(headers: list[str], row: list[str]) -> str:
    """Serialise one CSV row as 'field: value' pairs on separate lines."""
    pairs = [f"{h.strip()}: {v.strip()}" for h, v in zip(headers, row) if v.strip()]
    return "\n".join(pairs)


def chunk_csv(text: str, max_chars: int, overlap: int) -> list[Section]:
    """Return Section list for one CSV document."""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = rows[0]
    header_line = "Columns: " + ", ".join(h.strip() for h in headers)
    sections: list[Section] = []
    current_lines: list[str] = []
    current_len = len(header_line) + 2  # account for the prepended header

    for row in rows[1:]:
        line = _row_to_text(headers, row)
        if not line:
            continue
        if current_lines and current_len + len(line) + 1 > max_chars:
            batch = header_line + "\n\n" + "\n\n".join(current_lines)
            for chunk in split_text(batch, max_chars, overlap):
                sections.append(Section(page=None, section="", body=chunk))
            current_lines = []
            current_len = len(header_line) + 2
        current_lines.append(line)
        current_len += len(line) + 1

    if current_lines:
        batch = header_line + "\n\n" + "\n\n".join(current_lines)
        for chunk in split_text(batch, max_chars, overlap):
            sections.append(Section(page=None, section="", body=chunk))

    return sections
