"""Chunk Markdown documents into retrieval-ready sections.

Strategy:
  1. Strip YAML frontmatter and promote its title/tags into a metadata prefix
     that is prepended to the first content chunk so searches on title or tag
     terms still hit the right document.
  2. Extract fenced code blocks before cleaning so they become standalone chunks
     rather than being discarded. Code blocks often contain the exact syntax a
     user searches for.
  3. Split remaining prose on headings (H1–H6); each heading + its body is one
     structural unit fed to split_text for size-based subdivision.
"""

import re

from chunk_max_chars import Section, split_text


# Matches a YAML frontmatter block at the very start of the file.
_FRONTMATTER_RE = re.compile(r"^\s*---\n(.*?)\n---\n", re.S)
# Matches fenced code blocks (``` or ~~~) with an optional language tag.
_CODE_FENCE_RE = re.compile(r"(```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~)", re.S)
# Matches ATX headings H1–H6.
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")


def _extract_frontmatter(text: str) -> tuple[str, str]:
    """Return (metadata_prefix, remaining_text).

    Pulls title and tags from YAML frontmatter into a short natural-language
    prefix so those terms are present in the first chunk's embedding.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return "", text
    body = text[match.end():]
    fm = match.group(1)
    title_m = re.search(r"^title:\s*(.+)$", fm, re.M)
    tags_m = re.search(r"^tags:\s*(.+)$", fm, re.M)
    parts = []
    if title_m:
        parts.append(f"Title: {title_m.group(1).strip()}")
    if tags_m:
        parts.append(f"Tags: {tags_m.group(1).strip()}")
    return ("\n".join(parts) + "\n\n" if parts else ""), body


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split on headings; return (heading_title, section_body) pairs.

    Pre-heading content is kept as an unnamed section so no text is lost.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections = []
    if text[: matches[0].start()].strip():
        sections.append(("", text[: matches[0].start()]))
    for i, m in enumerate(matches):
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1).strip(), text[m.start() : stop]))
    return sections


def chunk_md(text: str, max_chars: int, overlap: int) -> list[Section]:
    """Return Section list for one Markdown document.

    Code blocks are extracted first and emitted as individual sections so they
    are never silently dropped by the cleaner.
    """
    meta_prefix, body = _extract_frontmatter(text)
    sections: list[Section] = []

    # Pull code blocks out before structural splitting so they stay intact.
    code_blocks = _CODE_FENCE_RE.findall(body)
    prose = _CODE_FENCE_RE.sub("\n\n", body)

    for heading, section_body in _split_sections(prose):
        # Prepend the frontmatter metadata to the very first prose chunk only.
        effective_body = (meta_prefix + section_body) if (not sections and meta_prefix) else section_body
        for chunk in split_text(effective_body, max_chars, overlap):
            sections.append(Section(page=None, section=heading, body=chunk))

    for block in code_blocks:
        # Each code block is a self-contained chunk; slide only if it exceeds the limit.
        for chunk in split_text(block, max_chars, overlap):
            sections.append(Section(page=None, section="code", body=chunk))

    return sections
