"""LangGraph agent: call_llm → call_tool loop.

Tools are fetched from MCP once at graph-build time and converted to
LangChain StructuredTool objects so the LLM can see their schemas.
Tool calls are executed against the live MCP server.
"""

import asyncio
import json
from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from pydantic import ConfigDict, Field, create_model

from agents.app import mcp_client
from agents.app.llm import get_llm
from agents.app.skills import load_skills

# JSON Schema `type` -> Python type, used to build each tool's args schema.
_JSON_TYPES = {
    "string": str,
    "integer": int,
    "int": int,
    "number": float,
    "float": float,
    "boolean": bool,
    "bool": bool,
    "array": list,
    "object": dict,
}

# Keys the LLM may (mistakenly) nest the real args under; we unwrap them.
_ARG_WRAPPER_KEYS = ("config", "kwargs", "args", "arguments")


def _json_schema_to_args_model(schema: dict):
    """Convert an MCP JSON inputSchema into the Pydantic args model that
    LangChain shows to the LLM as the tool's argument schema.

    Previously `args_schema=None` let LangChain infer a generic
    `config/args/kwargs` schema from the `**kwargs` wrapper, so the model
    emitted `{"config": {...}}` and the MCP rejected it with "query required".
    Building the model from the MCP schema makes the LLM see the real fields
    (e.g. `query`), so it emits `{"query": "..."}`.
    """
    if not schema or not isinstance(schema, dict):
        return create_model("ToolArgs")
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    field_defs = {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            prop = {"type": "string"}
        ftype = _JSON_TYPES.get(prop.get("type"), object)
        if name in required:
            field_defs[name] = ftype  # bare type -> required field
        else:
            field_defs[name] = (ftype, Field(default=prop.get("default")))
    return create_model(
        "ToolArgs",
        __config__=ConfigDict(extra="ignore"),
        **field_defs,
    )


def _unwrap_args(args: dict) -> dict:
    """Unwrap args the LLM may have nested under config/kwargs.

    With the correct schema the model emits flat args, but a weak model can
    still nest the real args under 'kwargs'/'config'. If a wrapper key holds a
    non-empty dict, use it (keeping any genuine top-level keys); otherwise
    return the args unchanged.
    """
    if not isinstance(args, dict) or not args:
        return args
    for key in _ARG_WRAPPER_KEYS:
        inner = args.get(key)
        if isinstance(inner, dict) and inner:
            real = {k: v for k, v in args.items() if k not in _ARG_WRAPPER_KEYS}
            return {**real, **inner}
    return args


# Hard cap on tool-call rounds before the graph is forced to stop.
MAX_TOOL_CALLS = 10


def _call_signature(msg: AIMessage) -> tuple:
    """Stable signature of a tool-call round: (name, sorted-args) per call."""
    return tuple(
        (tc["name"], json.dumps(tc["args"], sort_keys=True))
        for tc in msg.tool_calls
    )


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _mcp_tool(name: str, description: str, schema: dict) -> StructuredTool:
    """Wrap an MCP tool as a LangChain StructuredTool that calls back over HTTP."""

    def _run(**kwargs):
        return asyncio.get_event_loop().run_until_complete(
            mcp_client.call_tool(name, kwargs)
        )

    async def _arun(**kwargs):
        return await mcp_client.call_tool(name, kwargs)

    return StructuredTool(
        name=name,
        description=description,
        args_schema=_json_schema_to_args_model(schema),
        func=_run,
        coroutine=_arun,
        # Pass the raw JSON schema so LangChain forwards it to the LLM.
        metadata={"inputSchema": schema},
    )


async def build_graph():
    """Fetch MCP tools and compile the LangGraph graph."""
    tool_schemas = await mcp_client.list_tools()
    tools = [_mcp_tool(t["name"], t["description"], t["inputSchema"]) for t in tool_schemas]
    tool_map = {t.name: t for t in tools}

    skill_text = load_skills()
    system_msg = SystemMessage(
        content=(skill_text + "\n\n" if skill_text else "")
        + "You are a helpful assistant with access to a personal knowledge pipeline. "
        + "You have access to tools that let you search and read documents. "
        + "Use these tools to gather the information you need, then answer the user's question. "
        + "When you have enough information to answer the user's question, stop calling tools "
        + "and provide your final answer in a single message. Do not keep calling tools "
        + "indefinitely or repeat the same search. If you cannot find the information you "
        + "need, say so and give your best answer based on what you have. "
        + "Always end with a clear, helpful answer to the user's question."
    )

    llm = get_llm().bind_tools(tools)

    def call_llm(state: AgentState) -> dict:
        messages = [system_msg] + state["messages"]
        response: AIMessage = llm.invoke(messages)
        return {"messages": [response]}

    async def call_tool(state: AgentState) -> dict:
        last: AIMessage = state["messages"][-1]
        results = []
        for tc in last.tool_calls:
            try:
                output = await mcp_client.call_tool(
                    tc["name"], _unwrap_args(tc["args"])
                )
            except Exception as exc:
                output = f"ERROR: {exc}"
            results.append(
                ToolMessage(content=output, tool_call_id=tc["id"], name=tc["name"])
            )
        return {"messages": results}

    def should_continue(state: AgentState) -> str:
        messages = state["messages"]
        last = messages[-1]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            return END

        # Tool-call rounds = AIMessages that requested tools.
        rounds = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]

        # Safety net: hard cap so a stuck model can't loop forever.
        if len(rounds) >= MAX_TOOL_CALLS:
            return END

        # Loop detection: if the last two rounds requested the same tools
        # with the same args, the model is stuck — stop it.
        if len(rounds) >= 2:
            prev = _call_signature(rounds[-2])
            curr = _call_signature(rounds[-1])
            if prev == curr:
                return END

        return "call_tool"

    graph = StateGraph(AgentState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("call_tool", call_tool)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges("call_llm", should_continue)
    graph.add_edge("call_tool", "call_llm")
    return graph.compile()
