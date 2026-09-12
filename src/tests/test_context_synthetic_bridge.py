"""Tests for synthetic-message handling in TauContext.

NOTE: The old "append_assistant auto-inserts a synthetic bridge" behavior was
intentionally removed in the RLM redesign ("RLM mode: no continuation bridge —
the loop guarantees a synthetic user message follows every assistant message",
agent_context_messages.append_assistant). Consecutive assistants are now merged
at turn close instead. Tests rewritten to the current contract: cleanup_synthetic()
removes synthetic messages, close_turn() cleans + merges, and
append_synthetic_user_with_bridge() injects a synthetic user. Tool-call bridge
tests were dropped (append_tool / assistant tool_calls removed in Phase 7).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_context import TauContext
from agent_message_utils import is_synthetic_message


class TestNoContinuationBridge:
    """append_assistant() no longer inserts a bridge (RLM redesign)."""

    def _make_valid_context(self):
        ctx = TauContext()
        ctx.set_system("You are a helpful assistant.")
        ctx.append_user("Hello")
        return ctx

    def test_consecutive_assistant_no_bridge(self):
        """Consecutive assistants are appended directly — no synthetic bridge."""
        ctx = self._make_valid_context()
        ctx.append_assistant("First response.")
        ctx.append_assistant("Second response.")
        msgs = ctx.get_messages()
        # system, user, assistant, assistant — no bridge inserted
        assert len(msgs) == 4, f"Expected 4 messages, got {len(msgs)}"
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "assistant"]
        assert not any(is_synthetic_message(m) for m in msgs), "No bridge in RLM mode"

    def test_valid_sequence_no_bridge(self):
        """Valid user -> assistant sequence has no synthetic message."""
        ctx = self._make_valid_context()
        ctx.append_assistant("Response.")
        msgs = ctx.get_messages()
        assert len(msgs) == 3
        assert not any(is_synthetic_message(m) for m in msgs)

    def test_assistant_content_preserved(self):
        """Both assistant messages keep their content (no bridge, no merge yet)."""
        ctx = self._make_valid_context()
        ctx.append_assistant("Response A")
        ctx.append_assistant("Response B")
        assistants = [m for m in ctx.get_messages() if m["role"] == "assistant"]
        assert [m["content"] for m in assistants] == ["Response A", "Response B"]


class TestSyntheticUserWithBridge:
    """append_synthetic_user_with_bridge() injects a synthetic user message."""

    def test_injects_synthetic_user(self):
        ctx = TauContext([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
        ctx.append_assistant("a")
        ctx.append_synthetic_user_with_bridge("heartbeat", "beat")
        msgs = ctx.get_messages()
        # a synthetic user was appended
        assert msgs[-1]["role"] == "user"
        assert is_synthetic_message(msgs[-1])


class TestSyntheticCleanup:
    """cleanup_synthetic() removes synthetic messages, preserves real ones."""

    def _ctx_with_synthetic(self):
        ctx = TauContext()
        ctx.set_system("System.")
        ctx.append_user("User.")
        ctx.append_assistant("First.")
        ctx.append_synthetic_user("heartbeat", "beat")
        ctx.append_assistant("Second.")
        return ctx

    def test_cleanup_removes_synthetic(self):
        ctx = self._ctx_with_synthetic()
        assert any(is_synthetic_message(m) for m in ctx.get_messages())
        ctx.cleanup_synthetic()
        assert not any(is_synthetic_message(m) for m in ctx.get_messages())

    def test_cleanup_preserves_real_messages(self):
        ctx = self._ctx_with_synthetic()
        ctx.cleanup_synthetic()
        msgs = ctx.get_messages()
        # system, user, assistant, assistant (synthetic removed, NO merge)
        assert len(msgs) == 4
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert [m["role"] for m in msgs[2:]] == ["assistant", "assistant"]


class TestCloseTurn:
    """close_turn() cleans synthetic bridges AND merges consecutive assistants."""

    def test_close_turn_cleans_and_merges(self):
        ctx = TauContext()
        ctx.set_system("System.")
        ctx.append_user("User.")
        ctx.append_assistant("First.")
        ctx.append_assistant("Second.")
        ctx.close_turn("turn complete")
        msgs = ctx.get_messages()
        assert not any(is_synthetic_message(m) for m in msgs)
        assistants = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistants) == 1, f"Expected 1 merged assistant, got {len(assistants)}"
        assert "First." in assistants[0]["content"]
        assert "Second." in assistants[0]["content"]
