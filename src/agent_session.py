"""Session lifecycle management for TauErgon.

Encapsulates session file paths, audit writer management, error burst
detection, token tracking, and utility functions that were previously
inline in the TauErgon god class.

Responsibilities:
- Session file path resolution (audit, context) with env overrides
- Audit writer management (delegates to agent_audit_writer)
- Error burst detection (via AgentSessionManager.has_error_burst)
- Token tracking (session-wide totals + per-turn snapshots + cache tracking)
- Utility functions for oversized output and failed API requests

Audit logging has been extracted to agent_audit_writer.py for modularity.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime as dt
from pathlib import Path
from agent_audit_writer import AuditWriter
from agent_console import log_dir_error
from agent_audit_bridge import emit_console_warning, set_audit_writer
from agent_token_tracker import TokenTracker

__all__ = [
    "LOG_DIR",
    "SESSION_PREFIX",
    "AgentSessionManager",
    "write_oversized_output",
    "log_failed_api_request",
    "_get_log_filename_prefix",
    "_peek_log_filename_prefix",
]

# ── Directories ────────────────────────────────────────────────────────────────

_LOG_DIR_DEFAULT = Path.home() / ".local" / "taurlm" / "log"
LOG_DIR = Path(os.getenv("TAU_LOG_DIR", str(_LOG_DIR_DEFAULT)))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Global session prefix — set ONCE by the root agent, inherited by all children.
# Never overwritten. Guarantees all session files share the same prefix.
SESSION_PREFIX: str | None = None


# ── Filename helpers ───────────────────────────────────────────────────────


def _peek_log_filename_prefix() -> str:
    """Compute a prefix WITHOUT claiming/creating anything (display-only).

    Same ``{ppid}_{YYYYMMDDHHMMSS}_{counter}`` shape as
    :func:`_get_log_filename_prefix`, but it NEVER opens a file with
    ``O_CREAT`` — it only stats to skip numbers already taken by real
    sessions. Use this when a prefix is needed purely for display / template
    interpolation (e.g. ``read_system_prompt``); real sessions must keep
    using :func:`_get_log_filename_prefix`, which claims atomically.
    """
    ppid = os.getppid()
    dt_str = dt.now().strftime("%Y%m%d%H%M%S")
    counter = 1

    while True:
        prefix = f"{ppid}_{dt_str}_{counter}"
        ctx_file = LOG_DIR / f"{prefix}.context"
        audit_file = LOG_DIR / f"{prefix}.audit"
        if not ctx_file.exists() and not audit_file.exists():
            return prefix
        counter += 1


def _get_log_filename_prefix() -> str:
    """Generate unique filename prefix: {ppid}_{YYYYMMDDHHMMSS}_{counter}."""
    ppid = os.getppid()
    dt_str = dt.now().strftime("%Y%m%d%H%M%S")
    counter = 1

    while True:
        prefix = f"{ppid}_{dt_str}_{counter}"
        ctx_file = LOG_DIR / f"{prefix}.context"
        try:
            # M-S1: Atomic claim via O_CREAT|O_EXCL
            fd = os.open(ctx_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return prefix
        except FileExistsError:
            counter += 1


# ── Utility functions ────────────────────────────────────────────


def write_oversized_output(output: str, prefix: str | None) -> str | None:
    """Write oversized tool output to LOG_DIR/{prefix}.toolout.{NNN}.

    Returns the file path, or None if writing failed.
    """
    if prefix is None:
        return None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        counter = 1
        while counter <= 1000:
            fname = f"{prefix}.toolout.{counter:03d}"
            filepath = LOG_DIR / fname
            if not filepath.exists():
                filepath.write_text(output, encoding="utf-8")
                return str(filepath)
            counter += 1
        emit_console_warning(
            f"Oversized output discarded — exhausted 1000 toolout slots for {prefix}. "
            "Consider cleaning old log files."
        )
        return None
    except Exception:
        return None


def log_failed_api_request(
    request_body: dict,
    log_file: Path | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    status_code: int | None = None,
) -> None:
    """Write failed LLM request body to a JSON file for debugging.

    Args:
        request_body: The request kwargs that caused the failure.
        log_file: Path to derive output directory/prefix from.
        error_type: Exception class name (e.g., "BadRequestError", "TimeoutError").
        error_message: Human-readable error string from the exception.
        status_code: HTTP status code if available (e.g., 400, 401, 429, 500).
    """
    try:
        if log_file is not None:
            out_dir = log_file.parent
            prefix = log_file.stem
        elif SESSION_PREFIX is not None:
            out_dir = LOG_DIR
            prefix = SESSION_PREFIX
        else:
            out_dir = LOG_DIR
            prefix = f"{os.getppid()}_{dt.now().strftime('%Y%m%d%H%M%S')}"

        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / f"{prefix}.failed_request.json"

        # M-L1: Truncate long message content for audit log
        _req = dict(request_body)
        if "messages" in _req:
            _req["messages"] = [
                {**m, "content": m["content"][:500] + "... [truncated]"}
                if isinstance(m.get("content"), str) and len(m["content"]) > 500
                else m
                for m in _req["messages"]
            ]
        record = {
            "timestamp": dt.now().isoformat(),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "error": {
                "type": error_type or "unknown",
                "message": error_message or "",
                "status_code": status_code,
            },
            "request": _req,  # M-L1: use truncated version
        }
        filepath.write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )

        # Log cleanup disabled — agent_log_cleanup.py removed as dead code.
        # If needed, re-implement inline or as a smaller utility.
        pass
    except Exception as e:
        logging.debug("session: failed request processing failed: %s", e)


# ── AgentSessionManager ────────────────────────────────────────────


class AgentSessionManager(TokenTracker):
    """Manages session lifecycle: file paths, audit writer, error detection,
    and token tracking.

    Inherits from ``TokenTracker`` to provide token accounting directly
    (session totals, per-turn snapshots, cache tracking) without composition
    boilerplate. Provides a focused interface for session file management,
    audit logging, and token accounting.
    """

    def __init__(
        self,
        setup_files: bool = True,
        audit_file: Path | None = None,
        context_file: Path | None = None,
    ) -> None:
        """Initialise session manager.

        Args:
            setup_files: If True, ensure LOG_DIR exists and resolve file paths
                from the session prefix. Set to False when paths are provided
                explicitly (e.g., during tests).
            audit_file: Explicit audit file path (overrides env / prefix logic).
            context_file: Explicit context file path (overrides env / prefix logic).
        """
        super().__init__()
        self._audit_file = audit_file
        self._context_file = context_file
        self._audit_writer: AuditWriter | None = None
        self._prefix: str | None = None

        if setup_files:
            global SESSION_PREFIX
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log_dir_error(LOG_DIR, str(e))
                raise RuntimeError(f"Cannot create log directory {LOG_DIR}: {e}") from e

            # Resolve session prefix (global, set once by root agent).
            if SESSION_PREFIX is None:
                prefix = _get_log_filename_prefix()
                SESSION_PREFIX = prefix
            else:
                prefix = SESSION_PREFIX

            self._prefix = prefix
            self._audit_file = LOG_DIR / f"{prefix}.audit"
            self._context_file = LOG_DIR / f"{prefix}.context"

            # Environment-variable overrides (checked after prefix resolution).
            if env_audit := os.getenv("TAU_AUDIT_LOG_FILE"):
                self._audit_file = Path(env_audit)
            if env_ctx := os.getenv("TOOL_CONTEXT_FILE"):
                self._context_file = Path(env_ctx)

            # Parent audit file inheritance (for fork unification).
            # TAU_PARENT_AUDIT_FILE takes highest priority — forks append to parent's file.
            parent_audit = os.getenv("TAU_PARENT_AUDIT_FILE")
            if parent_audit:
                self._audit_file = Path(parent_audit)

            # Register session in registry (advisory — graceful if fails).
            try:
                from agent_session_registry import get_registry
                registry = get_registry()
                plan_file = LOG_DIR / f"{prefix}.plan"
                registry.register_session(
                    prefix=prefix,
                    context=self._context_file,
                    audit=self._audit_file,
                    plan=plan_file if plan_file.exists() else None,
                )
            except Exception as e:
                logging.debug("session: registry registration failed (advisory): %s", e)

    # ── File paths ──────────────────────────────────────────────────────────
    @property
    def audit_file(self) -> Path:
        """Path to the audit log file."""
        if self._audit_file is None:
            raise RuntimeError("Session files not initialised")
        return self._audit_file

    @audit_file.setter
    def audit_file(self, path: Path) -> None:
        self._audit_file = path

    @property
    def context_file(self) -> Path:
        """Path to the context file."""
        if self._context_file is None:
            raise RuntimeError("Session files not initialised")
        return self._context_file

    @context_file.setter
    def context_file(self, path: Path) -> None:
        self._context_file = path

    # ── Session identity ──────────────────────────────────────────────────────
    @property
    def prefix(self) -> str | None:
        """Session prefix (a.k.a. ``session_id``) used for log file naming.

        Returns the prefix the session was initialised with, or ``None`` if
        session files were not set up (e.g. in unit tests). Used to expose
        ``session_id`` in the A2A agent card.
        """
        if self._prefix is not None:
            return self._prefix
        if self._context_file is not None:
            return self._context_file.stem
        return None

    # ── Audit writer ────────────────────────────────────────────────────────

    def _create_audit_writer(self) -> AuditWriter:
        """Create, register, and return a new AuditWriter for the session.
        
        Includes pre-flight disk writability check to catch audit failures early.
        Uses graceful degradation — a transient disk glitch shouldn't kill the process.
        """
        # Pre-flight: verify the audit file's parent directory is writable
        audit_dir = self.audit_file.parent
        try:
            # Test writability by creating and deleting a temp file
            test_file = audit_dir / ".audit_writability_test"
            test_file.write_text("test")
            test_file.unlink()
        except (PermissionError, OSError) as e:
            # Graceful degradation: warn but continue. The actual write will fail
            # (and retry) in _flush() if the disk is truly flaky.
            sys.stderr.write(f"WARNING: Audit directory check failed: {audit_dir}\n{e}\n")
            sys.stderr.flush()

        initial_nesting = len(os.getenv("TAU_FORK_NESTING", ""))
        writer = AuditWriter(self.audit_file, initial_nesting=initial_nesting)
        set_audit_writer(writer)
        return writer

    @property
    def audit_writer(self) -> AuditWriter:
        """Lazy-initialised AuditWriter for the session."""
        if self._audit_writer is None:
            self._audit_writer = self._create_audit_writer()
        return self._audit_writer

    def init_audit_writer(self) -> None:
        """Eagerly initialise the audit writer."""
        _ = self.audit_writer  # Triggers lazy initialisation in the property
