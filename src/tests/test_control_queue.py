"""Tests for the control queue infrastructure.

Tests the _control_queue and _process_control_queue() methods on the TauErgon class.
Covers all 4 command types (inject, terminate, redirect, status), error handling
(invalid JSON, unknown commands), and edge cases (empty content, multiple commands).
"""

import json
import queue
from unittest.mock import patch

import pytest

from agent_lifecycle import AgentLifecycle


@pytest.fixture
def agent(test_config):
    """Create a TauErgon instance for control queue tests.

    Note: Agent context starts with a system prompt (set by set_system() in __init__).
    """
    from agent_core import TauErgon

    agent = TauErgon(
        config=test_config,
        base_url="http://test:8000/v1",
        model="test-model",
    )
    return agent


class TestControlQueueInitialization:
    """Test that _control_queue and _parent_pid are initialized correctly."""

    def test_control_queue_exists(self, agent):
        """_control_queue attribute exists on TauErgon instances."""
        assert hasattr(agent, "_control_queue")

    def test_control_queue_is_queue(self, agent):
        """_control_queue is a queue.Queue instance."""
        assert isinstance(agent._control_queue, queue.Queue)

    def test_control_queue_maxsize(self, agent):
        """_control_queue has maxsize=100."""
        assert agent._control_queue.maxsize == 100

    def test_parent_pid_exists(self, agent):
        """_parent_pid attribute exists on TauErgon instances."""
        assert hasattr(agent, "_parent_pid")

    def test_parent_pid_initial_none(self, agent):
        """_parent_pid defaults to None."""
        assert agent._parent_pid is None


class TestProcessControlQueueInject:
    """Test the 'inject' command type."""

    def test_inject_appends_message(self, agent):
        """Inject command appends synthetic user message to context."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "role": "user", "content": "Hello"})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len + 1

    def test_inject_content_in_context(self, agent):
        """Inject command message content appears in context."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "role": "user", "content": "Test message"})
        )
        agent._process_control_queue()
        # The last message should contain the injected content
        last = list(agent.context)[-1]
        assert "Test message" in last.get("content", "")

    def test_inject_empty_content_skipped(self, agent):
        """Inject with empty content is skipped (no message added)."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "role": "user", "content": ""})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len

    def test_inject_missing_content_skipped(self, agent):
        """Inject without content field is skipped."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "role": "user"})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len

    def test_inject_default_role(self, agent):
        """Inject without role defaults to 'user' (still works)."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "No role specified"})
        )
        agent._process_control_queue()
        assert len(agent.context) > 0


class TestProcessControlQueueTerminate:
    """Test the 'terminate' command type."""

    def test_terminate_graceful_sets_force_end_turn(self, agent):
        """Graceful terminate sets force_end_turn."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "terminate", "graceful": True})
        )
        agent._process_control_queue()
        assert agent.force_end_turn == "external_terminate_graceful"

    def test_terminate_graceful_appends_summary_request(self, agent):
        """Graceful terminate appends summary request message."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "terminate", "graceful": True})
        )
        agent._process_control_queue()
        last = list(agent.context)[-1]
        assert "final summary" in last.get("content", "").lower()

    def test_terminate_graceful_default(self, agent):
        """Terminate without graceful field defaults to graceful=True."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "terminate"})
        )
        agent._process_control_queue()
        assert agent.force_end_turn == "external_terminate_graceful"

    def test_terminate_forceful_sets_exit_requested(self, agent):
        """Forceful terminate sets AgentLifecycle exit_requested flag."""
        AgentLifecycle._reset()
        agent._control_queue.put_nowait(
            json.dumps({"type": "terminate", "graceful": False})
        )
        agent._process_control_queue()
        assert AgentLifecycle.is_exit_requested() is True

    def test_terminate_forceful_does_not_set_force_end_turn(self, agent):
        """Forceful terminate does not set force_end_turn."""
        agent.force_end_turn = None
        AgentLifecycle._reset()
        agent._control_queue.put_nowait(
            json.dumps({"type": "terminate", "graceful": False})
        )
        agent._process_control_queue()
        assert agent.force_end_turn is None


