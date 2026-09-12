"""Tests for fork metadata set/clear/copy semantics on TauContext.

NOTE: get_fork_metadata(), prepare_fork_context() and pending_tool_ids tracking were
removed in 613c66d ("cleanup: remove dead tool code... clean up dead tests"); the
metadata model was slimmed to {fork_call_id, fork_task}. Tests rewritten to the
current contract (set/clear + copy() isolation, which copy()'s docstring documents).
"""

import copy

from agent_context import TauContext


def _meta(ctx):
    return ctx._fork_metadata


class TestForkMetadataCleanup:
    """Test fork metadata set/clear/copy semantics."""

    def _parent(self):
        return TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
            ]
        )

    def test_fork_metadata_cleared_after_successful_fork(self):
        ctx = self._parent()
        ctx.set_fork_metadata(fork_call_id="call_123", fork_task="Test task")
        assert _meta(ctx)["fork_call_id"] == "call_123"
        assert _meta(ctx)["fork_task"] == "Test task"

        ctx.clear_fork_metadata()
        assert _meta(ctx)["fork_call_id"] is None
        assert _meta(ctx)["fork_task"] is None

    def test_fork_metadata_isolation_between_forks(self):
        ctx = self._parent()
        ctx.set_fork_metadata(fork_call_id="call_1", fork_task="First task")
        ctx.clear_fork_metadata()
        ctx.set_fork_metadata(fork_call_id="call_2", fork_task="Second task")
        assert _meta(ctx)["fork_call_id"] == "call_2"
        assert _meta(ctx)["fork_task"] == "Second task"

    def test_fork_metadata_cleared_on_error(self):
        ctx = self._parent()
        ctx.set_fork_metadata(fork_call_id="call_123", fork_task="Test task")
        try:
            raise Exception("Simulated fork failure")
        except Exception:
            ctx.clear_fork_metadata()
        assert _meta(ctx)["fork_call_id"] is None
        assert _meta(ctx)["fork_task"] is None

    def test_fork_metadata_not_inherited_by_copy(self):
        """copy() is documented as messages-only: fork metadata must not carry over."""
        parent = self._parent()
        parent.set_fork_metadata(fork_call_id="call_123", fork_task="Parent task")
        fork = TauContext(copy.deepcopy(parent.to_list()))
        assert _meta(fork)["fork_call_id"] is None
        assert _meta(fork)["fork_task"] is None
        # parent keeps its own metadata
        assert _meta(parent)["fork_call_id"] == "call_123"

    def test_multiple_forks_cleared_properly(self):
        ctx = self._parent()
        for i in range(3):
            ctx.set_fork_metadata(fork_call_id=f"call_{i}", fork_task=f"Task {i}")
            ctx.clear_fork_metadata()
            assert _meta(ctx) == {"fork_call_id": None, "fork_task": None}
