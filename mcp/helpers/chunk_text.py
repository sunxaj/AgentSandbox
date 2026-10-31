"""Chunk plain-text documents into retrieval-ready sections.

Strategy:
  Plain text has no structural markers, so the entire file is treated as one
  unit and passed directly to split_text for paragraph-aware size-based
  subdivision. If the file contains consistent structural patterns (e.g. log
  entries separated by timestamps, numbered sections, dialogue turns), consider
  adding a format-specific pre-splitter here rather than in 50_index_documents.
"""

from chunk_max_chars import Section, split_text


def chunk_text(text: str, max_chars: int, overlap: int) -> list[Section]:
    """Return Section list for one plain-text document."""
    return [
        Section(page=None, section="", body=chunk)
        for chunk in split_text(text, max_chars, overlap)
    ]
