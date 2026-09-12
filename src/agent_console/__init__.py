"""Agent console package for TauErgon.

Provides focused submodules for console display functions:
- audit: Console-to-audit bridging (_log_audit)
- audit_display: Audit log viewer (AuditRecord, parse_audit_file, show_audit)
- primitives: Low-level I/O (echo, status, _cw, prompt, etc.) + display helpers
- templates: _ConsoleMessage class, _msg() factory, and all message definitions
- display_command: Command display (show_help, show_commands, show_command_help)
- display_status: Status display (agent_status, exit_summary, print_context_status, ...)
- display_context: Context display (context_validation_warning, context_list_display, ...)
- display_misc: Misc display (error_display, undo_message, llm_timeout_message, ...)

Uses lazy loading via __getattr__ to avoid importing all submodules at package
load time. This eliminates the maintenance burden of keeping explicit re-exports
in sync with submodule changes and makes the dependency graph acyclic.

Symbols are resolved on first access from their source submodules.
Backward compatible: `from agent_console import X` works exactly as before.
"""
from __future__ import annotations

import importlib as _importlib
import logging

# ── Lazy loading configuration ─────────────────────────────────────────────────
# Ordered list of submodule paths to search when resolving symbols.
# Submodules are the source of truth — add symbols to submodule __all__, not here.
# Order matters: earlier submodules take precedence for duplicate symbol names.

_SUBMODULE_PATHS: tuple[str, ...] = (
    "agent_console.audit",
    "agent_console.audit_display",
    "agent_console.primitives",
    "agent_console.templates",
    "agent_console.display_command",
    "agent_console.display_status",
    "agent_console.display_context",
    "agent_console.display_misc",
)

# Cache for resolved symbols (avoids repeated imports)
_attr_cache: dict[str, object] = {}


def __getattr__(name: str) -> object:
    """Lazily resolve symbols from submodules on first access.

    This replaces the explicit re-export pattern that required maintaining
    a large __all__ list and importing all submodules at package load time.

    Resolution order:
    1. Check cache (already resolved)
    2. Iterate submodules, import from first one that has the symbol
    3. Raise AttributeError if not found

    Args:
        name: The attribute name to resolve.

    Returns:
        The resolved symbol from the appropriate submodule.

    Raises:
        AttributeError: If the symbol is not found in any submodule.
    """
    # Check cache first
    if name in _attr_cache:
        return _attr_cache[name]

    # Try each submodule in order
    import_errors: list[str] = []
    for module_path in _SUBMODULE_PATHS:
        try:
            module = _importlib.import_module(module_path)
            if hasattr(module, name):
                value = getattr(module, name)
                _attr_cache[name] = value
                return value
        except ImportError as exc:
            # Do NOT mask a broken submodule behind a generic AttributeError:
            # log it and surface it in the final error message.
            import_errors.append(f"{module_path}: {exc}")
            logging.getLogger(__name__).warning(
                "agent_console: failed to import submodule %s: %s", module_path, exc
            )

    if import_errors:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r} "
            f"(import errors while searching submodules: {'; '.join(import_errors)})"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return all resolvable symbols for IDE autocomplete and dir() support.

    Aggregates __all__ from all submodules plus standard dunder attributes.
    Uses the lazy __all__ to avoid repeated submodule imports.
    """
    # __all__ is lazy — accessing it triggers the first-time computation
    dunder = sorted(name for name in globals() if name.startswith("__"))
    return sorted(__all__) + dunder  # type: ignore[arg-type]


# Lazy __all__ — defers submodule imports until first access.
# This preserves the lazy loading promise: submodules are NOT imported
# at package load time. They are imported only when __all__ is accessed
# (e.g., `from agent_console import *`, IDE autocomplete, or explicit use).
class _LazyAll:
    """Lazy list that computes __all__ on first access and caches the result."""

    __slots__ = ("_computed",)

    def __init__(self) -> None:
        self._computed: list[str] | None = None

    def _compute(self) -> list[str]:
        if self._computed is None:
            self._computed = self._build()
        return self._computed

    @staticmethod
    def _build() -> list[str]:
        """Build __all__ by aggregating __all__ from all submodules.

        Deduplicates using dict.fromkeys to preserve first-seen order while
        eliminating duplicates from submodules that share symbols.
        """
        all_symbols: list[str] = []
        for module_path in _SUBMODULE_PATHS:
            try:
                module = _importlib.import_module(module_path)
                all_symbols.extend(getattr(module, "__all__", []))
            except ImportError:
                continue
        return list(dict.fromkeys(all_symbols))

    def __iter__(self) -> iter:
        return iter(self._compute())

    def __len__(self) -> int:
        return len(self._compute())

    def __getitem__(self, index: int) -> str:
        return self._compute()[index]

    def __contains__(self, item: object) -> bool:
        return item in self._compute()

    def __repr__(self) -> str:
        return repr(self._compute())


__all__: list[str] = _LazyAll()  # type: ignore[assignment]


# Note: Internal symbols (_cw, _ConsoleMessage, _msg) are available via
# lazy loading but are implementation details for use by templates.py and
# display_*.py modules only. They are not part of the public API.
