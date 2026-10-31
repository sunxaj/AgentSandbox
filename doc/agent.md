# Enterprise Data Analysis, Review & Report Generation Agent — Solution Design

This solution maps your 6-step workflow onto a **LangGraph `StateGraph`**, where "user code review" and "unsatisfied → loop back" correspond to `interrupt` and conditional edges, **MCP + PySpark** serve as the "hands and feet" for data execution, **PostgresSaver** simultaneously acts as both checkpoint store and audit log, and CLI and GUI share the same backend graph via different clients.

---

## Overall Architecture: One Graph, Two Frontends

The entire Agent is a compiled `StateGraph`, deployed as a LangGraph Agent Server. CLI and GUI are both **clients** of this Server: the CLI drives the graph interactively via the LangGraph SDK, while the GUI renders node states and approval cards via the frontend SDK (`useStream` or CopilotKit). Both operate on **the same graph, the same state, and the same audit database** — there is no "two sets of logic" maintenance burden.

```
User Input ──► [ retrieve_context ] ──► [ generate_pyspark ] ──► interrupt (code review)
                                                                    │
                              ┌─────────────────────────────────────┘
                              ▼
                     [ execute_via_mcp ] ──► [ summarize ] ──► END (user satisfied)
                              │                                    ▲
                              │ execution failed / user unsatisfied │
                              └──────────► [ revise_prompt ] ──────┘
```

**State design principle**: Follow the rule of "keep only the minimal routing fields and accumulated artifacts in state that routing requires." Store `pyspark_code` as a string in state (it is the object of routing and review), but do not pass the full DataFrame of execution results through state — store a **reference** (e.g., temp table name or result summary) instead. Raw data always remains on the MCP server side.

---

## Node-by-Node Design

### Node 1: `retrieve_context` (RAG Retrieval of Table Metadata)

Goal: Based on the user's question, retrieve the relevant **table names, categories, storage paths (Parquet paths), and schemas** as constrained context for generating PySpark code.

The recommended approach is the **schema-embedding RAG** pattern: vectorize each table's metadata (table name, category, path, column names, types, sample values) as a `Document` into the dedicated `metadata` collection. During retrieval, recall the Top-K most relevant tables by semantic similarity. The retrieval results are injected into the `generate_pyspark` node's prompt, rather than dumping the entire data catalog into the context — this avoids context bloat and routing confusion.

```python
# One-time offline metadata construction at index-building time
from langchain_core.documents import Document

def table_metadata_to_doc(table_meta: dict) -> Document:
    content = f"""
    Table Name: {table_meta['table_name']}
    Category: {table_meta['category']}
    Storage Path: {table_meta['parquet_path']}
    Columns: {table_meta['columns']}
    """
    return Document(
        page_content=content,
        metadata={"table_name": table_meta["table_name"], "path": table_meta["parquet_path"]},
    )
```

The retrieved `parquet_path` and `schema` flow directly into subsequent nodes. The LLM does not need to "remember" paths — they are carried by state.

---

### Node 2: `generate_pyspark` + **Review Interrupt**

The `generate_pyspark` node calls the LLM to generate PySpark code based on the user prompt + table context retrieved in Node 1, writing it to `state["pyspark_code"]`.

**Review** is not performed inside this node. Instead, a **separate `review_node`** is placed after it, using `interrupt()` to pause graph execution:

```python
from langgraph.types import interrupt

def review_node(state: State) -> dict:
    decision = interrupt({
        "type": "code_review",
        "pyspark_code": state["pyspark_code"],
        "retrieved_tables": state["retrieved_tables"],
        "options": ["approve", "reject"],
    })
    return {"review_decision": decision}
```

`interrupt()` returns an interrupt payload to the client, and **the graph durably pauses here** — the user can refresh the page, switch to another component for approval, and the Agent will not replay already-executed steps. After approval, the client resumes execution with `Command(resume=decision)`. The routing function decides whether to enter `execute_via_mcp` or loop back to `revise_prompt` based on `review_decision`.

---

### Node 3: `execute_via_mcp` (Execute PySpark via MCP, Read Parquet)

**Directly use an existing `pyspark-mcp` or `spark-connect-mcp` as the MCP server** — no need to implement the Spark connection layer from scratch.

