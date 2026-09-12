"""Shared context-file utilities for TauErgon.

Centralises functions that operate on context JSON files and the LOG_DIR
directory, eliminating duplication across ``agent_input``, ``agent_project``,
and ``agent_a2a``.

Public API
----------
- :func:`format_age` — human-readable age string from seconds
- :func:`get_all_context_files` — sorted list of context files in LOG_DIR
- :func:`read_context_metadata` — (msg_count, last_user) for display
- :func:`read_context_metadata_for_a2a` — (metadata_dict, message_count)
- :func:`get_context_file_by_parent_ppid` — find context file by parent PID
- :func:`list_context_files` — list context files with metadata
- :func:`preview_context` — preview last N messages from a file
"""

from __future__ import annotations

import json
import logging
import re
import os
import time
from pathlib import Path

from agent_session import LOG_DIR

# Base pattern for context-file naming convention: {ppid}_{YYYYMMDDHHMMSS}_{N}
# Exported so other modules can derive their own compiled regexes.
_CONTEXT_FILE_PATTERN = r"\d+_\d+_\d+\.context"
_CONTEXT_FILE_RE = re.compile(r"^" + _CONTEXT_FILE_PATTERN + "$")

# Pattern with capture group for extracting the PID (first field).
# Used by agent_a2a.py to parse session PIDs from context filenames.
_CONTEXT_FILE_CAPTURE_RE = re.compile(r"^(" + r"\d+" + r")_\d+_\d+\.context$")


# ── Helpers ────────────────────────────────────────────────────────────────


def format_age(seconds: float) -> str:
    """Format a duration in seconds into a human-readable string.

    Examples: ``"42s ago"``, ``"5m ago"``, ``"3h ago"``, ``"2d ago"``.
    """
    if seconds < 60:
        return f"{int(seconds)}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours // 24
    return f"{int(days)}d ago"


def _is_valid_path(path: Path) -> bool:
    """Check if path exists and is not a broken symlink."""
    try:
        if not path.exists():
            return False
        if path.is_symlink() and not path.resolve().exists():
            return False
        return True
    except OSError:
        return False


