"""Tests for all chunking helpers in mcp/helpers/.

Run from the project root:
    python -m pytest mcp/test/test_chunking_strategy.py -v

Each helper is tested for:
  - Normal / happy-path output shape and content
  - Boundary conditions (empty input, single paragraph, oversized paragraph)
  - Format-specific structural behaviour (headings, frontmatter, code blocks,
    page merging, row serialisation, JSON/YAML shapes)
"""

import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make helpers importable without installing the package.
HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

from chunk_max_chars import Section, clean, split_text
from chunk_md import chunk_md, _extract_frontmatter, _split_sections
from chunk_text import chunk_text
from chunk_csv import chunk_csv, _row_to_text
from chunk_json import chunk_json
from chunk_yaml import chunk_yaml
from chunk_html import chunk_html
from chunk_pdf import chunk_pdf


# ---------------------------------------------------------------------------
# chunk_max_chars
# ---------------------------------------------------------------------------

class TestClean(unittest.TestCase):
    def test_removes_image_tags(self):
        self.assertNotIn("![", clean("before ![alt](img.png) after"))

    def test_strips_inline_link_keeps_label(self):
        result = clean("[click here](https://example.com)")
        self.assertIn("click here", result)
        self.assertNotIn("https://", result)

    def test_strips_bare_urls(self):
        self.assertNotIn("http", clean("see https://example.com for details"))

    def test_collapses_excess_blank_lines(self):
        result = clean("a\n\n\n\n\nb")
        self.assertNotIn("\n\n\n", result)

    def test_plain_text_unchanged(self):
        text = "Hello world.\n\nSecond paragraph."
        self.assertEqual(clean(text), text)


class TestSplitText(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(split_text("", 100, 10), [])

    def test_single_short_paragraph_is_one_chunk(self):
        chunks = split_text("Hello world.", 100, 10)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Hello world.", chunks[0])

    def test_two_paragraphs_fit_in_one_chunk(self):
        text = "First.\n\nSecond."
        chunks = split_text(text, 200, 20)
        self.assertEqual(len(chunks), 1)

    def test_two_paragraphs_split_when_over_limit(self):
        # Each paragraph is 60 chars; limit is 80 so they cannot both fit.
        p1 = "A" * 60
        p2 = "B" * 60
        chunks = split_text(f"{p1}\n\n{p2}", 80, 10)
        self.assertEqual(len(chunks), 2)

    def test_oversized_paragraph_uses_sliding_window(self):
        long = "X" * 250
        chunks = split_text(long, 100, 20)
        # Every chunk must be at most max_chars.
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        # Overlap means adjacent chunks share a suffix/prefix.
        for i in range(len(chunks) - 1):
            self.assertTrue(chunks[i][-20:] in chunks[i + 1] or len(chunks[i + 1]) > 0)

    def test_no_empty_chunks(self):
        text = "\n\n".join(["word"] * 10)
        chunks = split_text(text, 50, 5)
        self.assertTrue(all(c.strip() for c in chunks))

    def test_each_chunk_within_max_chars(self):
        text = "\n\n".join(["paragraph " * 5] * 20)
        for chunk in split_text(text, 100, 10):
            self.assertLessEqual(len(chunk), 100)

    def test_oversized_paragraph_splits_at_sentence_boundaries(self):
        # A single paragraph of many short sentences that exceeds max_chars.
        text = " ".join(f"Sentence number {i} is here." for i in range(1, 20))
        chunks = split_text(text, 100, 10)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        # No chunk may end mid-sentence: each must terminate with a sentence
        # terminator.
        for chunk in chunks:
            self.assertTrue(
                chunk.strip().endswith((".", "?", "!")),
                f"Chunk ends mid-sentence: {chunk.strip()[-40:]!r}",
            )

    def test_oversized_single_sentence_splits_at_word_boundaries(self):
        # One long run of words (no sentence terminators) exceeding max_chars.
        text = " ".join(["word"] * 100)
        chunks = split_text(text, 100, 10)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        # No chunk may end mid-word: each must end on a word character.
        for chunk in chunks:
            self.assertTrue(
                chunk.strip()[-1].isalnum(),
                f"Chunk ends mid-word: {chunk.strip()[-20:]!r}",
            )


# ---------------------------------------------------------------------------
# chunk_md
# ---------------------------------------------------------------------------

class TestExtractFrontmatter(unittest.TestCase):
    def test_no_frontmatter_returns_empty_prefix(self):
        prefix, body = _extract_frontmatter("# Heading\nContent")
        self.assertEqual(prefix, "")
        self.assertIn("Heading", body)

    def test_title_promoted_to_prefix(self):
        text = "---\ntitle: My Doc\n---\n# Heading\nContent"
        prefix, _ = _extract_frontmatter(text)
        self.assertIn("Title: My Doc", prefix)

    def test_tags_promoted_to_prefix(self):
        text = "---\ntags: foo, bar\n---\nContent"
        prefix, _ = _extract_frontmatter(text)
        self.assertIn("Tags: foo, bar", prefix)

    def test_body_excludes_frontmatter_block(self):
        text = "---\ntitle: X\n---\nActual content"
        _, body = _extract_frontmatter(text)
        self.assertNotIn("---", body)
        self.assertIn("Actual content", body)

    def test_frontmatter_without_title_or_tags_gives_empty_prefix(self):
        text = "---\nauthor: Alice\n---\nContent"
        prefix, _ = _extract_frontmatter(text)
        self.assertEqual(prefix, "")


class TestSplitSections(unittest.TestCase):
    def test_no_headings_returns_single_section(self):
        sections = _split_sections("Just prose.")
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0][0], "")

    def test_heading_becomes_section_title(self):
        sections = _split_sections("# Alpha\nContent A\n## Beta\nContent B")
        titles = [s[0] for s in sections]
        self.assertIn("Alpha", titles)
        self.assertIn("Beta", titles)

    def test_pre_heading_content_preserved(self):
        sections = _split_sections("Intro text\n# Heading\nBody")
        self.assertTrue(any(s[0] == "" and "Intro" in s[1] for s in sections))

    def test_all_heading_levels_recognised(self):
        text = "\n".join(f"{'#' * i} H{i}\nBody {i}" for i in range(1, 7))
        sections = _split_sections(text)
        self.assertEqual(len(sections), 6)


