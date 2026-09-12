"""Project-level context persistence for TauErgon.

Manages ``.tau/contexts`` — an append-only log of context file paths —
enabling project-aware session continuation without parent-PID coupling.

Workflow:
    1. ``tau --init-project``  → creates ``.tau/`` + ``.tau/contexts``
    2. Every tau startup appends the current context path
    3. ``tau -p`` or ``/continue project`` loads the most recent entry
"""

from __future__ import annotations

import time
from pathlib import Path

from agent_context_utils import format_age, read_context_metadata

__all__ = [
    'CONTEXTS_FILE_NAME',
    'PROJECT_DIR_NAME',
    'Path',
    'annotations',
    'append_context',
    'find_project_root',
    'format_age',
    'get_all_entries',
    'get_context_by_index',
    'get_entry_info',
    'get_loadable_contexts',
    'get_valid_contexts',
    'init_project',
    'read_context_metadata'
]

PROJECT_DIR_NAME = ".tau"
CONTEXTS_FILE_NAME = "contexts"


def _contexts_path(project_dir: Path) -> Path:
    """Return the path to ``.tau/contexts`` within *project_dir*."""
    return project_dir / CONTEXTS_FILE_NAME


def find_project_root(cwd: Path | None = None) -> Path | None:
    """Return the ``.tau`` directory in *cwd*, or ``None`` if no project exists."""
    cwd = cwd or Path.cwd()
    project_dir = cwd / PROJECT_DIR_NAME
    if project_dir.is_dir():
        return project_dir
    return None


def init_project(cwd: Path | None = None) -> Path:
    """Create ``.tau/`` directory and ``.tau/contexts`` file (idempotent).

    Returns the project directory path.
    """
    cwd = cwd or Path.cwd()
    project_dir = cwd / PROJECT_DIR_NAME
    project_dir.mkdir(parents=True, exist_ok=True)
    contexts_file = _contexts_path(project_dir)
    if not contexts_file.exists():
        contexts_file.touch()
    return project_dir


def append_context(project_dir: Path, context_file: Path) -> None:
    """Append *context_file* path to ``.tau/contexts``.

    Creates the file if it doesn't exist (defensive — ``init_project``
    should have created it, but this makes ``append`` robust on its own).
    """
    contexts_file = _contexts_path(project_dir)
    with open(contexts_file, "a", encoding="utf-8") as f:
        f.write(f"{context_file}\n")


def _read_lines(project_dir: Path) -> list[str]:
    """Read all non-empty stripped lines from ``.tau/contexts``."""
    contexts_file = _contexts_path(project_dir)
    if not contexts_file.exists():
        return []
    with open(contexts_file, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def get_valid_contexts(project_dir: Path) -> list[Path]:
    """Return existing context file paths, oldest to newest.

    Entries whose files have been deleted are silently skipped.
    """
    return [Path(line) for line in _read_lines(project_dir) if Path(line).exists()]


def get_loadable_contexts(project_dir: Path) -> list[Path]:
    """Return existing context file paths with at least 1 message.

    Filters out empty/malformed context files (e.g. touched but
    never written because the session had fewer than 3 messages).
    """
    result: list[Path] = []
    for line in _read_lines(project_dir):
        p = Path(line)
        if not p.exists():
            continue
        msg_count, _ = read_context_metadata(p)
        if msg_count > 0:
            result.append(p)
    return result


def get_all_entries(project_dir: Path) -> list[str]:
    """Return all path strings from ``.tau/contexts`` (including stale)."""
    return _read_lines(project_dir)


def get_context_by_index(project_dir: Path, n: int) -> Path | None:
    """Get the *n*-th context from the end.

    Indexing: ``0`` = most recent, ``abs(n)`` = offset from end.
    e.g. ``0``→last, ``1``/``-1``→second-to-last, ``2``/``-2``→third-to-last.

    Returns ``None`` if out of range or the path doesn't exist.
    """
    valid = get_valid_contexts(project_dir)
    offset = abs(n)
    idx = len(valid) - 1 - offset
    if idx < 0:
        return None
    return valid[idx]


def get_entry_info(project_dir: Path) -> list[dict]:
    """Return formatted entries for ``/continue project list`` display.

    Returns list of dicts (newest first) with keys:
    ``id``, ``index`` (0-based from end), ``file``, ``name``, ``age``,
    ``msg_count``, ``last_user``, ``exists``.
    """
    lines = _read_lines(project_dir)
    if not lines:
        return []

    now = time.time()
    results: list[dict] = []
    for pos, line in enumerate(reversed(lines)):
        p = Path(line)
        exists = p.exists()
        if exists:
            msg_count, last_user = read_context_metadata(p)
            age = format_age(now - p.stat().st_mtime)
        else:
            msg_count = 0
            last_user = ""
            age = "stale"
        results.append(
            {
                "id": pos + 1,
                "index": pos,
                "file": p,
                "name": p.name,
                "age": age,
                "msg_count": msg_count,
                "last_user": last_user,
                "exists": exists,
            }
        )
    return results
