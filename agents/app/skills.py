"""Scan agents/skills/**/*.md and return their combined text as a system prompt prefix."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def load_skills() -> str:
    """Return concatenated skill file contents, separated by blank lines."""
    parts = []
    for path in sorted(_SKILLS_DIR.rglob("*.md")):
        parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)
