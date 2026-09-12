"""Console-to-audit bridging for TauErgon.

This module provides the audit logging bridge that connects console output
to the audit log. It uses a global audit writer reference (set by
agent_session.AgentSessionManager) and a lock to prevent recursive calls.

Dependency graph (acyclic):
    agent_audit_bridge  (no deps on console/session)
    ↑                     ↑
    agent_console.audit   agent_session
    (imports bridge)      (imports bridge)

The _log_audit function is the primary interface. It is called by:
  - agent_console.templates (_ConsoleMessage.__call__ when audit=True)
  - agent_console.display_context (context validation display functions)
"""

from __future__ import annotations

import threading
from typing import Callable

__all__ = ["_log_audit", "display_warn_audit"]

# Thread-safe recursion guard for _log_audit.
# Prevents audit → console → audit infinite loops.
_log_audit_lock = threading.Lock()

# Explicit dispatch table for audit levels.
# Maps level names to the corresponding audit writer functions.
from agent_audit_bridge import (  # noqa: E402
    console_error,
    console_info,
    console_success,
    console_warning,
)

_AUDIT_LEVEL_DISPATCH: dict[str, Callable[[str], None]] = {
    "error": console_error,
    "warning": console_warning,
    "info": console_info,
    "success": console_success,
}


def _log_audit(level: str, message: str) -> None:
    """Bridge console output to audit log.

    Calls the global audit writer if available. No-op if audit is not initialized
    or if the writer fails (audit must never break console output).
    Unknown levels are silently ignored.
    Uses a lock to prevent audit → console → audit recursion.

    Args:
        level: Log level ("error", "warning", "info", "success").
        message: Message to log to audit.
    """
    if not _log_audit_lock.acquire():
        return  # Already in progress — prevent recursion
    try:
        writer = _AUDIT_LEVEL_DISPATCH.get(level)
        if writer is not None:
            writer(message)
    finally:
        _log_audit_lock.release()


def display_warn_audit(text: str) -> None:
    """Display a warning message in yellow AND log to audit.

    Single source of truth for audit-logging warnings. Used by
    display_misc, display_context, and templates modules instead of
    duplicating the override pattern.

    Args:
        text: Warning message to display and log.
    """
    # P7-B6-01 (residual): primitives.display_warn audits internally, so
    # calling it here + _log_audit double-logged every warning. Use the raw
    # color writer (no audit side-effect) — audit flows solely via _log_audit.
    from agent_console.primitives import _cw
    from agent_models import Colors
    _cw(Colors.YELLOW, text)
    _log_audit("warning", text)
