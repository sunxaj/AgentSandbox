"""Thin async wrapper around the MCP Streamable HTTP transport.

Opens a fresh session per call so the agent process needs no persistent
background task.  All network errors propagate to the caller.
"""

import json
from agents.app.agent_config import get
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession


def _headers() -> dict:
    return {"Authorization": f"Bearer {get('mcp', 'bearer_token')}"}


async def list_tools() -> list[dict]:
    """Return MCP tool schemas as plain dicts (name, description, inputSchema)."""
    url = get("mcp", "url")
    async with streamablehttp_client(url, headers=_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema,
                }
                for t in result.tools
            ]


async def call_tool(name: str, arguments: dict) -> str:
    """Call a single MCP tool and return its text content as a string."""
    url = get("mcp", "url")
    async with streamablehttp_client(url, headers=_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            parts = [
                c.text if hasattr(c, "text") else json.dumps(c)
                for c in result.content
            ]
            return "\n".join(parts)
