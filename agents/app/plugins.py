"""Agent plugins: download chat history, upload a file to MCP, open a URL."""

import webbrowser
from datetime import datetime
from pathlib import Path

from agents.app import mcp_client
from agents.app.agent_config import get_path


def download_chat(history: list[dict], filename: str | None = None) -> Path:
    """Serialise Gradio chat history to a Markdown file in upload_dir.

    Each entry in history is {"role": "user"|"assistant", "content": str}.
    Returns the written path.
    """
    upload_dir = get_path("upload_dir")
    upload_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        filename = f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    lines = [f"# Chat export — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
    for msg in history:
        role = msg.get("role", "unknown").capitalize()
        lines.append(f"## {role}\n\n{msg.get('content', '')}\n")

    dest = upload_dir / filename
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


async def upload_file(local_path: str) -> str:
    """Copy a local file into upload_dir then import it via MCP.

    Returns the MCP tool response text.
    """
    import shutil

    src = Path(local_path).expanduser().resolve()
    upload_dir = get_path("upload_dir")
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = upload_dir / src.name
    if src != dest:
        shutil.copy2(src, dest)

    return await mcp_client.call_tool(
        "obsidian_import_conversations", {"path": str(dest)}
    )


def open_url(url: str) -> str:
    """Open url in the default Mac browser. Returns a confirmation string."""
    webbrowser.open(url)
    return f"Opened: {url}"
