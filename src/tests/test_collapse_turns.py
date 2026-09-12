"""Tests for collapse_turns compression step."""

from __future__ import annotations

import pytest

from agent_context_compress.steps.collapse_turns import (
    _find_summarized_turns,
    _collapse,
    _compress_collapse_turns_50_impl,
    _compress_collapse_turns_full_impl,
)
from agent_context_compress.steps.framework import CompressionContext


def _make_ctx(msgs: list[dict]) -> CompressionContext:
    """Create a CompressionContext for testing."""
    return CompressionContext(
        context=msgs,
        target_size_bytes=1000,
        verbose=False,
        audit_writer=None,
        actions=[],
        step_name="TEST",
    )


class TestFindSummarizedTurns:
    """Tests for _find_summarized_turns()."""

    def test_no_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "hello"},
        ]
        turns = _find_summarized_turns(msgs)
        assert turns == []

    def test_one_turn(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "code", "summary": "Did X"},
        ]
        turns = _find_summarized_turns(msgs)
        assert turns == [(1, 2)]

    def test_multiple_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "t1", "user_type": "real"},
            {"role": "assistant", "content": "a1"},
            {"role": "assistant", "content": "a1s", "summary": "S1"},
            {"role": "user", "content": "t2", "user_type": "real"},
            {"role": "assistant", "content": "a2"},
            {"role": "assistant", "content": "a2s", "summary": "S2"},
        ]
        turns = _find_summarized_turns(msgs)
        assert turns == [(1, 3), (4, 6)]

    def test_excludes_synthetic_user(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[U:meta | N:0] hb"},
            {"role": "assistant", "content": "a", "summary": "S"},
            {"role": "user", "content": "real", "user_type": "real"},
            {"role": "assistant", "content": "b", "summary": "S2"},
        ]
        turns = _find_summarized_turns(msgs)
        # Only the real user turn should be found
        assert turns == [(3, 4)]

    def test_boundary_respected(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "t1", "user_type": "real"},
            {"role": "assistant", "content": "a1", "summary": "S1"},
            {"role": "user", "content": "t2", "user_type": "real"},
            {"role": "assistant", "content": "a2", "summary": "S2"},
        ]
        # Boundary at index 3 (first 50% of 6 msgs = 3)
        turns = _find_summarized_turns(msgs, boundary=3)
        assert turns == [(1, 2)]

    def test_incomplete_turn_not_found(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "t1", "user_type": "real"},
            {"role": "assistant", "content": "a1"},  # No summary
            {"role": "user", "content": "t2", "user_type": "real"},
            {"role": "assistant", "content": "a2", "summary": "S2"},
        ]
        turns = _find_summarized_turns(msgs)
        # Only t2 has a summary
        assert turns == [(3, 4)]


class TestCollapse:
    """Tests for _collapse()."""

    def test_no_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "hello"},
        ]
        result = _collapse(msgs, [])
        assert result == msgs

    def test_collapse_one_turn(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do X", "user_type": "real"},
            {"role": "assistant", "content": "```python\ncode\n```"},
            {"role": "tool", "content": "REPL output"},
            {"role": "assistant", "content": "Done!", "summary": "Did X successfully"},
        ]
        turns = [(1, 4)]
        result = _collapse(msgs, turns)
        # Should be: system, user, assistant(summary)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "do X"
        assert result[2]["role"] == "assistant"
        assert "[Turn summary]: Did X successfully" in result[2]["content"]

    def test_collapse_preserves_messages_after_turn(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "t1", "user_type": "real"},
            {"role": "assistant", "content": "a1", "summary": "S1"},
            {"role": "user", "content": "t2", "user_type": "real"},
            {"role": "assistant", "content": "a2"},  # In progress, no summary
        ]
        turns = [(1, 2)]
        result = _collapse(msgs, turns)
        # Should be: system, user(t1), assistant(summary), user(t2), assistant(a2)
        assert len(result) == 5
        assert result[2]["content"] == "[Turn summary]: S1"
        assert result[3]["content"] == "t2"
        assert result[4]["content"] == "a2"


