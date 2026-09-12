"""Comprehensive unit tests for the streaming LLM migration.

Covers:
- SSE parsing in SimpleOpenAIClient.chat_completions_create_stream
- Delta accumulation in _invoke_llm_streaming
- StreamAbortChecker logic
- _StreamDisplay rendering
- Streaming integration / retry behavior
"""

from __future__ import annotations

import io
import json
import sys
import time
from typing import Any, Iterator
from unittest.mock import MagicMock, Mock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sse_response(lines: list[bytes]) -> MagicMock:
    """Create a mock HTTP response that iterates over raw SSE lines (bytes)."""
    resp = MagicMock()
    resp.__enter__ = Mock(return_value=resp)
    resp.__exit__ = Mock(return_value=False)
    resp.__iter__ = Mock(return_value=iter(lines))
    resp.headers = {'Content-Type': 'text/event-stream'}
    return resp


def _sse_line(data: str) -> bytes:
    """Encode an SSE data line."""
    return f"data: {data}\n".encode("utf-8")


def _make_chunk(content: str | None = None, reasoning: str | None = None,
                finish_reason: str | None = None, usage: dict | None = None) -> dict:
    """Build a streaming chunk dict."""
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    chunk: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def _make_config(**overrides) -> MagicMock:
    """Build a minimal LLMCallConfig-like mock."""
    from agent_llm_models import LLMCallConfig
    cfg = LLMCallConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# 1. TestSSEParser — SSE parsing in chat_completions_create_stream
# ---------------------------------------------------------------------------


class TestSSEParser:
    """Tests for SimpleOpenAIClient.chat_completions_create_stream SSE parsing."""

    def _make_client(self) -> MagicMock:
        from agent_llm_client import SimpleOpenAIClient
        client = SimpleOpenAIClient.__new__(SimpleOpenAIClient)
        client.base_url = "http://localhost:9999/v1"
        client.api_key = "test-key"
        client.timeout = 30
        client._cache_tracker = None
        return client

    def _run_stream(self, client, sse_lines: list[bytes]) -> list[dict]:
        """Run chat_completions_create_stream with mocked urlopen."""
        mock_resp = _make_sse_response(sse_lines)
        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch.object(client, "_report_cache_hit", Mock()):
            chunks = list(client.chat_completions_create_stream(model="test", messages=[]))
        return chunks

    def test_normal_data_lines(self):
        """Normal 'data: ' lines with JSON are parsed and yielded."""
        client = self._make_client()
        c1 = _make_chunk(content="Hello")
        c2 = _make_chunk(content=" world", finish_reason="stop")
        lines = [_sse_line(json.dumps(c1)), _sse_line(json.dumps(c2)), b"data: [DONE]\n"]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 2
        assert chunks[0]["choices"][0]["delta"]["content"] == "Hello"
        assert chunks[1]["choices"][0]["finish_reason"] == "stop"

    def test_data_without_space(self):
        """'data:' without a space is also parsed."""
        client = self._make_client()
        c1 = _make_chunk(content="no-space")
        lines = [f"data:{json.dumps(c1)}\n".encode(), b"data: [DONE]\n"]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["delta"]["content"] == "no-space"

    def test_done_sentinel_stops_iteration(self):
        """[DONE] sentinel stops the generator."""
        client = self._make_client()
        c1 = _make_chunk(content="a")
        c2 = _make_chunk(content="b")
        lines = [
            _sse_line(json.dumps(c1)),
            _sse_line("[DONE]"),
            _sse_line(json.dumps(c2)),  # should NOT be yielded
        ]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["delta"]["content"] == "a"

    def test_comment_lines_skipped(self):
        """Lines starting with ':' (SSE comments/keep-alives) are skipped."""
        client = self._make_client()
        c1 = _make_chunk(content="ok")
        lines = [
            b": keep-alive\n",
            b":\n",
            _sse_line(json.dumps(c1)),
            b"data: [DONE]\n",
        ]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1

    def test_empty_lines_skipped(self):
        """Empty lines are skipped."""
        client = self._make_client()
        c1 = _make_chunk(content="x")
        lines = [b"\n", b"\n", _sse_line(json.dumps(c1)), b"data: [DONE]\n"]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1

    def test_crlf_line_endings(self):
        """CRLF line endings are handled correctly."""
        client = self._make_client()
        c1 = _make_chunk(content="crlf")
        lines = [f"data: {json.dumps(c1)}\r\n".encode(), b"data: [DONE]\r\n"]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["delta"]["content"] == "crlf"

    def test_event_id_retry_lines_skipped(self):
        """event:, id:, retry: SSE metadata lines are skipped."""
        client = self._make_client()
        c1 = _make_chunk(content="meta")
        lines = [
            b"event: message\n",
            b"id: 42\n",
            b"retry: 3000\n",
            _sse_line(json.dumps(c1)),
            b"data: [DONE]\n",
        ]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1

    def test_usage_accumulated_from_chunks(self):
        """Usage data from any chunk is captured (final chunk typically)."""
        client = self._make_client()
        c1 = _make_chunk(content="Hi")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        c2 = _make_chunk(finish_reason="stop", usage=usage)
        lines = [_sse_line(json.dumps(c1)), _sse_line(json.dumps(c2)), b"data: [DONE]\n"]
        chunks = self._run_stream(client, lines)
        # Both chunks are yielded; usage is in the second
        assert "usage" in chunks[1]
        assert chunks[1]["usage"]["prompt_tokens"] == 10

    def test_mid_stream_connection_reset(self):
        """ConnectionResetError mid-stream propagates."""
        client = self._make_client()

        def _iter_lines():
            yield _sse_line(json.dumps(_make_chunk(content="partial")))
            raise ConnectionResetError("Connection reset by peer")

        mock_resp = MagicMock()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)
        mock_resp.__iter__ = Mock(return_value=_iter_lines())
        mock_resp.headers = {'Content-Type': 'text/event-stream'}

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch.object(client, "_report_cache_hit", Mock()):
            with pytest.raises(ConnectionResetError):
                list(client.chat_completions_create_stream(model="test", messages=[]))

    def test_invalid_json_skipped(self):
        """Malformed JSON in data lines is silently skipped."""
        client = self._make_client()
        c1 = _make_chunk(content="valid")
        lines = [
            b"data: not-json\n",
            _sse_line(json.dumps(c1)),
            b"data: [DONE]\n",
        ]
        chunks = self._run_stream(client, lines)
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# 2. TestDeltaAccumulation — _invoke_llm_streaming
# ---------------------------------------------------------------------------


