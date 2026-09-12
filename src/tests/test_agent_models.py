"""Tests for agent_models module.

Tests:
    test_input_message - Verify InputMessage creation and factory methods
    test_input_message_from_interactive - Verify interactive message creation
    test_input_message_from_command_line - Verify CLI message creation
    test_input_message_from_a2a - Verify A2A message creation
    test_colors - Verify Colors class has expected attributes
    test_subagent_result - Verify SubAgentResult dataclass
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_models import InputMessage, Colors, SubAgentResult


class TestInputMessage:
    """Test InputMessage class."""

    def test_input_message_from_interactive(self):
        """Verify from_interactive creates message with source='interactive'."""
        msg = InputMessage.from_interactive("Hello")
        assert msg.source == "interactive"
        assert msg.content == "Hello"

    def test_input_message_from_command_line(self):
        """Verify from_command_line creates message with source='command_line'."""
        msg = InputMessage.from_command_line("Hello from CLI")
        assert msg.source == "command_line"
        assert msg.content == "Hello from CLI"

    def test_input_message_from_a2a(self):
        """Verify from_a2a creates message with source='a2a'."""
        msg = InputMessage.from_a2a("Hello from A2A", request_id="test-123")
        assert msg.source == "a2a"
        assert msg.content == "Hello from A2A"
        assert msg.request_id == "test-123"

    def test_input_message_a2a_auto_generates_uuid(self):
        """Verify from_a2a auto-generates UUID when not provided."""
        msg = InputMessage.from_a2a("Hello")
        assert msg.source == "a2a"
        assert msg.request_id is not None
        assert len(msg.request_id) > 0

    def test_input_message_has_timestamp(self):
        """Verify InputMessage has timestamp."""
        msg = InputMessage.from_interactive("Hello")
        assert msg.timestamp is not None
        import time

        assert abs(msg.timestamp - time.time()) < 5  # Within 5 seconds

    def test_input_message_equality(self):
        """Verify InputMessage equality comparison."""
        msg1 = InputMessage.from_interactive("Hello")
        msg2 = InputMessage.from_interactive("Hello")
        msg3 = InputMessage.from_interactive("World")
        # Dataclass equality compares all fields
        assert msg1.content == msg2.content
        assert msg1.content != msg3.content


class TestInputMessageFromInteractive:
    """Test InputMessage.from_interactive method."""

    def test_from_interactive_basic(self):
        """Verify from_interactive creates message with source='interactive'."""
        msg = InputMessage.from_interactive("Hello")
        assert msg.source == "interactive"
        assert msg.content == "Hello"

    def test_from_interactive_command(self):
        """Verify from_interactive handles commands (starting with /)."""
        msg = InputMessage.from_interactive("/recap")
        assert msg.source == "interactive"
        assert msg.content == "/recap"
        assert msg.content.startswith("/")

    def test_from_interactive_empty_string(self):
        """Verify from_interactive handles empty string."""
        msg = InputMessage.from_interactive("")
        assert msg.content == ""
        assert msg.source == "interactive"

    def test_from_interactive_with_timestamp(self):
        """Verify from_interactive accepts custom timestamp."""
        import time

        custom_ts = time.time()
        msg = InputMessage.from_interactive("Hello", timestamp=custom_ts)
        assert msg.timestamp == custom_ts


class TestInputMessageFromCommandLine:
    """Test InputMessage.from_command_line method."""

    def test_from_command_line_basic(self):
        """Verify from_command_line creates message with source='command_line'."""
        msg = InputMessage.from_command_line("Hello from CLI")
        assert msg.source == "command_line"
        assert msg.content == "Hello from CLI"

    def test_from_command_line_preserves_content(self):
        """Verify from_command_line preserves exact content."""
        content = "This is a test with special chars: !@#$%^&*()"
        msg = InputMessage.from_command_line(content)
        assert msg.content == content

    def test_from_command_line_vs_interactive(self):
        """Verify from_command_line and from_interactive have different sources."""
        msg_cli = InputMessage.from_command_line("Test")
        msg_interactive = InputMessage.from_interactive("Test")
        assert msg_cli.source == "command_line"
        assert msg_interactive.source == "interactive"
        assert msg_cli.content == msg_interactive.content


class TestColors:
    """Test Colors class for terminal colors."""

    def test_colors_has_expected_attributes(self):
        """Verify Colors class has expected color attributes."""
        assert hasattr(Colors, "RED")
        assert hasattr(Colors, "GREEN")
        assert hasattr(Colors, "YELLOW")
        assert hasattr(Colors, "CYAN")
        assert hasattr(Colors, "BLUE")
        assert hasattr(Colors, "WHITE")
        assert hasattr(Colors, "MAGENTA")
        assert hasattr(Colors, "RESET")
        assert hasattr(Colors, "INVERT_CYAN")
        assert hasattr(Colors, "INVERT_BLUE")
        assert hasattr(Colors, "REASONING")

    def test_colors_are_strings(self):
        """Verify color attributes are strings."""
        for attr in [
            "RED",
            "GREEN",
            "YELLOW",
            "CYAN",
            "BLUE",
            "WHITE",
            "MAGENTA",
            "RESET",
        ]:
            assert isinstance(
                getattr(Colors, attr), str
            ), f"Colors.{attr} is not a string"

    def test_colors_are_ansi_codes(self):
        """Verify color attributes are ANSI escape codes."""
        import re

        # All colors should be ANSI escape codes
        ansi_pattern = r"\033\[\d+(;\d+)*m"
        for color_name in [
            "RED",
            "GREEN",
            "YELLOW",
            "CYAN",
            "BLUE",
            "WHITE",
            "MAGENTA",
        ]:
            color = getattr(Colors, color_name)
            assert isinstance(color, str)
            assert re.match(
                ansi_pattern, color
            ), f"Colors.{color_name} is not a valid ANSI code: {color}"

    def test_reset_is_reset_code(self):
        """Verify RESET is the standard ANSI reset code."""
        assert Colors.RESET == "\033[0m"

    def test_invert_codes_exist(self):
        """Verify inverted color codes exist."""
        assert hasattr(Colors, "INVERT_CYAN")
        assert hasattr(Colors, "INVERT_BLUE")
        assert isinstance(Colors.INVERT_CYAN, str)
        assert isinstance(Colors.INVERT_BLUE, str)

    def test_reasoning_color_exists(self):
        """Verify REASONING color exists for thinking output."""
        assert hasattr(Colors, "REASONING")
        assert isinstance(Colors.REASONING, str)


class TestSubAgentResult:
    """Test SubAgentResult dataclass."""

    def test_subagent_result_creation(self):
        """Verify SubAgentResult can be created with all fields."""
        result = SubAgentResult(
            output="Test output",
            input_tokens=10,
            output_tokens=5,
        )
        assert result.output == "Test output"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    def test_subagent_result_all_fields_required(self):
        """Verify all fields are required for SubAgentResult."""
        with pytest.raises(TypeError):
            SubAgentResult(output="Test")  # Missing input_tokens and output_tokens

        with pytest.raises(TypeError):
            SubAgentResult(output="Test", input_tokens=10)  # Missing output_tokens

    def test_subagent_result_equality(self):
        """Verify SubAgentResult equality comparison."""
        result1 = SubAgentResult(output="Test", input_tokens=10, output_tokens=5)
        result2 = SubAgentResult(output="Test", input_tokens=10, output_tokens=5)
        result3 = SubAgentResult(output="Different", input_tokens=10, output_tokens=5)
        assert result1 == result2
        assert result1 != result3

    def test_subagent_result_dataclass(self):
        """Verify SubAgentResult is a dataclass."""
        assert hasattr(SubAgentResult, "__dataclass_fields__")
        fields = SubAgentResult.__dataclass_fields__
        assert "output" in fields
        assert "input_tokens" in fields
        assert "output_tokens" in fields
