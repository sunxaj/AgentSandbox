# 企业数据分析、审核与报表生成 Agent —— 方案设计

本方案将你描述的 6 步流程映射为一个 **LangGraph `StateGraph`**，其中“用户审核代码”和“不满意则循环”对应 `interrupt` 与条件边，**MCP + PySpark** 作为执行数据的“手脚”，**PostgresSaver** 同时承担检查点与审计日志的双重角色，CLI 和 GUI 共享同一个后端图，通过不同的客户端接入。

---

## 总体架构：一张图，两种前端

整个 Agent 是一个编译后的 `StateGraph`，部署为 LangGraph Agent Server。CLI 和 GUI 都是这个 Server 的**客户端**：CLI 通过 LangGraph SDK 以交互式方式驱动图运行，GUI 通过前端 SDK（`useStream` 或 CopilotKit）渲染节点状态和审批卡片。两者操作的是**同一张图、同一套状态、同一个审计数据库**，不存在“两套逻辑”的维护负担。

```
用户输入 ──► [ retrieve_context ] ──► [ generate_pyspark ] ──► interrupt (审核代码)
                                                                    │
                              ┌─────────────────────────────────────┘
                              ▼
                     [ execute_via_mcp ] ──► [ summarize ] ──► END (用户满意)
                              │                                    ▲
                              │ 执行失败 / 用户不满意                │
                              └──────────► [ revise_prompt ] ──────┘
```

**状态设计原则**：遵循“状态只放路由所需的最小路由字段和累积工件”。`pyspark_code` 以字符串存入状态（它是路由和审核的对象），但执行结果不在状态中传递完整 DataFrame，而是存**引用**（如临时表名或结果摘要），原始数据始终留在 MCP 服务器侧。

---

## 逐节点设计

### 节点 1：`retrieve_context`（RAG 检索表元数据）

目标：根据用户问题，检索出相关的**表名、类别、存储路径（Parquet 路径）和 schema**，作为生成 PySpark 代码的约束上下文。

推荐采用 **schema-embedding RAG** 模式：将每张表的元数据（表名、类别、路径、列名、类型、示例值）作为一个 `Document` 向量化存入向量库，检索时按语义相似度召回最相关的 Top-K 张表。检索结果注入 `generate_pyspark` 节点的提示词，而不是把整个数据目录塞进去，避免上下文膨胀和路由混乱。

```python
# 建库时的元数据构造（一次性离线完成）
from langchain_core.documents import Document

def table_metadata_to_doc(table_meta: dict) -> Document:
    content = f"""
    表名: {table_meta['table_name']}
    类别: {table_meta['category']}
    存储路径: {table_meta['parquet_path']}
    列: {table_meta['columns']}
    """
    return Document(
        page_content=content,
        metadata={"table_name": table_meta["table_name"], "path": table_meta["parquet_path"]},
    )
```

检索到的 `parquet_path` 和 `schema` 直接进入后续节点，LLM 不需要“记住”路径，而是由状态携带。

---

### 节点 2：`generate_pyspark` + **审核中断**

`generate_pyspark` 节点调用 LLM，根据用户提示词 + 节点1检索到的表上下文，生成 PySpark 代码，写入 `state["pyspark_code"]`。

**审核**不在这个节点内部完成，而是在该节点之后设置一个**独立的 `review_node`**，使用 `interrupt()` 暂停图执行：

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

`interrupt()` 会向客户端返回一个 interrupt 负载，**图在此处 durable 地暂停**——用户可以刷新页面、换一个组件审批，Agent 不会重放已执行的步骤。审批后，客户端以 `Command(resume=decision)` 恢复执行。路由函数根据 `review_decision` 决定进入 `execute_via_mcp` 还是回到 `revise_prompt`。

---

### 节点 3：`execute_via_mcp`（通过 MCP 执行 PySpark，读取 Parquet）

**直接使用现成的 `pyspark-mcp` 或 `spark-connect-mcp` 作为 MCP 服务器**，不需要自己从零实现 Spark 连接层。

- **`pyspark-mcp`**：`pip install pyspark-mcp`，启动后暴露 tools，包括 `Get table schema`、`Run query, get the result`、`Read first N lines` 等。它自动处理 `spark-submit` 配置，支持 `--master yarn`、`--conf`、`--jars` 等标准 Spark 选项，适合已有 YARN/Standalone Spark 集群的场景。它还暴露 catalog 和 table 信息，可以与节点1的 RAG 形成互补——RAG 提供语义路由，MCP 的 `Get table schema` 提供**权威的实时 schema 校验**。
- **`spark-connect-mcp`**：如果你的环境使用 Spark Connect（或 Databricks Connect），通过 `SPARK_REMOTE=sc://host:15002` 即可连接，MCP tools 无需额外参数。Databricks 用户选择 `[databricks]` 后端即可。

