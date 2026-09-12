"""Tests for agent_loop_detect module (code-execution loop detection).

NOTE: The old tool-call API (detect_tool_loop / _tool_call_key / tool_call_history)
was removed in Phase 7 (cebdc3a); the detector now tracks CODE executions via
record_code_execution(). These tests were rewritten to assert the CURRENT contract.
"""

from agent_loop_detect import LoopDetector


class TestLoopDetector:
    """Test LoopDetector class (code-execution based)."""

    def test_no_loop_on_diverse_calls(self):
        """Diverse code executions must not raise consecutive repeats."""
        detector = LoopDetector()
        for i in range(3):
            detector.record_code_execution(f"print({i})")
        assert detector.consecutive_repeats == 1

    def test_repeat_loop_detection(self):
        """Identical code repeated to repeat_threshold triggers a warning."""
        detector = LoopDetector(repeat_threshold=3)
        assert detector.record_code_execution("print(1)") is None
        assert detector.record_code_execution("print(1)") is None
        assert detector.record_code_execution("print(1)") is not None

    def test_entropy_calculation(self):
        """Diverse executions produce high entropy."""
        detector = LoopDetector(window_size=10)
        for i in range(10):
            detector.record_code_execution(f"print({i})")
        stats = detector.get_stats()
        assert stats["entropy"] > 1.0  # Diverse = high entropy

    def test_reset(self):
        """reset() clears repeat/warning state (history preserved, L-S3)."""
        detector = LoopDetector()
        detector.record_code_execution("print(1)")
        detector.reset()
        assert detector.consecutive_repeats == 0
        assert detector.total_warnings == 0

    def test_entropy_low_with_repeated_calls(self):
        """Entropy drops to 0 when only one unique call exists."""
        detector = LoopDetector(window_size=10)
        for _ in range(15):
            detector.record_code_execution("print(1)")
        stats = detector.get_stats()
        assert stats["entropy"] == 0.0

    def test_ab_ab_pattern(self):
        """An A-B alternating pattern retains entropy > 0."""
        detector = LoopDetector(window_size=10)
        for i in range(10):
            if i % 2 == 0:
                detector.record_code_execution(f"print({i})")
            else:
                detector.record_code_execution(f"print(-{i})")
        stats = detector.get_stats()
        assert stats["entropy"] > 0

    def test_get_stats(self):
        """get_stats() exposes the escalation/entropy contract."""
        detector = LoopDetector(window_size=30)
        for i in range(5):
            detector.record_code_execution(f"print({i})")
        stats = detector.get_stats()
        assert "entropy" in stats
        assert "consecutive_repeats" in stats
        assert "escalation_level" in stats
        assert stats["consecutive_repeats"] == 1
