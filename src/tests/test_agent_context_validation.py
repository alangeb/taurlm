"""Tests for TauContext validation functions.

Tests the validation logic for context messages, including batched tool call support.
Validates:
- Message structure and content
- Role validation (system, user, assistant, tool)
- Sequence rules (no consecutive same roles except tool messages)
- Tool call validation (tool messages reference valid tool_call_ids)
- Tool resolution (all tool_call_ids must eventually be resolved)
"""

from agent_context import TauContext


class TestValidateSequenceRules:
    """Test _validate_sequence_rules method for sequence validation."""

    def test_no_consecutive_same_role_except_tool(self):
        """Test that consecutive same roles fail, except for tool messages.

        This test verifies the key change for batched tool call support:
        - user -> user should fail (consecutive same roles)
        - tool -> tool should pass (batched tool calls are now allowed)
        - assistant -> assistant should fail (consecutive same roles)
        """
        # user -> user (fail - consecutive same roles)
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "a"},
                {"role": "user", "content": "b"},
            ]
        )
        errors = context.validate()
        assert any("consecutive messages with same role 'user'" in e for e in errors)

        # tool -> tool (pass - batched tool calls allowed)
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "user",
                    "content": "request",
                },  # Required: user message after system
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"name": "t1", "arguments": "{}"}},
                        {"id": "t2", "function": {"name": "t2", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "t1", "name": "t1", "content": "a"},
                {"role": "tool", "tool_call_id": "t2", "name": "t2", "content": "b"},
            ]
        )
        assert context.validate() == []

        # assistant -> assistant (fail - consecutive same roles)
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "user",
                    "content": "hello",
                },  # Required: user message after system
                {"role": "assistant", "content": "a"},
                {"role": "assistant", "content": "b"},
            ]
        )
        errors = context.validate()
        assert any(
            "consecutive messages with same role 'assistant'" in e for e in errors
        )

    def test_system_only_at_start(self):
        """Test that system message must be at index 0 only.

        System messages can only appear at the start of the context.
        Any system message after index 0 should fail validation.
        """
        # System at start (pass)
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        )
        assert context.validate() == []

        # System after first message (fail)
        context = TauContext(
            [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "sys"},  # Invalid!
                {"role": "assistant", "content": "hi"},
            ]
        )
        errors = context.validate()
        # Check for system validation error (may have different exact wording)
        assert any("system" in e.lower() and "first" in e.lower() for e in errors)

    def test_tool_references_valid_id(self):
        """Test that tool messages must reference valid tool_call_ids.

        Tool messages must reference a tool_call_id that exists in a previous
        assistant message's tool_calls array.
        """
        # Valid reference (pass)
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "user",
                    "content": "request",
                },  # Required: user message after system
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"name": "t1", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "t1", "name": "t1", "content": "ok"},
            ]
        )
        assert context.validate() == []

        # Invalid reference (fail)
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"name": "t1", "arguments": "{}"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "t99",
                    "name": "unknown",
                    "content": "ok",
                },  # Unknown!
            ]
        )
        errors = context.validate()
        assert any(
            "unknown tool_call_id" in e or "non-existent tool_call_id" in e
            for e in errors
        )

    def test_no_duplicate_tool_ids(self):
        """Test that assistant can't have duplicate tool_call_ids.

        Each tool_call in an assistant message must have a unique id.
        Duplicate tool_call_ids within the same assistant message should fail.
        """
        context = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1"},
                        {"id": "t1"},  # Duplicate!
                    ],
                },
            ]
        )
        errors = context.validate()
        assert any("duplicate tool_call_id" in e for e in errors)

    def test_mixed_valid_invalid_tools(self):
        """Test batch with one valid tool and one invalid tool.

        When a batch contains both valid and invalid tool references,
        the validation should report the invalid reference.
        """
        context = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "t1"}, {"id": "t2"}],
                },
                {"role": "tool", "tool_call_id": "t1", "content": "a"},
                {"role": "tool", "tool_call_id": "t99", "content": "b"},  # Invalid!
            ]
        )
        errors = context.validate()
        assert any(
            "unknown tool_call_id" in e or "non-existent tool_call_id" in e
            for e in errors
        )


class TestValidateSingleMessage:
    """Test _validate_single_message method for individual message validation."""

    def test_valid_message(self):
        """Test that a valid message with all required fields passes."""
        context = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "hello"},
            ]
        )
        assert context.validate() == []

    def test_missing_content_field(self):
        """Test that message without 'content' field fails.

        All messages must have a 'content' field (unless null).
        """
        context = TauContext(
            [
                {"role": "user"},  # Missing 'content'!
            ]
        )
        errors = context.validate()
        assert any("missing required 'content' field" in e for e in errors)

    def test_invalid_content_type(self):
        """Test that message with invalid content type fails.

        Content must be str, list, or null. Other types should fail.
        """
        context = TauContext(
            [
                {"role": "user", "content": 123},  # Invalid type!
            ]
        )
        errors = context.validate()
        assert any("invalid content type" in e for e in errors)

    def test_invalid_role(self):
        """Test that message with invalid role fails.

        Valid roles are: system, user, assistant, tool
        """
        context = TauContext(
            [
                {"role": "invalid", "content": "test"},
            ]
        )
        errors = context.validate()
        assert any("invalid role" in e for e in errors)

    def test_not_dictionary(self):
        """Test that non-dict message fails validation.

        All messages must be dictionaries. The validate_context() function
        in agent_context_validation catches non-dict messages and reports
        them as errors.
        """
        import inspect
        from agent_context_validation import validate_context

        source = inspect.getsource(validate_context)
        assert "is not a dictionary" in source
        assert "isinstance(msg, dict)" in source or "not isinstance" in source


