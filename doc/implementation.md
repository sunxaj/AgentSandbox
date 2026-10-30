# Implementation Plan — Data Analysis, Review & Reporting Agent

> Derived from the design in doc/agent_en.md (with the node-level specification
> from doc/agent_cn.md) and the existing `agents/` codebase. This is a plan
> document only — no implementation code is included.

## 1. Goal

Build one compiled LangGraph `StateGraph` implementing the 6-step pipeline:

1. `retrieve_context` — RAG lookup of relevant table metadata (name, category,
   parquet path, schema)
2. `generate_pyspark` — LLM generates PySpark code into state
3. `review` — human code review via `interrupt()` (approve / reject with edits)
4. `execute_via_mcp` — run the code through a PySpark MCP server (read-only);
   store only a result summary / reference in state, never full DataFrames
5. `summarize` — natural-language summary + downloadable result file
6. `revise_prompt` — bounded retry loop back to step 2 on reject, execution
   error, or user dissatisfaction

One graph, two frontends: a CLI client and a GUI, both hitting the same backend
graph. `PostgresSaver` checkpoints double as the audit log.

## 2. Current baseline

Existing in `agents/app/`:

- `agent.py` — current MCP retrieval agent: generic `call_llm` → `call_tool`
  loop over MCP tools, session-only state (no persistence)
- `mcp_client.py` — Streamable-HTTP MCP client (fresh session per call)
- `llm.py` — `ChatOpenAI` → Bonsai llama.cpp at 192.168.0.222:8081
- `ui.py` — Gradio chat UI at 127.0.0.1:7860, tool-call details rendering
- `agent_config.py` + `agents/config/agent.conf` — `[llm]`, `[mcp]`, `[agent]`
  sections, re-read on every call

Existing on the Linux server (see doc/mcp.md, doc/install.md):

- MCP HTTP server with knowledge tools: `obsidian_*` and
  `rag_index_documents` / `rag_search_documents` / `rag_read_source`
- Qdrant (:6333), Bonsai (:8081), MCP HTTP (:8000)

Gaps vs. the target design:

- No named-node `StateGraph` (the current agent is a generic tool loop)
- No table-metadata index (the current RAG collection holds knowledge docs)
- No PySpark execution MCP server (`pyspark-mcp` / `spark-connect-mcp`); **no
  Spark deployment exists anywhere in the current environment**
- No `interrupt`-based human review
- No `PostgresSaver` checkpoints / audit
- No `langgraph-sdk` CLI client or LangGraph Agent Server
## 3. Engineering decisions to lock in first (Phase 0, before graph code)

**D1 — Spark execution backend** (decision ① in the design doc): the current
environment has no Spark cluster.

- Option A (recommended): `pyspark-mcp` against a local Spark standalone on the
  Mac for development, later a YARN/Standalone cluster on the server, both
  mounted read-only
- Option B: `spark-connect-mcp` only if we move to Spark Connect / Databricks
- Option C (dev bridge, optional): an in-process local Spark executor behind
  the same node interface so the loop can be built before Spark is up; remove
  it when the MCP server runs, keeping the node contract identical

**D2 — GUI path** (decision ② in the design doc):

- Phases P0–P1: keep Gradio (it already works), add a review panel and node
  status display
- Phase P2: evaluate official `@langchain/react` (`useStream`) vs. CopilotKit
  for a "graph-native" review card; the backend graph is unchanged by this choice

**D3 — Postgres location**: local Docker on the Mac for development (default);
server-side only if the team prefers.

**D4 — Table-metadata RAG**: recommended — publish table metadata as documents
in the existing RAG source folder, index with the existing `rag_index_documents`,
retrieve with `rag_search_documents`. This reuses Qdrant + EmbeddingGemma on the
server with zero new infrastructure. Alternative: a local vector store for
development if server RAG proves insufficient.

## 4. Phased plan

### Phase 0 — Environment and data preparation (no graph code yet)

1. Lock decisions D1–D4.
2. Stand up local Spark standalone (dev) and the chosen PySpark MCP server
   (`pyspark-mcp` in its own venv). Verify its tools: schema lookup, run
   query, read first N rows.
3. Prepare sample data: a directory of 2–6 parquet tables across 2 categories,
   with a short README defining categories and the canonical data directory.
4. Write a table-metadata extraction script (outside the graph): scan the
   parquet directory and emit one document per table — table name, category,
   parquet path, columns with types, sample values.
5. Run `rag_index_documents`; confirm `rag_search_documents` returns the correct
   table name, path, and schema for a fixed set of test questions.
