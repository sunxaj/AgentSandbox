"""Gradio chat UI wrapping the LangGraph agent.

Layout
------
- ChatInterface for conversation (streams full buffered response).
- Sidebar row: Download Chat button, Upload File component, Open URL textbox.

Tool calls are rendered as collapsed <details> blocks inside the assistant
message after the full response is buffered.
"""

import asyncio

import gradio as gr
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.app import plugins
from agents.app.agent import build_graph

# Built once at startup; reused across all sessions.
_graph = None


def _format_tool_calls(messages: list) -> str:
    """Render tool call/result pairs as collapsed Markdown details blocks."""
    blocks = []
    tool_results: dict[str, str] = {}

    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_results[msg.tool_call_id] = msg.content

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                result = tool_results.get(tc["id"], "")
                import json
                args_str = json.dumps(tc["args"], indent=2)
                blocks.append(
                    f"<details><summary>🔧 {tc['name']}</summary>\n\n"
                    f"**Args:**\n```json\n{args_str}\n```\n\n"
                    f"**Result:**\n```\n{result}\n```\n</details>"
                )
    return "\n\n".join(blocks)


async def _chat(message: str, history: list[dict]) -> str:
    """Run the agent graph and return the final assistant text."""
    lc_messages = []
    for h in history:
        if h["role"] == "user":
            lc_messages.append(HumanMessage(content=h["content"]))
        else:
            lc_messages.append(AIMessage(content=h["content"]))
    lc_messages.append(HumanMessage(content=message))

    # Raise the recursion limit so multi-step tool use (e.g. repeated
    # search_documents calls) has room to complete instead of crashing.
    result = await _graph.ainvoke(
        {"messages": lc_messages}, config={"recursion_limit": 50}
    )
    all_messages = result["messages"]

    # Last AIMessage is the final reply.
    final_text = ""
    for msg in reversed(all_messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls:
            final_text = msg.content
            break

    tool_block = _format_tool_calls(all_messages)
    return (tool_block + "\n\n" + final_text).strip() if tool_block else final_text


def _download_chat(history: list[dict]):
    path = plugins.download_chat(history)
    return str(path), gr.update(value=f"Saved to {path}")


async def _upload_file(file_obj, status):
    if file_obj is None:
        return gr.update(value="No file selected.")
    result = await plugins.upload_file(file_obj.name)
    return gr.update(value=f"Uploaded: {result[:200]}")


def _open_url(url: str):
    msg = plugins.open_url(url)
    return gr.update(value=msg)


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Sandbox Agent") as demo:
        gr.Markdown("## Sandbox Agent")

        chatbot = gr.Chatbot(type="messages", height=520, render_markdown=True)
        chat = gr.ChatInterface(
            fn=_chat,
            chatbot=chatbot,
            type="messages",
        )

        with gr.Row():
            dl_btn = gr.Button("⬇ Download Chat")
            dl_status = gr.Textbox(label="", interactive=False, scale=3)

        with gr.Row():
            upload = gr.File(label="Upload file to MCP", file_count="single")
            upload_status = gr.Textbox(label="", interactive=False, scale=3)

        with gr.Row():
            url_box = gr.Textbox(label="Open URL", placeholder="https://...")
            url_btn = gr.Button("Open")
            url_status = gr.Textbox(label="", interactive=False, scale=3)

        dl_btn.click(
            fn=_download_chat,
            inputs=[chatbot],
            outputs=[gr.File(visible=False), dl_status],
        )
        upload.change(
            fn=_upload_file,
            inputs=[upload, upload_status],
            outputs=[upload_status],
        )
        url_btn.click(
            fn=_open_url,
            inputs=[url_box],
            outputs=[url_status],
        )

    return demo


def launch():
    global _graph
    _graph = asyncio.get_event_loop().run_until_complete(build_graph())
    ui = build_ui()
    ui.launch(server_name="127.0.0.1", server_port=7860)
