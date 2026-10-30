#!/usr/bin/env python3
"""Launch the Sandbox agent Gradio UI."""

from agents.app.ui import launch

if __name__ == "__main__":
    try:
        launch()
    except Exception as exc:
        import sys
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
