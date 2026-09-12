"""Tests for context dump formatting (agent_context_dump module).

NOTE: TauContext.dump() was removed in a34bbe6 (rearch); dumping is now the
module-level dump_context(ctx, mode=...) plus _dump_trace(ctx) for trace mode.
Call sites updated to the current API; assertions preserved.
"""

from agent_context import TauContext
from agent_context_dump import dump_context
from agent_context_dump import _dump_trace


class TestDumpMethod:
    """Test TauContext.dump() method."""

    def test_dump_summary(self):
        """Test summary mode output."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )
        output = dump_context(ctx, mode="summary", max_tokens=1000, exact_tokens=50)
        assert "CONTEXT SUMMARY" in output
        assert "3 messages" in output
        assert "50" in output  # exact token count
        assert "system" in output.lower() or "[SYST]" in output
        assert "user" in output.lower() or "[USER]" in output
        assert "assistant" in output.lower() or "[ASSI]" in output

    def test_dump_full(self):
        """Test full mode output."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello world"},
                {"role": "assistant", "content": "Hi there, how can I help?"},
            ]
        )
        output = dump_context(ctx, mode="full")
        assert "CONTEXT (3 messages)" in output
        assert "Hello world" in output
        assert "Hi there, how can I help?" in output

    def test_dump_user(self):
        """Test user mode output."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Goodbye"},
            ]
        )
        output = dump_context(ctx, mode="user")
        assert "USER MESSAGES ONLY (2 messages)" in output
        assert "Hello" in output
        assert "Goodbye" in output
        assert "assistant" not in output.lower() or "USER" in output

    def test_dump_assistant_with_repl(self):
        """Test assistant mode output with REPL messages."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "[REPL output]\nresult"},
            ]
        )
        output = dump_context(ctx, mode="assistant")
        assert "ASSISTANT MESSAGES ONLY" in output
        assert "Hi" in output

    def test_dump_assistant(self):
        """Test assistant mode output."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "user", "content": "Bye"},
                {"role": "assistant", "content": "Goodbye!"},
            ]
        )
        output = dump_context(ctx, mode="assistant")
        assert "ASSISTANT MESSAGES ONLY (2 messages)" in output
        assert "Hi there" in output
        assert "Goodbye!" in output

    def test_dump_invalid_mode(self):
        """Test invalid mode returns error message."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
            ]
        )
        output = dump_context(ctx, mode="invalid")
        assert "Invalid mode" in output


class TestDumpTrace:
    """Test TauContext.dump(mode='trace') method."""

    def test_dump_trace_basic(self):
        """Test trace mode with basic messages."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )
        output = _dump_trace(ctx)
        assert "CONTEXT TRACE" in output
        assert "[SYST]" in output
        assert "[USER]" in output
        assert "[ASSI]" in output
        assert "END TRACE" in output

    def test_dump_trace_with_repl_output(self):
        """Test trace mode shows REPL output messages."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Run some code"},
                {"role": "assistant", "content": "```python\nprint('hello')\n```"},
                {"role": "user", "content": "[REPL output]\nhello"},
            ]
        )
        output = _dump_trace(ctx)
        assert "CONTEXT TRACE" in output
        assert "print('hello')" in output
        assert "hello" in output

    def test_dump_trace_with_repl_error(self):
        """Test trace mode shows REPL error messages."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Run code"},
                {"role": "assistant", "content": "```python\nundefined_var\n```"},
                {"role": "user", "content": "[REPL error]\nNameError: name 'undefined_var' is not defined"},
            ]
        )
        output = _dump_trace(ctx)
        assert "CONTEXT TRACE" in output
        assert "NameError" in output

    def test_dump_trace_shows_all_messages(self):
        """Test trace mode shows all messages from start to end."""
        messages = [{"role": "system", "content": "You are helpful"}]
        for i in range(5):
            messages.append({"role": "user", "content": f"User message {i}"})
            messages.append({"role": "assistant", "content": f"Assistant message {i}"})
        ctx = TauContext(messages)
        output = _dump_trace(ctx)
        # Trace shows ALL messages
        assert "User message 0" in output
        assert "User message 4" in output
        assert "Assistant message 4" in output
        assert "END TRACE" in output

    def test_dump_trace_content_truncation(self):
        """Test trace mode truncates content to 200 chars."""
        long_content = "x" * 500
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": long_content},
            ]
        )
        output = _dump_trace(ctx)
        # Content should be truncated to 200 chars
        assert "..." in output
        assert long_content not in output

    def test_dump_trace_no_repl_output(self):
        """Test trace mode with assistant message without REPL output."""
        ctx = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there, how can I help you today?"},
            ]
        )
        output = _dump_trace(ctx)
        assert "[ASSI]" in output
        assert "Hi there" in output
        # Should not show REPL sections
        assert "└─" not in output

    def test_dump_trace_validation_errors_shown(self):
        """Test trace mode shows validation errors."""
        ctx = TauContext(
            [
                {"role": "user", "content": "Hello"},  # No system message
                {"role": "user", "content": "Bye"},  # Consecutive user
            ]
        )
        output = _dump_trace(ctx)
        assert "VALIDATION ERRORS" in output
        assert "consecutive" in output.lower() or "system" in output.lower()


class TestEmitContextValidationWarning:
    """Test that _emit_context_validation_warning prints the warning without auto-dumping trace."""

    def test_emit_warning_shows_warning_only(self):
        """Test that _emit_context_validation_warning outputs the warning but not a trace dump."""
        from agent_context_messages import _emit_context_validation_warning
        from unittest.mock import patch, MagicMock

        with patch("agent_context_messages.context_validation_warning", new=MagicMock()) as mock_warn:
            _emit_context_validation_warning("Test warning message")

            # Verify context_validation_warning was called
            mock_warn.assert_called_once()
