"""Configuration loading."""

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
        return yaml.safe_load(f)


def expand_path(path: str) -> Path:
    """Expand ~ and resolve to absolute path."""
    return Path(path).expanduser().resolve()
