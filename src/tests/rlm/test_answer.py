"""Tests for RLM Answer Mechanism (rlm/answer.py)

Tests cover:
- Answer initialization
- Answer content setting
- Answer ready detection
- Answer state persistence
- Progressive answers
- Serialization
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from rlm.answer import AnswerManager, AnswerState


class TestAnswerManager:
    """Tests for AnswerManager class."""

    def test_initialization(self):
        """Test that answer is initialized correctly."""
        manager = AnswerManager()
        assert manager.content == ""
        assert manager.ready is False
        assert manager.is_ready() is False
        assert manager.get_content() == ""

    def test_set_content(self):
        """Test setting answer content."""
        manager = AnswerManager()
        manager.update_content("test answer")
        assert manager.content == "test answer"
        assert manager.get_content() == "test answer"

    def test_set_ready(self):
        """Test setting answer ready flag."""
        manager = AnswerManager()
        manager.set_ready(True)
        assert manager.ready is True
        assert manager.is_ready() is True

    def test_progressive_content(self):
        """Test that content can be updated progressively."""
        manager = AnswerManager()
        manager.update_content("Part 1")
        assert manager.content == "Part 1"
        manager.update_content("Part 1\nPart 2")
        assert manager.content == "Part 1\nPart 2"

    def test_ready_detection(self):
        """Test that ready state is detected correctly."""
        manager = AnswerManager()
        assert manager.is_ready() is False
        manager.set_ready(True)
        assert manager.is_ready() is True
        manager.set_ready(False)
        assert manager.is_ready() is False

    def test_state_persistence(self):
        """Test that answer state persists across updates."""
        manager = AnswerManager()
        manager.update_content("answer")
        manager.set_ready(True)
        assert manager.get_content() == "answer"
        assert manager.is_ready() is True

    def test_reset(self):
        """Test that answer can be reset."""
        manager = AnswerManager()
        manager.update_content("answer")
        manager.set_ready(True)
        manager.reset()
        assert manager.content == ""
        assert manager.ready is False
        assert manager.is_ready() is False

    def test_to_dict(self):
        """Test answer serialization."""
        manager = AnswerManager()
        manager.update_content("test")
        manager.set_ready(True)
        d = manager.to_dict()
        assert d["content"] == "test"
        assert d["ready"] is True

    def test_from_dict(self):
        """Test answer deserialization."""
        data = {
            "content": "test",
            "ready": True,
        }
        manager = AnswerManager.from_dict(data)
        assert manager.content == "test"
        assert manager.ready is True

    def test_get_dict(self):
        """Test get_dict for kernel namespace."""
        manager = AnswerManager()
        manager.update_content("answer")
        d = manager.get_dict()
        assert d["content"] == "answer"
        assert d["ready"] is False
        manager.set_ready(True)
        d = manager.get_dict()
        assert d["ready"] is True


class TestAnswerState:
    """Tests for AnswerState class."""

    def test_state_creation(self):
        """Test AnswerState creation."""
        state = AnswerState(content="test", ready=True)
        assert state.content == "test"
        assert state.ready is True

    def test_state_frozen(self):
        """Test that AnswerState is immutable."""
        state = AnswerState(content="test", ready=True)
        with pytest.raises(Exception):
            state.content = "modified"

    def test_state_clone(self):
        """Test AnswerState cloning."""
        state = AnswerState(content="test", ready=True)
        cloned = state.clone()
        assert cloned.content == "test"
        assert cloned.ready is True
        assert cloned is not state

    def test_state_to_dict(self):
        """Test AnswerState serialization."""
        state = AnswerState(content="test", ready=True)
        d = state.to_dict()
        assert d["content"] == "test"
        assert d["ready"] is True

    def test_state_from_dict(self):
        """Test AnswerState deserialization."""
        d = {"content": "test", "ready": True}
        state = AnswerState.from_dict(d)
        assert state.content == "test"
        assert state.ready is True
