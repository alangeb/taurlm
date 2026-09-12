"""Agent lifecycle management — system-wide interrupt/exit flags.

Consolidates the module-level _interrupted and _exit_requested flags into
a proper class with class-attribute-based state ownership.

Thread safety
-------------
Uses threading.Event for state — safe under CPython's GIL and forward-
compatible with free-threaded CPython (PEP 703).
"""

from __future__ import annotations

import threading

__all__ = ["AgentLifecycle"]


class AgentLifecycle:
    """Manage system-wide interrupt/exit flags for cooperative shutdown.

    Uses threading.Event for state ownership — thread-safe under both
    GIL and free-threaded CPython.
    """

    _interrupted: threading.Event = threading.Event()
    _exit_requested: threading.Event = threading.Event()

    # ── Interrupt ──────────────────────────────────────────────────────────

    @classmethod
    def is_interrupted(cls) -> bool:
        """Return whether an interrupt (first Ctrl+C) has been received."""
        return cls._interrupted.is_set()

    @classmethod
    def set_interrupted(cls, value: bool) -> None:
        """Set the interrupt flag."""
        if value:
            cls._interrupted.set()
        else:
            cls._interrupted.clear()

    # ── Exit ───────────────────────────────────────────────────────────────

    @classmethod
    def is_exit_requested(cls) -> bool:
        """Return whether an exit (second Ctrl+C or /exit) has been requested."""
        return cls._exit_requested.is_set()

    @classmethod
    def set_exit_requested(cls, value: bool) -> None:
        """Set the exit-requested flag."""
        if value:
            cls._exit_requested.set()
        else:
            cls._exit_requested.clear()

    # ── Reset (testing only — private) ─────────────────────────────────────

    @classmethod
    def _reset(cls) -> None:
        """Testing only. Catching SystemExit in production is unsupported. L-A6
        Reset both flags to False.  Testing only."""
        cls._interrupted.clear()
        cls._exit_requested.clear()
