#!/usr/bin/env python3
"""Read an original source file from the configured document directory.

The path is always interpreted relative to the configured source root. PDF page
numbers exposed to callers are one-based, matching printed document references.
"""

import argparse
import json
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_DIR))
from rag_config import load_rag_config


def main():
    """Validate and read one source while preventing paths outside the collection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_path")
    parser.add_argument("--page", type=int)
    parser.add_argument("--max-chars", type=int, default=12000)
    args = parser.parse_args()
    if not 1 <= args.max_chars <= 20000:
        raise ValueError("max_chars must be between 1 and 20000")
    if args.page is not None and args.page < 1:
        raise ValueError("page numbers start at 1")

    config = load_rag_config()
    source_root = config["source"].resolve()
    relative = Path(args.source_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("source_path must be a relative path inside the configured document folder")
    # Resolve symlinks before checking containment so a link cannot escape the source folder.
    path = (source_root / relative).resolve()
    if not path.is_relative_to(source_root) or not path.is_file():
        raise ValueError("The requested source file does not exist inside the configured document folder")
    if path.suffix.lower() not in config["extensions"]:
        raise ValueError("The requested file type is not enabled in config/rag.conf")

    metadata = {"source_path": path.relative_to(source_root).as_posix(), "file_name": path.name,
                "file_type": path.suffix.lower().lstrip(".")}
    # Keep PDF extraction page-aware; callers can request one page or a bounded whole-file excerpt.
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF reading needs pypdf installed in the MCP virtual environment") from exc
        reader = PdfReader(str(path))
        if args.page is not None:
            if args.page > len(reader.pages):
                raise ValueError(f"PDF has only {len(reader.pages)} pages")
            page_text = reader.pages[args.page - 1].extract_text() or ""
            content = page_text
            metadata["page"] = args.page
            metadata["total_pages"] = len(reader.pages)
        else:
            page_texts = [(index, page.extract_text() or "") for index, page in enumerate(reader.pages, 1)]
            content = "\n\n".join(f"--- Page {index} ---\n{text}" for index, text in page_texts)
            metadata["total_pages"] = len(reader.pages)
    else:
        if args.page is not None:
            raise ValueError("page can only be used when reading a PDF")
        content = path.read_text(encoding="utf-8", errors="replace")
    print(json.dumps({**metadata, "content": content[:args.max_chars],
                      "truncated": len(content) > args.max_chars}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