class TestDeltaAccumulation:
    """Tests for _invoke_llm_streaming delta accumulation and stats."""

    def _run_streaming(self, chunks: list[dict], **cfg_overrides) -> Any:
        from agent_llm_invoke import _invoke_llm_streaming
        from agent_llm_models import LLMCallConfig

        config = LLMCallConfig()
        for k, v in cfg_overrides.items():
            setattr(config, k, v)

        client = MagicMock()
        client.chat.completions.create_stream.return_value = iter(chunks)

        tokens_displayed = [0]
        return _invoke_llm_streaming(
            client, "test-model", [{"role": "user", "content": "hi"}],
            config, tokens_displayed,
        )

    def test_content_deltas_accumulated(self):
        chunks = [
            _make_chunk(content="Hello"),
            _make_chunk(content=" world"),
            _make_chunk(content="!", finish_reason="stop"),
        ]
        resp = self._run_streaming(chunks)
        assert resp.text == "Hello world!"

    def test_reasoning_deltas_accumulated(self):
        chunks = [
            _make_chunk(reasoning="Let me"),
            _make_chunk(reasoning=" think..."),
            _make_chunk(content="42", finish_reason="stop"),
        ]
        resp = self._run_streaming(chunks)
        assert resp.reasoning == "Let me think..."
        assert resp.text == "42"

    def test_reasoning_content_field_llama_style(self):
        """llama.cpp uses 'reasoning_content' key in delta."""
        chunks = [
            _make_chunk(reasoning="thinking"),
            _make_chunk(content="answer", finish_reason="stop"),
        ]
        resp = self._run_streaming(chunks)
        assert resp.reasoning == "thinking"

    def test_finish_reason_captured(self):
        chunks = [_make_chunk(content="done", finish_reason="length")]
        resp = self._run_streaming(chunks)
        assert resp.stats.finish_reason == "length"

    def test_usage_data_extracted(self):
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        chunks = [
            _make_chunk(content="text"),
            _make_chunk(finish_reason="stop", usage=usage),
        ]
        resp = self._run_streaming(chunks)
        assert resp.stats.prompt_tokens == 100
        assert resp.stats.completion_tokens == 50

    def test_on_token_callback(self):
        calls: list[str] = []
        chunks = [
            _make_chunk(content="A"),
            _make_chunk(content="B"),
            _make_chunk(content="C", finish_reason="stop"),
        ]
        self._run_streaming(chunks, on_token=lambda t: calls.append(t))
        assert calls == ["A", "B", "C"]

    def test_on_reasoning_callback(self):
        calls: list[str] = []
        chunks = [
            _make_chunk(reasoning="R1"),
            _make_chunk(reasoning="R2"),
            _make_chunk(content="x", finish_reason="stop"),
        ]
        self._run_streaming(chunks, on_reasoning=lambda t: calls.append(t))
        assert calls == ["R1", "R2"]

    def test_call_stats_stream_fields(self):
        chunks = [
            _make_chunk(content="word"),
            _make_chunk(content=" word", finish_reason="stop"),
        ]
        resp = self._run_streaming(chunks)
        # stream_duration should be > 0 (tiny but positive)
        assert resp.stats.stream_duration >= 0
        # ttft should be >= 0
        assert resp.stats.ttft >= 0
        # tokens_generated should be >= 2 (2 content tokens)
        assert resp.stats.tokens_generated >= 2
        # tg_tps should be >= 0
        assert resp.stats.tg_tps >= 0

    def test_llm_response_fields(self):
        chunks = [
            _make_chunk(reasoning="think"),
            _make_chunk(content="result", finish_reason="stop"),
        ]
        resp = self._run_streaming(chunks)
        assert resp.text == "result"
        assert resp.reasoning == "think"
        assert resp.success is True
        assert resp.error is None

    def test_no_reasoning_gives_none(self):
        chunks = [_make_chunk(content="just content", finish_reason="stop")]
        resp = self._run_streaming(chunks)
        assert resp.reasoning is None

    def test_tokens_displayed_updated(self):
        chunks = [
            _make_chunk(content="a"),
            _make_chunk(content="b"),
            _make_chunk(content="c", finish_reason="stop"),
        ]
        from agent_llm_invoke import _invoke_llm_streaming
        from agent_llm_models import LLMCallConfig
        config = LLMCallConfig()
        client = MagicMock()
        client.chat.completions.create_stream.return_value = iter(chunks)
        tokens_displayed = [0]
        _invoke_llm_streaming(client, "m", [], config, tokens_displayed)
        assert tokens_displayed[0] == 3


# ---------------------------------------------------------------------------
# 3. TestStreamAbortChecker
# ---------------------------------------------------------------------------


class TestStreamAbortChecker:
    """Tests for StreamAbortChecker."""

    def test_normal_tokens_no_abort(self):
        from agent_loop_detect import StreamAbortChecker
        checker = StreamAbortChecker(max_tokens=100)
        for _ in range(10):
            checker.feed("hello")
        assert not checker.should_abort

    def test_token_count_exceeds_max(self):
        from agent_loop_detect import StreamAbortChecker
        # Each feed adds len(token.split()) + 1 tokens
        # "a b c" -> 3 + 1 = 4 tokens per feed
        checker = StreamAbortChecker(max_tokens=5)
        checker.feed("a b c")  # 4 tokens
        assert not checker.should_abort
        checker.feed("d")  # 1 + 1 = 2 tokens -> total 6 > 5
        assert checker.should_abort
        assert "exceeded" in checker.reason

    def test_max_tokens_zero_only_patterns(self):
        """max_tokens=0 means no token count check."""
        from agent_loop_detect import StreamAbortChecker
        checker = StreamAbortChecker(max_tokens=0)
        for _ in range(100):
            checker.feed("word")
        assert not checker.should_abort

    def test_max_tokens_none_no_count_check(self):
        """max_tokens=None means no token count check."""
        from agent_loop_detect import StreamAbortChecker
        checker = StreamAbortChecker(max_tokens=None)
        for _ in range(200):
            checker.feed("word")
        assert not checker.should_abort

    def test_token_count_property(self):
        from agent_loop_detect import StreamAbortChecker
        checker = StreamAbortChecker()
        checker.feed("hello world")  # 2 words + 1 = 3
        assert checker.token_count == 3

    def test_already_aborted_ignores_feed(self):
        from agent_loop_detect import StreamAbortChecker
        checker = StreamAbortChecker(max_tokens=2)
        checker.feed("a b")  # 3 tokens > 2, aborts
        assert checker.should_abort
        count_before = checker.token_count
        checker.feed("c d")  # should be ignored
        assert checker.token_count == count_before

    def test_reason_is_none_when_not_aborted(self):
        from agent_loop_detect import StreamAbortChecker
        checker = StreamAbortChecker(max_tokens=100)
        checker.feed("ok")
        assert checker.reason is None


