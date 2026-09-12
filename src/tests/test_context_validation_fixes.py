"""Tests for context validation.

Covers:
1. Synthetic prefix detection with correct prefix
2. Empty content with empty tool_calls validation
3. System message preservation after compression
4. Mid-batch validation suppression

"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from agent_context import TauContext
from agent_context_compress import (
    _compress_wrapper,
)
from agent_context_compress.steps.conversation_summary import _extract_synthetic_category
from agent_message_utils import is_synthetic_message


class TestSyntheticPrefixDetection:
    """Test that is_synthetic_message() detects both legacy and new format prefixes."""

    def test_correct_prefix_detected(self):
        """Messages with [SYSTEM-SYNTHETIC: category] are detected as synthetic."""
        msg = {"role": "user", "content": "[SYSTEM-SYNTHETIC: end_turn_reminder] Previous turn ended."}
        assert is_synthetic_message(msg) is True

    def test_old_prefix_not_detected(self):
        """Messages with old [SYNTHETIC: prefix] are NOT detected as synthetic."""
        msg = {"role": "user", "content": "[SYNTHETIC:end_turn_reminder] Previous turn ended."}
        assert is_synthetic_message(msg) is False

    def test_non_synthetic_message(self):
        """Regular messages are not detected as synthetic."""
        msg = {"role": "user", "content": "Hello, how are you?"}
        assert is_synthetic_message(msg) is False

    def test_synthetic_in_list_content(self):
        """Synthetic messages with list content are detected."""
        msg = {"role": "user", "content": [{"type": "text", "text": "[SYSTEM-SYNTHETIC: bridge] bridge content"}]}
        assert is_synthetic_message(msg) is True

    def test_new_format_meta_detected(self):
        """Messages with [U:meta | N:0] prefix are detected as synthetic."""
        msg = {"role": "user", "content": "[U:meta | N:0] Some meta content"}
        assert is_synthetic_message(msg) is True

    def test_new_format_real_not_detected(self):
        """Messages with [U:real | N:0] prefix are NOT detected as synthetic."""
        msg = {"role": "user", "content": "[U:real | N:0] Hello, how are you?"}
        assert is_synthetic_message(msg) is False

    def test_extract_category_correct(self):
        """Category extraction works with correct prefix."""
        msg = {"role": "user", "content": "[SYSTEM-SYNTHETIC: end_turn_reminder] content"}
        category = _extract_synthetic_category(msg)
        assert category == "end_turn_reminder"

    def test_extract_category_old_prefix_fails(self):
        """Category extraction fails with old prefix."""
        msg = {"role": "user", "content": "[SYNTHETIC:end_turn_reminder] content"}
        category = _extract_synthetic_category(msg)
        assert category == "unknown"

    def test_extract_category_new_format(self):
        """Category extraction works with new [U:TYPE | N:stack] format."""
        msg = {"role": "user", "content": "[U:meta | N:0] Some meta content"}
        category = _extract_synthetic_category(msg)
        assert category == "meta"


class TestEmptyToolCallsValidation:
    """Test that empty tool_calls list doesn't trigger false validation errors."""

    def test_empty_content_with_empty_tool_calls_fails(self):
        """Assistant with content='' and tool_calls=[] fails validation (no tool calls present)."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": "", "tool_calls": []},
        ])
        errors = ctx.validate()
        # Empty tool_calls list means no tool calls, so validation should fail
        assert any("content cannot be empty string" in e for e in errors)

    def test_empty_content_with_no_tool_calls_fails(self):
        """Assistant with content='' and no tool_calls field fails validation."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": ""},
        ])
        errors = ctx.validate()
        assert any("content cannot be empty string" in e for e in errors)

    def test_none_content_with_empty_tool_calls_fails(self):
        """Assistant with content=None and tool_calls=[] fails validation (no tool calls present)."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": None, "tool_calls": []},
        ])
        errors = ctx.validate()
        # Empty tool_calls list means no tool calls, so validation should fail
        assert "content cannot be None" in " ".join(errors)

    def test_empty_content_with_real_tool_calls_passes(self):
        """Assistant with content='' and real tool_calls passes validation."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
            },
        ])
        errors = ctx.validate()
        assert "content cannot be empty string" not in " ".join(errors)

    def test_none_content_with_real_tool_calls_passes(self):
        """Assistant with content=None and real tool_calls passes validation."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
            },
        ])
        errors = ctx.validate()
        assert "content cannot be None" not in " ".join(errors)


class TestSystemMessagePreservation:
    """Test that system message is preserved after compression."""

    def test_compression_wrapper_restores_system_message(self):
        """_compress_wrapper restores system message if impl loses it."""
        # Mock impl that returns context without system message
        def impl_no_system(ctx, *args, **kwargs):
            # Return context without system message
            return [msg for msg in ctx.context if msg.get("role") != "system"], "LOST_SYSTEM"

        ctx = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Task"},
        ]
        result, metadata = _compress_wrapper(
            "TEST_STEP", impl_no_system, ctx, 1000, False, None
        )
        assert result[0].get("role") == "system"
        assert result[0].get("content") == "System prompt"

    def test_compression_wrapper_keeps_existing_system_message(self):
        """_compress_wrapper keeps system message if impl preserves it."""
        def impl_keeps_system(ctx, *args, **kwargs):
            return ctx.context, "OK"

        ctx = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Task"},
        ]
        result, metadata = _compress_wrapper(
            "TEST_STEP", impl_keeps_system, ctx, 1000, False, None
        )
        assert result[0].get("role") == "system"
        assert result[0].get("content") == "System prompt"

    def test_compression_wrapper_no_system_in_original(self):
        """_compress_wrapper doesn't add system message if original lacks one."""
        def impl_no_system(ctx, *args, **kwargs):
            return ctx.context, "OK"

        ctx = [
            {"role": "user", "content": "Task"},
        ]
        result, metadata = _compress_wrapper(
            "TEST_STEP", impl_no_system, ctx, 1000, False, None
        )
        assert result[0].get("role") == "user"


class TestMidBatchValidationSuppression:
    """Test that _validate_on_mutation() suppresses transient errors during mid-batch."""

    def test_consecutive_roles_suppressed_during_mid_batch(self):
        """Consecutive role errors are suppressed when last msg is tool."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": "Calling tool", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]},
            {"role": "tool", "content": "result", "tool_call_id": "1", "name": "test"},
        ])
        # During mid-batch, consecutive role errors should be suppressed
        # (tool message at end means in_progress=True)
        ctx._validate_on_mutation()
        # Should not raise or log warnings for transient errors

    def test_synthetic_errors_suppressed_during_mid_batch(self):
        """Synthetic message errors are suppressed when last msg is tool."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": "Calling tool", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]},
            {"role": "tool", "content": "result", "tool_call_id": "1", "name": "test"},
        ])
        # Should not raise or log warnings
        ctx._validate_on_mutation()

    def test_unresolved_tool_calls_suppressed_during_mid_batch(self):
        """Unresolved tool call errors are suppressed when last msg is assistant with tool_calls."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {
                "role": "assistant",
                "content": "Calling tool",
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
            },
        ])
        # Should not raise or log warnings for unresolved tool calls
        ctx._validate_on_mutation()

    def test_real_errors_not_suppressed(self):
        """Real validation errors are NOT suppressed during mid-batch."""
        ctx = TauContext([
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": ""},  # Empty content, no tool_calls
        ])
        errors = ctx.validate()
        assert any("content cannot be empty string" in e for e in errors)
