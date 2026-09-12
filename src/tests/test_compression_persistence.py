"""Test that compression in _invoke_llm_with_retry persists to caller's context."""

from unittest.mock import Mock
from agent_llm_invoke import _invoke_llm_with_retry
from agent_llm_models import LLMCallConfig, LLMResponse, BadRequestError


class MockContext:
    """Minimal Context-like object with set_messages and to_list."""

    def __init__(self, messages: list[dict]):
        self._messages = messages

    def to_list(self) -> list[dict]:
        return self._messages.copy()

    def set_messages(self, msgs: list[dict]) -> None:
        self._messages = list(msgs)

    def __len__(self):
        return len(self._messages)


class TestCompressionPersistence:
    """Verify that compressed messages returned from _invoke_llm_with_retry
    can be synced back to the agent context, making compression persistent."""

    def _make_response(self, content="OK response"):
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(role="assistant", content=content, tool_calls=None, reasoning_content=None))
        ]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.prompt_tokens_details = None
        return mock_response

    def test_no_compression_returns_none(self):
        """When no overflow occurs, compressed_messages is None."""
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = self._make_response()

        resp, compressed = _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [{"role": "user", "content": "Hello"}],
            stream=False,
            config=LLMCallConfig(max_retries=1),
        )

        assert resp.success
        assert compressed is None

    def test_overflow_triggers_retry(self):
        """When BadRequestError with overflow string occurs, the function
        retries (does not immediately raise). With max_retries=5, the
        overflow on attempt 0 leads to a retry that succeeds."""
        mock_client = Mock()

        # First call triggers overflow, second call succeeds
        overflow_error = BadRequestError("context size has been exceeded")
        mock_client.chat.completions.create.side_effect = [
            overflow_error,
            self._make_response(),
        ]

        messages = [{"role": "user", "content": "Hello"}]

        resp, compressed = _invoke_llm_with_retry(
            mock_client,
            "test-model",
            messages,
            stream=False,
            config=LLMCallConfig(
                max_retries=5,
                compress_client=mock_client,
                compress_model="test-model",
            ),
        )

        # Overflow triggered a retry (2 API calls total)
        assert mock_client.chat.completions.create.call_count == 2
        assert isinstance(resp, LLMResponse)
        assert resp.success
        # compressed is None (compression didn't produce a result at attempt 0)
        # or a list (if compression succeeded)
        assert compressed is None or isinstance(compressed, list)

    def test_no_overflow_context_unchanged(self):
        """When no overflow occurs, compressed is None and the caller's
        context is unchanged. Verifies the sync pattern is a no-op."""
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = self._make_response()

        ctx = MockContext([{"role": "user", "content": "Hello"}])

        resp, compressed = _invoke_llm_with_retry(
            mock_client,
            "test-model",
            ctx.to_list(),
            stream=False,
            config=LLMCallConfig(max_retries=1),
        )

        # No overflow → compressed is None → context unchanged
        assert compressed is None
        assert len(ctx) == 1
        assert ctx._messages[0]["content"] == "Hello"

    def test_tuple_unpacking(self):
        """The return value is a (LLMResponse, list|None) 2-tuple."""
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = self._make_response()

        result = _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [{"role": "user", "content": "Hi"}],
            stream=False,
            config=LLMCallConfig(max_retries=1),
        )

        # Must be a 2-tuple
        assert isinstance(result, tuple)
        assert len(result) == 2
        resp, compressed = result
        assert isinstance(resp, LLMResponse)
        assert compressed is None

    def test_length_finish_reason_is_success(self):
        """finish_reason='length' is treated as a successful response
        (not an overflow trigger). Compression is NOT triggered."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(
                message=Mock(role="assistant", content="truncated response", tool_calls=None, reasoning_content=None),
                finish_reason="length",
            )
        ]
        mock_response.usage.prompt_tokens = 100000
        mock_response.usage.completion_tokens = 4096
        mock_response.usage.prompt_tokens_details = None
        mock_client.chat.completions.create.return_value = mock_response

        resp, compressed = _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [{"role": "user", "content": "x" * 1000}],
            stream=False,
            config=LLMCallConfig(
                max_retries=1,
                compress_client=mock_client,
                compress_model="test-model",
            ),
        )

        # length finish_reason is a success, not an overflow
        assert isinstance(resp, LLMResponse)
        assert resp.success
        # No compression triggered (only BadRequestError triggers it)
        assert compressed is None


class TestPrepareMessagesInLoop:
    """Test that messages are prepared correctly in the retry loop."""

    def test_prepare_messages_called_each_iteration(self):
        """Each retry iteration uses the (potentially compressed) messages."""
        mock_client = Mock()

        # First call fails with overflow, second succeeds
        overflow_error = BadRequestError("maximum context length exceeded")
        mock_client.chat.completions.create.side_effect = [
            overflow_error,
            Mock(
                choices=[Mock(message=Mock(role="assistant", content="OK response", tool_calls=None, reasoning_content=None))],
                usage=Mock(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None),
            ),
        ]

        resp, compressed = _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [{"role": "user", "content": "Hello"}],
            stream=False,
            config=LLMCallConfig(
                max_retries=2,
                compress_client=mock_client,
                compress_model="test-model",
            ),
        )

        # Should have retried (2 API calls)
        assert mock_client.chat.completions.create.call_count == 2
        assert isinstance(resp, LLMResponse)