# ---------------------------------------------------------------------------
# 4. TestStreamDisplay
# ---------------------------------------------------------------------------


class TestStreamDisplay:
    """Tests for _StreamDisplay rendering."""

    def _make_display(self, hidden: bool = False) -> Any:
        from agent_pipeline import _StreamDisplay
        return _StreamDisplay(hidden=hidden)

    @staticmethod
    def _reset_latch() -> None:
        import agent_console.primitives as pr
        pr._set_at_line_start(True)

    def setup_method(self) -> None:
        # The newline latch is a module global; reset so tests are order-independent.
        self._reset_latch()

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_reasoning_in_reasoning_color(self, mock_stdout, mock_audit):
        from agent_models import Colors
        d = self._make_display()
        d.on_reasoning("thinking")
        d.finalize()
        output = mock_stdout.getvalue()
        assert Colors.REASONING in output
        assert "thinking" in output

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_content_in_green_color(self, mock_stdout, mock_audit):
        from agent_models import Colors
        d = self._make_display()
        d.on_token("hello")
        d.finalize()
        output = mock_stdout.getvalue()
        assert Colors.GREEN in output
        assert "hello" in output

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_reasoning_to_content_transition(self, mock_stdout, mock_audit):
        """Reasoning flushes immediately; content follows without newline."""
        from agent_models import Colors
        d = self._make_display()
        d.on_reasoning("think")
        # After on_reasoning, buffer is flushed immediately
        output_after_reasoning = mock_stdout.getvalue()
        assert "think" in output_after_reasoning
        assert Colors.REASONING in output_after_reasoning
        assert d._reasoning_buf == ""
        d.on_token("answer")
        # After on_token, content is flushed immediately
        output = mock_stdout.getvalue()
        reasoning_idx = output.find("think")
        content_idx = output.find("answer")
        assert reasoning_idx < content_idx
        # No newline between reasoning and content (immediate flush, no trailing \n)
        between = output[reasoning_idx + 5:content_idx]
        assert "\n" not in between
        assert Colors.GREEN in output
        assert d._content_buf == ""

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_line_buffering_partial_not_flushed(self, mock_stdout, mock_audit):
        """Partial lines are flushed immediately (no newline needed)."""
        from agent_models import Colors
        d = self._make_display()
        d.on_token("partial")
        # Buffer is flushed immediately — content_buf is empty
        assert d._content_buf == ""
        # stdout should contain the flushed content
        output = mock_stdout.getvalue()
        assert "partial" in output
        assert Colors.GREEN in output

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_finalize_flushes_remaining(self, mock_stdout, mock_audit):
        d = self._make_display()
        d.on_token("no newline here")
        d.finalize()
        output = mock_stdout.getvalue()
        assert "no newline here" in output

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_hidden_mode_no_output(self, mock_stdout, mock_audit):
        d = self._make_display(hidden=True)
        d.on_reasoning("secret")
        d.on_token("hidden content")
        d.finalize()
        assert mock_stdout.getvalue() == ""
        # But full content is still tracked
        assert d._full_reasoning == "secret"
        assert d._full_content == "hidden content"

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_audit_called_at_finalize(self, mock_stdout, mock_audit):
        """stream_audit is called once per phase at finalize, not per token."""
        d = self._make_display()
        d.on_token("a")
        d.on_token("b")
        d.on_token("c")
        # No audit calls yet
        assert mock_audit.call_count == 0
        d.finalize()
        # One call for content
        assert mock_audit.call_count == 1
        mock_audit.assert_called_once_with("abc")

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_empty_response_no_output(self, mock_stdout, mock_audit):
        d = self._make_display()
        d.finalize()
        assert mock_stdout.getvalue() == ""
        assert mock_audit.call_count == 0

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_full_line_flushed_immediately(self, mock_stdout, mock_audit):
        """Lines with newline are flushed immediately."""
        d = self._make_display()
        d.on_token("line1\n")
        output = mock_stdout.getvalue()
        assert "line1" in output


    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_finalize_closes_line_at_column_zero(self, mock_stdout, mock_audit):
        """A streamed message ending mid-line must be closed with exactly one newline."""
        d = self._make_display()
        d.on_token("ends mid-line")  # no trailing newline
        d.finalize()
        out = mock_stdout.getvalue()
        # The visible text ends with a newline (cursor at column 0 for the next block).
        import agent_console.primitives as pr
        assert pr._at_line_start is True
        assert out.endswith("\x1b[0m\n") or out.endswith("here\n") or out.rstrip().endswith("mid-line")
        # Exactly one trailing newline, not two.
        assert out.endswith("\n") and not out.endswith("\n\n")

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_finalize_no_extra_newline_when_already_at_col0(self, mock_stdout, mock_audit):
        """If streamed text already ends in \n, finalize adds no extra blank line."""
        d = self._make_display()
        d.on_token("ends with newline\n")
        d.finalize()
        out = mock_stdout.getvalue()
        import agent_console.primitives as pr
        assert pr._at_line_start is True
        # Only the one newline from the token; no doubled blank line.
        assert not out.endswith("\n\n")

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_reason_only_turn_closes_line(self, mock_stdout, mock_audit):
        """Reasoning-only turn (no content) still ends at column 0."""
        d = self._make_display()
        d.on_reasoning("only thinking")
        d.finalize()
        out = mock_stdout.getvalue()
        import agent_console.primitives as pr
        assert "only thinking" in out
        assert pr._at_line_start is True
        assert out.endswith("\n") and not out.endswith("\n\n")

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_repl_code_header_starts_on_new_line(self, mock_stdout, mock_audit):
        """End-to-end: streamed content ending mid-line, then [REPL CODE] header.

        Reproduces the reported bug where the [REPL CODE] banner landed on the
        same line as the assistant's last word. After the fix the header must
        start at column 0 (a single newline precedes it).
        """
        from agent_console import repl_code
        import agent_console.primitives as pr
        d = self._make_display()
        d.on_token("last word of content")  # ends mid-line, no newline
        d.finalize()
        repl_code("print('hi')")
        out = mock_stdout.getvalue()
        # Strip ANSI so we test cursor position, not color codes.
        plain = pr._ANSI_RE.sub("", out)
        header = "[REPL CODE]"
        assert header in plain
        hidx = plain.index(header)
        assert plain[hidx - 1] == "\n", "header not at column 0"
        # Exactly one newline between content and header (no blank line).
        between = plain[plain.index("last word of content") + len("last word of content"):hidx]
        assert between == "\n", repr(between)

    @patch("agent_console.primitives.stream_audit")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_empty_finalize_emits_nothing(self, mock_stdout, mock_audit):
        """An empty turn must not emit a stray newline."""
        d = self._make_display()
        d.finalize()
        assert mock_stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# 5. TestStreamingIntegration
