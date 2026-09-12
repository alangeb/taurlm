"""Integration tests for LoopEscalationManager in agent_loop_escalation.py.

Tests the escalation manager that orchestrates loop recovery:
- Level 4+ (warnings 12+): Returns (termination message, True)
- Level 3 (warnings 9-11): Returns (guided introspection text, False)
- Level 2 (warnings 6-8): Returns (self-reflection text, False)
- Level 0-1 (warnings 0-5): Returns (None, False)

The manager does NOT append to context — it just returns (text, force_end)
tuples for the caller to handle. Tests verify NO context modifications.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from agent_loop_detect import LoopDetector
from agent_context import TauContext
from agent_loop_escalation import LoopEscalationManager


def _make_manager(escalation_level=0, total_warnings=0, tool_warnings=None):
    """Build a LoopEscalationManager with controllable escalation state."""
    detector = LoopDetector(repeat_threshold=1)
    detector.escalation_level = escalation_level
    detector.total_warnings = total_warnings
    detector.tool_warnings = tool_warnings or {}
    detector.get_escalation_info = MagicMock(return_value={
        "escalation_level": escalation_level,
        "total_warnings": total_warnings,
        "tool_warnings": tool_warnings or {},
    })
    context = TauContext()
    context.set_system("You are a helpful assistant.")
    agent = MagicMock()
    agent.last_substantive_response = None
    return LoopEscalationManager(detector, context, agent)


class TestHandleLoopEscalationLevel4:
    """Test Level 4+ (termination) escalation."""

    @patch("agent_loop_escalation.loop_warning")
    def test_level_4_returns_termination_message_and_true(self, mock_warn):
        """Level 4+ returns (termination message, True)."""
        mgr = _make_manager(escalation_level=4, total_warnings=12, tool_warnings={"bash": 10})
        text, force_end = mgr.handle_loop_escalation()

        assert force_end is True
        assert text is not None
        assert "12" in text
        assert "terminating" in text.lower()
        mock_warn.assert_called_once()

    @patch("agent_loop_escalation.loop_warning")
    def test_level_5_returns_termination_message_and_true(self, mock_warn):
        """Level 5 returns (termination message, True)."""
        mgr = _make_manager(escalation_level=5, total_warnings=15, tool_warnings={"bash": 10})
        text, force_end = mgr.handle_loop_escalation()

        assert force_end is True
        assert text is not None
        assert "15" in text
        mock_warn.assert_called_once()

    @patch("agent_loop_escalation.loop_warning")
    def test_level_4_does_not_modify_context(self, mock_warn):
        """Level 4 does NOT append to context."""
        mgr = _make_manager(escalation_level=4, total_warnings=12, tool_warnings={"bash": 10})
        initial_messages = mgr._context.get_messages()[:]
        mgr.handle_loop_escalation()

        # Context should be unchanged
        assert mgr._context.get_messages() == initial_messages

    @patch("agent_loop_escalation.loop_warning")
    def test_level_4_does_not_set_force_end_turn(self, mock_warn):
        """Level 4 does NOT set agent.force_end_turn — caller handles it."""
        mgr = _make_manager(escalation_level=4, total_warnings=12, tool_warnings={"bash": 10})
        mgr._agent.force_end_turn = None
        mgr.handle_loop_escalation()

        # force_end_turn should remain None — the manager just returns True
        assert mgr._agent.force_end_turn is None


class TestHandleLoopEscalationLevel3:
    """Test Level 3 (guided introspection) escalation."""

    @patch("agent_loop_escalation.loop_warning")
    def test_level_3_returns_guided_introspection_text(self, mock_warn):
        """Level 3 returns (guided introspection text, False)."""
        mgr = _make_manager(escalation_level=3, total_warnings=9, tool_warnings={"bash": 10})
        text, force_end = mgr.handle_loop_escalation()

        assert force_end is False
        assert text is not None
        assert "Guided introspection" in text
        mock_warn.assert_called_once()

    @patch("agent_loop_escalation.loop_warning")
    def test_level_3_text_varies_by_warning_count(self, mock_warn):
        """Level 3 text varies based on warning count (mod 3 cycling)."""
        texts = []
        for warnings in [9, 10, 11]:
            mgr = _make_manager(escalation_level=3, total_warnings=warnings, tool_warnings={"bash": 5})
            text, _ = mgr.handle_loop_escalation()
            texts.append(text)

        # All three texts should be different
        assert len(set(texts)) == 3

    @patch("agent_loop_escalation.loop_warning")
    def test_level_3_does_not_modify_context(self, mock_warn):
        """Level 3 does NOT append to context."""
        mgr = _make_manager(escalation_level=3, total_warnings=9, tool_warnings={"bash": 10})
        initial_messages = mgr._context.get_messages()[:]
        mgr.handle_loop_escalation()

        assert mgr._context.get_messages() == initial_messages


class TestHandleLoopEscalationLevel2:
    """Test Level 2 (simulated self-reflection) escalation."""

    @patch("agent_loop_escalation.loop_warning")
    def test_level_2_returns_self_reflection_text(self, mock_warn):
        """Level 2 returns (self-reflection text, False)."""
        mgr = _make_manager(escalation_level=2, total_warnings=6, tool_warnings={"bash": 5})
        text, force_end = mgr.handle_loop_escalation()

        assert force_end is False
        assert text is not None
        assert "Self-reflection" in text
        mock_warn.assert_called_once()

    @patch("agent_loop_escalation.loop_warning")
    def test_level_2_text_varies_by_warning_count(self, mock_warn):
        """Level 2 text varies based on warning count (mod 3 cycling)."""
        texts = []
        for warnings in [6, 7, 8]:
            mgr = _make_manager(escalation_level=2, total_warnings=warnings, tool_warnings={"bash": 3})
            text, _ = mgr.handle_loop_escalation()
            texts.append(text)

        # All three texts should be different
        assert len(set(texts)) == 3

    @patch("agent_loop_escalation.loop_warning")
    def test_level_2_does_not_modify_context(self, mock_warn):
        """Level 2 does NOT append to context."""
        mgr = _make_manager(escalation_level=2, total_warnings=6, tool_warnings={"bash": 5})
        initial_messages = mgr._context.get_messages()[:]
        mgr.handle_loop_escalation()

        assert mgr._context.get_messages() == initial_messages


class TestHandleLoopEscalationLevel1:
    """Test Level 1 (informational warning) escalation."""

    @patch("agent_loop_escalation.loop_warning")
    def test_level_1_returns_none_false(self, mock_warn):
        """Level 1 returns (None, False) — warning only."""
        mgr = _make_manager(escalation_level=1, total_warnings=3, tool_warnings={"bash": 3})
        text, force_end = mgr.handle_loop_escalation()

        assert text is None
        assert force_end is False
        mock_warn.assert_called_once()

    @patch("agent_loop_escalation.loop_warning")
    def test_level_1_does_not_modify_context(self, mock_warn):
        """Level 1 does NOT modify context."""
        mgr = _make_manager(escalation_level=1, total_warnings=3, tool_warnings={"bash": 3})
        initial_messages = mgr._context.get_messages()[:]
        mgr.handle_loop_escalation()

        assert mgr._context.get_messages() == initial_messages


class TestHandleLoopEscalationLevel0:
    """Test Level 0 (normal operation) — no escalation."""

    def test_level_0_returns_none_false(self):
        """Level 0 returns (None, False) with no side effects."""
        mgr = _make_manager(escalation_level=0, total_warnings=0, tool_warnings={})
        text, force_end = mgr.handle_loop_escalation()

        assert text is None
        assert force_end is False

    def test_level_0_does_not_modify_context(self):
        """Level 0 does NOT modify context."""
        mgr = _make_manager(escalation_level=0, total_warnings=0, tool_warnings={})
        initial_messages = mgr._context.get_messages()[:]
        mgr.handle_loop_escalation()

        assert mgr._context.get_messages() == initial_messages


class TestConstructor:
    """Test LoopEscalationManager constructor."""

    def test_constructor_requires_three_args(self):
        """Constructor takes detector, context, agent — no ReflectionScheduler."""
        detector = LoopDetector(repeat_threshold=1)
        context = TauContext()
        agent = MagicMock()
        mgr = LoopEscalationManager(detector, context, agent)

        assert mgr._loop_detector is detector
        assert mgr._context is context
        assert mgr._agent is agent

    def test_constructor_has_no_reflection_scheduler(self):
        """Constructor does not accept or store a ReflectionScheduler."""
        detector = LoopDetector(repeat_threshold=1)
        context = TauContext()
        agent = MagicMock()
        mgr = LoopEscalationManager(detector, context, agent)

        assert not hasattr(mgr, '_reflection_scheduler')


class TestReturnType:
    """Test that handle_loop_escalation always returns a tuple."""

    @patch("agent_loop_escalation.loop_warning")
    def test_always_returns_tuple(self, mock_warn):
        """handle_loop_escalation always returns tuple[str | None, bool]."""
        for level, warnings in [(0, 0), (1, 3), (2, 6), (3, 9), (4, 12), (5, 15)]:
            mgr = _make_manager(escalation_level=level, total_warnings=warnings, tool_warnings={"bash": 1})
            result = mgr.handle_loop_escalation()

            assert isinstance(result, tuple), f"Level {level}: expected tuple, got {type(result)}"
            assert len(result) == 2, f"Level {level}: expected 2-element tuple"
            text, force_end = result
            assert isinstance(force_end, bool), f"Level {level}: force_end should be bool"
            assert text is None or isinstance(text, str), f"Level {level}: text should be None or str"
