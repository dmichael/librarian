"""Configuration loading."""

import os
from pathlib import Path

import yaml


def find_config_file() -> Path:
    """Find settings.yaml, checking for local override first."""
    # __file__ is src/librarian/config.py, so .parent.parent.parent is project root
    config_dir = Path(__file__).parent.parent.parent / "config"

    local = config_dir / "settings.local.yaml"
    if local.exists():
        return local

    return config_dir / "settings.yaml"


def load_config() -> dict:
    """Load configuration from settings file."""
    config_path = find_config_file()
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Environment variable overrides (for Docker / production)
    if url := os.getenv("LIBRARIAN_DB_URL"):
        config.setdefault("vector_store", {})["pgvector_url"] = url
        config["vector_store"]["backend"] = "pgvector"

    if device := os.getenv("LIBRARIAN_EMBEDDING_DEVICE"):
        config.setdefault("embedding", {})["device"] = device

    if data_root := os.getenv("LIBRARIAN_DATA_ROOT"):
        config["output_path"] = f"{data_root}/converted"
        config["intake_path"] = f"{data_root}/intake/ebooks"

    if public_url := os.getenv("LIBRARIAN_PUBLIC_URL"):
        config["public_url"] = public_url

    return config


def expand_path(path: str) -> Path:
    """Expand ~ and resolve to absolute path."""
    return Path(path).expanduser().resolve()