6. Config: add `[db]` (postgres DSN, placeholder), `[data]` (parquet dir),
   `[spark]` (MCP URL) and `[limits]` (max_retries, max_result_rows) sections
   to `agent.conf`.

**Acceptance**: a test question returns the right table(s) via the existing MCP
RAG path; the PySpark MCP server runs a trivial `count()` query on a sample
table.

### Phase 1 (P0) — Core loop on MemorySaver

Scope: complete graph without persistence. Verified by a dev script and a
Gradio review panel.

1. **State** (`agents/app/state.py`): `AnalysisState` with `user_prompt`,
   `retrieved_tables`, `pyspark_code`, `review_decision`,
   `execution_result_summary`, `execution_error`, `attempt_count`, `messages`.
   Only routing fields and artifacts; full results stay as references.
2. **Nodes** (new `agents/app/nodes/` package):
   - `retrieve_context` — LLM + `rag_search_documents` MCP → top tables into
     state
   - `generate_pyspark` — LLM with user prompt + retrieved schemas (+ any
     retry feedback) → `state.pyspark_code`
   - `review` — `interrupt()` with payload {pyspark_code, retrieved_tables,
     options: approve/reject}
   - `execute_via_mcp` — submit code to the PySpark MCP tool; success → result
     summary (top rows + schema) into state; failure → `state.execution_error`
   - `summarize` — LLM natural-language summary from the result summary; store
     result file at a download path
   - `revise_prompt` — merge prompt, previous code, and error/user feedback
     into a new prompt; increment `attempt_count`
3. **Edges**: retrieve → generate → review; conditional from review: approve →
   execute, reject → revise; execute: success → summarize, failure → revise;
   revise: `attempt_count < max_retries` → generate, else → END.
4. **Compile** with `MemorySaver`. Dev driver `agents/app/dev_run.py`: run the
   graph, handle the interrupt interactively, print summary and retrieved
   tables.
5. **Gradio UI v1** (refactor of `ui.py`):
   - Graph compiled once; per-session `gr.State` holding the checkpoint config
   - On interrupt: render a review panel — approve/reject radio, an edit-prompt
     textbox, and a Continue button — instead of a normal reply
   - Continue button resumes with the user's decision until END or interrupt;
     render the final summary plus the existing tool-call details
   - Download button serves the saved result file

The existing retrieval agent in `agent.py` is kept intact; it is not modified
by this phase.

**Acceptance**:

- Full happy path: prompt → tables retrieved → code shown → approve → run →
  summary + download
- Reject path returns to code generation with the user's edited prompt
- Bad code path: execution error feeds back into revision with no human input
- A contrived infinite loop terminates at `max_retries`
- All of the above visible in the Gradio UI; no full datasets leave the
  MCP/Spark side

### Phase 2 (P1) — Server, persistence, audit, CLI

Scope: one LangGraph Agent Server; CLI and GUI become its clients; PostgresSaver
is the single source of truth.

1. Move the compiled graph into a server-importable module
   (`agents/app/graph_analysis.py` exposing `app = graph.compile(...)`), with
   the checkpointer constructed there.
2. Serve the graph on port 2024 via the LangGraph SDK (`langgraph dev` for
   development, built server for deployment).
3. Postgres (local container for development): `PostgresSaver.setup()`; verify
   the four checkpoint tables and that `checkpoints` / `checkpoint_writes`
   contain a queryable per-thread history.
4. **Audit**: register a `query_audit_log` tool (read-only SQL over the
   checkpoint tables, filtered by `thread_id` or time range) so the agent can
   inspect its own history; provide a SQL view or script for ops.
5. **CLI client** (`agents/app/cli.py`, `langgraph_sdk`): create thread → run
   with `user_prompt` → on interrupt, print code and options, prompt for
   approve / reject + new prompt → resume → print summary and result file path.
6. **Gradio UI v2**: replace the in-process `ainvoke` with the `langgraph_sdk`
   client against the server (one thread per Gradio session). This gives the
   GUI the same durable interrupts, so a run started in the CLI can be
   approved from the GUI (and vice versa) against the same audit.
7. **Dependencies**: add `langgraph-sdk`, `langgraph-checkpoint-postgres`, and
   `psycopg` to `requirements-agent.txt`.

**Acceptance**:

- CLI completes a full end-to-end analysis including code review
- CLI-started / GUI-approved runs share one thread and one audit trail
- SQL against the checkpoint tables yields every node execution, review
  decision, execution summary, and per-attempt prompt for a given `thread_id`

### Phase 3 (P2) — GUI experience