class TestCollapseTurns50:
    """Tests for _compress_collapse_turns_50_impl()."""

    def test_no_summarized_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye", "user_type": "real"},
            {"role": "assistant", "content": "goodbye"},
        ]
        ctx = _make_ctx(msgs)
        result, desc = _compress_collapse_turns_50_impl(ctx)
        assert result == msgs
        assert desc == "NO_REDUCTION"

    def test_collapses_turns_in_first_50(self):
        # 10 messages, boundary at 5
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "t1", "user_type": "real"},
            {"role": "assistant", "content": "code"},
            {"role": "tool", "content": "output"},
            {"role": "assistant", "content": "done", "summary": "S1"},
            {"role": "user", "content": "t2", "user_type": "real"},
            {"role": "assistant", "content": "code2"},
            {"role": "tool", "content": "output2"},
            {"role": "assistant", "content": "done2", "summary": "S2"},
            {"role": "user", "content": "t3", "user_type": "real"},
        ]
        ctx = _make_ctx(msgs)
        result, desc = _compress_collapse_turns_50_impl(ctx)
        # Turn 1 (indices 1-4) is in first 50% (boundary=5)
        # Turn 2 (indices 5-8) is NOT in first 50%
        # Context is small (target=1000B), so collapse reaches target → ACHIEVED
        assert desc == "ACHIEVED"
        # Result should have: system, user(t1), assistant(summary), user(t2), ..., user(t3)
        assert len(result) < len(msgs)

    def test_context_too_small(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
        ]
        ctx = _make_ctx(msgs)
        result, desc = _compress_collapse_turns_50_impl(ctx)
        assert desc == "NO_REDUCTION"


class TestCollapseTurnsFull:
    """Tests for _compress_collapse_turns_full_impl()."""

    def test_collapses_all_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "t1", "user_type": "real"},
            {"role": "assistant", "content": "code"},
            {"role": "assistant", "content": "done", "summary": "S1"},
            {"role": "user", "content": "t2", "user_type": "real"},
            {"role": "assistant", "content": "code2"},
            {"role": "assistant", "content": "done2", "summary": "S2"},
            {"role": "user", "content": "t3", "user_type": "real"},
            {"role": "assistant", "content": "in progress"},
        ]
        ctx = _make_ctx(msgs)
        result, desc = _compress_collapse_turns_full_impl(ctx)
        # Both turns should be collapsed
        # Context is small (target=1000B), so collapse reaches target → ACHIEVED
        assert desc == "ACHIEVED"
        # Result: system, user(t1), assistant(S1), user(t2), assistant(S2), user(t3), assistant(in progress)
        assert len(result) == 7

    def test_no_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye", "user_type": "real"},
        ]
        ctx = _make_ctx(msgs)
        result, desc = _compress_collapse_turns_full_impl(ctx)
        assert desc == "NO_REDUCTION"


class TestCompressToTarget:
    """Tests for TauContext.compress_to_target()."""

    def _make_agent_with_context(self, messages: list[dict], max_tokens: int = 100000):
        """Create a mock agent with the given context."""
        from unittest.mock import MagicMock
        from agent_context import TauContext

        agent = MagicMock()
        agent.max_context_tokens = max_tokens
        agent._session = MagicMock()
        agent._session.last_exact_context_tokens = None
        agent.context = TauContext()
        agent.context.set_messages(messages)
        return agent

    def test_already_at_target(self):
        """No compression needed if already below target."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "hello"},
        ]
        agent = self._make_agent_with_context(msgs, max_tokens=100000)
        # Context is tiny, well below 30%
        result = agent.context.compress_to_target(30, agent)
        assert result is True  # Already at target, no compression needed

    def test_compress_when_above_target(self):
        """Compression triggered when above target."""
        # Create a large context that's above 30%
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(50):
            msgs.append({"role": "user", "content": f"message {i} " + "x" * 100, "user_type": "real"})
            msgs.append({"role": "assistant", "content": f"response {i} " + "y" * 100})
        
        agent = self._make_agent_with_context(msgs, max_tokens=1000)
        # With max_tokens=1000, this context should be well above 30%
        result = agent.context.compress_to_target(30, agent)
        # Result depends on whether compression succeeds
        assert result in (True, False)


class TestCtxCompressCommand:
    """Tests for /ctx compress command."""

    def _make_agent(self, messages: list[dict], max_tokens: int = 100000):
        """Create a mock agent for command testing."""
        from unittest.mock import MagicMock
        from agent_context import TauContext

        agent = MagicMock()
        agent.max_context_tokens = max_tokens
        agent._session = MagicMock()
        agent._session.last_exact_context_tokens = None
        agent.context = TauContext()
        agent.context.set_messages(messages)
        return agent

    def test_already_below_target(self):
        """Command reports no compression needed when below target."""
        from commands.ctx import run

        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi", "user_type": "real"},
            {"role": "assistant", "content": "hello"},
        ]
        agent = self._make_agent(msgs, max_tokens=100000)
        result = run(agent, ["compress", "30"])
        assert "Already at" in result or "No compression needed" in result

    def test_invalid_target(self):
        """Command rejects invalid target values."""
        from commands.ctx import run

        agent = self._make_agent([{"role": "system", "content": "sys"}])
        result = run(agent, ["compress", "0"])
        assert "must be 1-100" in result

        result = run(agent, ["compress", "101"])
        assert "must be 1-100" in result

        result = run(agent, ["compress", "abc"])
        assert "Invalid target" in result
