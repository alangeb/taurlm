"""Tests for new functionality added in context management improvements.

Tests:
    TestEstimateTokensReasoning — estimate_tokens() counts reasoning content
    TestEstimateTokensToolCalls — estimate_tokens() counts tool call arguments
    TestEstimateTokensOverhead — per-message overhead is 15 (not 10)
    TestToolCallFieldStripping — internal fields stripped before API call
    TestLogFailedApiRequest — writes failed request body to file
    TestInvokeRetryLogFile — _log_file parameter wired through
"""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_context import TauContext
from agent_llm_models import _ALLOWED_TOOL_CALL_FIELDS
from agent_session import log_failed_api_request

# ---------------------------------------------------------------------------
# Test 1: estimate_tokens() now counts reasoning content
# ---------------------------------------------------------------------------


class TestEstimateTokensReasoning:
    """Verify estimate_tokens() includes reasoning field (stored separately)."""

    def test_reasoning_counted(self):
        """Reasoning content should contribute to token estimate."""
        ctx = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "Short answer",
                    "reasoning": "This is a long chain of thought that explains the reasoning process in detail.",
                },
            ]
        )
        tokens = ctx.estimate_tokens()
        # content: "Short answer" = 12 // 3 = 4
        # reasoning: 80 chars // 3 = 26
        # overhead: 15
        # total: 4 + 26 + 15 = 45
        assert tokens == 45

    def test_reasoning_only(self):
        """Message with reasoning but empty content."""
        ctx = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning": "x" * 300,  # 300 chars = 100 tokens
                },
            ]
        )
        tokens = ctx.estimate_tokens()
        # content: "" = 0
        # reasoning: 300 // 3 = 100
        # overhead: 15
        assert tokens == 115

    def test_reasoning_ignored_if_not_string(self):
        """Non-string reasoning should be silently skipped."""
        ctx = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "Hello",
                    "reasoning": 42,  # not a string
                },
            ]
        )
        tokens = ctx.estimate_tokens()
        # content: "Hello" = 5 // 3 = 1
        # reasoning: skipped (not str)
        # overhead: 15
        assert tokens == 16

    def test_no_reasoning_key(self):
        """Messages without reasoning key should work normally."""
        ctx = TauContext(
            [
                {"role": "user", "content": "Hello"},
            ]
        )
        tokens = ctx.estimate_tokens()
        # "Hello" = 5 // 3 = 1 + 15 overhead
        assert tokens == 16


# ---------------------------------------------------------------------------
# Test 2: estimate_tokens() now counts tool call arguments
# ---------------------------------------------------------------------------


class TestEstimateTokensToolCalls:
    """Verify estimate_tokens() includes tool_calls[].function.arguments."""

    def test_multiple_tool_calls(self):
        """Multiple tool calls in one message."""
        ctx = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "grep",
                                "arguments": '{"pattern": "def"}',
                            },
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "cd", "arguments": '{"path": "/tmp"}'},
                        },
                    ],
                },
            ]
        )
        tokens = ctx.estimate_tokens()
        # H8 CJK-aware estimator now counts BOTH tool-call arguments AND
        # function names (the old test only counted arguments). Per message:
        #   _estimate_text_tokens(args1) + _estimate_text_tokens(args2)
        #   + _estimate_text_tokens("grep") + _estimate_text_tokens("cd")
        #   + 15 structural overhead = 27 (verified against current estimator).
        assert tokens == 27

    def test_no_tool_calls_key(self):
        """Messages without tool_calls key should work normally."""
        ctx = TauContext(
            [
                {"role": "assistant", "content": "No tools here"},
            ]
        )
        tokens = ctx.estimate_tokens()
        # "No tools here" = 13 // 3 = 4 + 15 overhead
        assert tokens == 19

class TestEstimateTokensOverhead:
    """Verify per-message overhead is 15 (not the old 10)."""

    def test_empty_message_overhead(self):
        """A message with empty content should still have overhead."""
        ctx = TauContext(
            [
                {"role": "user", "content": ""},
            ]
        )
        tokens = ctx.estimate_tokens()
        # content: "" = 0, overhead: 15
        assert tokens == 15

    def test_multiple_messages_overhead(self):
        """Overhead applies per message."""
        ctx = TauContext(
            [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": ""},
            ]
        )
        tokens = ctx.estimate_tokens()
        # 3 messages × 15 overhead = 45
        assert tokens == 45


# ---------------------------------------------------------------------------
# Test 4: Tool call field stripping
# ---------------------------------------------------------------------------