1. If Gradio proves insufficient for the review UX (likely), build the React
   frontend with the official `@langchain/react` (`useStream`) or CopilotKit
   per the design doc:
   - Review card: pyspark code block, retrieved tables, approve / reject / edit
   - Node status cards: `retrieve_context` (tables), `execute_via_mcp`
     (status), `summarize` (summary + download)
   - Retry flow with visible attempt count; interrupts survive page refresh
     (server-side persistence)
2. If staying with Gradio: streaming node status bar, collapsible execution
   log, and download support.
3. Enforce result-size guards on download (top N rows inline; full file served
   from the MCP server download path when configured).

**Acceptance**: a user can start a run, approve code, view the summary, download
the file, and request a retry — without reload breaking an in-flight review.

### Phase 4 (P3) — Governance and hardening

1. Read-only enforcement: read-only Spark session / read-only parquet mount; the
   MCP server rejects DDL/DML
2. State hygiene: hard cap on `max_retries`, execution timeout per attempt,
   result-row cap in state
3. Checkpoint retention policy (TTL / cleanup job); review for PII in
   checkpointed prompts and code
4. Observability: structured logs per run, error classification, an audit view;
   re-evaluate `graph-abstract` only if a zero-config console is wanted
5. Security: bearer-token handling, download-path permissions

**Acceptance**: automated tests fail the build when any hard limit is bypassed;
all P0–P2 paths stay green.

## 5. Target module layout

    agents/app/
      main.py            (entry — unchanged)
      agent.py           (existing knowledge-retrieval agent — retained)
      state.py           (new: AnalysisState)
      nodes/
        retrieve_context.py
        generate_pyspark.py
        review.py
        execute.py
        summarize.py
        revise.py
      graph_analysis.py  (new: compiled graph + checkpointer)
      dev_run.py         (new, P0 dev driver)
      cli.py             (new, P1 langgraph-sdk CLI)
      ui.py              (P1 review panel → P2 langgraph_sdk client)
      mcp_client.py      (existing; may gain a second MCP base-URL for spark)
    agents/config/agent.conf
      [db]       postgres DSN            (P1)
      [data]     parquet directory
      [spark]    PySpark MCP URL
      [limits]   max_retries, max_result_rows

## 6. Dependency changes

`requirements-agent.txt` (existing: langgraph 0.2, langchain-core,
langchain-openai, mcp, gradio, pydantic) covers Phase 0.

- Phase 2 adds: `langgraph-sdk`, `langgraph-checkpoint-postgres`, `psycopg`
- Phase 3: no Python deps for Gradio; web build deps only if the React
  frontend is adopted

## 7. Testing approach

- Phase 0: RAG recall spot-checks over a fixed question set; trivial PySpark
  queries against sample data
- Phase 1: manual dev runs covering approve, reject + edit, execution failure,
  and max-retry exhaustion; state transitions asserted in the dev driver
- Phase 2: pytest fixtures for state/edge transitions; SQL audit assertions
  against a recorded run; CLI + GUI cross-client thread test
- Phase 3: UI smoke tests (review survives refresh, download, retry)
- The existing knowledge-retrieval agent path is regression-tested at every
  phase

## 8. Risks and open items

- **R1 (blocking)**: no Spark deployment exists; the execute path depends on
  standing up local Spark in Phase 0 — do this first
- **R2**: the Bonsai model may struggle with multi-step PySpark generation
  (max_tokens is currently 4096); budget for a larger code-generation budget
  or a dedicated code model
- **R3**: Gradio interrupt UX can be awkward; if so, Phase 3 is a hard
  requirement, not a nice-to-have
- **R4**: `mcp_client.py` opens a fresh MCP session per call; fine for P0
  (queries are stateless), revisit only for performance
- Open: canonical data directory, category taxonomy, which tables to onboard

## 9. Phase summary

| Phase | Goal | Key deliverables | Exit criteria |
|---|---|---|---|
| 0 | Environment & data prep | Spark + PySpark MCP, sample tables, table-metadata RAG index, config sections | Correct tables recalled; trivial query runs |
| 1 (P0) | Core loop on MemorySaver | StateGraph skeleton, 6 nodes, interrupt review, dev CLI, Gradio v1 | Full loop incl. reject/failure/retry-cap paths visible end-to-end |
| 2 (P1) | Persistence, audit, server, CLI | LangGraph server (:2024), PostgresSaver, audit tool, langgraph-sdk CLI, Gradio v2 | Durable interrupts; one shared thread/audit across CLI and GUI |
| 3 (P2) | GUI experience | Review card / node status cards / download / retry UI (Gradio or React) | Review survives refresh; full user flow without reload |
| 4 (P3) | Governance | Read-only enforcement, hard limits, retention, observability | Limits enforced by test; all prior paths green |
