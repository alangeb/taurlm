"""Low-level console I/O primitives for TauErgon.

All other console modules import from here. This is the foundation layer
with zero dependencies on other agent_console submodules.

Exports:
    - Timing utilities: compute_duration, format_duration_ms
    - Color helpers: _cw, _role_color
    - Output primitives: echo, blank_line, echo_no_newline, prompt, status, reasoning, verbose
    - Display helpers: display_error, display_warn, display_warning, display_success, display_info, display_synthetic
"""
from __future__ import annotations

import re
import sys
import time

from agent_models import Colors

# Audit bridge imports — agent_audit_bridge has NO deps on agent_console,
# so this is safe and creates no circular imports.
from agent_audit_bridge import (
    console_error,
    console_info,
    console_success,
    console_warning,
)


# ── Newline-state latch ──────────────────────────────────────────────────────
# The console writes to stdout from many independent code paths (streaming
# assistant text, block-header writers, display helpers). When one path leaves
# the cursor mid-line and the next writes a block header (e.g. ``[REPL CODE]``),
# the header lands on the same line. We cannot detect this by inspecting the
# last character of a written string because ANSI color codes (``\033[0m``)
# trail the visible text without moving the cursor. Instead we track the cursor
# position logically: every stdout writer calls ``_track`` with the exact string
# it wrote; ``_track`` strips trailing ANSI and records whether the write ended
# at column 0. ``ensure_newline`` then emits at most one ``\n`` — never a
# duplicate — so a block header always starts at the beginning of a line.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_at_line_start = True


def _track(text: str) -> None:
    """Record whether *text* (the exact string just written to stdout) ends at column 0.

    Trailing ANSI SGR sequences are stripped before testing, since they do not
    move the cursor. An empty write is treated as 'not at line start' only if it
    genuinely does not end in a newline.

    Args:
        text: The exact string passed to ``sys.stdout.write``.
    """
    global _at_line_start
    _at_line_start = _ANSI_RE.sub("", text).endswith("\n")


def _set_at_line_start(value: bool) -> None:
    """Force the latch to a known state (used by block writers that end at col 0).

    Args:
        value: True if the cursor is known to be at column 0.
    """
    global _at_line_start
    _at_line_start = value


def ensure_newline() -> None:
    """Emit a single ``\n`` iff the cursor is currently mid-line; flush.

    Idempotent: never adds a newline when already at column 0, so it cannot
    introduce a stray blank line. Call before writing a block header.
    """
    global _at_line_start
    if not _at_line_start:
        sys.stdout.write("\n")
        _at_line_start = True
    sys.stdout.flush()


__all__ = [
    # Timing
    "compute_duration",
    "format_duration_ms",
    # Color helpers
    "_cw",
    "_role_color",
    # Output primitives
    "echo",
    "blank_line",
    "echo_no_newline",
    "prompt",
    "status",
    "reasoning",
    "verbose",
    # Display helpers (colorized output)
    "display_error",
    "display_warn",
    "display_warning",  # compat alias
    "display_success",
    "display_info",
    "display_synthetic",
    # Newline-state latch
    "ensure_newline",
    "_track",
    "_set_at_line_start",
    # Streaming
    "stream_write_raw",
    "stream_write_plain",
    "stream_newline",
    "stream_audit",
]


def compute_duration(obj: object, attr: str = "_start_time") -> float:
    """Compute session duration from a start-time attribute on *obj*.

    Returns 0 if *obj* has no such attribute (e.g., start time never set).
    """
    return time.time() - getattr(obj, attr, time.time()) if hasattr(obj, attr) else 0


def format_duration_ms(duration_ms: float) -> str:
    """Format a duration in milliseconds into a human-readable string.

    Uses seconds for values >= 1000ms, milliseconds otherwise.
    """
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.1f}s"
    return f"{duration_ms:.0f}ms"


# ── Color helpers ────────────────────────────────────────────────────────────


def _cw(color: str, text: str, newline: bool = True) -> None:
    """Write colorized text to stdout, updating the newline latch."""
    out = f"{color}{text}{Colors.RESET}" + ("\n" if newline else "")
    sys.stdout.write(out)
    _track(out)


