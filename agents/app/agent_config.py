"""Load agent.conf; re-read on every call so edits take effect without restart."""

import configparser
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "agent.conf"


def _parser() -> configparser.ConfigParser:
    p = configparser.ConfigParser()
    p.read(_CONFIG_PATH)
    return p


def get(section: str, key: str) -> str:
    return _parser()[section][key].strip()


def get_path(key: str) -> Path:
    return Path(get("agent", key)).expanduser().resolve()
