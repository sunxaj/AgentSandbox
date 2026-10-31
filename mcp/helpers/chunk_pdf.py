"""Chunk PDF documents into retrieval-ready sections.

Strategy:
  1. Extract text page-by-page via pypdf; record empty pages separately so the
     caller can report them without treating them as errors.
  2. Merge consecutive short pages (each below half of max_chars) into a single
     unit before size-based splitting. This prevents a section that spans a page
     boundary from being split into two semantically incomplete chunks.
  3. Pass each merged unit through split_text for size-based subdivision.

Limitation: pypdf text extraction is layout-blind. Multi-column layouts, tables,
and scanned pages will produce garbled or empty text. For scanned PDFs an OCR
pre-processing step is required before this chunker is useful.
"""

from chunk_max_chars import Section, split_text


def chunk_pdf(path, max_chars: int, overlap: int) -> tuple[list[Section], list[int]]:
    """Return (sections, empty_page_numbers) for one PDF file.

    path is a pathlib.Path. Importing pypdf is deferred so the rest of the
    pipeline works even if pypdf is not installed and no PDFs are present.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF extraction requires pypdf; install it in the MCP virtual environment"
        ) from exc

    reader = PdfReader(str(path))
    pages: list[tuple[int, str]] = []
    empty: list[int] = []

    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((number, text))
        else:
            empty.append(number)

    # Merge consecutive short pages so cross-page sections stay together.
    # A page is "short" when its text is less than half the chunk size limit.
    merged: list[tuple[int, str]] = []
    for page_number, text in pages:
        if merged and len(merged[-1][1]) + len(text) + 1 < max_chars // 2:
            prev_num, prev_text = merged[-1]
            merged[-1] = (prev_num, prev_text + "\n" + text)
        else:
            merged.append((page_number, text))

    sections: list[Section] = []
    for page_number, text in merged:
        for chunk in split_text(text, max_chars, overlap):
            sections.append(Section(page=page_number, section="", body=chunk))

    return sections, empty