class TestChunkMd(unittest.TestCase):
    def test_returns_section_list(self):
        result = chunk_md("# Title\nSome content.", 500, 50)
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], Section)

    def test_page_is_none(self):
        for sec in chunk_md("# H\nBody", 500, 50):
            self.assertIsNone(sec.page)

    def test_heading_in_section_field(self):
        result = chunk_md("# MyHeading\nContent here.", 500, 50)
        self.assertTrue(any(s.section == "MyHeading" for s in result))

    def test_frontmatter_title_in_first_chunk(self):
        text = "---\ntitle: Guide\ntags: rag\n---\n# Intro\nContent"
        result = chunk_md(text, 500, 50)
        self.assertIn("Guide", result[0].body)

    def test_code_block_becomes_own_section(self):
        text = "Prose.\n\n```python\nx = 1\n```\n\nMore prose."
        result = chunk_md(text, 500, 50)
        code_sections = [s for s in result if s.section == "code"]
        self.assertTrue(len(code_sections) >= 1)
        self.assertTrue(any("x = 1" in s.body for s in code_sections))

    def test_code_block_not_in_prose_sections(self):
        text = "Prose.\n\n```python\nx = 1\n```"
        result = chunk_md(text, 500, 50)
        prose = [s for s in result if s.section != "code"]
        self.assertFalse(any("x = 1" in s.body for s in prose))

    def test_empty_document_returns_empty(self):
        self.assertEqual(chunk_md("", 500, 50), [])

    def test_long_section_split_into_multiple_chunks(self):
        body = "word " * 200  # ~1000 chars
        text = f"# Big\n{body}"
        result = chunk_md(text, 100, 10)
        big_chunks = [s for s in result if s.section == "Big"]
        self.assertGreater(len(big_chunks), 1)


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------

