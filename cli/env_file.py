"""Load local ``KEY=VALUE`` files before a CLI command runs.

The files live outside the repository data model, for example
``.secrets/aws.env``. Parsing is deliberately small: comments, blank lines,
an optional ``export`` prefix and single/double quotes are supported. Values
are never printed, and existing environment variables win by default.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

KEY_PATTERN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _parse_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _valid_key(key: str) -> bool:
    return bool(key) and (key[0].isalpha() or key[0] == "_") and all(
        char in KEY_PATTERN for char in key
    )


def parse(text: str) -> dict[str, str]:
    """Parse env-file text without touching the process environment."""
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            raise ValueError(f"line {number}: expected KEY=VALUE")
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if not _valid_key(key):
            raise ValueError(f"line {number}: invalid variable name {key!r}")
        values[key] = _parse_value(raw)
    return values


def load(path: Path, *, override: bool = False, key_map: dict[str, str] | None = None) -> dict[str, str]:
    """Load one env file; optional key mapping precedes existing-environment checks."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read env file {path}: {error}") from None
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        print(
            f"warning: {path} is readable by other users (mode {mode:o}); "
            "use chmod 600",
            file=sys.stderr,
        )
    values = parse(text)
    loaded: dict[str, str] = {}
    for key, value in values.items():
        key = (key_map or {}).get(key, key)
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded
