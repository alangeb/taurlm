"""Test FULL_RESET: after compression, context must end with USER message.

D5 REVERTED (2026-09-12): compressed context ends on USER (overflow-retry re-sends
the compressed list as the live prompt; a trailing assistant causes prefill).
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agent_context_compress.steps.full_reset import _compress_full_reset_impl, _merge_consecutive
from agent_context_compress.steps.framework import CompressionContext


def _make_ctx(messages: list[dict]) -> CompressionContext:
    """Build a minimal CompressionContext for testing."""
    ctx = CompressionContext.__new__(CompressionContext)
    ctx.context = messages
    ctx.target_size_bytes = 0
    ctx.verbose = False
    ctx.audit_writer = None
    ctx.actions = []
    ctx.step_name = "FULL_RESET"
    return ctx


def _mock_llm_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def _mock_is_real_user(msg: dict) -> bool:
    """Mock is_real_user_request: treat user messages with type='real' as real."""
    return msg.get("role") == "user" and msg.get("type") == "real"


# Make original context large so compressed version is smaller
LONG_CONTENT = "x" * 5000


class TestFullResetContextStructure:
    """Verify the FULL_RESET output context structure."""

    def _run_full_reset(self, context: list[dict]) -> tuple[list[dict], str]:
        """Run _compress_full_reset_impl with mocked LLM calls."""
        ctx = _make_ctx(context)
        with patch("agent_context_compress.steps.full_reset.is_real_user_request", _mock_is_real_user), \
             patch("agent_context_compress.steps.full_reset._invoke_llm_with_retry_compression") as mock_llm:
            # First call: summary (must be >= 50 chars to pass validation)
            # Second call: plan
            mock_llm.side_effect = [
                _mock_llm_response("Summary: analyzed codebase, fixed bug in module X, wrote tests."),
                _mock_llm_response("Next steps: 1. Write tests 2. Run the suite 3. Deploy."),
            ]
            result = _compress_full_reset_impl(ctx, client=None, model_name="test")
        return result

    def _make_large_context(self) -> list[dict]:
        """Create a context with large messages so compression reduces size."""
        return [
            {"role": "system", "content": "You are TauRLM."},
            {"role": "user", "content": LONG_CONTENT, "type": "real"},
            {"role": "assistant", "content": LONG_CONTENT},
            {"role": "user", "content": "Follow up question", "type": "real"},
        ]

    def test_context_ends_with_user(self):
        """R1 regression: after full_reset, context must end with role='user'.

        D5 REVERTED 2026-09-12: a trailing assistant makes the model prefill
        instead of answering the pending request (the compressed list is re-sent
        as the live prompt). Must end on USER.
        """
        context = self._make_large_context()
        new_context, status = self._run_full_reset(context)
        assert status == "RESET", f"Expected RESET, got {status}"
        assert new_context[-1]["role"] == "user", (
            f"Context must end with user, got: {new_context[-1]['role']}"
        )

    def test_valid_role_alternation(self):
        """No consecutive same-role messages in the resulting context."""
        context = self._make_large_context()
        new_context, status = self._run_full_reset(context)
        assert status == "RESET", f"Expected RESET, got {status}"
        for i in range(1, len(new_context)):
            assert new_context[i]["role"] != new_context[i - 1]["role"], (
                f"Consecutive same-role at index {i}: {new_context[i-1]['role']} -> {new_context[i]['role']}"
            )

    def test_no_trailing_synthetic_assistant(self):
        """R1 regression: full_reset must NOT append a synthetic assistant dummy.

        The compressed list is re-sent AS-IS as the live prompt, so it ends on the
        user's pending request with no trailing assistant turn.
        """
        context = self._make_large_context()
        new_context, status = self._run_full_reset(context)
        assert status == "RESET"
        assert new_context[-1]["role"] == "user"
        # No synthetic assistant anywhere in the rebuilt body.
        for m in new_context:
            assert "Acknowledged" not in str(m.get("content", "")), (
                "full_reset must not append a synthetic assistant message"
            )

    def test_system_message_preserved(self):
        """The system message should be preserved as the first message."""
        context = self._make_large_context()
        new_context, status = self._run_full_reset(context)
        assert status == "RESET"
        assert new_context[0]["role"] == "system"
        assert new_context[0]["content"] == "You are TauRLM."

    def test_merge_consecutive_helper(self):
        """_merge_consecutive should merge adjacent same-role messages."""
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
            {"role": "assistant", "content": "Hi"},
        ]
        merged = _merge_consecutive(msgs)
        assert len(merged) == 2
        assert merged[0]["role"] == "user"
        assert "Hello" in merged[0]["content"]
        assert "World" in merged[0]["content"]
        assert merged[1]["role"] == "assistant"
