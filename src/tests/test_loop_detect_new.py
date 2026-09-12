"""Tests for agent_loop_detect.py — threshold-based escalation logic."""
import pytest
from agent_loop_detect import LoopDetector


class TestDefaultThresholds:
    """Test escalation levels with default thresholds (3, 7, 11, 15)."""

    def test_no_escalation_below_warn_threshold(self):
        d = LoopDetector()
        d.total_warnings = 2
        d._update_escalation_level()
        assert d.escalation_level == 0

    def test_level1_at_warn_threshold(self):
        d = LoopDetector()
        d.total_warnings = 3
        d._update_escalation_level()
        assert d.escalation_level == 1

    def test_level2_at_inject_threshold(self):
        d = LoopDetector()
        d.total_warnings = 7
        d._update_escalation_level()
        assert d.escalation_level == 2

    def test_level3_at_force_think_threshold(self):
        d = LoopDetector()
        d.total_warnings = 11
        d._update_escalation_level()
        assert d.escalation_level == 3

    def test_level4_at_end_turn_threshold(self):
        d = LoopDetector()
        d.total_warnings = 15
        d._update_escalation_level()
        assert d.escalation_level == 4

    def test_level5_at_end_turn_plus_3(self):
        d = LoopDetector()
        d.total_warnings = 18
        d._update_escalation_level()
        assert d.escalation_level == 5


class TestCustomThresholds:
    """Test escalation with custom thresholds."""

    def test_custom_thresholds(self):
        d = LoopDetector(warn_threshold=5, inject_threshold=10,
                        force_think_threshold=15, end_turn_threshold=20)
        d.total_warnings = 5
        d._update_escalation_level()
        assert d.escalation_level == 1

        d.total_warnings = 10
        d._update_escalation_level()
        assert d.escalation_level == 2

        d.total_warnings = 15
        d._update_escalation_level()
        assert d.escalation_level == 3

        d.total_warnings = 20
        d._update_escalation_level()
        assert d.escalation_level == 4

        d.total_warnings = 23
        d._update_escalation_level()
        assert d.escalation_level == 5


class TestLevelMonotonic:
    """Escalation level only increases, never decreases."""

    def test_level_does_not_decrease(self):
        d = LoopDetector()
        d.total_warnings = 10
        d._update_escalation_level()
        assert d.escalation_level == 2

        # Lower the warnings (simulating reset of count but not level)
        d.total_warnings = 2
        d._update_escalation_level()
        assert d.escalation_level == 2  # stays at 2


class TestReset:
    """reset() clears state."""

    def test_reset_clears_warnings(self):
        d = LoopDetector()
        d.total_warnings = 10
        d.escalation_level = 3
        d.reset()
        assert d.total_warnings == 0
        assert d.escalation_level == 0

    def test_reset_preserves_code_history(self):
        d = LoopDetector()
        d.code_history.append("hash1")
        d.code_history.append("hash2")
        d.reset()
        assert len(d.code_history) == 2  # preserved


class TestRecordCodeExecution:
    """record_code_execution() increments warnings on repeat."""

    def test_no_warning_on_first_execution(self):
        d = LoopDetector()
        result = d.record_code_execution("print(1)")
        assert result is None
        assert d.total_warnings == 0

    def test_warning_on_consecutive_repeats(self):
        d = LoopDetector(repeat_threshold=3)
        d.record_code_execution("print(1)")
        d.record_code_execution("print(1)")
        result = d.record_code_execution("print(1)")
        assert result is not None
        assert d.total_warnings >= 1

    def test_different_code_resets_repeat(self):
        d = LoopDetector(repeat_threshold=3)
        d.record_code_execution("print(1)")
        d.record_code_execution("print(1)")
        d.record_code_execution("print(2)")  # different
        d.record_code_execution("print(2)")
        # Only 2 repeats of print(2), below threshold
        assert d.total_warnings == 0


class TestAnswerStagnation:
    """check_answer_stagnation() detects repeated answers."""

    def test_no_stagnation_on_first_call(self):
        d = LoopDetector()
        result = d.check_answer_stagnation("hello")
        assert result is None

    def test_stagnation_after_threshold(self):
        d = LoopDetector(answer_stagnation_threshold=3)
        d.check_answer_stagnation("same")  # sets baseline
        d.check_answer_stagnation("same")  # stagnant_turns=1
        d.check_answer_stagnation("same")  # stagnant_turns=2
        result = d.check_answer_stagnation("same")  # stagnant_turns=3 >= threshold
        assert result is not None

    def test_no_stagnation_on_different_answer(self):
        d = LoopDetector(answer_stagnation_threshold=3)
        d.check_answer_stagnation("a")
        d.check_answer_stagnation("b")
        d.check_answer_stagnation("c")
        assert d._answer_stagnant_turns == 0