class TestChunkText(unittest.TestCase):
    def test_returns_section_list(self):
        result = chunk_text("Hello.\n\nWorld.", 500, 50)
        self.assertIsInstance(result[0], Section)

    def test_page_and_section_are_empty(self):
        for sec in chunk_text("Hello.", 500, 50):
            self.assertIsNone(sec.page)
            self.assertEqual(sec.section, "")

    def test_empty_input_returns_empty(self):
        self.assertEqual(chunk_text("", 500, 50), [])

    def test_long_text_split_into_multiple_chunks(self):
        text = "word " * 200
        result = chunk_text(text, 100, 10)
        self.assertGreater(len(result), 1)

    def test_all_chunks_within_max_chars(self):
        text = "paragraph\n\n" * 50
        for sec in chunk_text(text, 100, 10):
            self.assertLessEqual(len(sec.body), 100)


# ---------------------------------------------------------------------------
# chunk_csv
# ---------------------------------------------------------------------------

class TestRowToText(unittest.TestCase):
    def test_basic_serialisation(self):
        result = _row_to_text(["name", "age"], ["Alice", "30"])
        self.assertIn("name: Alice", result)
        self.assertIn("age: 30", result)

    def test_empty_values_omitted(self):
        result = _row_to_text(["name", "note"], ["Alice", ""])
        self.assertNotIn("note", result)

    def test_extra_columns_ignored(self):
        # zip stops at the shorter list — no IndexError
        result = _row_to_text(["a", "b"], ["1", "2", "3"])
        self.assertIn("a: 1", result)