class TestProcessControlQueueRedirect:
    """Test the 'redirect' command type."""

    def test_redirect_clears_context(self, agent):
        """Redirect clears the context (preserving system prompt)."""
        agent.context.append_user("Original task")
        agent.context.append_assistant("Working on it...")
        initial_len = len(agent.context)
        assert initial_len > 2  # system + user + assistant

        agent._control_queue.put_nowait(
            json.dumps({"type": "redirect", "task": "New task"})
        )
        agent._process_control_queue()
        # Context should have: system prompt + new task message
        assert len(agent.context) == 2

    def test_redirect_adds_new_task(self, agent):
        """Redirect adds the new task as a user message."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "redirect", "task": "Analyze this code"})
        )
        agent._process_control_queue()
        # Last message should be the new task (system prompt is first)
        last = list(agent.context)[-1]
        assert "Analyze this code" in last.get("content", "")

    def test_redirect_resets_loop_detector(self, agent):
        """Redirect resets the loop detector."""
        agent.context.append_user("First message")
        agent.context.append_assistant("First response")
        agent.context.append_user("Second message")
        agent.context.append_assistant("Second response")

        with patch.object(agent.loop_detector, "reset") as mock_reset:
            agent._control_queue.put_nowait(
                json.dumps({"type": "redirect", "task": "New task"})
            )
            agent._process_control_queue()
            mock_reset.assert_called_once()

    def test_redirect_empty_task_skipped(self, agent):
        """Redirect with empty task is skipped."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "redirect", "task": ""})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len

    def test_redirect_missing_task_skipped(self, agent):
        """Redirect without task field is skipped."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "redirect"})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len

    def test_redirect_clears_stale_state(self, agent):
        """Redirect clears _pending_a2a_responses, _pending_a2a_chunks, _current_a2a_request_id, and _queued_images."""
        agent._pending_a2a_responses = {"stale": "data"}
        agent._pending_a2a_chunks = {"stale": [{"type": "tool_call"}]}
        agent._current_a2a_request_id = "stale-request"
        agent._queued_images = [("fake", "data", "uri")]

        agent._control_queue.put_nowait(
            json.dumps({"type": "redirect", "task": "New task"})
        )
        agent._process_control_queue()

        assert agent._pending_a2a_responses == {}
        assert agent._pending_a2a_chunks == {}
        assert agent._current_a2a_request_id is None
        assert agent._queued_images == []


class TestProcessControlQueueStatus:
    """Test the 'status' command type."""

    def test_status_does_not_crash(self, agent):
        """Status command completes without raising an exception."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "status"})
        )
        # Should not raise
        agent._process_control_queue()

    def test_status_preserves_context(self, agent):
        """Status command does not modify the context."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "status"})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len


class TestProcessControlQueueErrorHandling:
    """Test error handling for invalid inputs."""

    def test_invalid_json_handled(self, agent):
        """Invalid JSON in queue does not raise an exception."""
        agent._control_queue.put_nowait("not valid json {{{")
        # Should not raise
        agent._process_control_queue()

    def test_unknown_command_type_handled(self, agent):
        """Unknown command type does not raise an exception."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "unknown_command"})
        )
        # Should not raise
        agent._process_control_queue()

    def test_unknown_command_type_preserves_context(self, agent):
        """Unknown command type does not modify the context."""
        initial_len = len(agent.context)
        agent._control_queue.put_nowait(
            json.dumps({"type": "foobar"})
        )
        agent._process_control_queue()
        assert len(agent.context) == initial_len

    def test_empty_string_in_queue_handled(self, agent):
        """Empty string in queue is handled gracefully."""
        agent._control_queue.put_nowait("")
        # Should not raise
        agent._process_control_queue()

    def test_missing_type_field_handled(self, agent):
        """Command without 'type' field is handled as unknown command."""
        agent._control_queue.put_nowait(json.dumps({"foo": "bar"}))
        # Should not raise
        agent._process_control_queue()


class TestProcessControlQueueMultiple:
    """Test processing multiple commands from the queue."""

    def test_multiple_commands_processed(self, agent):
        """Multiple commands are processed in order with proper alternation."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "First"})
        )
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "Second"})
        )
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "Third"})
        )

        initial_len = len(agent.context)
        agent._process_control_queue()
        # First inject: system -> synthetic user (no bridge needed)
        # Second inject: user -> assistant bridge + synthetic user
        # Third inject: user -> assistant bridge + synthetic user
        # Total: 1 (first) + 2 (second) + 2 (third) = 5 new messages
        assert len(agent.context) == initial_len + 5

        # Verify proper alternation — no consecutive user messages
        roles = [m["role"] for m in agent.context.get_messages()]
        for i in range(1, len(roles)):
            assert not (roles[i] == "user" and roles[i - 1] == "user"), (
                f"Consecutive user messages at index {i-1},{i}"
            )

    def test_multiple_mixed_commands_processed(self, agent):
        """Mixed command types are all processed."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "Message"})
        )
        agent._control_queue.put_nowait(
            json.dumps({"type": "status"})
        )
        agent._control_queue.put_nowait(
            json.dumps({"type": "terminate", "graceful": True})
        )

        agent._process_control_queue()

        # Inject added a message
        assert len(agent.context) > 0
        # Terminate set force_end_turn
        assert agent.force_end_turn == "external_terminate_graceful"

    def test_invalid_json_in_middle_does_not_stop_processing(self, agent):
        """Invalid JSON in the middle of the queue doesn't stop processing."""
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "Before"})
        )
        agent._control_queue.put_nowait("invalid json")
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "After"})
        )

        initial_len = len(agent.context)
        agent._process_control_queue()
        # Both valid injects should be processed despite invalid JSON in middle
        # First inject: system -> synthetic user (no bridge needed)
        # Second inject: user -> assistant bridge + synthetic user
        # Total: 1 (first) + 2 (second) = 3 new messages
        assert len(agent.context) == initial_len + 3

    def test_empty_queue_no_op(self, agent):
        """Processing an empty queue is a no-op."""
        initial_len = len(agent.context)
        agent.force_end_turn = None
        AgentLifecycle._reset()

        agent._process_control_queue()

        assert len(agent.context) == initial_len
        assert agent.force_end_turn is None
        assert AgentLifecycle.is_exit_requested() is False

    def test_redirect_then_inject(self, agent):
        """Redirect clears context, then subsequent inject adds to clean context."""
        agent.context.append_user("Original task")

        agent._control_queue.put_nowait(
            json.dumps({"type": "redirect", "task": "New task"})
        )
        agent._control_queue.put_nowait(
            json.dumps({"type": "inject", "content": "Additional info"})
        )

        agent._process_control_queue()

        # Context should have: system + new task (user) + assistant bridge + injected message
        # The inject adds a bridge because context ends with user (the redirect task)
        assert len(agent.context) == 4
        # Verify original task is gone
        for msg in agent.context:
            assert "Original task" not in msg.get("content", "")