- **`pyspark-mcp`**: `pip install pyspark-mcp`. Once started, it exposes tools including `Get table schema`, `Run query, get the result`, `Read first N lines`, etc. It automatically handles `spark-submit` configuration and supports standard Spark options such as `--master yarn`, `--conf`, `--jars` — suitable for environments with an existing YARN/Standalone Spark cluster. It also exposes catalog and table information, which can complement Node 1's RAG — RAG provides semantic routing, while MCP's `Get table schema` provides **authoritative real-time schema validation**.
- **`spark-connect-mcp`**: If your environment uses Spark Connect (or Databricks Connect), simply set `SPARK_REMOTE=sc://host:15002` to connect, and the MCP tools require no additional parameters. Databricks users can choose the `[databricks]` backend.

On the LangGraph side, register the MCP server as a tool. The `execute_via_mcp` node calls this tool, submits `state["pyspark_code"]`, and writes the returned **result summary** (not the full dataset) into state. The full result remains on the MCP server side for user download. On execution failure, write the error message to `state["execution_error"]` and route back to `revise_prompt` via a conditional edge.

**Execution submission hook (MCP call audit).** At the moment `execute_via_mcp` hands the code to the MCP server, a small hook fires. It records an **append-only audit event** of *the command that was sent for execution*: `user` (the authenticated actor who initiated the run / triggered this attempt), `time` (UTC), `syntax` (the full PySpark code payload actually submitted), `mcp_target` (the MCP server base URL), and `mcp_tool_name` (the tool invoked, e.g. `run_query` or `get_table_schema`), plus `thread_id`, `attempt`, and `status` for correlation. In P0 this appends to a local JSONL file (`agents/data/mcp_execution_log.jsonl`); in P1 it writes to an append-only Postgres `mcp_execution_log` table that the `query_audit_log` tool can read. This **complements** the PostgresSaver checkpoint history: checkpoints store per-node *state snapshots*, while the hook stores a precise per-command "who / when / what / against which target and tool" line.

**Security constraint**: The MCP server should connect to a **read-only Spark session** or read-only storage mount. PySpark code executes on the server side, and Parquet data is never pulled into the LLM context.

---

### Node 4: `summarize` and Download

The `summarize` node calls the LLM to generate a natural-language summary based on `state["execution_result_summary"]` (or the first N rows + schema of the result), writing it back to state. The user can choose to download the result file and the summary. In implementation, the result file can be written by the MCP server to a temporary downloadable path, and the summary is taken from state. This node does not require an interrupt — it is a linear step.

---

### Node 5: `revise_prompt` — Loop Back to Step 2

When **execution fails**, **the user rejects the code after review**, or **the user is unsatisfied with the result**, enter `revise_prompt`:

- If review rejected: `state["review_decision"] == "reject"`, carrying the user's modified prompt (`Command(resume=...)` can pass back any JSON payload).
- If execution failed: inject `state["execution_error"]` as feedback.
- If result unsatisfied: the user modifies the prompt in the GUI/CLI and submits.

The `revise_prompt` node merges the original prompt, original code, failure reason/user feedback into a new prompt, updates `state["user_prompt"]`, and then loops back to `generate_pyspark` via a **conditional edge**. LangGraph natively supports this "generate → validate → retry" loop pattern — simply read the routing field in `state` from the conditional edge, no special syntax required. It is recommended to maintain an `attempt_count` in state and set a `max_retries` hard limit in the conditional edge to prevent infinite loops.

---

### Node 6: Audit Log — Using PostgresSaver as Both Checkpoint and Audit Store

This is the step in the solution that most leverages LangGraph's native capabilities. **PostgresSaver not only saves checkpoints — its checkpoint tables themselves are a queryable audit log**: every node execution, state change, interrupt trigger and resume, and every loop iteration is serialized and written to PostgreSQL. In enterprise deployments, querying the checkpoint tables directly with SQL can replace separate audit log infrastructure.

The four core tables `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, and `checkpoint_migrations` store snapshots, incremental writes, large objects, and migration versions, respectively. Your **user prompts, generated PySpark code, review decisions, execution result summaries, error messages, and prompt versions for each loop iteration** are all in state, and therefore all traceable in the checkpoint history.

```python
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg

conn = psycopg.connect(DB_URI, autocommit=True)
checkpointer = PostgresSaver(conn)
checkpointer.setup()          # auto-create tables
app = graph.compile(checkpointer=checkpointer)
```

**"Agent can query log records at any time"** has two implementation approaches:

1. **As a tool query**: Register a `query_audit_log` tool for the Agent. This tool uses read-only SQL to query the `checkpoints` table, filtering by `thread_id` or time range, and returns a summary of historical steps for that session.
2. **Via LangGraph Store**: If cross-thread long-term memory and aggregate queries are needed, use LangGraph's built-in persistent store. It complements checkpoints and is suitable for storing "log aggregation summaries" and other long-term data.

> **A pragmatic option**: If the team wants zero-configuration "checkpoints + telemetry logs + visual console," consider evaluating the `graph-abstract` library. By simply replacing the `StateGraph` import, it automatically configures PostgreSQL checkpointing and execution trace logging, and provides an optional Streamlit audit console. However, the core audit capability is natively available with PostgresSaver — no additional dependency is strictly necessary.

---

## CLI and GUI: Dual Access to the Same Backend

### CLI: Interactive Driving via LangGraph SDK

`langgraph dev` / `langgraph build` are **development and deployment** tools for starting the Agent Server, not an interactive data-analysis CLI for end users. What you need is a **client CLI** that connects to the deployed Server via `langgraph_sdk`:

```python
from langgraph_sdk import get_client
from langgraph_sdk.schema import Command

client = get_client(url="http://your-agent-server:2024")
thread = await client.threads.create()
thread_id = thread["thread_id"]

# Start the graph until interrupt
result = await client.runs.wait(
    thread_id, "data_analysis_agent",
    input={"user_prompt": "Count Q3 travel expenses by department"}
)

# Check whether it interrupted at the review point
if "__interrupt__" in result:
    interrupt_payload = result["__interrupt__"][0]
    print("Generated PySpark code:")
    print(interrupt_payload["value"]["pyspark_code"])
    decision = input("Enter approve / reject / modified prompt: ")

    # Resume execution
    result = await client.runs.wait(
        thread_id, "data_analysis_agent",
        command=Command(resume=decision)
    )
```

The CLI's advantage: it **does not bypass the review point**. The `interrupt` pauses on the Server side, and the CLI only decides when and how to resume. Execution results and summaries can be printed to the terminal, and downloads are completed via the download path exposed by the MCP server.

### GUI: Rendering "Graph-Native" Review Interfaces with the Frontend SDK

The official frontend SDK (`@langchain/react`, etc.) exposes `stream.interrupt` via `useStream`. When the graph pauses at `review_node`, the GUI renders an **approval card** showing the PySpark code, retrieved table paths, and approve/reject options. After the user acts, it calls `stream.submit(null, { command: { resume: response } })` to resume execution. The official SDK also exposes `stream.values` and `stream.subgraphs` to the frontend. You can map each node to a UI card — for example, `retrieve_context` shows "Retrieved Tables," `execute_via_mcp` shows "Execution Status," and `summarize` shows the summary and download button.

If you want to get a complete React chat runtime with approval workflows faster, **CopilotKit** is an integration explicitly recommended in the official documentation: it mounts a custom HTTP route alongside the LangGraph deployment, and the frontend connects via the `CopilotKit` runtime, which can render structured UI payloads rather than just plain text messages. For interactions like "review code → approve/reject → resume," CopilotKit's ready-made chat runtime can reduce the work of building `stream.messages` yourself.

**Audit consistency between CLI and GUI**: Both operate on the same `thread_id` or at least the same `PostgresSaver`. Regardless of which end initiates the review and which end resumes, the checkpoint table is the single source of truth. You can start an analysis in the CLI and complete approval in the GUI — the audit log will not fragment due to switching clients.

---

## Phased Implementation Recommendations

| Phase | Goal | Key Deliverables |
|---|---|---|
| **P0: Validate Core Loop** | Run the complete "retrieve → generate → review interrupt → MCP execute → summarize → loop" graph on `MemorySaver` | `StateGraph` skeleton, `pyspark-mcp` connection, `interrupt` approval node |
| **P1: Production Persistence & Audit** | Switch to `PostgresSaver`, verify checkpoint tables are queryable; CLI client available | PostgresSaver configuration, `mcp_execution_log` table (user, time, syntax, mcp_target, mcp_tool_name), audit query tools, CLI SDK wrapper |
| **P2: GUI & Experience** | Implement review cards, result download, and loop-retry interfaces using `useStream` or CopilotKit | Frontend approval components, node status cards |
| **P3: Governance & Scaling** | Read-only Spark session, result size limits, `max_retries` hard limit, checkpoint retention policy | Security boundary configuration, operational policies |

**Two engineering decisions to confirm early**: ① Whether the MCP server should be `pyspark-mcp` or `spark-connect-mcp` depends on your Spark deployment mode (YARN/Standalone vs Spark Connect/Databricks); ② Whether the GUI should use the official frontend SDK or CopilotKit depends on whether the team is willing to maintain custom React components and streaming rendering logic. Both are officially supported paths, and the CLI and backend graph design are unaffected by this choice.
