"""Tests for agent_llm module."""

from unittest.mock import Mock

import pytest

from agent_llm_invoke import _extract_token_usage, _invoke_llm_with_retry
from agent_llm_models import CallStats, CacheTracker, LLMCallConfig
from agent_llm_models import APITimeoutError


class TestLegacyExtractTokenUsage:
    """Test _extract_token_usage legacy tuple-based function."""

    def test_extract_tokens(self):
        """Test token extraction from mock response."""
        mock_response = Mock()
        mock_response.choices = []
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150
        mock_response.usage.prompt_tokens_details = {}

        result = _extract_token_usage(mock_response, response_text="")

        assert result[0] == 100  # prompt_tokens
        assert result[1] == 50  # completion_tokens
        assert result[2] == 0  # cached_tokens

    def test_extract_tokens_with_cached(self):
        """Test token extraction with cached tokens."""
        mock_response = Mock()
        mock_response.choices = []
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150
        mock_response.usage.prompt_tokens_details = {"cached_tokens": 30}

        result = _extract_token_usage(mock_response, response_text="")

        assert result[0] == 100
        assert result[1] == 50
        assert result[2] == 30

    def test_extract_tokens_missing_usage(self):
        """Test fallback when usage attribute missing.

        When usage is missing, _extract_token_usage returns None for all
        token fields. Callers must fall back to estimation themselves.
        """
        mock_response = Mock()
        mock_response.choices = []
        del mock_response.usage

        result = _extract_token_usage(mock_response, response_text="Hello world")

        # Returns None when usage data is missing (no estimation in this function)
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None

    def test_extract_tokens_exception(self):
        """Test fallback when usage is None.

        When usage is None, _extract_token_usage returns None for all
        token fields. Callers must fall back to estimation themselves.
        """
        mock_response = Mock()
        mock_response.choices = []
        mock_response.usage = None

        result = _extract_token_usage(mock_response, response_text="")

        # Returns None when usage data is missing (no estimation in this function)
        assert result[0] is None
        assert result[1] is None
        assert result[2] is None


class TestCallStatsDataclass:
    """Test CallStats dataclass properties."""

    def test_total_tokens(self):
        s = CallStats(100, 50, 30)
        assert s.total_tokens == 150

    def test_hit_rate(self):
        s = CallStats(100, 50, 30)
        assert s.hit_rate == 0.3

    def test_zero_hit_rate_on_zero_prompt(self):
        s = CallStats(0, 50, 0)
        assert s.hit_rate == 0.0


class TestCacheTracker:
    """Test CacheTracker session-level statistics."""

    def test_cumulative(self):
        t = CacheTracker()
        t.record(CallStats(100, 50, 30))
        t.record(CallStats(200, 100, 80))
        assert t.cumulative_hit_rate == 110 / 300

    def test_sliding_window(self):
        t = CacheTracker()
        # Window of 5: last 3 calls
        t.record(CallStats(100, 50, 100))  # 100%
        t.record(CallStats(100, 50, 0))  # 0%
        t.record(CallStats(100, 50, 50))  # 50%
        assert t.sliding_hit_rate == 150 / 300  # last 3: 150/300

    def test_last_hit_rate(self):
        t = CacheTracker()
        t.record(CallStats(100, 50, 10))
        assert t.last_hit_rate == 0.1

    def test_call_count(self):
        t = CacheTracker()
        assert t.call_count == 0
        t.record(CallStats(100, 50, 10))
        assert t.call_count == 1

    def test_window_evicts_old(self):
        t = CacheTracker()
        for i in range(10):
            t.record(CallStats(100, 50, 10))
        # All 10 records stored, sliding window considers last 5
        assert len(t._records) == 10
        assert t.call_count == 10


import pytest as _pytest
from unittest.mock import patch as _patch


@_pytest.fixture(autouse=True)
def _no_backoff_sleep():
    """Neutralize RetryBackoff.wait so retry tests don't sleep for real.

    Mirrors test_wave1_fixes / test_streaming. Does not weaken assertions —
    only removes the real-time backoff sleep between retries.
    """
    with _patch("agent_llm_client.RetryBackoff.wait", lambda self, attempt: None):
        yield


