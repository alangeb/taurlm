"""Audit bridge — the SINGLE entry point for all audit writes.

This module provides a clean interface for console-to-audit bridging WITHOUT
creating circular imports. It contains NO imports from agent_console,
or agent_session, making the dependency graph acyclic.

IMPORTANT: This module is the ONLY place that should call methods on the
audit writer. Direct access to AuditWriter methods from other modules
bypasses the recursion guard (_log_audit_lock in agent_console.audit)
and breaks the architectural boundary.

Dependency graph (acyclic):
    agent_audit_bridge  (no deps on console/session)
    ↑                     ↑
    agent_console agent_session
    (facade imports bridge)   (imports bridge)

Public API:
    - set_audit_writer(writer): Set the global audit writer
    - register_console_warning_callback(callback): Register warning callback
    - emit_console_warning(message): Emit a console warning
    - console_error/warning/info/success(): Console wrapper functions
    - log_context_add/remove/merge/snapshot(): Context modification logging
NOTE: All writes are synchronous. A disk I/O stall will block the calling thread.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__all__ = [
    "set_audit_writer",
    "register_console_warning_callback",
    "emit_console_warning",
    "console_error",
    "console_warning",
    "console_info",
    "console_success",
    "log_context_add",
    "log_context_remove",
    "log_context_merge",
    "log_context_snapshot",
]


# ── Audit writer protocol ────────────────────────────────────────────────────
# We use a string-based protocol description instead of a formal Protocol class
# to avoid importing from agent_session (which would create the cycle).
#
# The audit writer object must support (INTERNAL methods — underscore prefixed):
#   _console_error(message: str) -> None
#   _console_warning(message: str) -> None
#   _console_info(message: str) -> None
#   _console_success(message: str) -> None
#   context_add(count, total, bytes_total) -> None
#   context_remove(count, total, bytes_total) -> None
#   context_merge(source, target, count) -> None
#   context_snapshot(total, bytes_total, max_tokens) -> None


# ── Global state ─────────────────────────────────────────────────────────────
# Single audit writer reference, shared across the process.
# Set once during AgentSessionManager initialization.
# Forks inherit via subprocess isolation (separate address spaces).
_audit_writer: Any = None

# Callback registered by agent_console.templates to emit console warnings without
# importing agent_session directly. This breaks the cycle.
#
# NOTE: agent_console.templates registers its warning() function when
# register_console_messages() is called. If emit_console_warning() is called
# before the callback is registered, the warning is silently dropped. This is acceptable: agent_console.templates is always
# imported early via agent_console during normal startup.
_console_warning_callback: Callable[[str], None] | None = None


# ── Console wrapper functions ────────────────────────────────────────────────
# Each function calls the corresponding method on the audit writer directly.
# No dynamic dispatch — explicit calls for clarity and safety.


def _safe_writer_call(method: Callable[..., None], *args: Any) -> None:
    """Safely call a method on the audit writer.

    No-op if no writer is set. Audit failures must never suppress
    the caller's output, so all exceptions are caught.

    Args:
        method: A callable that accepts (writer, *args) and invokes
            the desired method on the writer.
        *args: Arguments to pass through to *method*.
    """
    global _audit_writer
    writer = _audit_writer
    if writer is not None:
        try:
            method(writer, *args)
        except Exception as e:
            logging.warning("audit_bridge: _safe_writer_call failed: %s", e)


def console_error(message: str, *_extra: Any) -> None:
    """Log a console error to the audit writer.

    Total sink: never raises (telemetry must not break a turn).
    """
    try:
        _safe_writer_call(lambda w, msg: w._console_error(msg), message)
    except Exception as e:  # telemetry must never crash a turn
        logging.debug("audit_bridge: console_error failed: %s", e)


def console_warning(message: str, *_extra: Any) -> None:
    """Log a console warning to the audit writer.

    Total sink: never raises (telemetry must not break a turn).
    """
    try:
        _safe_writer_call(lambda w, msg: w._console_warning(msg), message)
    except Exception as e:  # telemetry must never crash a turn
        logging.debug("audit_bridge: console_warning failed: %s", e)


def console_info(message: str, *_extra: Any) -> None:
    """Log a console info to the audit writer.

    Total sink: never raises (telemetry must not break a turn).
    """
    try:
        _safe_writer_call(lambda w, msg: w._console_info(msg), message)
    except Exception as e:  # telemetry must never crash a turn
        logging.debug("audit_bridge: console_info failed: %s", e)


def console_success(message: str, *_extra: Any) -> None:
    """Log a console success to the audit writer.

    Total sink: never raises (telemetry must not break a turn).
    """
    try:
        _safe_writer_call(lambda w, msg: w._console_success(msg), message)
    except Exception as e:  # telemetry must never crash a turn
        logging.debug("audit_bridge: console_success failed: %s", e)


def set_audit_writer(writer: Any) -> None:
    """Set the global audit writer reference for console-to-audit bridging.

    This allows console functions (error, warning) to log to audit
    without requiring a direct dependency on AuditWriter.

    Args:
        writer: An AuditWriter instance, or None to clear.
    """
    global _audit_writer
    _audit_writer = writer


def register_console_warning_callback(callback: Callable[[str], None]) -> None:
    """Register a callback for emitting console warnings.

    agent_session calls this to emit warnings without importing agent_session.
    agent_console.templates registers its warning() function here.

    Args:
        callback: A function that accepts a warning message string.
    """
    global _console_warning_callback
    _console_warning_callback = callback


def emit_console_warning(message: str) -> None:
    """Emit a console warning via the registered callback.

    No-op if no callback is registered (e.g., before agent_console.templates is loaded).
    Exceptions from the callback are caught to prevent audit/logging failures
    from breaking tool execution.
    """
    if _console_warning_callback is not None:
        try:
            _console_warning_callback(message)
        except Exception as e:
            # Callback failure must never break execution.
            logging.warning("audit_bridge: _console_warning_callback failed: %s", e)


# ── Context logging ──────────────────────────────────────────────────────────
# Bridge functions for context modification logging. Each calls the
# corresponding method on the audit writer directly (no dynamic dispatch).


def log_context_add(count: int, total: int, bytes_total: int) -> None:
    """Log context messages added."""
    _safe_writer_call(lambda w, c, t, b: w.context_add(c, t, b), count, total, bytes_total)


def log_context_remove(count: int, total: int, bytes_total: int) -> None:
    """Log context messages removed."""
    _safe_writer_call(lambda w, c, t, b: w.context_remove(c, t, b), count, total, bytes_total)


def log_context_merge(source: str, target: str, count: int) -> None:
    """Log context messages merged."""
    _safe_writer_call(lambda w, s, t, c: w.context_merge(s, t, c), source, target, count)


def log_context_snapshot(total: int, bytes_total: int, max_tokens: int) -> None:
    """Log a context snapshot."""
    _safe_writer_call(lambda w, t, b, m: w.context_snapshot(t, b, m), total, bytes_total, max_tokens)
