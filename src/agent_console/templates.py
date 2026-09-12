"""Console message templates for TauErgon.

Declarative message templates using ``_ConsoleMessage``. Each template is a
callable that formats and displays a message at the specified level, with
optional audit logging.

This module is the single source of truth for all console message templates.
Import from here rather than
duplicating templates across the codebase.

Categories
----------
- Error display: error, warning, invalid_mode_error, unknown_command_error, ...
- Message display: user_echo, assistant_message_display, user_message_display
- Flow control: restart_flow_info, restart_flow_success, restart_failure, ...
- Loop warnings: loop_warning_display
- Synthetic/injected: synthetic_user, synthetic_assistant, synthetic_bridge
- Subagent/fork: subagent_start_display, fork_display, subagent_output_footer, ...
- A2A/agent: agents_json, agent_card_json, agent_status_message, ...
- Compression: compress_success, compress_fail
- Context display: context_cleared_success, context_restored, ...

"""
from __future__ import annotations

import sys
from typing import Callable, Literal

# Audit logger — imported at module level (no circular dependency).
from agent_console.audit import _log_audit

# Display helpers live in primitives.py (foundation layer) — no circular
# dependency. Import directly.
from agent_console.primitives import (
    _cw,
    _set_at_line_start,
    ensure_newline,
)
from agent_models import Colors


# Audit bridge callback registration.
from agent_audit_bridge import register_console_warning_callback


# ---------------------------------------------------------------------------
# Template infrastructure
# ---------------------------------------------------------------------------

# _DISPLAY_BY_LEVEL maps level names to *non-auditing* display writers.
# P7-B6-01: primitives' display_* helpers audit internally; using them here
# double-logged (and display_warn triple-logged) every audit=True template.
# Audit for templates flows exclusively through _ConsoleMessage._log_audit,
# which honors the audit flag and audit_level override.
_DISPLAY_BY_LEVEL: dict[str, Callable[[str], None]] = {
    "error": lambda t: _cw(Colors.RED, t),
    "warning": lambda t: _cw(Colors.YELLOW, t),
    "success": lambda t: _cw(Colors.GREEN, t),
    "info": lambda t: _cw(Colors.CYAN, t),
    "synthetic": lambda t: _cw(Colors.WHITE, t),
}


def _get_display(level: str) -> Callable[[str], None]:
    """Return the display function for *level*.

    Falls back to the info writer for unknown levels (defense-in-depth).
    """
    return _DISPLAY_BY_LEVEL.get(level, _DISPLAY_BY_LEVEL["info"])


# _log_audit is imported at module level — no need for lazy wrapper.


class _ConsoleMessage:
    """Declarative message template that generates a callable display function.

    Replaces thin wrapper functions like::

        def unknown_command_error(cmd_name: str) -> None:
            display_error(f"Unknown command: /{cmd_name}")

    With template calls::

        unknown_command_error = _msg("error", "Unknown command: /{cmd_name}")

    The optional *audit_level* parameter allows the audit log level to differ
    from the display level (e.g., display as error but log as warning).
    """
    __slots__ = ("_level", "_template", "_audit", "_audit_level", "_writer")

    def __init__(
        self,
        level: Literal["error", "warning", "success", "info"],
        template: str,
        *,
        audit: bool = False,
        audit_level: str | None = None,
        writer: Callable[[str], None] | None = None,
    ) -> None:
        self._level = level
        self._template = template
        self._audit = audit
        self._audit_level = audit_level
        self._writer = writer

    def __call__(self, *args, **kwargs) -> None:
        msg = self._template.format(*args, **kwargs)
        if self._writer is not None:
            self._writer(msg)
        else:
            _get_display(self._level)(msg)
        if self._audit:
            _log_audit(self._audit_level or self._level, msg)


def _msg(
    level: Literal["error", "warning", "success", "info"],
    template: str,
    *,
    audit: bool = False,
    audit_level: str | None = None,
    writer: Callable[[str], None] | None = None,
) -> _ConsoleMessage:
    """Create a console message template.

    Args:
        level: Display level ("error", "warning", "success", "info").
        template: Message template string with {} placeholders.
        audit: If True, also log to audit.
        audit_level: Override audit log level (defaults to *level*).
        writer: Custom writer function (bypasses standard display).
    """
    return _ConsoleMessage(level, template, audit=audit, audit_level=audit_level, writer=writer)


