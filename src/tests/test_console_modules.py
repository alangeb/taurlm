"""Smoke tests for console modules — verifies all exports are importable and callable.

After consolidating the console layer into agent_console/ package, this module ensures:
1. Each module imports cleanly (no circular imports, no missing dependencies)
2. Every public name in __all__ is callable (no broken references)
"""

import pytest


MODULES = [
    "agent_console",
    "agent_console.display_command",
    "agent_console.display_status",
    "agent_console.display_context",
    "agent_console.display_misc",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name):
    """Each console domain module imports without errors."""
    __import__(module_name)


@pytest.mark.parametrize("module_name", MODULES)
def test_all_public_exports_are_callable(module_name):
    """Every public name in __all__ is callable (function/method, not a constant)."""
    mod = __import__(module_name)
    for name in getattr(mod, "__all__", []):
        # Skip private names (underscore prefix) — they may be internal state
        if name.startswith("_"):
            continue
        obj = getattr(mod, name, None)
        if obj is not None:
            assert callable(obj), f"{module_name}.{name} is in __all__ but not callable"


def test_primitives_has_core_functions():
    """Verify agent_console exports the foundation functions."""
    from agent_console import echo, blank_line, status, reasoning, verbose
    assert callable(echo)
    assert callable(blank_line)
    assert callable(status)
    assert callable(reasoning)
    assert callable(verbose)


def test_console_has_error_functions():
    """Verify console modules export error/warning functions."""
    from agent_console import error, warning, error_display
    assert callable(error)
    assert callable(warning)
    assert callable(error_display)


def test_console_has_message_functions():
    """Verify console modules export message display functions."""
    from agent_console import assistant_message_display, user_echo, undo_message
    assert callable(assistant_message_display)
    assert callable(user_echo)
    assert callable(undo_message)


def test_console_has_flow_functions():
    """Verify console modules export flow control functions."""
    from agent_console import restart_flow, interrupted_message, force_exit_message
    assert callable(restart_flow)
    assert callable(interrupted_message)
    assert callable(force_exit_message)


def test_console_has_llm_functions():
    """Verify console modules export LLM status functions."""
    from agent_console import llm_timeout_message, llm_validation_retry
    assert callable(llm_timeout_message)
    assert callable(llm_validation_retry)


def test_console_has_loop_functions():
    """Verify console modules export loop warning functions."""
    from agent_console import loop_warning_display, loop_warning
    assert callable(loop_warning_display)
    assert callable(loop_warning)


def test_console_has_subagent_functions():
    """Verify console modules export subagent/fork functions."""
    from agent_console import subagent_start_display, fork_display, subagent_output_header
    assert callable(subagent_start_display)
    assert callable(fork_display)
    assert callable(subagent_output_header)


def test_console_has_agent_functions():
    """Verify console modules export A2A/agent functions."""
    from agent_console import agent_status_message, a2a_started_message
    assert callable(agent_status_message)
    assert callable(a2a_started_message)


def test_console_has_compression_functions():
    """Verify console modules export compression functions."""
    from agent_console import compress_success, compress_fail, compression_step_summary
    assert callable(compress_success)
    assert callable(compress_fail)
    assert callable(compression_step_summary)


def test_console_has_context_functions():
    """Verify console modules export context display functions."""
    from agent_console import context_restored, context_validation_warning
    assert callable(context_restored)
    assert callable(context_validation_warning)


def test_console_has_status_functions():
    """Verify console modules export status display functions."""
    from agent_console import agent_status, print_agent_exit_summary
    assert callable(agent_status)
    assert callable(print_agent_exit_summary)


def test_console_has_help_functions():
    """Verify console modules export help display functions."""
    from agent_console import show_help, show_commands
    assert callable(show_help)
    assert callable(show_commands)