在 LangGraph 侧，将 MCP 服务器注册为工具，`execute_via_mcp` 节点调用该工具，提交 `state["pyspark_code"]`，将返回的**结果摘要**（而非完整数据集）写入状态，完整结果留在 MCP 服务器侧供用户下载。执行失败时，将错误信息写入 `state["execution_error"]`，通过条件边路由回 `revise_prompt`。

**执行提交钩子（MCP 调用审计）。** `execute_via_mcp` 在把代码交给 MCP 服务器的瞬间触发一个钩子，记录**被发送执行的命令**的**追加式审计事件**：`user`（发起运行/触发本次尝试的认证操作者）、`time`（UTC 时间）、`syntax`（实际提交的完整 PySpark 代码载荷）、`mcp_target`（MCP 服务器基础 URL）、`mcp_tool_name`（调用的工具，如 `run_query` 或 `get_table_schema`），另加 `thread_id`、`attempt`、`status` 作关联字段。P0 阶段追加写入本地 JSONL 文件（`agents/data/mcp_execution_log.jsonl`）；P1 阶段写入 Postgres 的追加式 `mcp_execution_log` 表，可由 `query_audit_log` 工具查询。它**补充**（而非替代）PostgresSaver 检查点历史：检查点保存每节点的*状态快照*，而钩子保存一条精确的命令级“谁 / 何时 / 什么 / 发给哪个目标与工具”审计行。

**安全约束**：MCP 服务器应连接**只读 Spark 会话**或只读存储挂载，PySpark 代码在服务器侧执行，不将 Parquet 数据拉入 LLM 上下文。

---

### 节点 4：`summarize` 与下载

`summarize` 节点调用 LLM，基于 `state["execution_result_summary"]`（或结果的前 N 行 + schema）生成自然语言摘要，写回状态。用户可以选择下载结果文件和摘要。实现上，结果文件可由 MCP 服务器写入一个临时可下载路径，摘要则从状态中取。这个节点不需要中断，是线性步骤。

---

### 节点 5：`revise_prompt` —— 循环回第 2 步

当**执行出错**或**用户审核后拒绝代码**或**用户对结果不满意**时，进入 `revise_prompt`：

- 如果是审核拒绝：`state["review_decision"] == "reject"`，携带用户修改后的提示词（`Command(resume=...)` 可传回任意 JSON 负载）。
- 如果是执行失败：将 `state["execution_error"]` 作为反馈注入。
- 如果是结果不满意：用户在 GUI/CLI 中修改提示词并提交。

`revise_prompt` 节点将原提示词、原代码、失败原因/用户反馈合并为新的提示词，更新 `state["user_prompt"]`，然后通过**条件边**回到 `generate_pyspark`。LangGraph 原生支持这种“生成 → 验证 → 重试”的循环模式，用条件边读取 `state` 中的路由字段即可，无需特殊语法。建议在状态中维护 `attempt_count`，并在条件边中设置 `max_retries` 硬上限，防止无限循环。

---

### 节点 6：审计日志 —— 用 PostgresSaver 同时做检查点和审计库

这是方案中最值得利用 LangGraph 原生能力的一步。**PostgresSaver 不仅保存检查点，它的检查点表本身就是一张可查询的审计日志**：每个节点的执行、状态变更、interrupt 的触发与恢复、每次循环的尝试，都被序列化写入 PostgreSQL。在企业级部署中，直接用 SQL 查询检查点表就可以替代独立的审计日志基础设施。

四张核心表 `checkpoints`、`checkpoint_writes`、`checkpoint_blobs`、`checkpoint_migrations` 分别存储快照、增量写入、大对象和迁移版本。你的**用户提示词、生成的 PySpark 代码、审核决定、执行结果摘要、错误信息、每次循环的提示词版本**，全部都在状态中，因而全部在检查点历史中可追溯。

```python
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg

conn = psycopg.connect(DB_URI, autocommit=True)
checkpointer = PostgresSaver(conn)
checkpointer.setup()          # 自动建表
app = graph.compile(checkpointer=checkpointer)
```

**“Agent 随时查询日志记录”** 有两种实现方式：

1. **作为工具查询**：为 Agent 注册一个 `query_audit_log` 工具，该工具用只读 SQL 查询 `checkpoints` 表，按 `thread_id` 或时间范围过滤，返回该会话的历史步骤摘要。
2. **通过 LangGraph Store 查询**：如果需要跨 thread 的长期记忆和聚合查询，使用 LangGraph 内置的 persistent store，它与 checkpoint 互补，适合存放“日志聚合摘要”等长期数据。