# ---------------------------------------------------------------------------
# Message Definitions
# ---------------------------------------------------------------------------

# Error display (all errors logged to audit)
error = _msg("error", "{}", audit=True)
warning = _msg("warning", "{}", audit=True)
invalid_mode_error = _msg("error", "Invalid mode '{}'. Valid modes: {}", audit=True)
unknown_command_error = _msg("error", "Unknown command: /{}", audit=True)


command_file_not_found = _msg("error", "Command file not found: {}", audit=True)
log_dir_error = _msg("error", "ERROR: Cannot create log directory {}: {}", audit=True)

dynamic_command_result = _msg("success", "{}", audit=True)

# Message display (all logged to audit)
user_echo = _msg("info", ">>> {}", writer=lambda t: sys.stdout.write(f"{t}\n"), audit=True)
assistant_message_display = _msg("success", "[ASSISTANT] {}", audit=True)
user_message_display = _msg("info", "[USER] {}", writer=lambda t: sys.stdout.write(f"{t}\n"), audit=True)

# Flow control (all logged to audit)
restart_flow_info = _msg("info", "[Restarting agent...] Command: {}", audit=True)
restart_flow_success = _msg("success", "Exiting current agent...", audit=True)
restart_failure = _msg("error", "Failed to restart agent: {}", audit=True)
restart_fallback_failure = _msg("error", "Fallback also failed: {}", audit=True)
force_exit_message = _msg("info", "\n\nForced exit requested. Cleaning up...", audit=True)
interrupted_message = _msg("info", "\n\nInterrupted. Press Ctrl+C again to force exit.", audit=True)

# Loop warnings
loop_warning_display = _msg("warning", "{}", audit=True)

# Synthetic/injected messages (white color, logged to audit)
def _synthetic_writer(t: str) -> None:
    _get_display("synthetic")(t)

synthetic_user = _msg("info", "[SYNTHETIC USER: {}] {}", audit=True, writer=_synthetic_writer)
synthetic_assistant = _msg("info", "[SYNTHETIC ASSISTANT] {}", audit=True, writer=_synthetic_writer)
synthetic_bridge = _msg("info", "[SYNTHETIC BRIDGE: {}] {}", audit=True, writer=_synthetic_writer)

# REPL output (cyan for distinction from assistant green)
_REPL_SEP = "─" * 56
def _repl_output_writer(t: str) -> None:
    """Display REPL output with header/footer in cyan."""
    ensure_newline()
    sys.stdout.write(f"\033[96m[REPL OUTPUT] {_REPL_SEP}\033[0m\n")
    # DESIGN DECISION (accepted): REPL output `line` is written VERBATIM, so any
    # ANSI escape sequences the program emitted pass straight through to the
    # terminal. This is intentional — output is terminal-local display only, not
    # a privilege/security boundary, and stripping ANSI would destroy legitimate
    # colored output (pytest, compilers, progress bars). Do NOT sanitize ANSI here.
    for line in t.split("\n"):
        sys.stdout.write(f"\033[96m{line}\033[0m\n")
    # C1: audit happens ONCE via _ConsoleMessage._log_audit (audit=True on
    # repl_output); the per-line console_info here double-audited. Removed.
    sys.stdout.write(f"\033[90m{_REPL_SEP}\033[0m\n")
    _set_at_line_start(True)
    sys.stdout.flush()

def _repl_error_writer(t: str) -> None:
    """Display REPL error with header/footer in red."""
    ensure_newline()
    sys.stdout.write(f"\033[91m[REPL ERROR] {_REPL_SEP}\033[0m\n")
    for line in t.split("\n"):
        sys.stdout.write(f"\033[91m{line}\033[0m\n")
    # C1: audit happens ONCE via _ConsoleMessage._log_audit (audit=True on
    # repl_error); the per-line console_info here double-audited. Removed.
    sys.stdout.write(f"\033[90m{_REPL_SEP}\033[0m\n")
    _set_at_line_start(True)
    sys.stdout.flush()

repl_output = _msg("info", "{}", audit=True, writer=_repl_output_writer)
repl_error = _msg("error", "{}", audit=True, writer=_repl_error_writer)

