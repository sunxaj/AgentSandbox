# Chunking Strategy

How each supported file type is chunked for the RAG document collection. All
chunkers live in `mcp/helpers/` and are dispatched by file suffix in
`mcp/workers/rag/50_index_documents.py`. Supported extensions (configured in
`mcp/config/rag.conf`): `.md`, `.txt`, `.pdf`, `.csv`, `.json`, `.yaml`,
`.yml`, `.html`.

## Shared pipeline

Every format chunker follows the same two-stage shape:

1. **Structural extraction** — the format-specific helper produces a list of
   *structural units* (headings, CSV rows, JSON items, HTML blocks, PDF pages,
   etc.).
2. **Size-based subdivision** — each unit is passed to the shared
   `split_text()` in `chunk_max_chars.py`, which enforces `max_chars` /
   `overlap_chars`.

The result is a list of `Section(page, section, body)` tuples.
`50_index_documents.py` turns each `Section` into a Qdrant point.

### Shared config (`mcp/config/rag.conf`)

| Key | Default | Meaning |
|---|---|---|
| `max_chars` | 500 | Hard upper bound on any chunk's character length. Chosen to stay under the embedding server's 512-token limit. |
| `overlap_chars` | 100 | Overlap used by the sliding-window fallback. |
| `embedding_batch_size` | 8 | Number of chunks embedded per API call. |

### `clean()` — text normalisation

Applied inside `split_text()` before chunking. It:

- Replaces image tags `![alt](url)` with a space.
- Converts inline links `[label](url)` to just the label.
- Replaces bare URLs with a space.
- Collapses runs of 3+ newlines into a double newline.

### `split_text()` — the size enforcer

`split_text(text, max_chars, overlap)` is the single source of truth for size.
It:

1. Splits the input into **paragraphs** on blank lines (`\n\s*\n`).
2. **Greedy paragraph packing** — accumulates whole paragraphs into the current
   chunk until the next paragraph would push it over `max_chars`. Normal
   paragraphs are never split mid-paragraph.
3. **Oversized units** — when a single paragraph exceeds `max_chars`, it is
   split in tiers so text is never cut mid-sentence (or mid-word):
   - **Sentence boundaries** — split on `.`, `?`, `!` followed by whitespace,
     then greedily pack whole sentences.
   - **Word boundaries** — if a single sentence exceeds `max_chars`, split on
     word boundaries.
   - **Hard cut** — only as a last resort when a single *word* exceeds
     `max_chars`; uses the sliding window with `overlap_chars`.

So every chunk is ≤ `max_chars`, and boundaries fall on
paragraph → sentence → word → character, in that order of preference.

## Per-format strategy

### `.md` — Markdown (`chunk_md.py`)

1. **Frontmatter** — YAML frontmatter at the top of the file is stripped; its
   `title` and `tags` are promoted into a natural-language prefix
   (`Title: …`, `Tags: …`) prepended to the **first** content chunk so title/tag
   terms are searchable.
2. **Code blocks** — fenced code blocks (``` or ~~~) are extracted *before*
   cleaning so they become standalone chunks (section=`code`) instead of being
   discarded. Code often contains the exact syntax users search for.
3. **Headings** — remaining prose is split on ATX headings (H1–H6). Each heading
   + its body is one structural unit; pre-heading content is kept as an unnamed
   section so no text is lost. Each unit is fed to `split_text`.

### `.txt` — Plain text (`chunk_text.py`)

No structural markers, so the whole file is one unit passed directly to
`split_text` for paragraph-aware subdivision. If a file has consistent structure
(timestamp-separated log entries, numbered sections, dialogue turns), add a
format-specific pre-splitter here rather than in the worker.

### `.csv` — CSV (`chunk_csv.py`)

Raw CSV syntax is noise for an embedding model, so:

1. Each row is serialised to `field: value` pairs (empty values omitted).
2. Short rows are **batched** up to `max_chars` so each chunk has enough context.
3. The **header row is prepended to every batch** (`Columns: …`) so each chunk
   is self-contained and a hit is understandable without surrounding rows.
4. Batches are fed to `split_text`; rows that alone exceed `max_chars` go
   through the sliding window.

### `.json` — JSON (`chunk_json.py`)

Raw JSON syntax is noise, so each top-level item is serialised to a readable
`key: value` string:

- **Array of objects** → each object is one unit (keys become field labels).
- **Array of scalars** → each value is one unit.
- **Single object** → each top-level key is one unit.
- **Other shape** → the whole document is one unit.

Units are batched up to `max_chars`, then fed to `split_text`. Invalid JSON
raises `ValueError`.

### `.yaml` / `.yml` — YAML (`chunk_yaml.py`)

Mirrors `chunk_json`:

- **Mapping** → each top-level key is one unit.
- **Sequence** → each item is one unit.
- **Scalar / other** → the whole document is one unit.

Parsed with PyYAML. PyYAML is an **optional** dependency (not in `requirements-mcp.txt`); if
absent, the file is treated as plain text via `chunk_text` so indexing degrades
gracefully instead of hard-failing.
Invalid YAML raises `ValueError`.

### `.html` — HTML (`chunk_html.py`)

Parsed with the standard-library `html.parser` (no third-party dep):

1. **Title** — the `<title>` tag is extracted and promoted as a `Title: …`
   metadata prefix on the first non-code chunk (mirrors MD frontmatter).
2. **Block boundaries** — split on block-level elements
   (`h1–h6`, `p`, `li`, `td`, `th`, `blockquote`, `pre`, `div`, `article`,
   `section`) so each chunk maps to a coherent visual unit.
3. **Cleaning** — remaining tags are stripped, whitespace collapsed, then passed
   to `split_text`.
4. **Code** — `<pre>`/`<code>` blocks are kept as standalone chunks
   (section=`code`).
5. **Skipped** — `script`, `style`, `nav`, `footer`, `noscript` are dropped
   entirely.

### `.pdf` — PDF (`chunk_pdf.py`)

Uses `pypdf` (deferred import):

1. **Page extraction** — text is extracted page-by-page; empty pages are
   recorded separately (reported, not treated as errors).
2. **Short-page merge** — consecutive pages whose combined text is under
   `max_chars // 2` are merged into one unit before splitting, so a section
   spanning a page boundary stays together.
3. **Size subdivision** — each merged unit is passed to `split_text`. Each
   resulting chunk carries the **page number** of the first page in the unit.

**Limitation:** pypdf extraction is layout-blind. Multi-column layouts, tables,
and scanned pages produce garbled or empty text; scanned PDFs need an OCR
pre-processing step.

## Chunk → Qdrant point

`50_index_documents.py` maps each `Section` to a Qdrant point:

- **ID** — deterministic `uuid5` of
  `(relative_path, page, section, chunk_number)`, so re-runs are idempotent
  (same chunk → same ID → upsert).
- **Payload** — `source_path`, `file_name`, `file_type`, `title`, `page`,
  `section`, `chunk`, `sha256` (source file hash), and `text` (the chunk body).
- **Vector** — embedding of a prefixed text:
  `Title: …\nSource: …\n[Page: …\n][Section: …\n]\n<body>`.

## Limitations & notes

- `max_chars` is a **character** proxy for the embedding server's token limit,
  not a token count.
- Sentence detection is a regex heuristic (`(?<=[.!?])\s+`); abbreviations like
  "e.g." or "U.S." may split incorrectly.
- A single sentence/word longer than `max_chars` is the only case that can
  still be cut mid-sentence (at a word boundary) or mid-word (hard cut).