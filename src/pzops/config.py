"""Configuration loading.

Config lives in a TOML file (see ``config/pzops.example.toml``). Every value can
be overridden from the environment with a ``PZOPS__`` prefix and ``__`` as the
section separator, e.g. ``PZOPS__BACKUP__INTERVAL_MINUTES=30``. That keeps the
file readable while still letting docker-compose drive everything from ``.env``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_PREFIX = "PZOPS__"
ENV_SEP = "__"

DEFAULTS: dict[str, Any] = {
    "server": {
        "data_dir": "/data",
    },
    "rcon": {
        "host": "127.0.0.1",
        "port": 27015,
        "password_env": "RCON_PASSWORD",
        "timeout_seconds": 5.0,
    },
    "backup": {
        "source_dir": "/data/Saves",
        "staging_dir": "/backups",
        "interval_minutes": 60,
        "keep_local": 24,
        "name_prefix": "pzsave",
        "announce": True,
        "cloud": {
            "enabled": False,
            "rclone_remote": "",
            "rclone_binary": "rclone",
            "keep_remote": 168,
            "extra_args": [],
        },
    },
}


@dataclass(frozen=True)
class Config:
    """A merged config tree with dotted-path access."""

    data: dict[str, Any] = field(default_factory=dict)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, path: str) -> dict[str, Any]:
        value = self.get(path, {})
        return value if isinstance(value, dict) else {}

    def secret(self, path: str, default: str = "") -> str:
        """Read a secret by indirection: the config names an env var, not a value."""
        var = self.get(path)
        if not var:
            return default
        return os.environ.get(str(var), default)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce(raw: str, current: Any) -> Any:
    """Coerce an env string to the type of the value it is replacing."""
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [item for item in (part.strip() for part in raw.split(",")) if item]
    return raw


def env_overrides(base: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Build an overlay dict from ``PZOPS__SECTION__KEY`` environment variables."""
    environ = os.environ if environ is None else environ
    overlay: dict[str, Any] = {}
    for name, raw in environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        parts = [p.lower() for p in name[len(ENV_PREFIX) :].split(ENV_SEP) if p]
        if not parts:
            continue
        current: Any = base
        for part in parts:
            current = current.get(part) if isinstance(current, dict) else None
        node = overlay
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(raw, current)
    return overlay


def load(path: str | Path | None = None, environ: dict[str, str] | None = None) -> Config:
    """Load defaults, then the TOML file (if present), then environment overrides."""
    environ = os.environ if environ is None else environ
    data = DEFAULTS
    candidate = path or environ.get("PZOPS_CONFIG")
    if candidate:
        file_path = Path(candidate)
        if file_path.is_file():
            with file_path.open("rb") as handle:
                data = deep_merge(data, tomllib.load(handle))
        elif path is not None:
            raise FileNotFoundError(f"config file not found: {file_path}")
    return Config(deep_merge(data, env_overrides(data, environ)))
