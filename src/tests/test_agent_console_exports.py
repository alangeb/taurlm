"""Validation test for agent_console package exports.

Ensures that __init__.py imports match __all__ and submodule __all__ lists.
This prevents drift when new symbols are added to submodules.
"""
import importlib


def test_all_exports_are_imported():
    """Every symbol in __all__ must be importable from the package."""
    import agent_console
    for name in agent_console.__all__:
        assert hasattr(agent_console, name), f"{name!r} in __all__ but not imported"


def test_all_imports_are_in_all():
    """Every imported symbol must be in __all__ (except internal names and submodules)."""
    import agent_console
    # Exclude submodule modules and __future__ imports
    submodule_names = {"audit", "audit_display", "display_command",
                        "display_status", "display_context", "display_misc",
                        "primitives", "templates"}
    imported = {
        name for name in dir(agent_console)
        if not name.startswith('_') and name not in submodule_names and name != "annotations"
    }
    exported = set(agent_console.__all__)
    missing = imported - exported
    assert not missing, f"Imported but not in __all__: {sorted(missing)}"


def test_exports_match_submodule_all():
    """Every exported symbol must exist in its submodule's __all__."""
    import agent_console
    submodules = [
        "agent_console.audit",
        "agent_console.audit_display",
        "agent_console.primitives",
        "agent_console.templates",
                "agent_console.display_command",
        "agent_console.display_status",
        "agent_console.display_context",
        "agent_console.display_misc",
    ]
    submodule_symbols = set()
    for mod_name in submodules:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "__all__"):
            submodule_symbols.update(mod.__all__)
    exported = set(agent_console.__all__)
    missing = exported - submodule_symbols
    assert not missing, f"Exported but not in any submodule __all__: {sorted(missing)}"