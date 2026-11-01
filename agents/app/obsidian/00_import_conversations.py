
#!/usr/bin/env python3
"""Convert ChatGPT JSONL archives and copy Markdown into staging and Obsidian Inbox.

Each source folder maps to a matching output subfolder. Existing files are
preserved, making repeat imports safe and allowing a retry to fill a missing
Inbox mirror without replacing prior content.
"""

import json
import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# This importer is agent-owned. Load its sibling runtime module explicitly so
# direct CLI runs do not depend on the MCP package or the current directory.
OBSIDIAN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OBSIDIAN_DIR))
from runtime_config import get_path, get_vault_folder

OUTPUT_DIR = get_path("import_output")
SOURCE_DIR = get_path("conversation_exports")
INBOX_DIR = get_vault_folder("inbox")


def safe_filename(text, max_length=90):
    """Remove filesystem-reserved characters while keeping a readable title."""
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length].strip(" .") or "Untitled"

def yaml_string(value):
    """Use JSON quoting, which is also valid YAML scalar syntax."""
    # JSON strings are valid YAML quoted strings.
    return json.dumps(str(value), ensure_ascii=False)

def format_time(value):
    """Format Unix timestamps consistently; unknown or invalid values stay explicit."""
    if not isinstance(value, (int, float)):
        return "Unknown"
    try:
        return datetime.fromtimestamp(
            value, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError, OverflowError):
        return "Unknown"

def format_content(content):
    """Render text as Markdown and preserve non-text message payloads as JSON."""
    if content is None or content == "":
        return "_[No text content]_"
    if isinstance(content, str):
        return content.strip() or "_[No text content]_"
    return "```json\n" + json.dumps(
        content, ensure_ascii=False, indent=2
    ) + "\n```"

def load_conversation(path):
    """Read JSONL records into a message-id map and conversation-level metadata."""
    metadata = None
    messages = {}

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"WARNING: {path.name}:{line_number}: {exc}")
                continue

            if not isinstance(record, dict):
                continue

            # Export rows are either message nodes or one conversation metadata record.
            if isinstance(record.get("message"), dict):
                msg = record["message"]
                msg_id = msg.get("id")
                if msg_id:
                    messages[msg_id] = msg
            elif "currNode" in record and "id" in record:
                metadata = record

    if metadata is None:
        metadata = {}

    return metadata, messages

def main_path(messages, current_id):
    """Follow parent links from currNode back to the root."""
    path = []
    seen = set()
    node_id = current_id

    # The export is a graph of message branches; walk parent IDs from the selected leaf.
    # `seen` also prevents malformed parent cycles from looping forever.
    while node_id and node_id in messages and node_id not in seen:
        seen.add(node_id)
        msg = messages[node_id]
        path.append(node_id)
        node_id = msg.get("parent")

    path.reverse()
    return path

def message_section(msg):
    """Render one message with a stable timestamp and hidden source message ID."""
    role = str(msg.get("role") or "unknown").upper()
    timestamp = format_time(msg.get("timestamp"))
    msg_id = msg.get("id", "unknown")

    lines = [
        f"### {role} — {timestamp}",
        "",
        f"<!-- message_id: {msg_id} -->",
        "",
        format_content(msg.get("content")),
        "",
    ]

    return "\n".join(lines)

def export_conversation(source_path, output_dir):
    """Build a review-marked Markdown transcript without overwriting an earlier import."""
    metadata, messages = load_conversation(source_path)

    title = str(metadata.get("name") or source_path.stem)
    conversation_id = str(metadata.get("id") or source_path.stem)
    current_id = metadata.get("currNode")

    selected_path = main_path(messages, current_id)
    selected_ids = set(selected_path)

    # If currNode is missing or invalid, retain all known messages in timestamp order
    # rather than dropping the conversation because its active branch is unavailable.
    if not selected_path:
        selected_path = sorted(
            messages,
            key=lambda mid: (
                messages[mid].get("timestamp")
                if isinstance(messages[mid].get("timestamp"), (int, float))
                else 0
            ),
        )
        selected_ids = set(selected_path)

    output = [
        "---",
        f"title: {yaml_string(title)}",
        f"source_id: {yaml_string(conversation_id)}",
        f"source_file: {yaml_string(source_path.name)}",
        f"last_modified: {yaml_string(format_time(metadata.get('lastModified')))}",
        "source_type: conversation_archive",
        "tags:",
        "  - imported-conversation",
        "  - needs-review",
        "---",
        "",
        f"# {title}",
        "",
        "> Imported from a local JSONL archive. This is a raw transcript,",
        "> not yet an AI-generated knowledge summary.",
        "",
        f"- **Conversation ID:** `{conversation_id}`",
        f"- **Current node:** `{current_id or 'Unknown'}`",
        f"- **Message nodes found:** {len(messages)}",
        f"- **Messages in current path:** {len(selected_path)}",
        "",
        "## Main conversation",
        "",
    ]

    for msg_id in selected_path:
        output.append(message_section(messages[msg_id]))

    # Preserve alternate branches separately so transcript data is not silently lost.
    other_ids = set(messages) - selected_ids

    if other_ids:
        output.extend([
            "## Other message nodes / alternate branches",
            "",
            "> These nodes were not on the current-node parent chain.",
            "> Their order below is by timestamp, not necessarily turn order.",
            "",
        ])

        def sort_key(mid):
            """Order non-main-branch messages by timestamp with ID as a stable tie-breaker."""
            ts = messages[mid].get("timestamp")
            return (ts if isinstance(ts, (int, float)) else 0, mid)

        for msg_id in sorted(other_ids, key=sort_key):
            msg = messages[msg_id]
            output.extend([
                f"<!-- parent_id: {msg.get('parent')} -->",
                message_section(msg),
            ])

    filename = safe_filename(title) + " [" + safe_filename(conversation_id, 40) + "].md"
    destination = output_dir / filename

    # Do not overwrite an existing note.
    if destination.exists():
        print(f"SKIP (already exists): {destination.name}")
        return destination

    destination.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    print(
        f"EXPORTED: {destination.name} "
        f"({len(selected_path)} main-path nodes, "
        f"{len(other_ids)} alternate nodes)"
    )
    return destination