def get_all_context_files() -> list[Path]:
    """Get all context files in LOG_DIR, sorted newest first.

    Sources are UNIONED, not chosen one-or-the-other:

    1. The session registry — includes archived sessions and provides
       canonical paths for files that may live outside LOG_DIR.
    2. A direct scan of ``LOG_DIR/*.context`` — catches files that exist on
       disk but were never registered (or whose registration was lost), so a
       stale/partial registry can no longer hide a valid context file.

    Results are de-duplicated by resolved path and filtered for broken
    symlinks. Previously the disk scan only ran when the registry returned an
    empty list, so any file missing from a non-empty registry was invisible to
    ``/context list`` and ``/continue`` (registry drift). This union makes the
    listing self-healing and immune to that drift.
    """
    seen: set[str] = set()
    ctx_files: list[Path] = []

    def _add(path: Path) -> None:
        if not _is_valid_path(path):
            return
        try:
            # Skip 0-byte placeholder files (created by the atomic-claim
            # prefix generator but never written) — they hold no messages and
            # are never loadable. Every consumer filters these out anyway.
            if path.stat().st_size == 0:
                return
            key = str(path.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        ctx_files.append(path)

    # Source 1: session registry (may raise if unavailable — that's fine).
    try:
        from agent_session_registry import get_registry
        for f in get_registry().get_context_files(include_archived=True):
            _add(f)
    except (ImportError, OSError, KeyError, ValueError) as e:
        # Deliberately NARROW: a missing registry module or an unreadable store
        # is an expected fallback (the LOG_DIR scan below recovers the files),
        # but a TypeError/AttributeError here is a real registry bug and must
        # not be swallowed at debug level.
        logging.warning(
            "context_utils: registry unavailable, relying on LOG_DIR scan: %s", e
        )

    # Source 2: direct LOG_DIR scan (always run — recovers unregistered files).
    for f in LOG_DIR.glob("*.context"):
        if _CONTEXT_FILE_RE.match(f.name):
            _add(f)

    def _mtime(f: Path) -> float:
        # _is_valid_path already filtered the list, but stat() can still race
        # with deletion (TOCTOU) between the validity check and this sort, so
        # guard it and fall back to 0 (oldest) rather than crash the listing.
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(ctx_files, key=_mtime, reverse=True)


# ── Context-file readers ───────────────────────────────────────────────────

def _extract_last_user(data: list) -> str:
    """Extract a short preview of the last user message from *data*."""
    for msg in reversed(data):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                image_count = sum(
                    1
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "image_url"
                )
                text_parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                result = f"[{image_count} image(s)]"
                if text_parts:
                    t = text_parts[0]
                    if len(t) > 80:
                        t = t[:77] + "..."
                    result += ": " + t
                return result
            elif isinstance(content, str):
                if len(content) > 80:
                    return content[:77] + "..."
                return content
    return ""


def _content_is_blank(msg) -> bool:
    """True when a message carries nothing a human or the model could read.

    Placeholder entries (``{"role": "user", "content": ""}``, a list whose text
    parts are all empty) inflate the message count and make an empty context
    look loadable, so /continue <n> can land on it.
    """
    if not isinstance(msg, dict):
        return False
    content = msg.get("content", "")
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                return False
            ptype = part.get("type")
            if ptype == "text" and str(part.get("text", "")).strip():
                return False
            if ptype == "image_url":
                return False
        return True
    return False


def _count_messages(messages: list) -> int:
    """Number of messages that actually hold content."""
    return sum(1 for m in messages if not _content_is_blank(m))


def read_context_metadata(context_file: Path) -> tuple[int, str]:
    """Read message count and last user message from a context JSON file.

    Handles both the legacy bare-array format (just a list of messages)
    and the TAU_005 metadata-wrapped format::

        {"metadata": {...}, "messages": [...]}

    Returns ``(msg_count, last_user)``.  On any read error returns ``(0, "")``.
    """
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return (0, "")

    if isinstance(data, dict) and "messages" in data:
        # New format with metadata — extract messages list
        messages = data.get("messages")
        if not isinstance(messages, list):
            return (0, "")
        msg_count = _count_messages(messages)
        last_user = _extract_last_user(messages)
        return (msg_count, last_user)
    if isinstance(data, list):
        # Legacy bare-array format
        msg_count = _count_messages(data)
        last_user = _extract_last_user(data)
        return (msg_count, last_user)

    return (0, "")


def read_context_metadata_for_a2a(context_file: Path) -> tuple[dict, int]:
    """Read a context file for A2A session metadata.

    Handles both the legacy bare-array format (just a list of messages)
    and the TAU_005 metadata-wrapped format::

        {"metadata": {...}, "messages": [...]}

    Returns ``(metadata_dict, message_count)``.
    Missing or unreadable files yield an empty metadata dict and a
    message count of 0.
    """
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return {}, 0

    if isinstance(data, dict) and "messages" in data:
        return data.get("metadata") or {}, len(data.get("messages") or [])
    if isinstance(data, list):
        return {}, len(data)
    return {}, 0


# ── Context file listing & preview ─────────────────────────────────────────


def get_context_file_by_parent_ppid(exclude: Path | None = None) -> Path | None:
    """Get the most recent valid context file matching the current parent PID.

    Skips the current session's own context file (exclude) and all 0-byte
    files. Falls back to the most recent valid file if no PID match.

    Uses the session registry if available, falling back to scanning
    LOG_DIR directly.
    """
    ppid = os.getppid()

    # Always scan LOG_DIR as baseline; supplement with registry if available
    ctx_files = get_all_context_files()
    try:
        from agent_session_registry import get_registry
        registry = get_registry()
        registry_files = registry.get_context_files(include_archived=True)
        ctx_files = list(dict.fromkeys(ctx_files + list(registry_files)))
    except Exception:
        pass

    # Exclude current session's own file and 0-byte files
    def _valid(f: Path) -> bool:
        if exclude is not None and f.resolve() == exclude.resolve():
            return False
        try:
            return f.stat().st_size > 0
        except OSError:
            return False

    ctx_files = [f for f in ctx_files if _valid(f)]

    # Filter by parent PID
    ctx_pattern = re.compile(rf"^{ppid}_\d+_\d+\.context$")
    matching = [f for f in ctx_files if ctx_pattern.match(f.name)]

    if matching:
        return max(matching, key=lambda f: f.stat().st_mtime)

    # Fallback: return the most recent valid context file regardless of PID
    all_ctx = get_all_context_files()
    all_ctx = [f for f in all_ctx if _valid(f)]
    return all_ctx[0] if all_ctx else None


def list_context_files(
    ppid_filter: int | None = None, limit: int | None = None
) -> list[dict]:
    """List context files with metadata, sorted newest first."""
    ctx_files = get_all_context_files()
    results: list[dict] = []
    now = time.time()
    for f in ctx_files:
        if ppid_filter is not None:
            if int(f.name.split("_")[0]) != ppid_filter:
                continue
        msg_count, last_user = read_context_metadata(f)
        results.append(
            {
                "file": f,
                "name": f.name,
                "age": format_age(now - f.stat().st_mtime),
                "msg_count": msg_count,
                "last_user": last_user,
            }
        )
        if limit is not None and len(results) >= limit:
            break
    for idx, entry in enumerate(results, start=1):
        entry["id"] = idx
    return results


def preview_context(context_file: Path, n_messages: int = 3) -> list[dict]:
    """Preview the last N messages from a context file."""
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    preview = []
    messages = data if isinstance(data, list) else data.get("messages", [])
    for msg in messages[-n_messages:]:
        content = msg.get("content", "")
        if isinstance(content, list):
            image_count = sum(1 for p in content if p.get("type") == "image_url")
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = f"[{image_count} image(s)]"
            if text_parts:
                t = text_parts[0]
                if len(t) > 200:
                    t = t[:197] + "..."
                content += ": " + t
        elif isinstance(content, str) and len(content) > 200:
            content = content[:197] + "..."
        preview.append({"role": msg.get("role", "unknown"), "content": content})
    return preview


__all__ = [
    "_CONTEXT_FILE_PATTERN",
    "_CONTEXT_FILE_CAPTURE_RE",
    "format_age",
    "get_all_context_files",
    "read_context_metadata",
    "read_context_metadata_for_a2a",
    "get_context_file_by_parent_ppid",
    "list_context_files",
    "preview_context",
]