class TestToolCallFieldStripping:
    """Verify _ALLOWED_TOOL_CALL_FIELDS strips internal keys from tool_calls."""

    def test_allowed_fields_constant(self):
        """_ALLOWED_TOOL_CALL_FIELDS should contain only standard fields."""
        assert _ALLOWED_TOOL_CALL_FIELDS == {"id", "type", "function"}

    def test_message_status_stripped(self):
        """message_status (TauErgon internal) should be stripped."""
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test", "arguments": "{}"},
                    "message_status": "tool_calls",  # internal field
                },
            ],
        }
        # Simulate the stripping logic from agent_llm_validation.py
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                for key in list(tc.keys()):
                    if key not in _ALLOWED_TOOL_CALL_FIELDS:
                        tc.pop(key, None)
        assert "message_status" not in msg["tool_calls"][0]
        assert msg["tool_calls"][0] == {
            "id": "call_1",
            "type": "function",
            "function": {"name": "test", "arguments": "{}"},
        }

    def test_custom_field_stripped(self):
        """Any non-standard field should be stripped."""
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test", "arguments": "{}"},
                    "custom_debug": "should be removed",
                    "another_field": 42,
                },
            ],
        }
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                for key in list(tc.keys()):
                    if key not in _ALLOWED_TOOL_CALL_FIELDS:
                        tc.pop(key, None)
        assert "custom_debug" not in msg["tool_calls"][0]
        assert "another_field" not in msg["tool_calls"][0]

    def test_standard_fields_preserved(self):
        """Standard fields should survive stripping."""
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "grep", "arguments": '{"pattern": "x"}'},
                },
            ],
        }
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                for key in list(tc.keys()):
                    if key not in _ALLOWED_TOOL_CALL_FIELDS:
                        tc.pop(key, None)
        assert msg["tool_calls"][0]["id"] == "call_1"
        assert msg["tool_calls"][0]["type"] == "function"
        assert msg["tool_calls"][0]["function"]["name"] == "grep"

    def test_no_tool_calls_key(self):
        """Messages without tool_calls should not crash."""
        msg = {"role": "user", "content": "Hello"}
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                for key in list(tc.keys()):
                    if key not in _ALLOWED_TOOL_CALL_FIELDS:
                        tc.pop(key, None)
        # Should not crash
        assert msg["content"] == "Hello"


# ---------------------------------------------------------------------------
# Test 5: log_failed_api_request()
# ---------------------------------------------------------------------------


class TestLogFailedApiRequest:
    """Verify log_failed_api_request writes correct JSON to file."""

    def test_writes_file_with_log_file_prefix(self, temp_dir):
        """File should use same prefix as log_file."""
        log_file = temp_dir / "12345_20260326092700_1.audit"
        request_body = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [],
            "tool_choice": "auto",
        }
        log_failed_api_request(request_body, log_file)

        expected = temp_dir / "12345_20260326092700_1.failed_request.json"
        assert expected.exists()

        with open(expected) as f:
            data = json.load(f)
        assert data["request"]["model"] == "test-model"
        assert data["request"]["messages"][0]["content"] == "Hello"
        assert "timestamp" in data
        assert "pid" in data
        assert "ppid" in data

    def test_overwrites_previous_failure(self, temp_dir):
        """Second call should overwrite the first."""
        log_file = temp_dir / "prefix_1.audit"
        log_failed_api_request({"model": "first"}, log_file)
        log_failed_api_request({"model": "second"}, log_file)

        expected = temp_dir / "prefix_1.failed_request.json"
        with open(expected) as f:
            data = json.load(f)
        assert data["request"]["model"] == "second"

    def test_none_log_file_uses_fallback(self, temp_dir):
        """When log_file is None, should use LOG_DIR with generic name."""
        with patch("agent_session.LOG_DIR", temp_dir):
            log_failed_api_request({"model": "test"}, None)

        # Should have created a file matching the fallback pattern
        # Filename format: {ppid}_{timestamp}.failed_request.json
        files = list(temp_dir.glob("*.failed_request.json"))
        assert len(files) >= 1

        with open(files[0]) as f:
            data = json.load(f)
        assert data["request"]["model"] == "test"

    def test_handles_non_serializable_objects(self, temp_dir):
        """Non-serializable objects should be converted via default=str."""
        log_file = temp_dir / "prefix.audit"
        request_body = {
            "model": "test",
            "extra": set([1, 2, 3]),  # sets are not JSON-serializable
        }
        log_failed_api_request(request_body, log_file)

        expected = temp_dir / "prefix.failed_request.json"
        with open(expected) as f:
            data = json.load(f)
        # Should not crash, set converted to string
        assert "extra" in data["request"]

    def test_silent_on_io_error(self):
        """Should not raise on write failure."""
        log_file = Path("/nonexistent/directory/file.audit")
        # Should not raise
        log_failed_api_request({"model": "test"}, log_file)

    def test_request_body_preserved_intact(self, temp_dir):
        """Full request body including tools should be preserved."""
        log_file = temp_dir / "prefix.audit"
        request_body = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "What is Python?"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "description": "Read a file",
                        "parameters": {"type": "object"},
                    },
                },
            ],
            "tool_choice": "auto",
            "stream": False,
            "extra_body": {"top_k": 50},
        }
        log_failed_api_request(request_body, log_file)

        expected = temp_dir / "prefix.failed_request.json"
        with open(expected) as f:
            data = json.load(f)
        assert data["request"]["model"] == "gpt-4"
        assert len(data["request"]["messages"]) == 2
        assert len(data["request"]["tools"]) == 1
        assert data["request"]["tools"][0]["function"]["name"] == "file_read"
        assert data["request"]["extra_body"]["top_k"] == 50

    def test_error_field_captured(self, temp_dir):
        """Error type, message, and status_code should be recorded."""
        log_file = temp_dir / "prefix.audit"
        log_failed_api_request(
            {"model": "test-model"},
            log_file,
            error_type="BadRequestError",
            error_message="context too long",
            status_code=400,
        )

        expected = temp_dir / "prefix.failed_request.json"
        with open(expected) as f:
            data = json.load(f)
        assert data["error"]["type"] == "BadRequestError"
        assert data["error"]["message"] == "context too long"
        assert data["error"]["status_code"] == 400

    def test_error_field_defaults(self, temp_dir):
        """Error fields should default to unknown/empty/None when not provided."""
        log_file = temp_dir / "prefix.audit"
        log_failed_api_request({"model": "test-model"}, log_file)

        expected = temp_dir / "prefix.failed_request.json"
        with open(expected) as f:
            data = json.load(f)
        assert data["error"]["type"] == "unknown"
        assert data["error"]["message"] == ""
        assert data["error"]["status_code"] is None


