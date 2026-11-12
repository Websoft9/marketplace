"""Backed-up, atomic staging writes with cooperative locking on Linux."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import tempfile


def read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def replace(path: Path, text: str, *, expected: bytes | None) -> Path | None:
    """Refuse stale writers, retain exact old bytes, then atomically replace.

    The lock coordinates CLI writers. External editors must not write during
    commit; their changes before the locked check are detected by byte equality.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if read_bytes(path) != expected:
            raise ValueError("catalog changed since it was read; refusing to overwrite")
        backup = None
        if expected is not None:
            with tempfile.NamedTemporaryFile(prefix=path.name + ".before-", suffix=".json", dir=path.parent, delete=False) as saved:
                saved.write(expected)
                backup = Path(saved.name)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=path.name + ".pending-", dir=path.parent, delete=False) as pending:
                temporary = Path(pending.name)
                pending.write(text)
                pending.flush()
                os.fsync(pending.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return backup
