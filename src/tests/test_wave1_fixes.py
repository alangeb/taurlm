"""Regression tests for wave-1 fixes: B4 (connection retry), D2 (plan retry), D5 (assistant-end)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_llm_models import APIConnectionError, LLMResponse


class _FakeResp:
    def __init__(self, text):
        self.text = text


def _ctx(num_turns=8):
    from tests.test_compression_pipeline import _make_large_context
    return _make_large_context(num_turns)


class TestB4ConnectionRetry:
    """APIConnectionError must go through the retry path, not no-retry."""

    def test_connection_error_retries_then_succeeds(self, monkeypatch):
        import agent_llm_invoke as inv
        from agent_llm_models import LLMCallConfig

        # Patch backoff so we don't actually sleep.
        import agent_llm_client as cl
        monkeypatch.setattr(cl.RetryBackoff, "wait", lambda self, a: None)

        calls = {"n": 0}

        class _Choice:
            def __init__(self):
                self.message = type("M", (), {"role": "assistant", "content": "ok", "reasoning_content": None})()
                self.finish_reason = "stop"

        class _Create:
            def create(self, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise APIConnectionError("Connection refused: socket")
                return type("R", (), {"choices": [_Choice()], "usage": None})()

        client = type("C", (), {"chat": type("Ch", (), {"completions": _Create()})()})()
        cfg = LLMCallConfig(max_retries=2, min_response_bytes=None, log_on_failure=False)
        resp, _ = inv._invoke_llm_with_retry(
            client=client, model_name="m",
            messages=[{"role": "user", "content": "hi"}],
            stream=False, config=cfg,
        )
        assert calls["n"] == 2, "APIConnectionError should have triggered a retry"
        assert resp.text == "ok"

    def test_connection_error_exhausts_then_raises(self, monkeypatch):
        import agent_llm_invoke as inv
        from agent_llm_models import LLMCallConfig
        import agent_llm_client as cl
        monkeypatch.setattr(cl.RetryBackoff, "wait", lambda self, a: None)

        class _Create:
            def create(self, **kwargs):
                raise APIConnectionError("Connection refused: socket")

        client = type("C", (), {"chat": type("Ch", (), {"completions": _Create()})()})()
        cfg = LLMCallConfig(max_retries=1, min_response_bytes=None, log_on_failure=False)
        try:
            inv._invoke_llm_with_retry(
                client=client, model_name="m",
                messages=[{"role": "user", "content": "hi"}],
                stream=False, config=cfg,
            )
            assert False, "should have raised"
        except APIConnectionError:
            pass  # expected after exhausting retries (not immediately)


class TestD2PlanRetry:
    """Invalid plan must trigger a retry of the plan LLM call."""

    def test_invalid_plan_triggers_retry(self, monkeypatch):
        import agent_context_compress.steps.full_reset as fr

        good_plan = "Step 1: do X. Step 2: do Y. Step 3: verify Z. Step 4: finalize the deliverable."
        calls = []
        plan_call_count = [0]

        def fake(client, model_name, messages, **kw):
            calls.append(messages)
            sys_prompt = messages[0].get("content", "")
            if "summarizer" in sys_prompt.lower():
                return _FakeResp("summary " * 30)  # valid summary
            # plan call: first invalid, then valid
            plan_call_count[0] += 1
            if plan_call_count[0] == 1:
                return _FakeResp("I cannot help with that.")  # invalid plan
            return _FakeResp(good_plan)

        monkeypatch.setattr(fr, "_invoke_llm_with_retry_compression", fake)

        from tests.test_compression_pipeline import _make_large_context
        ctx = _make_large_context(num_turns=6)
        # Make compression worthwhile: pass tiny target so size guard passes.
        out, status = fr.compress_full_reset(
            context=ctx, client=None, model_name="m", target_size_bytes=1,
        )
        # summary(1) + plan invalid(1) + plan valid(1) = 3 LLM calls
        assert len(calls) >= 3, f"expected plan retry, got {len(calls)} calls"
        assert (status["status"] if isinstance(status, dict) else status) == "RESET"
        # The valid plan text should be present, the invalid one absent.
        joined = out[-2]["content"] if out[-1]["role"] == "assistant" else out[-1]["content"]
        assert "Step 1: do X" in joined
        assert "I cannot help" not in joined


class TestD5UserEnd:
    """R1 REVERTED (2026-09-12): compressed context must end on USER, not assistant.

    Compression is triggered by an overflow error MID-INVOKE and the compressed
    list is RE-SENT AS-IS as the live prompt, so a trailing assistant would make
    the model prefill instead of answering.
    """

    def test_conversation_summary_ends_with_user(self):
        from agent_context_compress.steps.conversation_summary import compress_conversation_summary
        from tests.test_compression_pipeline import _make_large_context
        ctx = _make_large_context(num_turns=6)
        out, meta = compress_conversation_summary(
            context=ctx, client=None, model_name="m", target_size_bytes=1,
        )
        assert out[-1]["role"] == "user", f"last role={out[-1]['role']}"

    def test_full_reset_ends_with_user(self):
        import agent_context_compress.steps.full_reset as fr
        from tests.test_compression_pipeline import _make_large_context

        good_plan = "Step 1: do X. Step 2: do Y. Step 3: verify Z. Step 4: finalize the deliverable."

        def fake(client, model_name, messages, **kw):
            sys_prompt = messages[0].get("content", "")
            if "summarizer" in sys_prompt.lower():
                return _FakeResp("summary " * 30)
            return _FakeResp(good_plan)

        orig = fr._invoke_llm_with_retry_compression
        fr._invoke_llm_with_retry_compression = fake
        try:
            ctx = _make_large_context(num_turns=6)
            out, status = fr.compress_full_reset(
                context=ctx, client=None, model_name="m", target_size_bytes=1,
            )
        finally:
            fr._invoke_llm_with_retry_compression = orig
        assert out[-1]["role"] == "user", f"last role={out[-1]['role']}"

    def test_pipeline_full_output_ends_with_user(self):
        from agent_context_compress import compress_context
        from tests.test_compression_pipeline import _make_large_context
        ctx = _make_large_context(num_turns=20)
        result, _, _ = compress_context(
            context=ctx, client=None, model_name="test-model",
            compression_factor=0.5, verbose=False,
            last_known_tokens=100000, max_context_tokens=128000,
            max_output_tokens=4096,
        )
        assert result[-1]["role"] == "user", f"last role={result[-1]['role']}"
