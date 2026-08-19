"""Minimal ``${VAR}`` templating for the server config files.

Project Zomboid's ``servertest.ini`` is plain ``key=value`` text, so a full
template engine would be overkill. This renders ``${NAME}`` and ``${NAME:-fallback}``
from a mapping and refuses to write a file with unresolved placeholders, which
turns a typo in ``.env`` into a startup error instead of a silently broken server.
"""

from __future__ import annotations

import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class MissingVariableError(KeyError):
    """Raised when a template references a variable with no value and no default."""


def render(text: str, values: dict[str, str]) -> str:
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        if name in values and values[name] != "":
            return str(values[name])
        if fallback is not None:
            return fallback
        missing.append(name)
        return match.group(0)

    result = PLACEHOLDER.sub(substitute, text)
    if missing:
        raise MissingVariableError(
            "unset template variables: " + ", ".join(sorted(set(missing)))
        )
    return result


def render_file(src: Path, dest: Path, values: dict[str, str], overwrite: bool = True) -> bool:
    """Render ``src`` to ``dest``. Returns True if the file was written."""
    if dest.exists() and not overwrite:
        return False
    rendered = render(src.read_text(encoding="utf-8"), values)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_text(encoding="utf-8") == rendered:
        return False
    dest.write_text(rendered, encoding="utf-8")
    return True


def render_tree(src_dir: Path, dest_dir: Path, values: dict[str, str],
                overwrite: bool = True, suffix: str = ".tmpl") -> list[Path]:
    """Render every ``*.tmpl`` in ``src_dir`` into ``dest_dir``, dropping the suffix."""
    written: list[Path] = []
    for template in sorted(src_dir.glob(f"*{suffix}")):
        dest = dest_dir / template.name[: -len(suffix)]
        if render_file(template, dest, values, overwrite=overwrite):
            written.append(dest)
    return written
