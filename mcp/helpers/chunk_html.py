"""Chunk HTML documents into retrieval-ready sections.

Strategy:
  1. Parse the HTML with the standard library html.parser; no third-party
     dependency is required.
  2. Extract the <title> tag and promote it as a metadata prefix on the first
     chunk, mirroring what chunk_md does with YAML frontmatter.
  3. Split on block-level structural elements (h1-h6, p, li, td, th,
     blockquote, pre) so each chunk maps to a coherent visual unit rather than
     an arbitrary character slice.
  4. Strip all remaining tags from each block, collapse whitespace, and pass
     the cleaned text through split_text for size-based subdivision.
  5. <pre> / <code> blocks are kept as standalone chunks (section="code") so
     code snippets are not silently discarded, matching chunk_md behaviour.
"""

import re
from html.parser import HTMLParser

from chunk_max_chars import Section, split_text

# Block-level tags that act as natural section boundaries.
_BLOCK_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "li", "td", "th", "blockquote", "pre", "div", "article", "section",
}
# Tags whose text content should become standalone code chunks.
_CODE_TAGS = {"pre", "code"}
# Tags to skip entirely (no useful text content).
_SKIP_TAGS = {"script", "style", "nav", "footer", "noscript"}


class _HtmlExtractor(HTMLParser):
    """Walk the HTML tree and collect (tag, text) block pairs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._skip_depth = 0
        self.title = ""
        # Each entry: ("block_tag_or_empty", accumulated_text)
        self.blocks: list[tuple[str, str]] = []
        self._current_tag = ""
        self._current_buf = ""

    def _flush(self):
        text = re.sub(r"\s+", " ", self._current_buf).strip()
        if text:
            self.blocks.append((self._current_tag, text))
        self._current_tag = ""
        self._current_buf = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        self._stack.append(tag)
        if tag in _BLOCK_TAGS:
            self._flush()
            self._current_tag = tag

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        # Capture <title> separately for the metadata prefix.
        if self._stack and self._stack[-1] == "title":
            self.title += data
            return
        self._current_buf += data

    def close(self):
        self._flush()
        super().close()


def chunk_html(text: str, max_chars: int, overlap: int) -> list[Section]:
    """Return Section list for one HTML document."""
    parser = _HtmlExtractor()
    parser.feed(text)
    parser.close()

    meta_prefix = f"Title: {parser.title.strip()}\n\n" if parser.title.strip() else ""
    sections: list[Section] = []

    for tag, block_text in parser.blocks:
        is_code = tag in _CODE_TAGS
        # Prepend the title metadata to the very first non-code chunk only.
        effective = (meta_prefix + block_text) if (not sections and meta_prefix and not is_code) else block_text
        label = "code" if is_code else (tag if tag else "")
        for chunk in split_text(effective, max_chars, overlap):
            sections.append(Section(page=None, section=label, body=chunk))

    return sections
