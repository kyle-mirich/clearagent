from pathlib import Path
import tomllib
from typing import Any


DEFAULT_CONFIG_PATH = Path(".clearagent/config.toml")


def load_project_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load a project TOML config, returning an empty mapping when it is absent."""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"ClearAgent config {config_path} must contain a mapping.")
    return data


def tracing_config(path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[bool, Path]:
    """Return validated tracing enablement and database path from project config."""
    section = load_project_config(path).get("tracing") or {}
    if not isinstance(section, dict):
        raise ValueError("ClearAgent config [tracing] must be a mapping.")
    enabled = section.get("enabled", True)
    db_path = section.get("db_path", ".clearagent/traces.sqlite")
    if not isinstance(enabled, bool):
        raise ValueError("ClearAgent config tracing.enabled must be a boolean.")
    if not isinstance(db_path, str) or not db_path:
        raise ValueError("ClearAgent config tracing.db_path must be a non-empty string.")
    return enabled, Path(db_path)
