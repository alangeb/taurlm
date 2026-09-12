"""Session registry — index of all session files for TauErgon.

Provides a JSON-based registry (stored at LOG_DIR/registry.json) that tracks
all session files regardless of their physical location. Enables archiving,
tagging, and smart filtering without breaking session continuation.

Public API
----------
- :class:`SessionRegistry` — singleton registry for session file management
- :func:`get_registry` — get or create the global registry instance
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime as dt
from pathlib import Path
from typing import Any

from agent_session import LOG_DIR

__all__ = [
    "REGISTRY_FILE",
    "SessionRegistry",
    "get_registry",
]

# ── Constants ────────────────────────────────────────────────────────────────

REGISTRY_FILE = LOG_DIR / "registry.json"

# Regex matching the session-file naming convention: {ppid}_{YYYYMMDDHHMMSS}_{N}
_SESSION_RE = re.compile(r"^(\d+_\d+_\d+)$")


# ── Session Registry ────────────────────────────────────────────────────────


class SessionRegistry:
    """Manage session file registry.

    The registry is a JSON file at LOG_DIR/registry.json with structure::

        {
            "version": 1,
            "updated": "2026-09-12T14:57:03+00:00",
            "sessions": {
                "1234_20260720120000_1": {
                    "prefix": "1234_20260720120000_1",
                    "context": "/home/user/.local/taurlm/log/1234_20260720120000_1.context",
                    "audit": "/home/user/.local/taurlm/log/1234_20260720120000_1.audit",
                    "plan": "/home/user/.local/taurlm/log/1234_20260720120000_1.plan",
                    "created": "2026-09-12T14:57:03+00:00",
                    "updated": "2026-09-12T14:57:03+00:00",
                    "status": "active",
                    "tags": [],
                    "metadata": {}
                }
            }
        }

    Status values: ``"active"``, ``"archived"``.

    Parameters
    ----------
    registry_path : Path, optional
        Override default registry file location (useful for testing).
    """

    def __init__(self, registry_path: Path | None = None):
        self._path = registry_path or REGISTRY_FILE
        self._data: dict[str, Any] | None = None

    # ── Internal ──────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """Load registry from disk, creating if missing."""
        if self._data is not None:
            return self._data

        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                # Handle corrupt content: empty string, null, array, etc.
                if not isinstance(self._data, dict):
                    self._data = None
                elif "sessions" not in self._data:
                    self._data["sessions"] = {}
                if self._data is not None:
                    return self._data
            except (json.JSONDecodeError, IOError):
                pass

        self._data = {"version": 1, "updated": self._now(), "sessions": {}}
        self._save()
        return self._data

    def _save(self) -> None:
        """Save registry to disk using atomic write (temp file + os.replace)."""
        if self._data is None:
            return
        self._data["updated"] = self._now()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")  # L-S1: unique temp path
            tmp_path.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
            os.replace(str(tmp_path), str(self._path))  # Atomic on POSIX
        except IOError:
            pass  # Graceful degradation — registry is advisory

    @staticmethod
    def _now() -> str:
        """Return current ISO timestamp."""
        return dt.now().isoformat()

    def _invalidate(self) -> None:
        """Clear cached data (for testing or after external changes)."""
        self._data = None

    # ── Session management ────────────────────────────────────────────────

    def register_session(
        self,
        prefix: str,
        context: Path,
        audit: Path,
        plan: Path | None = None,
    ) -> None:
        """Register a new session in the registry.

        Parameters
        ----------
        prefix : str
            Session prefix (e.g. ``"1234_20260720120000_1"``).
        context : Path
            Path to the context file.
        audit : Path
            Path to the audit file.
        plan : Path, optional
            Path to the plan file.
        """
        with _registry_lock:  # H15: prevent concurrent _load/_save races
            data = self._load()
            now = self._now()
            data["sessions"][prefix] = {
                "prefix": prefix,
                "context": str(context),
                "audit": str(audit),
                "plan": str(plan) if plan else None,
                "created": now,
                "updated": now,
                "status": "active",
                "tags": [],
                "metadata": {},
            }
            self._save()

    def get_session(self, prefix: str) -> dict[str, Any] | None:
        """Get session info by prefix.

        Returns ``None`` if the session is not found.
        """
        data = self._load()
        return data["sessions"].get(prefix)

    def list_sessions(
        self,
        status: str | None = None,
        include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        """List all sessions, optionally filtered by status.

        Parameters
        ----------
        status : str, optional
            Filter by status (``"active"``, ``"archived"``).
        include_archived : bool
            Include archived sessions in results.

        Returns
        -------
        list[dict]
            Sessions sorted newest first (by ``updated`` timestamp).
        """
        data = self._load()
        sessions = list(data["sessions"].values())

        if not include_archived:
            sessions = [s for s in sessions if s.get("status") != "archived"]
        if status:
            sessions = [s for s in sessions if s.get("status") == status]

        return sorted(sessions, key=lambda s: s.get("updated", ""), reverse=True)

    def get_context_files(self, include_archived: bool = True) -> list[Path]:
        """Get all context files from registry, sorted newest first.

        Falls back to scanning LOG_DIR if registry has no entries.
        """
        sessions = self.list_sessions(include_archived=include_archived)
        files: list[Path] = []
        for s in sessions:
            ctx = s.get("context")
            if ctx:
                p = Path(ctx)
                if p.exists():
                    files.append(p)

        # Fallback: scan LOG_DIR if registry is empty
        if not files:
            files = [
                f
                for f in LOG_DIR.glob("*.context")
                if _SESSION_RE.match(f.stem)
            ]

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0

        return sorted(files, key=_mtime, reverse=True)

    def get_audit_files(self, include_archived: bool = True) -> list[Path]:
        """Get all audit files from registry, sorted newest first.

        Falls back to scanning LOG_DIR if registry has no entries.
        """
        sessions = self.list_sessions(include_archived=include_archived)
        files: list[Path] = []
        for s in sessions:
            audit = s.get("audit")
            if audit:
                p = Path(audit)
                if p.exists():
                    files.append(p)

        # Fallback: scan LOG_DIR if registry is empty
        if not files:
            files = [
                f
                for f in LOG_DIR.glob("*.audit")
                if _SESSION_RE.match(f.stem)
            ]

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0

        return sorted(files, key=_mtime, reverse=True)

    def update_session(self, prefix: str, **kwargs: Any) -> None:
        """Update session metadata.

        Parameters
        ----------
        prefix : str
            Session prefix to update.
        **kwargs
            Fields to update (``status``, ``tags``, ``metadata``, etc.).
            ``tags`` and ``metadata`` are merged/extended, not replaced.
        """
        data = self._load()
        if prefix not in data["sessions"]:
            return

        session = data["sessions"][prefix]
        for key, value in kwargs.items():
            if key in ("tags", "metadata"):
                if isinstance(value, dict):
                    session[key].update(value)
                elif isinstance(value, list):
                    session[key].extend(value)
                else:
                    session[key] = value
            else:
                session[key] = value
        session["updated"] = self._now()
        self._save()

    def archive_session(self, prefix: str, new_paths: dict[str, str]) -> None:
        """Archive a session, updating file paths.

        Parameters
        ----------
        prefix : str
            Session prefix to archive.
        new_paths : dict
            New file paths (keys: ``"context"``, ``"audit"``, ``"plan"``).
        """
        data = self._load()
        if prefix not in data["sessions"]:
            return

        session = data["sessions"][prefix]
        for key, value in new_paths.items():
            if key in ("context", "audit", "plan"):
                session[key] = value
        session["status"] = "archived"
        session["updated"] = self._now()
        self._save()

    def remove_session(self, prefix: str) -> None:
        """Remove a session from the registry."""
        data = self._load()
        data["sessions"].pop(prefix, None)
        self._save()

    def cleanup_orphans(self) -> int:
        """Remove registry entries for sessions where all files are missing.

        Returns number of entries removed.
        """
        data = self._load()
        removed = 0
        to_remove: list[str] = []

        for prefix, session in data["sessions"].items():
            any_exists = False
            for key in ("context", "audit", "plan"):
                path_str = session.get(key)
                if path_str and Path(path_str).exists():
                    any_exists = True
                    break
            if not any_exists:
                to_remove.append(prefix)

        for prefix in to_remove:
            data["sessions"].pop(prefix, None)
            removed += 1

        if removed:
            self._save()
        return removed

    def search_by_tags(
        self,
        tags: list[str],
        match_all: bool = True,
        include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        """Search sessions by tags.

        Parameters
        ----------
        tags : list[str]
            Tags to search for.
        match_all : bool
            If True, session must have ALL tags. If False, ANY tag matches.
        include_archived : bool
            Include archived sessions in results.

        Returns
        -------
        list[dict]
            Matching sessions sorted newest first.
        """
        sessions = self.list_sessions(include_archived=include_archived)
        results = []
        for s in sessions:
            session_tags = s.get("tags", [])
            if match_all:
                if all(tag in session_tags for tag in tags):
                    results.append(s)
            else:
                if any(tag in session_tags for tag in tags):
                    results.append(s)
        return results

    def add_tags(self, prefix: str, tags: list[str]) -> None:
        """Add tags to a session.

        Parameters
        ----------
        prefix : str
            Session prefix to tag.
        tags : list[str]
            Tags to add.
        """
        data = self._load()
        if prefix not in data["sessions"]:
            return
        session = data["sessions"][prefix]
        for tag in tags:
            if tag not in session["tags"]:
                session["tags"].append(tag)
        session["updated"] = self._now()
        self._save()

    def remove_tags(self, prefix: str, tags: list[str]) -> None:
        """Remove tags from a session.

        Parameters
        ----------
        prefix : str
            Session prefix to untag.
        tags : list[str]
            Tags to remove.
        """
        data = self._load()
        if prefix not in data["sessions"]:
            return
        session = data["sessions"][prefix]
        session["tags"] = [t for t in session["tags"] if t not in tags]
        session["updated"] = self._now()
        self._save()

    def rebuild(self) -> int:
        """Rebuild registry by scanning LOG_DIR for session files.

        Scans for ``*.context`` files in LOG_DIR and registers any that are
        not already in the registry. Existing entries are not modified.

        Returns
        -------
        int
            Number of new sessions found and registered.
        """
        data = self._load()
        found = 0

        for ctx_file in LOG_DIR.glob("*.context"):
            match = _SESSION_RE.match(ctx_file.stem)
            if match:
                prefix = match.group(1)
                if prefix not in data["sessions"]:
                    audit = ctx_file.with_suffix(".audit")
                    plan = ctx_file.with_suffix(".plan")
                    data["sessions"][prefix] = {
                        "prefix": prefix,
                        "context": str(ctx_file),
                        "audit": str(audit) if audit.exists() else None,
                        "plan": str(plan) if plan.exists() else None,
                        "created": self._now(),
                        "updated": self._now(),
                        "status": "active",
                        "tags": [],
                        "metadata": {},
                    }
                    found += 1

        self._save()
        return found


# ── Global singleton ────────────────────────────────────────────────────────

_registry: SessionRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> SessionRegistry:
    """Get or create the global registry instance (thread-safe).

    Auto-rebuilds from LOG_DIR if the registry is empty (first use or
    after external cleanup). Subsequent calls return the cached instance.
    Call ``_registry._invalidate()`` to force a reload from disk.
    """
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = SessionRegistry()
            # Auto-rebuild if registry is empty — discovers existing sessions
            if not _registry._load()["sessions"]:
                _registry.rebuild()
    return _registry
