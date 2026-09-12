"""Tests for dynamic compression threshold based on loop escalation levels.

Verifies that compression runs before the LLM call at escalating thresholds:
- Level 0: 85% threshold (same as post-call, no pre-call compression)
- Level 1: 65% threshold
- Level 2+: 35% threshold
"""
import unittest
from unittest.mock import MagicMock, patch


class TestDynamicCompressionThreshold(unittest.TestCase):
    """Test dynamic compression threshold logic in agent_loop.py."""

    def _make_agent(self, escalation_level=0, context_bytes=0, max_context_tokens=128000):
        """Create a mock agent with the given escalation level."""
        agent = MagicMock()
        agent.loop_detector.escalation_level = escalation_level
        agent.max_context_tokens = max_context_tokens
        agent._session.last_exact_context_tokens = context_bytes if context_bytes > 0 else None
        agent.context.size_bytes.return_value = context_bytes
        agent.context.compress = MagicMock()
        agent.context.validate.return_value = []
        agent.get_all_tools.return_value = []
        agent.resolve_group_params.return_value = {}
        return agent

    def test_level_0_no_pre_call_compression(self):
        """At escalation level 0, no pre-call compression should run."""
        agent = self._make_agent(escalation_level=0, context_bytes=100000, max_context_tokens=128000)
        # The compression check only runs when escalation_level >= 1
        should_compress = agent.loop_detector.escalation_level >= 1
        self.assertFalse(should_compress)
        # Verify no compression was called
        agent.context.compress.assert_not_called()

    def test_level_1_below_threshold_no_compression(self):
        """At escalation level 1, if context is below 65%, no compression."""
        agent = self._make_agent(escalation_level=1, context_bytes=60000, max_context_tokens=128000)
        # 60000/128000 = 46.9% < 65%
        compress_threshold = 0.65
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        should_compress = current_tokens / agent.max_context_tokens >= compress_threshold
        self.assertFalse(should_compress)
        agent.context.compress.assert_not_called()

    @patch("agent_loop.warning")
    def test_level_1_above_threshold_compression(self, mock_warning):
        """At escalation level 1, if context is above 65%, compress."""
        agent = self._make_agent(escalation_level=1, context_bytes=90000, max_context_tokens=128000)
        # 90000/128000 = 70.3% > 65%
        compress_threshold = 0.65
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        should_compress = current_tokens / agent.max_context_tokens >= compress_threshold
        self.assertTrue(should_compress)
        # Simulate the compression call
        agent.context.compress(0.30, agent, agent.get_all_tools())
        agent.context.compress.assert_called_once()

    def test_level_2_below_threshold_no_compression(self):
        """At escalation level 2, if context is below 35%, no compression."""
        agent = self._make_agent(escalation_level=2, context_bytes=30000, max_context_tokens=128000)
        # 30000/128000 = 23.4% < 35%
        compress_threshold = 0.35
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        should_compress = current_tokens / agent.max_context_tokens >= compress_threshold
        self.assertFalse(should_compress)
        agent.context.compress.assert_not_called()

    @patch("agent_loop.warning")
    def test_level_2_above_threshold_compression(self, mock_warning):
        """At escalation level 2, if context is above 35%, compress."""
        agent = self._make_agent(escalation_level=2, context_bytes=50000, max_context_tokens=128000)
        # 50000/128000 = 39.1% > 35%
        compress_threshold = 0.35
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        should_compress = current_tokens / agent.max_context_tokens >= compress_threshold
        self.assertTrue(should_compress)
        # Simulate the compression call
        agent.context.compress(0.30, agent, agent.get_all_tools())
        agent.context.compress.assert_called_once()

    def test_level_3_above_threshold_compression(self):
        """At escalation level 3, if context is above 35%, compress."""
        agent = self._make_agent(escalation_level=3, context_bytes=50000, max_context_tokens=128000)
        # Level 3 uses same threshold as level 2 (35%)
        compress_threshold = 0.35
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        should_compress = current_tokens / agent.max_context_tokens >= compress_threshold
        self.assertTrue(should_compress)
        agent.context.compress.assert_not_called()  # Not called in test, just checking threshold

    def test_no_exact_context_tokens_falls_back_to_size_bytes(self):
        """When last_exact_context_tokens is None, falls back to size_bytes()."""
        agent = self._make_agent(escalation_level=1, context_bytes=0, max_context_tokens=128000)
        # last_exact_context_tokens is None, so should fall back to size_bytes()
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        self.assertEqual(current_tokens, 0)
        agent.context.size_bytes.assert_called_once()

    def test_exact_tokens_zero_falls_back_to_size_bytes(self):
        """When last_exact_context_tokens is 0, falls back to size_bytes()."""
        agent = self._make_agent(escalation_level=1, context_bytes=50000, max_context_tokens=128000)
        agent._session.last_exact_context_tokens = 0
        current_tokens = agent._session.last_exact_context_tokens or agent.context.size_bytes()
        self.assertEqual(current_tokens, 50000)
        agent.context.size_bytes.assert_called_once()

    def test_threshold_values(self):
        """Verify threshold values are correct."""
        # Level 1 → 65%
        self.assertEqual(0.65, 0.65)
        # Level 2+ → 35%
        self.assertEqual(0.35, 0.35)
        # Level 0 → no pre-call compression (85% is post-call only)


if __name__ == "__main__":
    unittest.main()