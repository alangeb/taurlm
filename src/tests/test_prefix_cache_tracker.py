"""Tests for PrefixCacheTracker — byte-level prefix cache estimation.

Covers:
- First call (no previous context) → 0% expected hit
- Same context → 100% expected hit
- Partially different context → proportional expected hit
- Params changed → 0% expected hit with diagnostic reason
- Low prefix match detection
- Reset behavior
"""

import json

from agent_llm_cache import PrefixCacheTracker


def _make_body(messages=None, model="test-model", tools=None, **extra):
    """Helper to build a deterministic JSON request body."""
    body = {
        "model": model,
        "messages": messages or [{"role": "system", "content": "You are helpful"}],
    }
    if tools is not None:
        body["tools"] = tools
    body.update(extra)
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TestComputeExpectedHit:
    """Test PrefixCacheTracker.compute_expected_hit()."""

    def test_first_call_returns_zero(self):
        """First call has no previous context → 0% expected hit."""
        tracker = PrefixCacheTracker()
        body = _make_body()
        expected, reason = tracker.compute_expected_hit(body)

        assert expected == 0.0
        assert "first call" in reason

    def test_same_context_returns_100(self):
        """Identical context → 100% expected hit."""
        tracker = PrefixCacheTracker()
        body = _make_body()

        # Prime the tracker
        tracker.compute_expected_hit(body)

        # Same body → 100% match
        expected, reason = tracker.compute_expected_hit(body)

        assert expected == 1.0
        assert "prefix match" in reason

    def test_different_context_returns_proportional(self):
        """Partially different context → proportional expected hit."""
        tracker = PrefixCacheTracker()

        # Short context
        short = _make_body(messages=[{"role": "system", "content": "You are helpful"}])
        # Long context (same prefix + extra)
        long = _make_body(
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Tell me a very long story that makes this message significantly longer"},
            ]
        )

        tracker.compute_expected_hit(short)
        expected, reason = tracker.compute_expected_hit(long)

        # The prefix (system message + JSON overhead) matches, but total is larger
        assert 0.0 < expected < 1.0
        assert "prefix match" in reason

    def test_params_changed_returns_zero(self):
        """Different model/tools → 0% expected hit with diagnostic."""
        tracker = PrefixCacheTracker()

        body1 = _make_body(model="model-a", tools=[{"name": "tool1"}])
        body2 = _make_body(model="model-b", tools=[{"name": "tool1"}])

        tracker.compute_expected_hit(body1)
        expected, reason = tracker.compute_expected_hit(body2)

        assert expected == 0.0
        assert "params changed" in reason
        assert "model" in reason

    def test_tools_changed_returns_zero(self):
        """Different tools → 0% expected hit."""
        tracker = PrefixCacheTracker()

        body1 = _make_body(tools=[{"name": "tool1"}])
        body2 = _make_body(tools=[{"name": "tool2"}])

        tracker.compute_expected_hit(body1)
        expected, reason = tracker.compute_expected_hit(body2)

        assert expected == 0.0
        assert "params changed" in reason
        assert "tools" in reason

    def test_generation_params_do_not_invalidate(self):
        """Temperature/top_p changes do NOT invalidate prefix cache.

        Generation parameters (temperature, top_p, max_tokens, etc.) do NOT
        affect the KV cache built during prefill. Only 'model' and 'tools'
        changes invalidate the prefix cache in major backends (vLLM,
        llama.cpp, SGLang).
        """
        tracker = PrefixCacheTracker()

        body1 = _make_body(temperature=0.7, top_p=0.9)
        body2 = _make_body(temperature=0.1, top_p=0.1)

        tracker.compute_expected_hit(body1)
        expected, reason = tracker.compute_expected_hit(body2)

        # Generation param changes do NOT trigger "params changed" warning
        # because they don't affect prefix cache
        assert expected > 0.0  # Should still have prefix match
        assert "params changed" not in reason

    def test_low_prefix_match_threshold(self):
        """Verify that <25% prefix match is detectable."""
        tracker = PrefixCacheTracker()

        # Tiny prefix
        tiny = _make_body(messages=[{"role": "system", "content": "X"}])
        # Huge suffix (same prefix + massive addition)
        huge = _make_body(
            messages=[
                {"role": "system", "content": "X"},
                {"role": "user", "content": "A" * 10000},
            ]
        )

        tracker.compute_expected_hit(tiny)
        expected, reason = tracker.compute_expected_hit(huge)

        # The prefix "X" is tiny relative to the huge message
        assert expected < 0.25
        assert "prefix match" in reason

    def test_unparseable_body(self):
        """Non-JSON body → 0% expected hit with diagnostic."""
        tracker = PrefixCacheTracker()
        expected, reason = tracker.compute_expected_hit(b"NOT JSON AT ALL")

        assert expected == 0.0
        assert "unparseable" in reason


class TestReset:
    """Test PrefixCacheTracker.reset()."""

    def test_reset_clears_state(self):
        """After reset, next call should behave like first call."""
        tracker = PrefixCacheTracker()
        body = _make_body()

        # Prime the tracker
        tracker.compute_expected_hit(body)
        expected_before, _ = tracker.compute_expected_hit(body)
        assert expected_before == 1.0

        # Reset
        tracker.reset()

        # Next call should be like first call
        expected_after, reason = tracker.compute_expected_hit(body)
        assert expected_after == 0.0
        assert "first call" in reason