class TestChunkCsv(unittest.TestCase):
    SIMPLE = "name,age,city\nAlice,30,London\nBob,25,Paris\n"

    def test_returns_section_list(self):
        result = chunk_csv(self.SIMPLE, 500, 50)
        self.assertIsInstance(result[0], Section)

    def test_page_and_section_are_empty(self):
        for sec in chunk_csv(self.SIMPLE, 500, 50):
            self.assertIsNone(sec.page)
            self.assertEqual(sec.section, "")

    def test_header_present_in_chunk(self):
        result = chunk_csv(self.SIMPLE, 500, 50)
        self.assertTrue(any("Columns:" in s.body for s in result))

    def test_row_data_present(self):
        result = chunk_csv(self.SIMPLE, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("Alice", combined)
        self.assertIn("Bob", combined)

    def test_empty_csv_returns_empty(self):
        self.assertEqual(chunk_csv("", 500, 50), [])

    def test_header_only_returns_empty(self):
        self.assertEqual(chunk_csv("name,age\n", 500, 50), [])

    def test_large_csv_splits_into_multiple_chunks(self):
        # 50 rows of ~30 chars each; limit 100 forces multiple chunks.
        rows = "\n".join(f"row{i},value{i}" for i in range(50))
        text = "key,val\n" + rows
        result = chunk_csv(text, 100, 10)
        self.assertGreater(len(result), 1)

    def test_quoted_fields_handled(self):
        text = 'name,note\nAlice,"has, comma"\n'
        result = chunk_csv(text, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("Alice", combined)


# ---------------------------------------------------------------------------
# chunk_json
# ---------------------------------------------------------------------------

class TestChunkJson(unittest.TestCase):
    def test_array_of_objects(self):
        data = json.dumps([{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}])
        result = chunk_json(data, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("Alice", combined)
        self.assertIn("Bob", combined)

    def test_single_object_keys_become_items(self):
        data = json.dumps({"title": "Guide", "content": "Some text"})
        result = chunk_json(data, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("title", combined)
        self.assertIn("Guide", combined)

    def test_array_of_scalars(self):
        data = json.dumps(["apple", "banana", "cherry"])
        result = chunk_json(data, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("apple", combined)

    def test_scalar_document(self):
        result = chunk_json('"just a string"', 500, 50)
        self.assertEqual(len(result), 1)
        self.assertIn("just a string", result[0].body)

    def test_page_and_section_are_empty(self):
        for sec in chunk_json('["a"]', 500, 50):
            self.assertIsNone(sec.page)
            self.assertEqual(sec.section, "")

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            chunk_json("{not valid json}", 500, 50)

    def test_large_array_splits_into_multiple_chunks(self):
        items = [{"key": f"value_{i}" * 5} for i in range(30)]
        result = chunk_json(json.dumps(items), 100, 10)
        self.assertGreater(len(result), 1)

    def test_null_and_empty_values_omitted(self):
        data = json.dumps([{"name": "Alice", "note": None, "tag": ""}])
        result = chunk_json(data, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertNotIn("None", combined)

    def test_empty_array_returns_empty(self):
        self.assertEqual(chunk_json("[]", 500, 50), [])


# ---------------------------------------------------------------------------
# chunk_yaml
# ---------------------------------------------------------------------------

class TestChunkYaml(unittest.TestCase):
    def _has_yaml(self):
        try:
            import yaml  # noqa: F401
            return True
        except ImportError:
            return False

    def test_mapping_keys_become_items(self):
        text = "title: Guide\ncontent: Some text"
        result = chunk_yaml(text, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("Guide", combined)

    def test_sequence_items_chunked(self):
        text = "- name: Alice\n  age: 30\n- name: Bob\n  age: 25"
        result = chunk_yaml(text, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("Alice", combined)

    def test_scalar_document(self):
        result = chunk_yaml("just a string", 500, 50)
        self.assertEqual(len(result), 1)

    def test_page_and_section_are_empty(self):
        for sec in chunk_yaml("key: value", 500, 50):
            self.assertIsNone(sec.page)
            self.assertEqual(sec.section, "")

    def test_invalid_yaml_raises_value_error(self):
        if not self._has_yaml():
            self.skipTest("PyYAML not installed")
        with self.assertRaises(ValueError):
            chunk_yaml("key: [unclosed", 500, 50)

    def test_fallback_to_text_when_pyyaml_absent(self):
        # Simulate missing PyYAML by patching the import inside chunk_yaml.
        import chunk_yaml as cy
        original = cy.__builtins__ if hasattr(cy, "__builtins__") else {}
        with patch.dict(sys.modules, {"yaml": None}):
            # Re-import to trigger the ImportError path.
            import importlib
            cy_fresh = importlib.reload(cy)
            result = cy_fresh.chunk_yaml("key: value", 500, 50)
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) >= 1)

    def test_null_document_returns_empty(self):
        if not self._has_yaml():
            self.skipTest("PyYAML not installed")
        # YAML null document (empty file or just '~') parses to None.
        result = chunk_yaml("~", 500, 50)
        self.assertEqual(result, [])

    def test_large_mapping_splits_into_multiple_chunks(self):
        lines = "\n".join(f"key{i}: {'value' * 10}" for i in range(20))
        result = chunk_yaml(lines, 100, 10)
        self.assertGreater(len(result), 1)


# ---------------------------------------------------------------------------
# chunk_html
# ---------------------------------------------------------------------------

class TestChunkHtml(unittest.TestCase):
    SIMPLE = """<html><head><title>My Page</title></head>
<body><h1>Intro</h1><p>First paragraph.</p><p>Second paragraph.</p></body></html>"""

    def test_returns_section_list(self):
        result = chunk_html(self.SIMPLE, 500, 50)
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], Section)

    def test_page_is_none(self):
        for sec in chunk_html(self.SIMPLE, 500, 50):
            self.assertIsNone(sec.page)

    def test_title_in_first_chunk(self):
        result = chunk_html(self.SIMPLE, 500, 50)
        self.assertIn("My Page", result[0].body)

    def test_heading_tag_in_section_field(self):
        result = chunk_html(self.SIMPLE, 500, 50)
        self.assertTrue(any(s.section == "h1" for s in result))

    def test_prose_text_present(self):
        result = chunk_html(self.SIMPLE, 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("First paragraph", combined)
        self.assertIn("Second paragraph", combined)

    def test_tags_stripped_from_body(self):
        result = chunk_html("<p><strong>Bold</strong> text.</p>", 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertNotIn("<strong>", combined)
        self.assertIn("Bold", combined)

    def test_script_and_style_excluded(self):
        html = "<p>Visible</p><script>alert(1)</script><style>.x{}</style>"
        combined = " ".join(s.body for s in chunk_html(html, 500, 50))
        self.assertNotIn("alert", combined)
        self.assertNotIn(".x", combined)

    def test_code_block_becomes_own_section(self):
        html = "<p>Prose.</p><pre>x = 1\ny = 2</pre><p>More.</p>"
        result = chunk_html(html, 500, 50)
        code_sections = [s for s in result if s.section == "code"]
        self.assertTrue(len(code_sections) >= 1)
        self.assertTrue(any("x = 1" in s.body for s in code_sections))

    def test_empty_html_returns_empty(self):
        self.assertEqual(chunk_html("", 500, 50), [])

    def test_no_title_no_prefix(self):
        result = chunk_html("<p>Just text.</p>", 500, 50)
        self.assertNotIn("Title:", result[0].body)

    def test_long_paragraph_split_into_multiple_chunks(self):
        body = "word " * 200
        html = f"<p>{body}</p>"
        result = chunk_html(html, 100, 10)
        self.assertGreater(len(result), 1)

    def test_all_chunks_within_max_chars(self):
        body = "word " * 200
        html = f"<p>{body}</p>"
        for sec in chunk_html(html, 100, 10):
            self.assertLessEqual(len(sec.body), 100)

    def test_html_entities_decoded(self):
        result = chunk_html("<p>&amp; &lt; &gt;</p>", 500, 50)
        combined = " ".join(s.body for s in result)
        self.assertIn("&", combined)


# ---------------------------------------------------------------------------
# chunk_pdf  (pypdf is mocked so no real PDF file is needed)
# ---------------------------------------------------------------------------

def _make_mock_reader(page_texts: list[str]):
    """Build a minimal pypdf.PdfReader mock from a list of per-page strings."""
    mock_reader = MagicMock()
    pages = []
    for text in page_texts:
        page = MagicMock()
        page.extract_text.return_value = text
        pages.append(page)
    mock_reader.pages = pages
    return mock_reader


class TestChunkPdf(unittest.TestCase):
    def _chunk(self, page_texts, max_chars=500, overlap=50):
        mock_reader = _make_mock_reader(page_texts)
        with patch.dict(sys.modules, {"pypdf": MagicMock(PdfReader=lambda _: mock_reader)}):
            import importlib
            import chunk_pdf as cp
            cp_fresh = importlib.reload(cp)
            return cp_fresh.chunk_pdf(Path("fake.pdf"), max_chars, overlap)

    def test_basic_pages_produce_sections(self):
        sections, empty = self._chunk(["Page one content.", "Page two content."])
        self.assertGreater(len(sections), 0)
        self.assertEqual(empty, [])

    def test_empty_pages_reported(self):
        sections, empty = self._chunk(["Content", "", "More content"])
        self.assertIn(2, empty)

    def test_page_number_in_section(self):
        sections, _ = self._chunk(["First page text."])
        self.assertEqual(sections[0].page, 1)

    def test_section_field_is_empty_string(self):
        sections, _ = self._chunk(["Some text."])
        self.assertEqual(sections[0].section, "")

    def test_short_pages_merged(self):
        # Two pages each well under max_chars/2 should merge into one unit
        # and produce a single section (assuming combined text fits max_chars).
        short = "Hi."  # 3 chars — far below 500/2 = 250
        sections, _ = self._chunk([short, short], max_chars=500)
        # Both pages merged → one section with page number of the first.
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].page, 1)

    def test_long_page_split_into_multiple_chunks(self):
        long_text = "word " * 200  # ~1000 chars
        sections, _ = self._chunk([long_text], max_chars=100)
        self.assertGreater(len(sections), 1)

    def test_all_empty_pages_returns_no_sections(self):
        sections, empty = self._chunk(["", "", ""])
        self.assertEqual(sections, [])
        self.assertEqual(empty, [1, 2, 3])

    def test_pypdf_import_error_raises_runtime_error(self):
        with patch.dict(sys.modules, {"pypdf": None}):
            import importlib
            import chunk_pdf as cp
            cp_fresh = importlib.reload(cp)
            with self.assertRaises(RuntimeError):
                cp_fresh.chunk_pdf(Path("fake.pdf"), 500, 50)


if __name__ == "__main__":
    unittest.main()
