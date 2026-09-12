"""Regression tests for the unconditional 85% compression gate.

Design: validate_and_compress() compresses whenever the LIVE estimate
(prompt + reserved output) is >= 85% of the window. No cooldown, no 95% gate.
The compressor receives the RESERVE-FREE prompt count (target math subtracts
the output budget itself), so the reserve is not double-counted.
"""
from unittest.mock import MagicMock, patch


def _make_agent(prompt_tokens, max_tokens=100000, max_out=20000, ctx_len=10):
    """Agent whose LIVE token count (exact-anchored helper) equals prompt_tokens.

    No exact anchor is set, so the helper falls back to a full estimate of the
    message list; the list is sized so estimate_messages_tokens(msgs) ==
    prompt_tokens exactly (each "a"*255 msg estimates 85+15=100 tokens).
    """
    agent = MagicMock()
    agent.max_context_tokens = max_tokens
    agent.max_tokens = max_out
    agent.context.estimate_tokens.return_value = prompt_tokens
    agent.context.__len__ = MagicMock(return_value=ctx_len)
    agent.context.get_messages.return_value = [{"role": "user", "content": "a" * 255}] * (prompt_tokens // 100)
    # No anchor -> helper falls back to full estimate (prior behavior).
    agent._session.last_exact_context_tokens = None
    agent._session.last_exact_msg_count = None
    agent._session.last_turn_output_tokens = 0
    return agent


@patch("agent_console.echo")
@patch("agent_context_compress.compress_to_target")
def test_fires_at_85pct_live(mock_compress, mock_echo):
    # live = prompt(65000) + reserve(20000) = 85000 / 100000 = 85% -> fires
    agent = _make_agent(65000)
    from agent_pipeline import validate_and_compress
    validate_and_compress(agent, turn_count=0)
    mock_compress.assert_called_once()


@patch("agent_console.echo")
@patch("agent_context_compress.compress_to_target")
def test_does_not_fire_below_85pct(mock_compress, mock_echo):
    agent = _make_agent(64000)  # live 84000 / 100000 = 84%
    from agent_pipeline import validate_and_compress
    validate_and_compress(agent, turn_count=0)
    mock_compress.assert_not_called()


@patch("agent_console.echo")
@patch("agent_context_compress.compress_to_target")
def test_ignores_stale_exact_tokens(mock_compress, mock_echo):
    # No anchor set -> helper falls back to full estimate (low) -> must NOT fire.
    agent = _make_agent(10000)  # live 30000 / 100000 = 30%
    from agent_pipeline import validate_and_compress
    validate_and_compress(agent, turn_count=0)
    mock_compress.assert_not_called()


@patch("agent_console.echo")
@patch("agent_context_compress.compress_to_target")
def test_compressor_gets_reserve_free_count(mock_compress, mock_echo):
    # Double-count guard: current_tokens passed to compress_to_target must be
    # the reserve-free prompt count, NOT prompt+reserve.
    agent = _make_agent(65000, max_out=20000)
    from agent_pipeline import validate_and_compress
    validate_and_compress(agent, turn_count=0)
    _, kwargs = mock_compress.call_args
    assert kwargs["current_tokens"] == 65000, kwargs


@patch("agent_console.echo")
@patch("agent_context_compress.compress_to_target")
def test_no_progress_warns(mock_compress, mock_echo):
    agent = _make_agent(90000, ctx_len=10)  # live 110000 -> fires
    mock_compress.side_effect = lambda *a, **k: None  # no-op
    from agent_pipeline import validate_and_compress
    validate_and_compress(agent, turn_count=3)
    text = " ".join(str(c) for c in mock_echo.call_args_list)
    assert "No progress" in text
