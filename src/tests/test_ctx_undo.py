"""Tests for /ctx undo command."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from commands.ctx import run, _ctx_undo


def _make_agent(messages: list[dict]) -> MagicMock:
    """Create a mock agent with the given context messages."""
    agent = MagicMock()
    agent.context = MagicMock()
    agent.context._messages = messages
    return agent


def _real(content: str) -> dict:
    """Create a real user message with proper prefix."""
    return {"role": "user", "content": f"[U:real | N:0 | M:0 | C:0%] {content}"}


def _repl(content: str) -> dict:
    """Create a REPL user message with proper prefix."""
    return {"role": "user", "content": f"[U:repl | N:0 | M:1 | C:1%] {content}"}


def _meta(content: str) -> dict:
    """Create a meta user message with proper prefix."""
    return {"role": "user", "content": f"[U:meta | N:0 | M:2 | C:2%] {content}"}


class TestCtxUndo:
    """Tests for _ctx_undo() and /ctx undo command."""

    def test_undo_1_turn(self):
        """Undo 1 turn removes last user+assistant pair."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("Hello"),
            {"role": "assistant", "content": "Hi!"},
            _real("Bye"),
            {"role": "assistant", "content": "Goodbye!"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 1)
        assert "Undid 1 turn" in result
        # Should keep: system, user1, assistant1 (removed user2, assistant2)
        assert len(agent.context._messages) == 3
        assert agent.context._messages[-1]["content"] == "Hi!"

    def test_undo_2_turns(self):
        """Undo 2 turns removes last 2 user+assistant pairs."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("T1"),
            {"role": "assistant", "content": "A1"},
            _real("T2"),
            {"role": "assistant", "content": "A2"},
            _real("T3"),
            {"role": "assistant", "content": "A3"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 2)
        assert "Undid 2 turn" in result
        # Should keep: system, user1, assistant1
        assert len(agent.context._messages) == 3
        assert agent.context._messages[-1]["content"] == "A1"

    def test_undo_all_turns(self):
        """Undo more turns than available keeps only system message."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("Hello"),
            {"role": "assistant", "content": "Hi!"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 10)
        assert "Undid 10 turn" in result
        # Should keep only system message
        assert len(agent.context._messages) == 1
        assert agent.context._messages[0]["role"] == "system"

    def test_no_user_turns(self):
        """Undo with no user turns returns error."""
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "assistant", "content": "Hi!"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 1)
        assert "No user turns found" in result
        # Messages unchanged
        assert len(agent.context._messages) == 2

    def test_excludes_repl_and_meta(self):
        """REPL and meta user messages are not counted as turns."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("Real task"),
            {"role": "assistant", "content": "A1"},
            _repl("[REPL output] 42"),
            {"role": "assistant", "content": "A2"},
            _meta("continuing"),
            {"role": "assistant", "content": "A3"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 1)
        assert "Undid 1 turn" in result
        # Only 1 real user (index 1), n=1 >= len=1, so undo all
        # target = 1 (keep system)
        assert len(agent.context._messages) == 1
        assert agent.context._messages[0]["role"] == "system"

    def test_mixed_real_and_repl(self):
        """Multiple real turns with REPL in between."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("T1"),
            {"role": "assistant", "content": "A1"},
            _repl("[REPL output] result"),
            {"role": "assistant", "content": "A2"},
            _real("T2"),
            {"role": "assistant", "content": "A3"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 1)
        assert "Undid 1 turn" in result
        # Real users at indices 1, 5. n=1, target = user_indices[-1] = 5
        # Keep messages[0:5] = [system, T1, A1, repl, A2]
        assert len(agent.context._messages) == 5
        assert "T1" in agent.context._messages[1]["content"]

    def test_no_system_message(self):
        """Undo all turns without system message."""
        msgs = [
            _real("Hello"),
            {"role": "assistant", "content": "Hi!"},
        ]
        agent = _make_agent(msgs)
        result = _ctx_undo(agent, 10)
        assert "Undid 10 turn" in result
        assert len(agent.context._messages) == 0

    def test_via_run_command(self):
        """Test /ctx undo via the run() function."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("Hello"),
            {"role": "assistant", "content": "Hi!"},
        ]
        agent = _make_agent(msgs)
        result = run(agent, ["undo"])
        assert "Undid 1 turn" in result

    def test_via_run_command_with_n(self):
        """Test /ctx undo 2 via the run() function."""
        msgs = [
            {"role": "system", "content": "System"},
            _real("T1"),
            {"role": "assistant", "content": "A1"},
            _real("T2"),
            {"role": "assistant", "content": "A2"},
        ]
        agent = _make_agent(msgs)
        result = run(agent, ["undo", "2"])
        assert "Undid 2 turn" in result

    def test_invalid_n(self):
        """Test /ctx undo with invalid n."""
        agent = _make_agent([])
        result = run(agent, ["undo", "abc"])
        assert "Invalid undo count" in result

    def test_negative_n(self):
        """Test /ctx undo with negative n."""
        agent = _make_agent([])
        result = run(agent, ["undo", "-1"])
        assert "must be >= 1" in result