class TestShouldWarn:
    """Test PrefixCacheTracker.should_warn() deduplication."""

    def test_first_call_returns_true(self):
        """First call with any key always warns."""
        tracker = PrefixCacheTracker()
        assert tracker.should_warn("low_act:0%") is True

    def test_same_key_returns_false(self):
        """Repeated identical key is suppressed."""
        tracker = PrefixCacheTracker()
        assert tracker.should_warn("low_act:0%") is True
        assert tracker.should_warn("low_act:0%") is False
        assert tracker.should_warn("low_act:0%") is False

    def test_different_key_returns_true(self):
        """Different key is emitted (condition changed)."""
        tracker = PrefixCacheTracker()
        assert tracker.should_warn("low_act:0%") is True
        assert tracker.should_warn("low_act:0%") is False
        assert tracker.should_warn("low_act:50%") is True  # condition changed
        assert tracker.should_warn("low_act:50%") is False

    def test_distinct_keys_interleave(self):
        """Different unique keys are each emitted once."""
        tracker = PrefixCacheTracker()
        assert tracker.should_warn("low_act:0%") is True
        assert tracker.should_warn("low_exp:10%") is True
        assert tracker.should_warn("low_act:0%") is False  # already seen

    def test_reset_clears_warn_state(self):
        """After reset, previously-seen keys are emitted again."""
        tracker = PrefixCacheTracker()
        tracker.should_warn("low_act:0%")
        tracker.should_warn("low_act:0%")
        assert tracker.should_warn("low_act:0%") is False
        tracker.reset()
        assert tracker.should_warn("low_act:0%") is True


class TestLongestCommonPrefix:
    """Test PrefixCacheTracker._longest_common_prefix()."""

    def test_identical_bytes(self):
        assert PrefixCacheTracker._longest_common_prefix(b"hello", b"hello") == 5

    def test_no_common_prefix(self):
        assert PrefixCacheTracker._longest_common_prefix(b"abc", b"xyz") == 0

    def test_partial_prefix(self):
        assert PrefixCacheTracker._longest_common_prefix(b"hello", b"help") == 3

    def test_empty_a(self):
        assert PrefixCacheTracker._longest_common_prefix(b"", b"hello") == 0

    def test_empty_both(self):
        assert PrefixCacheTracker._longest_common_prefix(b"", b"") == 0

    def test_long_prefix(self):
        long = b"a" * 1000
        assert PrefixCacheTracker._longest_common_prefix(long, long) == 1000

    def test_unicode_prefix(self):
        """Unicode characters are compared byte-by-byte."""
        a = "Hello \u00e9".encode("utf-8")  # é = 2 bytes in UTF-8
        b = "Hello \u00e9 world".encode("utf-8")
        assert PrefixCacheTracker._longest_common_prefix(a, b) == len(a)


class TestParamChangeDetection:
    """Test _find_param_changes() diagnostic output."""

    def test_detects_model_change(self):
        tracker = PrefixCacheTracker()
        body1 = _make_body(model="model-a")
        body2 = _make_body(model="model-b")

        tracker.compute_expected_hit(body1)
        _, reason = tracker.compute_expected_hit(body2)

        assert "model" in reason

    def test_detects_tools_change(self):
        tracker = PrefixCacheTracker()
        body1 = _make_body(tools=[{"name": "t1"}])
        body2 = _make_body(tools=[{"name": "t2"}])

        tracker.compute_expected_hit(body1)
        _, reason = tracker.compute_expected_hit(body2)

        assert "tools" in reason

    def test_no_change_when_identical(self):
        tracker = PrefixCacheTracker()
        body = _make_body(model="m", tools=[{"name": "t"}])

        tracker.compute_expected_hit(body)
        expected, reason = tracker.compute_expected_hit(body)

        assert expected == 1.0
        assert "params changed" not in reason


class TestExtractParamsKey:
    """Test _extract_params_key() — only model and tools are tracked."""

    def test_only_model_and_tools_tracked(self):
        """Only 'model' and 'tools' affect prefix cache in major backends."""
        tracker = PrefixCacheTracker()

        body = {
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "t1"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 100,
        }
        key = tracker._extract_params_key(body)
        parsed = json.loads(key.decode("utf-8"))

        assert "model" in parsed
        assert "tools" in parsed
        # Generation params are NOT tracked (don't affect prefix cache)
        assert "temperature" not in parsed
        assert "top_p" not in parsed
        assert "max_tokens" not in parsed
        # Messages are always excluded (content, not params)
        assert "messages" not in parsed

    def test_deterministic_ordering(self):
        """sort_keys=True ensures deterministic serialization."""
        tracker = PrefixCacheTracker()

        body1 = {"model": "m", "tools": [{"name": "t"}]}
        body2 = {"tools": [{"name": "t"}], "model": "m"}

        key1 = tracker._extract_params_key(body1)
        key2 = tracker._extract_params_key(body2)

        assert key1 == key2