def import_markdown(source_path, output_dir):
    """Copy an existing Markdown transcript byte-for-byte into its staging folder."""
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / source_path.name
    if destination.exists():
        print(f"SKIP (already exists): {destination}")
        return destination
    shutil.copy2(source_path, destination)
    print(f"COPIED: {destination}")
    return destination


def copy_to_inbox(source_path, inbox_path):
    """Mirror a staged transcript into the configured Inbox without overwrites."""
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    if inbox_path.exists():
        print(f"SKIP (Inbox already exists): {inbox_path}")
        return False
    shutil.copy2(source_path, inbox_path)
    print(f"COPIED TO INBOX: {inbox_path}")
    return True


def output_folder_for(source_path, source_root, output_root):
    """Keep imports separated by input folder, preserving nested folder paths."""
    if source_root is None:
        relative_parent = Path(source_path.parent.name)
    else:
        relative_parent = source_path.parent.relative_to(source_root)
        if relative_parent == Path("."):
            relative_parent = Path(source_root.name)
    return output_root / relative_parent

def main():
    """Select explicit or recursive inputs, then stage and mirror each one independently."""
    parser = argparse.ArgumentParser(description="Import ChatGPT JSONL conversation exports.")
    parser.add_argument("--file", type=Path, help="Import one explicit JSONL or Markdown file.")
    parser.add_argument("--directory", type=Path, default=SOURCE_DIR,
                        help="Recursively scan this directory for JSONL and Markdown files (default configured archive).")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR,
                        help="Transcript output directory.")
    parser.add_argument("--inbox", type=Path, default=INBOX_DIR,
                        help="Configured Obsidian Inbox destination.")
    args = parser.parse_args()
    if args.file and args.file.suffix.lower() not in {".jsonl", ".md"}:
        parser.error("--file must have a .jsonl or .md extension")
    if args.file and not args.file.is_file():
        raise SystemExit(f"Export file not found: {args.file}")
    if not args.file and not args.directory.is_dir():
        raise SystemExit(f"Source directory not found: {args.directory}")

    # Explicit file mode preserves the source folder name; directory mode preserves
    # each file's path relative to the selected export root.
    if args.file:
        files = [args.file]
        source_root = None
    else:
        source_root = args.directory.resolve()
        files = sorted(
            path for path in source_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".jsonl", ".md"}
        )
    if not files:
        raise SystemExit(f"No .jsonl or .md files found in {args.directory}")

    args.output = args.output.expanduser().resolve()
    args.inbox = args.inbox.expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    print(f"Source: {args.file or args.directory}")
    print(f"Output: {args.output}")
    print(f"Inbox: {args.inbox}")
    print(f"Files found: {len(files)}\n")

    # Report a per-file error and continue so one malformed export does not block
    # valid transcripts from being imported in the same batch.
    for path in files:
        try:
            destination_dir = output_folder_for(path, source_root, args.output)
            if path.suffix.lower() == ".md":
                staged_path = import_markdown(path, destination_dir)
            else:
                destination_dir.mkdir(parents=True, exist_ok=True)
                staged_path = export_conversation(path, destination_dir)
            relative_path = staged_path.relative_to(args.output)
            copy_to_inbox(staged_path, args.inbox / relative_path)
        except Exception as exc:
            print(f"ERROR processing {path.name}: {exc}")

    print("\nImport finished.")
    print("Review the Markdown files before running any LLM extraction.")

if __name__ == "__main__":
    main()