> **一个务实的选择**：如果团队希望零配置获得“检查点 + 遥测日志 + 可视化控制台”，可以评估 `graph-abstract` 库，它通过替换 `StateGraph` 导入即可自动配置 PostgreSQL checkpointing 和 execution trace logging，并提供可选的 Streamlit 审计控制台。但核心审计能力 PostgresSaver 原生就已具备，不必额外引入依赖。

---

## CLI 与 GUI：共享同一后端的双端接入

### CLI：基于 LangGraph SDK 的交互式驱动

`langgraph dev` / `langgraph build` 是**开发与部署**工具，用于启动 Agent Server，而不是面向最终用户的交互式数据分析 CLI。你需要的是一个**客户端 CLI**，通过 `langgraph_sdk` 连接已部署的 Server：

```python
from langgraph_sdk import get_client
from langgraph_sdk.schema import Command

client = get_client(url="http://your-agent-server:2024")
thread = await client.threads.create()
thread_id = thread["thread_id"]

# 启动图，直到 interrupt
result = await client.runs.wait(
    thread_id, "data_analysis_agent",
    input={"user_prompt": "统计各部门Q3的差旅费用"}
)

# 检查是否在审核点中断
if "__interrupt__" in result:
    interrupt_payload = result["__interrupt__"][0]
    print("生成的 PySpark 代码：")
    print(interrupt_payload["value"]["pyspark_code"])
    decision = input("输入 approve / reject / 修改后的提示词：")

    # 恢复执行
    result = await client.runs.wait(
        thread_id, "data_analysis_agent",
        command=Command(resume=decision)
    )
```

CLI 的优势在于：它**不绕过审核点**。`interrupt` 在 Server 侧暂停，CLI 只是决定何时以及如何恢复。执行结果和摘要可以打印到终端，下载通过 MCP 服务器暴露的下载路径完成。

### GUI：用前端 SDK 渲染“图原生”的审核界面

官方前端 SDK（`@langchain/react` 等）的 `useStream` 暴露 `stream.interrupt`，当图在 `review_node` 暂停时，GUI 渲染一个**审批卡片**，展示 PySpark 代码、检索到的表路径、以及 approve/reject 选项；用户操作后调用 `stream.submit(null, { command: { resume: response } })` 恢复执行。官方 SDK 还将 `stream.values` 和 `stream.subgraphs` 暴露给前端，你可以把每个节点映射为一张 UI 卡片——例如 `retrieve_context` 显示“检索到的表”，`execute_via_mcp` 显示“执行状态”，`summarize` 显示摘要和下载按钮。

如果希望更快获得带审批工作流的完整 React 聊天运行时，**CopilotKit** 是官方文档中明确推荐的集成方案：它在 LangGraph 部署旁挂载一个自定义 HTTP 路由，前端通过 `CopilotKit` 运行时连接，可以渲染结构化的 UI 负载，而不只是纯文本消息。对于“审核代码 → 批准/拒绝 → 恢复”这种交互，CopilotKit 的现成聊天运行时可以减少自建 `stream.messages` 的工作量。

**CLI 与 GUI 的审计一致性**：两者操作的是同一个 `thread_id` 或至少同一套 `PostgresSaver`。无论从哪个端发起审核、从哪个端恢复，检查点表都是唯一真相源。你可以在 CLI 中启动一个分析，在 GUI 中完成审批，审计日志不会因为切换客户端而分裂。

---

## 分阶段落地建议

| 阶段 | 目标 | 关键交付 |
|---|---|---|
| **P0：验证核心循环** | 在 `MemorySaver` 上跑通“检索 → 生成 → 审核中断 → MCP 执行 → 摘要 → 循环”的完整图 | `StateGraph` 骨架、`pyspark-mcp` 连接、`interrupt` 审批节点 |
| **P1：生产持久化与审计** | 换 `PostgresSaver`，验证检查点表可查询；CLI 客户端可用 | PostgresSaver 配置、`mcp_execution_log` 表（user、time、syntax、mcp_target、mcp_tool_name）、审计查询工具、CLI SDK 封装 |
| **P2：GUI 与体验** | 用 `useStream` 或 CopilotKit 实现审核卡片、结果下载、循环重试界面 | 前端审批组件、节点状态卡片 |
| **P3：治理与扩展** | 只读 Spark 会话、结果大小上限、`max_retries` 硬限制、检查点保留策略 | 安全边界配置、运维策略 |

**需要提前确认的两个工程决策**：① MCP 服务器选择 `pyspark-mcp` 还是 `spark-connect-mcp`，取决于你的 Spark 部署模式（YARN/Standalone vs Spark Connect/Databricks）；② GUI 选择官方前端 SDK 还是 CopilotKit，取决于团队是否愿意维护自定义 React 组件与流式渲染逻辑。两者都是官方支持的路径，CLI 与后端图的设计不受这一选择影响。