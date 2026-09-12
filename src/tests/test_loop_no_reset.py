"""Tests for loop detection monotonic warning accumulation (no reset while loop persists).

NOTE: The tool-call API (detect_tool_loop, system_call flag, tool_call_history) was
removed in Phase 7 (cebdc3a); the detector now tracks CODE executions via
record_code_execution(). The system_call-skip feature has no replacement and its tests
were dropped; the surviving monotonic-accumulation contract is asserted here on the
current API.
"""

import pytest
from agent_loop_detect import LoopDetector


class TestMonotonicAccumulation:
    """Warnings accumulate monotonically without reset while the loop persists."""

    def test_warnings_accumulate_past_level_2(self):
        detector = LoopDetector(repeat_threshold=1)
        for _ in range(10):
            detector.record_code_execution("print(1)")
        assert detector.total_warnings >= 7
        assert detector.escalation_level >= 2

    def test_level_3_reachable(self):
        detector = LoopDetector(repeat_threshold=1)
        for _ in range(15):
            detector.record_code_execution("print(1)")
        assert detector.escalation_level >= 3

    def test_level_4_reachable(self):
        detector = LoopDetector(repeat_threshold=1)
        for _ in range(20):
            detector.record_code_execution("print(1)")
        assert detector.escalation_level >= 4

    def test_warnings_do_not_reset_on_non_repeat_call(self):
        """A different code block must NOT reset accumulated warnings (monotonic)."""
        detector = LoopDetector(repeat_threshold=2)
        for _ in range(4):
            detector.record_code_execution("print(1)")
        assert detector.total_warnings >= 3
        assert detector.escalation_level >= 1
        before = detector.total_warnings
        detector.record_code_execution("print(999)")  # non-repeat breaks the pattern
        assert detector.total_warnings == before  # monotonic: not reset
        assert detector.escalation_level >= 1

    def test_entropy_accumulates_with_repeated_history(self):
        """Repeated identical code drives entropy to 0 and raises entropy warnings."""
        detector = LoopDetector(repeat_threshold=15, sustained_threshold=0.5, window_size=10)
        for _ in range(12):
            detector.record_code_execution("print(1)")
        # repeat threshold (15) not reached, but entropy is 0 -> entropy warnings fire
        assert detector.entropy_warnings > 0
        assert detector._calculate_entropy() == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
