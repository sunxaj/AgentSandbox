"""Shared runtime configuration for the MCP server and its workers."""

import configparser
import os
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
DEFAULT_CONFIG = MCP_DIR / "config" / "Obsidian.conf"

# Environment overrides are explicit so an alternate endpoint/path can be
# configured without adding machine-specific values to worker source files.
ENV_OVERRIDES = {
    ("paths", "conversation_exports"): "CHATGPT_EXPORT_DIR",
    ("paths", "import_output"): "CONVERSATION_IMPORT_DIR",
    ("paths", "vault"): "KNOWLEDGE_VAULT",
    ("servers", "bonsai_base_url"): "BONSAI_BASE_URL",
    ("servers", "embeddings_url"): "EMBEDDINGS_URL",
}


def config_path():
    """Return the selected Obsidian config, defaulting to this repository's config."""
    configured = os.getenv("OBSIDIAN_CONFIG")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_CONFIG


def _parser():
    """Load a fresh parser per call so runtime config edits are not cached."""
    parser = configparser.ConfigParser()
    path = config_path()
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    parser.read(path, encoding="utf-8")
    return parser


def get_value(section, key, fallback=None):
    """Read a value, giving its documented environment override precedence."""
    env_name = ENV_OVERRIDES.get((section, key))
    if env_name and os.getenv(env_name):
        return os.environ[env_name]
    parser = _parser()
    return parser.get(section, key, fallback=fallback)


def get_path(key):
    """Resolve a configured path, anchoring relative values at the project root."""
    value = get_value("paths", key)
    if not value:
        raise KeyError(f"Missing [paths] {key} in {config_path()}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def get_vault_folder(key):
    """Resolve one configured folder name beneath the active vault."""
    name = get_value("vault_folders", key)
    if not name:
        raise KeyError(f"Missing [vault_folders] {key} in {config_path()}")
    return get_path("vault") / name


def get_vault_path(key):
    """Resolve a file path relative to the vault unless the config makes it absolute."""
    value = get_value("paths", key)
    if not value:
        raise KeyError(f"Missing [paths] {key} in {config_path()}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = get_path("vault") / path
    return path.resolve()


def get_server_url(key):
    """Return a configured service base URL without a trailing slash."""
    value = get_value("servers", key)
    if not value:
        raise KeyError(f"Missing [servers] {key} in {config_path()}")
    return value.rstrip("/")
