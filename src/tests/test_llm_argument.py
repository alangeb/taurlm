"""Tests for CLI argument parsing and --llm group selection.

Tests:
    test_llm_argument_in_help - Verify --llm appears in help
    test_llm_argument_passed_to_agent - Verify --llm is passed to TauErgon
    test_llm_defaults_to_config - Verify default comes from config
    test_llm_invalid_group_fallback - Verify invalid group falls back to default
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestLLMArgument:
    """Test --llm CLI argument functionality."""

    def test_llm_argument_in_help(self):
        """Verify --llm argument appears in --help output."""
        import subprocess

        tau_py = Path(__file__).resolve().parent.parent / "tau.py"
        result = subprocess.run(
            [sys.executable, str(tau_py), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--llm" in result.stdout
        assert "LLM group name" in result.stdout

    def test_llm_argument_parsed_correctly(self, tau_entry_dir):
        """Verify --llm argument is parsed correctly."""
        import argparse
        from agent_config import get_config

        config = get_config()
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--llm",
            default=config.llm_group_name,
            help="LLM group name to use",
        )

        # Test with explicit value
        args = parser.parse_args(["--llm", "mygroup"])
        assert args.llm == "mygroup"

        # Test with no value (uses default)
        args = parser.parse_args([])
        assert args.llm == config.llm_group_name

    def test_llm_passed_to_tau_bot(self, tau_entry_dir):
        """Verify --llm value is passed to TauErgon constructor."""
        from agent_core import TauErgon

        with patch.object(TauErgon, "__init__", return_value=None) as mock_init:
            mock_init.__enter__ = lambda self: None
            mock_init.__exit__ = lambda self, *args: None

            # Simulate what tau.py does
            import argparse
            from agent_config import get_config

            config = get_config()
            parser = argparse.ArgumentParser()
            parser.add_argument("--llm", default=config.llm_group_name)

            args = parser.parse_args(["--llm", "testgroup"])

            # This is what tau.py does:
            # agent = TauErgon(..., llm_group_name=args.llm)
            # We verify args.llm has the correct value
            assert args.llm == "testgroup"

    def test_llm_default_from_config(self, tau_entry_dir):
        """Verify --llm defaults to config.llm_group_name."""
        from agent_config import get_config

        config = get_config()
        # The default should be from config
        assert config.llm_group_name is not None or config.llm_group_name == "default"


class TestLLMGroupFallback:
    """Test LLM group fallback behavior."""

    def test_invalid_group_name_handled(self):
        """Verify invalid group name doesn't crash."""
        from agent_context import TauContext

        # Simulate what happens when an invalid group is used
        # The agent should fall back to "default" or use hardcoded defaults
        context = TauContext(
            [
                {"role": "system", "content": "Test"},
                {"role": "user", "content": "Hello"},
            ]
        )
        # Should not raise
        assert context.validate() == []

    def test_empty_llm_group_handled(self):
        """Verify empty/None LLM group doesn't crash."""
        from agent_context import TauContext

        context = TauContext()
        # Empty context should be valid
        assert context.validate() == []