class TestLegacyInvokeWithRetry:
    """Test that _invoke_llm_with_retry properly handles timeout exceptions."""

    def test_apitimeouterror_is_caught(self):
        """Test that APITimeoutError is caught by the retry handler."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(
                message=Mock(role="assistant", content="Success response", tool_calls=None, reasoning_content=None)
            )
        ]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.prompt_tokens_details = None

        # First call raises APITimeoutError, second succeeds
        mock_client.chat.completions.create.side_effect = [
            APITimeoutError("timed out"),
            mock_response,
        ]

        result, _ = _invoke_llm_with_retry(
            mock_client, "test-model", [], stream=False,
            config=LLMCallConfig(max_retries=3),
        )
        assert result.text == "Success response"
        assert isinstance(result.stats, CallStats)
        # Verify retry happened (2 calls: 1 failure + 1 success)
        assert mock_client.chat.completions.create.call_count == 2

    def test_all_timeout_variants_cycle(self):
        """Comprehensive test: Cycles through all three timeout types to ensure
        the retry loop handles them all correctly.
        """
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(
                message=Mock(role="assistant", content="Success response", tool_calls=None, reasoning_content=None)
            )
        ]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.prompt_tokens_details = None

        # Sequence: Custom -> Builtin -> Socket -> Success
        mock_client.chat.completions.create.side_effect = [
            APITimeoutError("custom timeout"),
            TimeoutError("builtin timeout"),
            TimeoutError("socket timeout"),
            mock_response,
        ]

        result, _ = _invoke_llm_with_retry(
            mock_client, "test-model", [], stream=False,
            config=LLMCallConfig(max_retries=5),
        )

        # 1. Verify we actually reached the end
        assert result.text == "Success response"

        # 2. Verify the retry loop ran 3 times (3 failures + 1 success)
        assert mock_client.chat.completions.create.call_count == 4

    def test_exhaust_retries_raises_error(self):
        """Test that exhausting retries raises the final error."""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = APITimeoutError("timed out")

        with pytest.raises(APITimeoutError):
            _invoke_llm_with_retry(
                mock_client, "test-model", [], stream=False,
                config=LLMCallConfig(max_retries=2),
            )

        # Should have called 3 times (initial + 2 retries)
        assert mock_client.chat.completions.create.call_count == 3


class TestRetryThinkingDisable:
    """Test that thinking is disabled after 5 failures."""

    def _make_response(self, content="Success response"):
        """Helper to create a mock LLM response."""
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(role="assistant", content=content, tool_calls=None, reasoning_content=None))
        ]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.prompt_tokens_details = None
        return mock_response

    def test_thinking_disabled_after_threshold(self):
        """Verify chat_template_kwargs is overwritten to disable thinking
        only after 5 consecutive failures.

        NOTE: _invoke_llm_with_retry may mutate extra_kwargs in-place.
        This is safe because all callers pass ephemeral dicts
        (resolve_group_params() creates fresh dicts per-call).
        """
        mock_client = Mock()
        mock_response = self._make_response()

        # Fail 5 times, then succeed
        mock_client.chat.completions.create.side_effect = [
            APITimeoutError("timeout") for _ in range(5)
        ] + [mock_response]

        extra_kwargs = {"chat_template_kwargs": {"enable_thinking": True}}
        _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [],
            stream=False,
            config=LLMCallConfig(max_retries=5 + 2, extra_kwargs=extra_kwargs),
        )

        # Verify thinking WAS disabled in the actual LLM call (functional check)
        # The last call should have enable_thinking=False
        last_call_kwargs = mock_client.chat.completions.create.call_args_list[-1][1]
        assert (
            last_call_kwargs.get("chat_template_kwargs", {}).get("enable_thinking")
            is False
        )
        # Total calls: 5 failures + 1 success
        assert (
            mock_client.chat.completions.create.call_count
            == 5 + 1
        )

    def test_thinking_not_disabled_with_few_retries(self):
        """When max_retries < 5, thinking stays enabled."""
        mock_client = Mock()
        mock_response = self._make_response()

        # Only 2 retries, which is less than the threshold
        mock_client.chat.completions.create.side_effect = [
            APITimeoutError("timeout"),
            APITimeoutError("timeout"),
            mock_response,
        ]

        extra_kwargs = {"chat_template_kwargs": {"enable_thinking": True}}
        _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [],
            stream=False,
            config=LLMCallConfig(max_retries=2, extra_kwargs=extra_kwargs),
        )

        # Verify thinking was NOT disabled (threshold not reached)
        # All calls should have enable_thinking=True
        for call_kwargs in mock_client.chat.completions.create.call_args_list:
            kwargs = call_kwargs[1]
            assert kwargs.get("chat_template_kwargs", {}).get("enable_thinking") is True

    def test_no_extra_kwargs_unchanged(self):
        """When extra_kwargs is None, no mutation occurs."""
        mock_client = Mock()
        mock_response = self._make_response()

        mock_client.chat.completions.create.side_effect = [
            APITimeoutError("timeout") for _ in range(10)  # Well beyond threshold
        ] + [mock_response]

        _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [],
            stream=False,
            config=LLMCallConfig(max_retries=10, extra_kwargs=None),
        )

        # No exception, no mutation to worry about
        assert mock_client.chat.completions.create.call_count == 11

    def test_no_chat_template_kwargs_unchanged(self):
        """When extra_kwargs has no chat_template_kwargs, no mutation occurs."""
        mock_client = Mock()
        mock_response = self._make_response()

        mock_client.chat.completions.create.side_effect = [
            APITimeoutError("timeout") for _ in range(10)
        ] + [mock_response]

        extra_kwargs = {"temperature": 0.7}  # No chat_template_kwargs
        _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [],
            stream=False,
            config=LLMCallConfig(max_retries=10, extra_kwargs=extra_kwargs),
        )

        # Should be unchanged
        assert extra_kwargs == {"temperature": 0.7}


class TestPrefixCacheTrackerMonitoring:
    """Test PrefixCacheTracker diagnostic features."""

    def _make_body(self, msg_text: str = "hello") -> bytes:
        """Create a minimal request body for testing."""
        import json as _json

        body = {
            "model": "test-model",
            "messages": [{"role": "user", "content": msg_text}],
        }
        return _json.dumps(body, sort_keys=True).encode("utf-8")

    def test_diagnose_miss_no_previous(self):
        """diagnose_miss returns message when no previous request."""
        from agent_llm_cache import PrefixCacheTracker

        tracker = PrefixCacheTracker()
        diag = tracker.diagnose_miss(self._make_body())
        assert "no previous request" in diag

    def test_diagnose_miss_with_prefix_match(self):
        """diagnose_miss reports prefix match and size delta."""
        from agent_llm_cache import PrefixCacheTracker

        tracker = PrefixCacheTracker()
        body1 = self._make_body("short")
        tracker.compute_expected_hit(body1)
        body2 = self._make_body("much longer text here")
        diag = tracker.diagnose_miss(body2)
        assert "prefix match:" in diag
        assert "body size delta:" in diag

    def test_diagnose_miss_params_changed(self):
        """diagnose_miss reports param changes."""
        from agent_llm_cache import PrefixCacheTracker
        import json as _json

        tracker = PrefixCacheTracker()
        body1 = _json.dumps({"model": "model-a", "messages": []}, sort_keys=True).encode()
        tracker.compute_expected_hit(body1)
        body2 = _json.dumps({"model": "model-b", "messages": []}, sort_keys=True).encode()
        diag = tracker.diagnose_miss(body2)
        assert "params changed" in diag

    def test_diagnose_miss_compares_prev_not_self(self):
        """diagnose_miss compares current body against the PREVIOUS body, not itself.

        Verifies that _prev_request_body is saved before _last_request_body is updated,
        so diagnose_miss reports meaningful divergence (not 100% self-match).
        """
        from agent_llm_cache import PrefixCacheTracker

        tracker = PrefixCacheTracker()
        body1 = self._make_body("original")
        tracker.compute_expected_hit(body1)
        body2 = self._make_body("completely different text")
        tracker.compute_expected_hit(body2)
        # diagnose_miss should compare body2 against body1 (the previous request)
        diag = tracker.diagnose_miss(body2)
        # Should show divergence, not 100% match
        assert "100.0%" not in diag
        assert "prefix match:" in diag

    def test_reset_clears_all(self):
        """reset clears all stored state."""
        from agent_llm_cache import PrefixCacheTracker

        tracker = PrefixCacheTracker()
        body = self._make_body("hello")
        tracker.compute_expected_hit(body)
        tracker.compute_expected_hit(body)
        tracker.reset()
        # Verify _prev_request_body and _prev_params_key are cleared
        assert tracker._prev_request_body is None
        assert tracker._prev_params_key is None
        assert tracker._last_request_body is None
        assert tracker._last_params_key is None


class TestReportCacheHitDedup:
    """Test that _report_cache_hit deduplicates consecutive identical warnings."""

    def _make_client(self, cached_tokens: int = 0):
        """Create a SimpleOpenAIClient with a tracker and mock response."""
        import json as _json
        from agent_llm_client import SimpleOpenAIClient
        from agent_llm_cache import PrefixCacheTracker
        from unittest.mock import Mock

        tracker = PrefixCacheTracker()
        body = _json.dumps({"model": "m", "messages": []}, sort_keys=True).encode()
        tracker.compute_expected_hit(body)

        client = SimpleOpenAIClient(
            base_url="http://localhost:9999",
            api_key="test",
            cache_tracker=tracker,
        )

        def _make_response(cached: int = 0):
            response = Mock()
            response.usage.prompt_tokens = 100
            response.usage.prompt_tokens_details = {"cached_tokens": cached}
            return response

        return client, tracker, body, _make_response

    def test_low_actual_dedup(self):
        """Repeated 'low actual' warnings are suppressed."""
        from unittest.mock import patch

        client, tracker, body, mk_resp = self._make_client(cached_tokens=0)
        hit_reason = "first call (no previous context)"

        with patch("agent_llm_client.warning") as mock_warning:
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason=hit_reason,
                body_bytes=body,
            )
            first_count = mock_warning.call_count
            # Second call with identical conditions → all suppressed
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason=hit_reason,
                body_bytes=body,
            )
            assert mock_warning.call_count == first_count

    def test_gap_dedup(self):
        """Repeated gap warnings are suppressed."""
        from unittest.mock import patch

        client, tracker, body, mk_resp = self._make_client(cached_tokens=0)

        with patch("agent_llm_client.warning") as mock_warning:
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.75,
                hit_reason="prefix match: 100/100 bytes (100.0%)",
                body_bytes=body,
            )
            first_count = mock_warning.call_count
            # Two more calls with same gap → suppressed
            for _ in range(2):
                client._report_cache_hit(
                    response=mk_resp(),
                    expected_hit=0.75,
                    hit_reason="prefix match: 100/100 bytes (100.0%)",
                    body_bytes=body,
                )
            assert mock_warning.call_count == first_count

    def test_low_expected_dedup_with_varying_hit_reason(self):
        """low_exp key excludes hit_reason, so varying descriptions still dedup.

        The hit_reason string contains volatile byte counts (e.g.
        'prefix match: 114/183 bytes (62.3%)') that would make the key
        unique per call.  The key uses only the rounded expected_hit,
        so identical conditions are still suppressed.
        """
        from unittest.mock import patch

        client, tracker, body, mk_resp = self._make_client(cached_tokens=0)

        with patch("agent_llm_client.warning") as mock_warning:
            # Same expected_hit, different hit_reason each time
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.10,
                hit_reason="prefix match: 100/200 bytes (50.0%)",
                body_bytes=body,
            )
            first_count = mock_warning.call_count
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.10,
                hit_reason="prefix match: 150/300 bytes (50.0%)",
                body_bytes=body,
            )
            # No new warnings — key only uses expected_hit, not hit_reason
            assert mock_warning.call_count == first_count

    def test_condition_change_re_warns(self):
        """When actual hit rate changes, warning is re-emitted."""
        from unittest.mock import patch

        client, tracker, body, mk_resp = self._make_client(cached_tokens=0)
        hit_reason = "first call (no previous context)"

        with patch("agent_llm_client.warning") as mock_warning:
            # First: 0% actual
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason=hit_reason,
                body_bytes=body,
            )
            first_count = mock_warning.call_count

            # Second: still 0% → suppressed
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason=hit_reason,
                body_bytes=body,
            )
            assert mock_warning.call_count == first_count

            # Third: actual changed to 10% → still <25%, key changed → re-emitted
            client._report_cache_hit(
                response=mk_resp(cached=10),
                expected_hit=0.0,
                hit_reason=hit_reason,
                body_bytes=body,
            )
            assert mock_warning.call_count > first_count

    def test_params_changed_dedup(self):
        """Repeated 'params changed' warnings are suppressed."""
        from unittest.mock import patch

        client, tracker, body, mk_resp = self._make_client(cached_tokens=0)
        reason = "params changed: model"

        with patch("agent_llm_client.warning") as mock_warning:
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason=reason,
                body_bytes=body,
            )
            first_count = mock_warning.call_count
            # Second call with identical params-change reason → suppressed
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason=reason,
                body_bytes=body,
            )
            assert mock_warning.call_count == first_count

    def test_params_changed_condition_change_re_warns(self):
        """Different params-changed reason re-emits a new warning."""
        from unittest.mock import patch

        client, tracker, body, mk_resp = self._make_client(cached_tokens=0)

        with patch("agent_llm_client.warning") as mock_warning:
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason="params changed: model",
                body_bytes=body,
            )
            first_count = mock_warning.call_count
            # Different reason string → new dedup key → re-emitted
            client._report_cache_hit(
                response=mk_resp(),
                expected_hit=0.0,
                hit_reason="params changed: tools",
                body_bytes=body,
            )
            assert mock_warning.call_count > first_count