# ---------------------------------------------------------------------------
# Test 6: _log_file parameter wired through _invoke_llm_with_retry
# ---------------------------------------------------------------------------


class TestInvokeRetryLogFile:
    """Verify _log_file parameter is threaded through and used on failure."""

    def test_log_file_written_on_timeout_exhaustion(self, temp_dir):
        """When all retries fail with timeout, failed request should be logged."""
        from agent_llm_invoke import _invoke_llm_with_retry
        from agent_llm_models import LLMCallConfig, APITimeoutError

        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = APITimeoutError("timed out")

        log_file = temp_dir / "session_1.audit"

        with pytest.raises(APITimeoutError):
            _invoke_llm_with_retry(
                mock_client,
                "test-model",
                [{"role": "user", "content": "Hello"}],
                stream=False,
                config=LLMCallConfig(max_retries=1, log_file=log_file),
            )

        expected = temp_dir / "session_1.failed_request.json"
        assert expected.exists()

        with open(expected) as f:
            data = json.load(f)
        assert data["request"]["model"] == "test-model"
        assert data["request"]["messages"][0]["content"] == "Hello"

    def test_no_log_file_when_success(self, temp_dir):
        """Successful call should NOT create a failed request file."""
        from agent_llm_invoke import _invoke_llm_with_retry
        from agent_llm_models import LLMCallConfig

        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(
                role="stop",
                message=Mock(role="assistant", content="OK response text here", tool_calls=None, reasoning_content=None),
            )
        ]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.prompt_tokens_details = None
        mock_client.chat.completions.create.return_value = mock_response

        log_file = temp_dir / "session_1.audit"

        _invoke_llm_with_retry(
            mock_client,
            "test-model",
            [{"role": "user", "content": "Hello"}],
            stream=False,
            config=LLMCallConfig(max_retries=1, log_file=log_file),
        )

        # No failed request file should exist
        failed_files = list(temp_dir.glob("*.failed_request.json"))
        assert len(failed_files) == 0

    def test_log_file_none_is_safe(self):
        """_log_file=None should not crash on failure."""
        from agent_llm_invoke import _invoke_llm_with_retry
        from agent_llm_models import LLMCallConfig, APITimeoutError

        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = APITimeoutError("timed out")

        with pytest.raises(APITimeoutError):
            _invoke_llm_with_retry(
                mock_client,
                "test-model",
                [{"role": "user", "content": "Hello"}],
                stream=False,
                config=LLMCallConfig(max_retries=1, log_file=None),
            )
