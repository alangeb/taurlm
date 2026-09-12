"""Test rlm/spawn.py — auto-compression threshold and _maybe_compress.

Regression tests for the fix where AUTO_COMPRESS_THRESHOLD was changed from
70 (percent) to 0.70 (fraction) and the compress_to_target call was fixed to
use the correct signature (AUTO_COMPRESS_TARGET, agent).
"""

from unittest.mock import MagicMock, patch

from rlm.spawn import (
    AUTO_COMPRESS_TARGET,
    AUTO_COMPRESS_THRESHOLD,
    SpawnHandle,
)


def _make_handle() -> SpawnHandle:
    return SpawnHandle(spawn_id="test-compress", name="compress-test")


def _make_agent(percentage: float) -> MagicMock:
    """Build a mock agent whose LIVE token count == percentage * max_context_tokens.

    P1: _maybe_compress now routes through compute_live_tokens (reads
    context.get_messages() + the session exact anchor), not get_usage_stats.
    We anchor exact to a full-length list (anchor==len => zero post-anchor
    delta), so live == last_exact_context_tokens == percentage * max.
    """
    agent = MagicMock()
    agent.max_context_tokens = 180000
    exact = round(percentage * 180000)
    agent.context.get_messages.return_value = [{"role": "user", "content": "x"}] * 5
    agent._session.last_exact_context_tokens = exact
    agent._session.last_exact_msg_count = 5
    agent._session.last_turn_output_tokens = 0
    return agent


class TestAutoCompressThreshold:
    """Guard against regression of the threshold constant."""

    def test_threshold_is_fraction(self):
        """AUTO_COMPRESS_THRESHOLD must be a 0.0-1.0 fraction (was 70 pre-fix)."""
        assert AUTO_COMPRESS_THRESHOLD == 0.70
        assert 0.0 < AUTO_COMPRESS_THRESHOLD < 1.0

    def test_target_is_percent(self):
        """AUTO_COMPRESS_TARGET is the post-compression target percentage (0-100)."""
        assert AUTO_COMPRESS_TARGET == 28
        assert 0 < AUTO_COMPRESS_TARGET < 100


class TestMaybeCompress:
    """Test _maybe_compress trigger logic and call signature."""

    def test_compresses_above_threshold(self):
        """Context usage above 0.70 triggers compress_to_target."""
        handle = _make_handle()
        agent = _make_agent(0.75)
        handle._agent = agent
        with patch("agent_context_compress.compress_to_target") as mock_c2t:
            handle._maybe_compress()
        mock_c2t.assert_called_once()

    def test_compresses_at_threshold(self):
        """Context usage exactly at 0.70 triggers compress_to_target (>=)."""
        handle = _make_handle()
        agent = _make_agent(0.70)
        handle._agent = agent
        with patch("agent_context_compress.compress_to_target") as mock_c2t:
            handle._maybe_compress()
        mock_c2t.assert_called_once()

    def test_no_compress_below_threshold(self):
        """Context usage below 0.70 does NOT trigger compress_to_target."""
        handle = _make_handle()
        agent = _make_agent(0.50)
        handle._agent = agent
        with patch("agent_context_compress.compress_to_target") as mock_c2t:
            handle._maybe_compress()
        mock_c2t.assert_not_called()

    def test_no_compress_well_below_threshold(self):
        """Context usage far below 0.70 does NOT trigger compress_to_target."""
        handle = _make_handle()
        agent = _make_agent(0.10)
        handle._agent = agent
        with patch("agent_context_compress.compress_to_target") as mock_c2t:
            handle._maybe_compress()
        mock_c2t.assert_not_called()

    def test_compress_call_signature(self):
        """compress_to_target is called with (context, agent, AUTO_COMPRESS_TARGET)."""
        handle = _make_handle()
        agent = _make_agent(0.80)
        handle._agent = agent
        with patch("agent_context_compress.compress_to_target") as mock_c2t:
            handle._maybe_compress()
        mock_c2t.assert_called_once_with(
            agent.context, agent, AUTO_COMPRESS_TARGET,
            current_tokens=round(0.80 * 180000),
        )

    def test_live_helper_reads_messages(self):
        """_maybe_compress reads the message list via get_messages (live helper)."""
        handle = _make_handle()
        agent = _make_agent(0.50)
        handle._agent = agent
        handle._maybe_compress()
        agent.context.get_messages.assert_called()

    def test_exception_is_swallowed(self):
        """A failure inside the compression check must not propagate (logged only)."""
        handle = _make_handle()
        agent = MagicMock()
        agent.max_context_tokens = 180000
        agent.context.get_messages.side_effect = RuntimeError("boom")
        handle._agent = agent
        # Must not raise
        handle._maybe_compress()

    def test_no_agent_no_crash(self):
        """_maybe_compress with _agent=None does nothing."""
        handle = _make_handle()
        handle._agent = None
        handle._maybe_compress()  # should not raise