# ---------------------------------------------------------------------------


class TestStreamingIntegration:
    """Integration tests for the streaming retry flow."""

    def _make_client_with_chunks(self, chunks: list[dict]) -> MagicMock:
        client = MagicMock()
        client.chat.completions.create_stream.return_value = iter(chunks)
        return client

    @patch("agent_llm_invoke._build_call_kwargs")
    @patch("agent_llm_invoke._extract_call_stats")
    def test_full_flow_sse_to_response(self, mock_stats, mock_kwargs):
        """Full flow: SSE chunks -> _invoke_llm_streaming -> LLMResponse."""
        from agent_llm_invoke import _invoke_llm_streaming
        from agent_llm_models import LLMCallConfig, CallStats

        mock_kwargs.return_value = {"model": "m", "messages": [], "stream": True}
        mock_stats.return_value = CallStats(prompt_tokens=10, completion_tokens=5)

        chunks = [
            _make_chunk(reasoning="hmm"),
            _make_chunk(content="Hello"),
            _make_chunk(content=" world", finish_reason="stop"),
        ]
        client = self._make_client_with_chunks(chunks)
        config = LLMCallConfig()
        tokens_displayed = [0]

        resp = _invoke_llm_streaming(client, "m", [], config, tokens_displayed)
        assert resp.text == "Hello world"
        assert resp.reasoning == "hmm"
        assert resp.success is True
        assert tokens_displayed[0] == 2

    @patch("agent_llm_invoke._extract_call_stats")
    @patch("agent_llm_invoke.error_display")
    @patch("agent_llm_invoke._build_call_kwargs")
    def test_partial_stream_retries_with_warning(self, mock_kwargs, mock_err, mock_stats, capsys):
        """Mid-stream failure after tokens displayed triggers retry with yellow warning."""
        from agent_llm_invoke import _invoke_llm_with_retry
        from agent_llm_models import LLMCallConfig, APITimeoutError, CallStats

        mock_kwargs.return_value = {"model": "m", "messages": [], "stream": True}
        mock_stats.return_value = CallStats(prompt_tokens=10, completion_tokens=5)

        call_count = [0]

        def _stream_side_effect(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: yields some tokens then raises
                yield _make_chunk(content="partial")
                raise APITimeoutError("timeout")
            # Second call: succeeds
            yield _make_chunk(content="Hello")
            yield _make_chunk(content=" world", finish_reason="stop")

        client = MagicMock()
        client.chat.completions.create_stream.side_effect = _stream_side_effect

        config = LLMCallConfig(max_retries=3)

        with patch("agent_model_health.get_health_monitor") as mock_hm:
            mock_hm.return_value = MagicMock()
            resp, _ = _invoke_llm_with_retry(client, "m", [], stream=True, config=config)

        # Should have retried: first call failed mid-stream, second succeeded
        assert call_count[0] == 2
        assert resp.text == "Hello world"

        # Verify yellow warning was printed
        captured = capsys.readouterr()
        assert "RETRY" in captured.out
        assert "\033[93m" in captured.out  # yellow ANSI color code

    @patch("agent_llm_invoke.error_display")
    @patch("agent_llm_invoke._build_call_kwargs")
    def test_pre_stream_error_still_retries(self, mock_kwargs, mock_err):
        """502 error before any tokens are displayed still retries."""
        from agent_llm_invoke import _invoke_llm_with_retry
        from agent_llm_models import LLMCallConfig, APIGatewayError

        mock_kwargs.return_value = {"model": "m", "messages": [], "stream": True}

        client = MagicMock()
        def _stream_side_effect(**kw):
            raise APIGatewayError("bad gateway")

        client.chat.completions.create_stream.side_effect = _stream_side_effect
        config = LLMCallConfig(max_retries=2)

        with patch("agent_model_health.get_health_monitor") as mock_hm, \
             patch("agent_llm_client.RetryBackoff.wait"):
            mock_hm.return_value = MagicMock()
            with pytest.raises(APIGatewayError):
                _invoke_llm_with_retry(client, "m", [], stream=True, config=config)

        # Should have been called multiple times (retried)
        assert client.chat.completions.create_stream.call_count > 1

    @patch("agent_llm_invoke.error_display")
    @patch("agent_llm_invoke._build_call_kwargs")
    def test_stream_aborted_not_retried(self, mock_kwargs, mock_err):
        """StreamAbortedError is re-raised immediately without retry."""
        from agent_llm_invoke import _invoke_llm_with_retry, StreamAbortedError
        from agent_llm_models import LLMCallConfig

        mock_kwargs.return_value = {"model": "m", "messages": [], "stream": True}

        client = MagicMock()

        def _stream_side_effect(**kw):
            yield _make_chunk(content="a")
            yield _make_chunk(content="a")
            yield _make_chunk(content="a")
            yield _make_chunk(content="a")
            yield _make_chunk(content="a")
            # Trigger abort via max_tokens
            raise StreamAbortedError("repeated token")

        client.chat.completions.create_stream.side_effect = _stream_side_effect
        config = LLMCallConfig(max_retries=5)

        with patch("agent_model_health.get_health_monitor") as mock_hm:
            mock_hm.return_value = MagicMock()
            with pytest.raises(StreamAbortedError):
                _invoke_llm_with_retry(client, "m", [], stream=True, config=config)

        # Only called once — no retry
        assert client.chat.completions.create_stream.call_count == 1
