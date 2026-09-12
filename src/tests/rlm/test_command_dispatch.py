"""Tests for .py command dispatch in rlm/command_dispatch.py.

Tests:
    TestCommandRegistry - COMMANDS dict populated correctly
    TestHelpDisplay - _show_help() works without errors
    TestGetAvailableCommands - returns COMMANDS dict
"""

import pytest

from rlm.command_dispatch import get_available_commands


class TestCommandRegistry:
    """Test COMMANDS registry."""

    def test_commands_dict_exists(self):
        from commands import COMMANDS
        assert isinstance(COMMANDS, dict)

    def test_expected_commands_present(self):
        from commands import COMMANDS
        expected = {"goal", "agent", "refine", "heartbeat", "autonomous", "continue"}
        assert expected.issubset(set(COMMANDS.keys()))

    def test_command_modules_are_callable(self):
        from commands import COMMANDS
        for name, mod in COMMANDS.items():
            assert hasattr(mod, "run"), f"Command {name} missing run() function"
            assert callable(mod.run), f"Command {name}.run() is not callable"


class TestHelpDisplay:
    """Test help display functions."""

    def test_show_help_does_not_crash(self):
        from agent_console.display_command import show_help
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            show_help()
        finally:
            sys.stdout = old_stdout

    def test_show_commands_does_not_crash(self):
        from agent_console.display_command import show_commands
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            show_commands()
        finally:
            sys.stdout = old_stdout


class TestGetAvailableCommands:
    """Test get_available_commands() function."""

    def test_returns_dict(self):
        result = get_available_commands()
        assert isinstance(result, dict)

    def test_returns_non_empty(self):
        result = get_available_commands()
        assert len(result) > 0

    def test_includes_expected_commands(self):
        result = get_available_commands()
        assert "goal" in result
        assert "agent" in result
        assert "continue" in result