# Subagent/fork (all logged to audit)
subagent_start_display = _msg("info", "[Starting subagent for: {}...]", audit=True)
fork_display = _msg("info", "/fork {}", audit=True)
subagent_output_footer = _msg("info", "[End of subagent output]", audit=True)
subagent_error = _msg("error", "Subagent error: {}", audit=True)
fork_error = _msg("error", "Fork error: {}", audit=True)

# A2A/agent (all logged to audit)
agents_json = _msg("info", "{}", audit=True)
agent_card_json = _msg("info", "{}", audit=True, writer=lambda t: sys.stdout.write(t + "\n"))
agent_status_message = _msg("warning", "{}", audit=True)
a2a_cli_error = _msg("error", "{}", audit=True)
a2a_started_message = _msg("info", "[A2A server started: {}]", audit=True)
agent_a2a_response = _msg("info", "[{}]", audit=True, writer=lambda t: sys.stdout.write(f"{t}\n"))

# Compression (all logged to audit)
compress_success = _msg("success", "[COMPRESS] Compression successful.", audit=True)
compress_fail = _msg("error", "[COMPRESS] Compression failed.", audit=True)

# Context display (all logged to audit)
context_cleared_success = _msg("info", "Context cleared.", audit=True)
context_restored = _msg("info", "[Context restored: {} messages from {}]", audit=True)
context_restore_failure = _msg("error", "[Context file empty/malformed: {}]", audit=True, audit_level="warning")
no_context_file_found = _msg("error", "[No context file found for this session]", audit=True, audit_level="warning")

# Tool display (all logged to audit)



shell_command_usage = _msg("info", "Usage: ! <command>", audit=True)






# REPL code display (bright white for readability)
def _code_writer(t: str) -> None:
    """Write REPL code with header/footer formatting.

    Displays Python code in bright white with gray header/footer for clear
    distinction from output.
    """
    ensure_newline()
    sys.stdout.write(
        "\033[90m[REPL CODE] ────────────────────────────────────────────────────────\033[0m\n"
    )
    for line in t.split("\n"):
        sys.stdout.write(f"\033[97m{line}\033[0m\n")
        try:
            from agent_audit_bridge import console_info
            console_info(f"[REPL CODE] {line}")
        except Exception:
            pass
    sys.stdout.write(
        "\033[90m────────────────────────────────────────────────────────\033[0m\n"
    )
    _set_at_line_start(True)
    sys.stdout.flush()


repl_code = _msg("info", "{}", audit=True, writer=_code_writer)


# Answer display (green for assistant messages)


def _answer_writer(t: str) -> None:
    """Display answer content in green."""
    ensure_newline()
    for line in t.split("\n"):
        sys.stdout.write(f"\033[92m{line}\033[0m\n")
    _set_at_line_start(True)
    sys.stdout.flush()


answer_display = _msg("success", "{}", audit=True, writer=_answer_writer)


# ── Registration ─────────────────────────────────────────────────────────────


def register_console_messages() -> None:
    """Register console message callbacks with the audit bridge.

    Call this once during agent initialization to wire up the warning
    callback for ``agent_session`` console warnings.
    """
    register_console_warning_callback(warning)


__all__ = [
    # Template infrastructure
    "_ConsoleMessage",
    "_msg",
    # Registration
    "register_console_messages",
    # Error display
    "error", "warning", "invalid_mode_error",
    "unknown_command_error",
    "command_file_not_found", "log_dir_error",
    "dynamic_command_result",
    # Message display
    "user_echo", "assistant_message_display", "user_message_display",
    # Flow control
    "restart_flow_info", "restart_flow_success", "restart_failure",
    "restart_fallback_failure", "force_exit_message", "interrupted_message",
    # Loop warnings
    "loop_warning_display",
    # Synthetic/injected messages
    "synthetic_user", "synthetic_assistant", "synthetic_bridge",
    # Subagent/fork
    "subagent_start_display", "fork_display",
    "subagent_output_footer",
    "subagent_error", "fork_error",
    # A2A/agent
    "agents_json", "agent_card_json", "agent_status_message",
    "a2a_cli_error", "a2a_started_message", "agent_a2a_response",
    # Compression
    "compress_success", "compress_fail",
    # Context display
    "context_cleared_success", "context_restored",
    "context_restore_failure", "no_context_file_found",
    "shell_command_usage",
    # REPL display
    "repl_code", "repl_output", "repl_error",
]
