"""Shared text cleaning and size-based chunking used by all format-specific helpers.

split_text() is the single source of truth for the greedy paragraph-packing
algorithm. It splits oversized paragraphs at sentence boundaries, then word
boundaries, so text is never cut mid-sentence (or mid-word). Every format
chunker calls it after producing its own structural units so the
max_chars / overlap_chars config values are applied consistently across all
document types.

Section is the common return type: a (page, section_title, body) triple that
50_index_documents.py uses to build Qdrant point payloads.
"""

import re
from typing import NamedTuple


class Section(NamedTuple):
    page: int | None        # PDF page number; None for non-PDF formats
    section: str            # Heading or structural label; empty string if absent
    body: str               # Chunk text ready for embedding


def clean(text: str) -> str:
    """Strip markup that adds noise without retrieval value.

    Code fences are replaced with a placeholder rather than discarded so
    format-specific chunkers can decide whether to keep them as dedicated chunks
    before calling split_text.
    """
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)          # image tags
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)        # inline links → label
    text = re.sub(r"https?://\S+", " ", text)                   # bare URLs
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Pack paragraphs greedily up to max_chars; split oversized units at
    sentence boundaries, then word boundaries, so text is never cut
    mid-sentence (or mid-word).

    Normal paragraphs are never split mid-paragraph and never overlapped with
    their neighbours. When a paragraph exceeds max_chars on its own it is
    split at sentence boundaries first, then at word boundaries, and only
    falls back to a hard character cut (with overlap) when a single word
    exceeds max_chars.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean(text)) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_unit(paragraph, max_chars, overlap))
        elif not current or len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _split_unit(unit: str, max_chars: int, overlap: int) -> list[str]:
    """Split one oversized unit without breaking sentences or words.

    Tries sentence boundaries first, then word boundaries, and only falls
    back to a hard character cut when a single word exceeds max_chars.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", unit) if s.strip()]
    if len(sentences) > 1:
        return _pack_units(sentences, max_chars, overlap, "\n\n")
    words = unit.split()
    if len(words) > 1:
        return _pack_units(words, max_chars, overlap, " ")
    return _hard_cut(unit, max_chars, overlap)


def _pack_units(units, max_chars: int, overlap: int, separator: str) -> list[str]:
    """Greedily pack atomic units (sentences or words) up to max_chars.

    Units are never split; a unit that alone exceeds max_chars is recursed
    into via _split_unit.
    """
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_unit(unit, max_chars, overlap))
            continue
        if not current or len(current) + len(separator) + len(unit) <= max_chars:
            current = f"{current}{separator}{unit}".strip()
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _hard_cut(unit: str, max_chars: int, overlap: int) -> list[str]:
    """Last resort: hard character cut with sliding-window overlap."""
    chunks: list[str] = []
    start = 0
    while start < len(unit):
        end = min(start + max_chars, len(unit))
        chunks.append(unit[start:end].strip())
        if end == len(unit):
            break
        start = max(start + 1, end - overlap)
    return chunks