def _role_color(role: str) -> str:
    """Return ANSI color code for the specified message role."""
    if role == "user":
        return Colors.RESET
    if role in ("tool", "tool_call"):
        return Colors.CYAN
    return Colors.GREEN


# ── Output primitives ────────────────────────────────────────────────────────


def echo(text: str, newline: bool = True) -> None:
    """Write text to stdout and log to audit."""
    out = text + ("\n" if newline else "")
    sys.stdout.write(out)
    _track(out)
    console_info(text)


def blank_line() -> None:
    """Write a blank line to stdout."""
    sys.stdout.write("\n")
    _track("\n")


def echo_no_newline(text: str) -> None:
    """Write text to stdout without trailing newline and log to audit."""
    sys.stdout.write(text)
    _track(text)
    console_info(text)


def prompt(text: str = "") -> None:
    """Display a prompt in cyan, flush stdout."""
    _cw(Colors.CYAN, text, newline=False)
    sys.stdout.flush()


def status(text: str) -> None:
    """Display a status message in cyan and log to audit."""
    _cw(Colors.CYAN, text)
    console_info(text)


def reasoning(text: str) -> None:
    """Display a reasoning message and log to audit."""
    _cw(Colors.REASONING, f"[REASON] {text}")
    console_info(f"[REASON] {text}")


def verbose(text: str) -> None:
    """Display a verbose message in green and log to audit."""
    _cw(Colors.GREEN, text)
    console_info(text)


# ── Display helpers (colorized output) ───────────────────────────────────────
# Factory to create display helpers that write *text* in *color* AND log to audit.


# Map display function names to their corresponding audit bridge functions.
_DISPLAY_AUDIT_MAP = {
    "display_error": console_error,
    "display_warn": console_warning,
    "display_warning": console_warning,  # compat alias
    "display_success": console_success,
    "display_info": console_info,
    "display_synthetic": console_info,  # synthetic messages are info-level
}


def _make_displayer(color: str, name: str, doc: str):
    """Create a display helper that writes *text* in *color* and logs to audit.

    Returns a function named *name* with a docstring of *doc*, so it
    behaves identically to an explicit ``def`` for introspection and
    debugging purposes.

    Audit logging is done AFTER stdout write. Audit failures are caught
    and must never suppress console output.
    """
    audit_fn = _DISPLAY_AUDIT_MAP.get(name)

    def display(text: str) -> None:
        _cw(color, text)
        # Log to audit — failures must never suppress console output
        if audit_fn is not None:
            try:
                audit_fn(text)
            except Exception:
                # Audit failures are silent — console output already done
                pass

    display.__name__ = name
    display.__qualname__ = name
    display.__doc__ = doc
    return display


display_error = _make_displayer(Colors.RED, "display_error", "Display an error message in red.")
display_warn = _make_displayer(Colors.YELLOW, "display_warn", "Display a warning message in yellow.")
display_warning = display_warn  # compat alias
display_success = _make_displayer(Colors.GREEN, "display_success", "Display a success message in green.")
display_info = _make_displayer(Colors.CYAN, "display_info", "Display an informational message in cyan.")
display_synthetic = _make_displayer(Colors.WHITE, "display_synthetic", "Display a synthetic/injected message in white.")


# ── Streaming primitives ─────────────────────────────────────────────────────


def stream_write_raw(text: str, color: str) -> None:
    """Write colored text to stdout without newline, no audit."""
    out = f"{color}{text}{Colors.RESET}"
    sys.stdout.write(out)
    _track(out)


def stream_write_plain(text: str) -> None:
    """Write plain text to stdout without newline, no audit."""
    sys.stdout.write(text)
    _track(text)


def stream_newline() -> None:
    """Write newline and flush stdout."""
    sys.stdout.write("\n")
    _track("\n")
    sys.stdout.flush()


def stream_audit(text: str) -> None:
    """Single audit call for accumulated streaming text (split by lines, call console_info per line)."""
    if not text:
        return
    for line in text.rstrip("\n").split("\n"):
        console_info(line)