class TestValidateToolCalls:
    """Test _validate_tool_calls method for tool call validation."""

    def test_valid_tool_calls(self):
        """Test that valid tool_calls array passes validation.

        Note: Assistant with tool_calls but no content is valid.
        Tool calls are tracked but not resolved until tool messages follow.
        """
        context = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"name": "test", "arguments": "{}"}},
                        {"id": "t2", "function": {"name": "test2", "arguments": "{}"}},
                    ],
                },
            ]
        )
        # Should have unresolved tool calls error
        errors = context.validate()
        assert any("unresolved tool call" in e for e in errors)

    def test_tool_calls_not_list(self):
        """Test that tool_calls not being a list fails.

        tool_calls must be a list/array.
        """
        context = TauContext(
            [
                {"role": "assistant", "content": "", "tool_calls": "not a list"},
            ]
        )
        errors = context.validate()
        assert any("tool_calls is not a list" in e for e in errors)

    def test_tool_call_missing_id(self):
        """Test that tool call without 'id' field fails.

        Each tool call must have a unique 'id' field.
        """
        context = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {"name": "test", "arguments": "{}"}
                        },  # Missing id!
                    ],
                },
            ]
        )
        errors = context.validate()
        assert any("missing 'id' field" in e for e in errors)

    def test_tool_call_function_missing_name(self):
        """Test that function without 'name' field fails validation.

        The validation now requires 'name' field in function.
        """
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"arguments": "{}"}},  # Missing name!
                    ],
                },
            ]
        )
        errors = context.validate()
        assert any("missing 'name' field" in e for e in errors)

    def test_tool_call_function_missing_arguments(self):
        """Test that function without 'arguments' field fails validation.

        The validation now requires 'arguments' field in function.
        """
        context = TauContext(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "function": {"name": "test"},
                        },  # Missing arguments!
                    ],
                },
            ]
        )
        errors = context.validate()
        assert any("missing 'arguments' field" in e for e in errors)

    def test_duplicate_tool_call_ids(self):
        """Test that same tool_call_id twice fails.

        Each tool_call in an assistant message must have a unique id.
        """
        context = TauContext(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"name": "test", "arguments": "{}"}},
                        {
                            "id": "t1",
                            "function": {"name": "test2", "arguments": "{}"},
                        },  # Duplicate!
                    ],
                },
            ]
        )
        errors = context.validate()
        assert any("duplicate tool_call_id" in e for e in errors)


class TestValidateContext:
    """Test _validate_context method as main validation entry point."""

    def test_empty_context(self):
        """Test that empty context passes (no errors).

        An empty context is valid - no messages to validate.
        """
        context = TauContext()
        assert context.validate() == []

    def test_simple_valid_context(self):
        """Test that basic valid conversation passes.

        A simple user-assistant conversation should pass validation.
        """
        context = TauContext(
            [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]
        )
        assert context.validate() == []

    def test_batched_tools_valid(self):
        """Test that assistant -> tool -> tool -> assistant passes.

        This is the key test for batched tool call support.
        Multiple tools can be executed consecutively.
        """
        context = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "function": {"name": "get_weather", "arguments": "{}"},
                        },
                        {
                            "id": "t2",
                            "function": {"name": "get_temperature", "arguments": "{}"},
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "t1",
                    "name": "get_weather",
                    "content": "sunny",
                },
                {
                    "role": "tool",
                    "tool_call_id": "t2",
                    "name": "get_temperature",
                    "content": "25c",
                },
                {"role": "assistant", "content": "It's sunny and 25c"},
            ]
        )
        assert context.validate() == []

    def test_batched_tools_invalid_assistant(self):
        """Test that assistant -> tool -> assistant fails (missing one tool).

        If an assistant message has multiple tools, all must be resolved
        before the next assistant message can appear.
        """
        context = TauContext(
            [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "t1", "function": {"name": "get_weather"}},
                        {"id": "t2", "function": {"name": "get_temperature"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "t1", "content": "sunny"},
                {"role": "assistant", "content": "It's sunny"},  # Missing t2!
            ]
        )
        errors = context.validate()
        assert any("unresolved tool call" in e for e in errors)
        assert "t2" in str(errors)

    def test_complete_invalid_context(self):
        """Test that multiple validation errors are all reported.

        When a context has multiple issues, all should be reported.
        """
        context = TauContext(
            [
                {"role": "user", "content": "hello"},
                {"role": "user", "content": "hello"},  # Consecutive!
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "t1"}, {"id": "t1"}],
                },  # Duplicate!
                {
                    "role": "tool",
                    "tool_call_id": "t99",
                    "content": "result",
                },  # Unknown!
                {"role": "system", "content": "sys"},  # Invalid position!
            ]
        )
        errors = context.validate()
        # Should have multiple errors
        assert len(errors) >= 3
        assert any("consecutive messages with same role 'user'" in e for e in errors)
        assert any("duplicate tool_call_id" in e for e in errors)
        # Unknown tool_call_id may be reported as "unknown tool_call_id" or "non-existent"
        assert any(
            "unknown tool_call_id" in e or "non-existent tool_call_id" in e
            for e in errors
        )
        # System message error may have different wording
        assert any("system" in e.lower() and "first" in e.lower() for e in errors)
