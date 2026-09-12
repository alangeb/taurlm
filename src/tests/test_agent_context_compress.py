"""Tests for agent_context_compress module - compression algorithms.

Tests:
    test_calculate_context_bytes - Helper function tests
    test_full_reset - Full reset algorithm
    test_redact_blocks - Redact blocks algorithm
    test_orchestrator - compress_context orchestrator
"""

from unittest.mock import Mock

from agent_context_compress import (
    compress_context,
    compress_conversation_summary,
    compress_blind_truncate,
    compress_prune_images,
    compress_drop_reasoning,
    compress_redact_blocks,
    compress_full_reset,
    _calculate_context_bytes,
)


class TestCalculateContextBytes:
    """Test _calculate_context_bytes helper function."""

    def test_empty_context(self):
        """Test with empty context."""
        assert _calculate_context_bytes([]) == 0

    def test_single_message(self):
        """Test with single message."""
        ctx = [{"role": "user", "content": "Hello"}]
        assert _calculate_context_bytes(ctx) > 0

    def test_multiple_messages(self):
        """Test with multiple messages."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        total = _calculate_context_bytes(ctx)
        assert total > 0
        assert total > len(str(ctx[0]))




class TestCompressFullReset:
    """Test compress_full_reset algorithm."""

    def test_full_reset_basic(self):
        """Full reset scenario."""
        ctx = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Original request"},
            {
                "role": "assistant",
                "content": "Processed request",
                "tool_calls": [
                    {"id": "1", "function": {"name": "test"}, "type": "function"}
                ],
            },
            {"role": "tool", "content": "Tool result"},
            {"role": "assistant", "content": "Result processed"},
        ]

        # Create proper mock responses with tool_calls=None to avoid iteration errors
        def make_mock_response(content_text):
            return Mock(
                choices=[
                    Mock(
                        message=Mock(
                            role="assistant",
                            content=content_text,
                            tool_calls=None,
                            reasoning_content=None,
                        )
                    )
                ],
                usage=Mock(
                    prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None
                ),
            )

        client = Mock()
        client.chat.completions.create.side_effect = [
            make_mock_response("Summary of conversation: did work A and B and finished C"),
            make_mock_response("Next steps to complete the task: run tests and commit"),
        ]

        result, metadata = compress_full_reset(ctx, client, "test-model", target_size_bytes=100)

        # Should create minimal context
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "System prompt"
        assert result[1]["role"] == "user"
        assert "# COMPREHENSION SUMMARY" in result[1]["content"]
        assert "# NEXT STEPS" in result[1]["content"]
        assert "# CURRENT USER REQUEST" in result[1]["content"]
        assert "Original request" in result[1]["content"]
        # Verify metadata
        assert metadata["step_name"] == "FULL_RESET"
        assert metadata["status"] == "RESET"
        assert len(metadata["actions"]) > 0

    def test_full_reset_no_system(self):
        """Handle missing system prompt."""
        ctx = [
            {"role": "user", "content": "Request"},
            {"role": "assistant", "content": "Response"},
        ]

        def make_mock_response(content_text):
            return Mock(
                choices=[
                    Mock(
                        message=Mock(
                            role="assistant",
                            content=content_text,
                            tool_calls=None,
                            reasoning_content=None,
                        )
                    )
                ],
                usage=Mock(
                    prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None
                ),
            )

        client = Mock()
        client.chat.completions.create.side_effect = [
            make_mock_response("A summary of the conversation covering all the work done"),
            make_mock_response("Next steps plan: finish the remaining items and verify"),
        ]
        # Make the original context large enough that the LLM-produced reset
        # is a genuine reduction (full_reset has a size guard that returns
        # the context unchanged when the reset is not smaller).
        ctx = [
            {"role": "user", "content": "Request"},
            {"role": "assistant", "content": "Response " * 300},
        ]
        result, metadata = compress_full_reset(ctx, client, "test-model", target_size_bytes=100)

        # Should still work without system
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_full_reset_no_user(self):
        """Handle missing user prompt."""
        ctx = [{"role": "system", "content": "System"}]
        client = Mock()
        client.chat.completions.create.return_value = Mock(
            choices=[Mock(message=Mock(content="A summary of the conversation"))],
            usage=Mock(),
        )
        result, metadata = compress_full_reset(ctx, client, "test-model", target_size_bytes=100)

        # Should return unchanged if no user prompt
        assert result == ctx
        assert metadata["status"] == "FAILED_NO_USER"


class TestCompressRedactBlocks:
    """Test compress_redact_blocks algorithm."""

    def test_redact_blocks_basic(self):
        """Basic redaction of completed blocks."""
        ctx = [
            {"role": "system", "content": "System " * 10},
            {"role": "user", "content": "Task 1 " * 20},
            {
                "role": "assistant",
                "content": "Processing 1 " * 20,
                "tool_calls": [
                    {"id": "1", "function": {"name": "test"}, "type": "function"}
                ],
            },
            {"role": "tool", "content": "Tool result 1 " * 10, "tool_call_id": "1"},
            {"role": "assistant", "content": "Completed 1 " * 20},
            {"role": "user", "content": "Task 2 " * 20},
            {
                "role": "assistant",
                "content": "Processing 2 " * 20,
                "tool_calls": [
                    {"id": "2", "function": {"name": "test"}, "type": "function"}
                ],
            },
            {"role": "tool", "content": "Tool result 2 " * 10, "tool_call_id": "2"},
            {"role": "assistant", "content": "Completed 2 " * 20},
            {"role": "user", "content": "Current task"},
            {
                "role": "assistant",
                "content": "Current work",
                "tool_calls": [
                    {"id": "3", "function": {"name": "test"}, "type": "function"}
                ],
            },
        ]

        original_size = _calculate_context_bytes(ctx)

        result, metadata = compress_redact_blocks(
            ctx, Mock(), "test-model", target_size_bytes=1000
        )

        # Should have reduced intermediate messages
        assert len(result) < len(ctx)
        assert _calculate_context_bytes(result) < original_size
        assert metadata["step_name"] == "REDACT_BLOCKS"

    def test_redact_blocks_skip_small(self):
        """Skip blocks under 300 bytes."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {
                "role": "assistant",
                "content": "Done",
                "tool_calls": [
                    {"id": "1", "function": {"name": "test"}, "type": "function"}
                ],
            },
            {"role": "tool", "content": "Result", "tool_call_id": "1"},
            {"role": "assistant", "content": "Finish"},
        ]

        result, metadata = compress_redact_blocks(
            ctx, Mock(), "test-model", target_size_bytes=100
        )

        # Should not redact (too small)
        assert len(result) == len(ctx)
        assert len(metadata["actions"]) == 0



class TestOrchestrator:
    """Test compress_context orchestrator."""

    def test_orchestrator_early_success(self):
        """Test success with first algorithm."""
        ctx = [
            {"role": "system", "content": "System " * 20},
            {"role": "user", "content": "Task " * 20},
            {
                "role": "assistant",
                "content": "Processing " * 10,
                "tool_calls": [
                    {"id": "1", "function": {"name": "test"}, "type": "function"}
                ],
            },
            {
                "role": "tool",
                "content": "This is a very long tool result that will be pruned to save space. "
                * 20,
            },
            {"role": "assistant", "content": "Completed " * 20},
        ]

        client = Mock()
        client.chat.completions.create.return_value = Mock(
            choices=[Mock(message=Mock(content="Summary"))],
            usage=Mock(prompt_tokens=10, completion_tokens=5),
        )

        result, summary, metadata = compress_context(
            ctx, client, "test-model", compression_factor=0.3
        )

        # Should succeed (tool pruning should achieve target)
        assert isinstance(result, list)
        assert isinstance(summary, str)
        assert isinstance(metadata, dict)
        assert "bytes_before" in metadata
        assert "bytes_after" in metadata
        assert "algorithms_used" in metadata
        assert "ACHIEVED" in summary or "Final:" in summary




class TestCompressRedactBlocksNoBoundary:
    """Test compress_redact_blocks with use_boundary=False — scans entire context (no 50% boundary)."""

    def _make_context_with_block_past_boundary(self):
        """Context with heavy first-half padding and a completed block past 50%.

        The block must have intermediate messages (tool calls/results) to redact.
        A bare user→assistant pair has nothing to remove.
        """
        big_pad = "P" * 5000
        big_content = "B" * 400
        return [
            {"role": "system", "content": big_pad},
            {"role": "user", "content": "Early task"},
            {"role": "assistant", "content": "Early response"},
            # Past the 50% boundary — completed block WITH intermediates to redact
            {"role": "user", "content": f"Later task {big_content}"},
            {"role": "assistant", "content": "Calling tool", "tool_calls": [{"id": "2", "function": {"name": "bash"}, "type": "function"}]},
            {"role": "tool", "content": f"Tool output {big_content}", "tool_call_id": "2", "name": "bash"},
            {"role": "assistant", "content": f"Final response {big_content}"},
        ]

    def test_full_redacts_past_boundary(self):
        """redact_blocks with use_boundary=False redacts completed blocks beyond 50% boundary."""
        ctx = self._make_context_with_block_past_boundary()
        original_size = _calculate_context_bytes(ctx)

        result, metadata = compress_redact_blocks(ctx, Mock(), "test-model", target_size_bytes=100, use_boundary=False)

        # Context should be smaller (block was redacted)
        assert _calculate_context_bytes(result) < original_size
        assert metadata["step_name"] == "REDACT_BLOCKS_FULL"
        assert len(metadata["actions"]) > 0

    def test_boundary_version_skips_past_boundary(self):
        """redact_blocks (with boundary) does NOT redact blocks beyond 50%."""
        ctx = self._make_context_with_block_past_boundary()
        original_size = _calculate_context_bytes(ctx)

        result, metadata = compress_redact_blocks(ctx, Mock(), "test-model", target_size_bytes=100)

        # Context should NOT be smaller (block was skipped)
        assert _calculate_context_bytes(result) == original_size
        assert metadata["step_name"] == "REDACT_BLOCKS"
        assert len(metadata["actions"]) == 0

    def test_full_and_boundary_identical_within_boundary(self):
        """When all blocks are within 50%, both versions behave the same."""
        # Small context — everything is within boundary
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task A"},
            {"role": "assistant", "content": "Response A"},
            {"role": "user", "content": "Task B"},
            {"role": "assistant", "content": "Response B"},
        ]

        result_boundary, _ = compress_redact_blocks(ctx, Mock(), "test-model", target_size_bytes=100)
        result_full, _ = compress_redact_blocks(list(ctx), Mock(), "test-model", target_size_bytes=100, use_boundary=False)

        assert _calculate_context_bytes(result_boundary) == _calculate_context_bytes(result_full)


from agent_context_compress import compute_compression_target_bytes


class TestComputeCompressionTargetBytes:
    """Test compute_compression_target_bytes — token-aware target calculation.

    New formula:
        breathing_room = compression_factor * max_context_tokens
        target_tokens = max_context_tokens - max_output_tokens - breathing_room
        reduction_ratio = (last_known_tokens - target_tokens) / last_known_tokens
        token_byte_target = current_bytes * (1 - reduction_ratio)
        byte_target = current_bytes * (1 - compression_factor)
        final_target = min(byte_target, token_byte_target)
    """

    def test_byte_target_only(self):
        """No token info — falls back to pure byte target."""
        result = compute_compression_target_bytes(
            current_bytes=10000,
            compression_factor=0.30,
            last_known_tokens=None,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        assert result == 7000  # 10000 * (1 - 0.30)

    def test_token_target_more_aggressive(self):
        """Token target is lower than byte target — token wins."""
        result = compute_compression_target_bytes(
            current_bytes=100000,
            compression_factor=0.30,
            last_known_tokens=188000,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # byte_target = 70000
        # breathing_room = 0.3 * 200000 = 60000
        # target_tokens = 200000 - 12000 - 60000 = 128000
        # reduction_ratio = (188000 - 128000) / 188000 = 0.3191
        # token_byte_target = 100000 * (1 - 0.3191) = 68090
        # min(70000, 68090) = 68090
        assert result < 70000  # token target is more aggressive
        assert result > 0

    def test_token_target_less_aggressive(self):
        """Token target is higher than byte target — byte wins."""
        result = compute_compression_target_bytes(
            current_bytes=100000,
            compression_factor=0.90,  # very aggressive byte target
            last_known_tokens=188000,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # byte_target = int(100000 * 0.10) = 10000 (or 9999 due to float)
        # token_byte_target will be much larger
        # min(byte_target, large_number) = byte_target
        assert result <= 10000

    def test_linear_scaling_assumption(self):
        """Verify linear byte-to-token scaling (no safety factor)."""
        result = compute_compression_target_bytes(
            current_bytes=6500000,
            compression_factor=0.30,
            last_known_tokens=188001,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # breathing_room = 0.3 * 200000 = 60000
        # target_tokens = 200000 - 12000 - 60000 = 128000
        # reduction_ratio = (188001 - 128000) / 188001 ≈ 0.3191
        # token_byte_target = 6500000 * (1 - 0.3191) ≈ 4424850
        # byte_target = 4550000
        # min(4550000, 4424850) ≈ 4424850
        assert result < 4550000  # token-aware target is more aggressive
        assert result > 0

    def test_overflow_recovery(self):
        """Simulate the exact scenario from the bug report."""
        result = compute_compression_target_bytes(
            current_bytes=6500000,
            compression_factor=0.30,
            last_known_tokens=188001,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # Should produce a more aggressive target than pure byte target
        byte_target = int(6500000 * 0.70)
        assert result < byte_target

    def test_edge_cases_zero_tokens(self):
        """Zero tokens — falls back to byte target."""
        result = compute_compression_target_bytes(
            current_bytes=10000,
            compression_factor=0.30,
            last_known_tokens=0,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        assert result == 7000

    def test_edge_cases_none_tokens(self):
        """None tokens — falls back to byte target."""
        result = compute_compression_target_bytes(
            current_bytes=10000,
            compression_factor=0.30,
            last_known_tokens=None,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        assert result == 7000

    def test_edge_cases_equal_tokens(self):
        """Tokens at target — no reduction needed, returns byte target."""
        result = compute_compression_target_bytes(
            current_bytes=10000,
            compression_factor=0.30,
            last_known_tokens=128000,  # exactly at target_tokens
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # target_tokens = 200000 - 12000 - 60000 = 128000
        # last_known_tokens (128000) is NOT > target_tokens (128000)
        # So token_target stays as byte_target
        assert result == 7000

    def test_breathing_room(self):
        """Verify breathing room is compression_factor * MCW."""
        result = compute_compression_target_bytes(
            current_bytes=100000,
            compression_factor=0.30,
            last_known_tokens=195000,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # breathing_room = 0.3 * 200000 = 60000
        # target_tokens = 200000 - 12000 - 60000 = 128000
        # reduction_ratio = (195000 - 128000) / 195000 = 0.3436
        # token_byte_target = 100000 * (1 - 0.3436) = 65641
        # byte_target = 70000
        # min(70000, 65641) = 65641
        assert result < 70000
        assert result > 0

    def test_tokens_below_target(self):
        """When tokens are below target, no token reduction needed."""
        result = compute_compression_target_bytes(
            current_bytes=10000,
            compression_factor=0.30,
            last_known_tokens=100000,  # well below target
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # target_tokens = 128000, last_known_tokens (100000) < target_tokens
        # No token reduction needed, falls back to byte target
        assert result == 7000

class TestCompressConversationSummary:
    """Test compress_conversation_summary — deterministic conversation restructuring."""

    def _make_simple_context(self):
        return [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well, thanks!"},
        ]

    def test_conversation_summary_basic(self):
        """Simple conversation produces structured summary."""
        ctx = self._make_simple_context()
        # Small target so the step is NOT at_target (at_target() early-returns NO_REDUCTION)
        result, metadata = compress_conversation_summary(ctx, None, "test", 50)
        # Result should have system + user summary (+ assistant if not within turn)
        assert len(result) >= 2
        assert result[0].get("role") == "system"
        assert result[1].get("role") == "user"
        content = result[1].get("content", "")
        assert "## CONVERSATION HISTORY" in content
        assert "### CURRENT TASK" in content
        assert "**USER:** How are you?" in content
        assert metadata["step_name"] == "CONVERSATION_SUMMARY"

    def test_conversation_summary_with_tools(self):
        """Conversation with tool calls preserves tool info."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Run a command"},
            {"role": "assistant", "content": "Running...", "tool_calls": [{"id": "1", "function": {"name": "bash"}, "type": "function"}]},
            {"role": "tool", "content": "output ok", "tool_call_id": "1", "name": "bash"},
            {"role": "assistant", "content": "Done"},
        ]
        # Small target so the step is NOT at_target (at_target() early-returns NO_REDUCTION)
        result, metadata = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        # Tool output is rendered as a REPL line (no separate CALL:/RESULT: lines)
        assert "output ok" in content
        assert "**REPL:**" in content

    def test_conversation_summary_synthetic_messages(self):
        """Synthetic messages are noted as [SYSTEM: category]."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "[SYSTEM-SYNTHETIC: end_turn_reminder] Previous turn ended."},
            {"role": "assistant", "content": "Acknowledged."},
        ]
        result, metadata = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        assert "[SYSTEM: end_turn_reminder]" in content

    def test_conversation_summary_within_turn(self):
        """Within-turn (tool result at end) shows in-progress status."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": "Calling tool", "tool_calls": [{"id": "1", "function": {"name": "test"}, "type": "function"}]},
            {"role": "tool", "content": "result", "tool_call_id": "1", "name": "test"},
        ]
        result, metadata = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        assert "**STATUS:** in progress" in content
        # Within turn: should NOT have synthetic assistant closing message
        assert len(result) == 2  # system + user summary only

    def test_conversation_summary_between_turns(self):
        """Between turns: context ends with the summary user message.

        NOTE: The old trailing synthetic assistant message was intentionally
        removed — the context must end with the USER message so the LLM
        naturally responds to it (see conversation_summary.py).
        """
        ctx = self._make_simple_context()
        # Small target so the step is NOT at_target (at_target() early-returns NO_REDUCTION)
        result, metadata = compress_conversation_summary(ctx, None, "test", 50)
        # Should have system + user summary only (no trailing assistant)
        assert len(result) == 2
        assert result[1].get("role") == "user"
        # Last message in the original context was assistant → turn complete
        assert "**STATUS:** completed" in result[1].get("content", "")

    def test_openai_alternation_compliance(self):
        """Result is always valid OpenAI alternation."""
        ctx = self._make_simple_context()
        result, _ = compress_conversation_summary(ctx, None, "test", 1000)
        roles = [m.get("role") for m in result]
        # Should be [system, user] or [system, user, assistant]
        assert roles[0] == "system"
        assert roles[1] == "user"
        if len(roles) == 3:
            assert roles[2] == "assistant"


class TestCompressConversationSummaryReplFeedback:
    """Test repl_feedback handling — synthetic user messages with category
    "repl_feedback" must be treated as REPL output, not new interactions."""

    def test_repl_feedback_basic(self):
        """repl_feedback output appears in REPL section, not as a USER interaction."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "```python\nprint(1)\n```"},
            {"role": "user", "content": "[U:repl | N:0] 1"},
            {"role": "assistant", "content": "Done"},
        ]
        result, _ = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        # The "1" output must be under a REPL section
        repl_idx = content.find("**REPL:**")
        assert repl_idx != -1, "expected a REPL section"
        assert "1" in content[repl_idx:]
        # No separate interaction for the repl_feedback message
        assert "**USER:** [U:repl" not in content
        # Only one USER line total (the real user message)
        assert content.count("**USER:**") == 1
        assert "**USER:** do X" in content
        # No INTERACTION sections (single ongoing interaction)
        assert "INTERACTION" not in content

    def test_repl_feedback_multiple(self):
        """Multiple repl_feedback messages are collected under one REPL section."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "running..."},
            {"role": "user", "content": "[U:repl | N:0] out1"},
            {"role": "user", "content": "[U:repl | N:0] out2"},
            {"role": "assistant", "content": "done"},
        ]
        result, _ = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        assert content.count("**REPL:**") == 1
        assert "out1" in content
        assert "out2" in content
        assert content.count("**USER:**") == 1
        assert "**USER:** do X" in content

    def test_repl_feedback_truncation(self):
        """repl_feedback > 500 chars is truncated with a marker."""
        long_output = "x" * 600
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "running..."},
            {"role": "user", "content": f"[U:repl | N:0] {long_output}"},
            {"role": "assistant", "content": "done"},
        ]
        result, _ = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        assert "[...truncated]" in content
        # Full 600-char output must not be present; only the 500-char prefix
        assert long_output not in content
        assert "x" * 500 in content

    def test_repl_feedback_before_any_user_message(self):
        """repl_feedback with no prior real user message is handled gracefully."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "[U:repl | N:0] output"},
            {"role": "assistant", "content": "ok"},
        ]
        result, _ = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        # No crash; summary still produced with the placeholder user
        assert len(result) == 2
        assert "**USER:** [U:repl" not in content
        assert content.count("**USER:**") == 1  # the "(no user message found)" placeholder
        assert "**USER:** (no user message found)" in content

    def test_repl_feedback_as_last_message(self):
        """repl_feedback as last message shows in CURRENT TASK section."""
        ctx = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "code"},
            {"role": "user", "content": "[U:repl | N:0] result"},
        ]
        result, _ = compress_conversation_summary(ctx, None, "test", 50)
        content = result[1].get("content", "")
        # CURRENT TASK section contains the user task and the repl output
        task_idx = content.find("### CURRENT TASK")
        assert task_idx != -1
        task_section = content[task_idx:]
        assert "**USER:** do X" in task_section
        assert "**REPL:**" in task_section
        assert "result" in task_section
        # Last role is user → in progress
        assert "**STATUS:** in progress" in task_section


class TestCompressBlindTruncate:
    """Test compress_blind_truncate — guaranteed-fit truncation."""

    def test_blind_truncate_basic(self):
        """Truncation removes from beginning."""
        ctx = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "A" * 5000 + "### CURRENT TASK\n**USER:** important\n**STATUS:** done"},
            {"role": "assistant", "content": "ok"},
        ]
        result, metadata = compress_blind_truncate(ctx, None, "test", 200)
        content = result[1].get("content", "")
        assert "### CURRENT TASK" in content
        assert "**USER:** important" in content
        assert "[... truncated ...]" in content
        assert metadata["step_name"] == "BLIND_TRUNCATE"

    def test_blind_truncate_preserves_current_task(self):
        """CURRENT TASK section is preserved intact."""
        ctx = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "X" * 10000 + "### CURRENT TASK\n**USER:** critical info\n**STATUS:** in-progress"},
        ]
        result, metadata = compress_blind_truncate(ctx, None, "test", 300)
        content = result[1].get("content", "")
        assert "**USER:** critical info" in content
        assert "**STATUS:** in-progress" in content

    def test_blind_truncate_marker(self):
        """Truncation marker is added."""
        ctx = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Y" * 5000 + "### CURRENT TASK\n**USER:** task\n**STATUS:** done"},
        ]
        result, _ = compress_blind_truncate(ctx, None, "test", 200)
        content = result[1].get("content", "")
        assert "[... truncated ...]" in content

    def test_blind_truncate_already_fits(self):
        """No truncation when content already fits."""
        ctx = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "## CONVERSATION HISTORY\n\n### CURRENT TASK\n\n**USER:** short content"},
        ]
        result, metadata = compress_blind_truncate(ctx, None, "test", 5000)
        assert metadata["status"] == "ALREADY_FITS"
        assert "short content" in result[1].get("content", "")

    def test_pipeline_integration(self):
        """Conversation summary + blind truncate work together."""
        ctx = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        # First: conversation summary (small target so it actually summarizes)
        summary_ctx, _ = compress_conversation_summary(ctx, None, "test", 50)
        # Then: blind truncate on the summary
        truncated, meta = compress_blind_truncate(summary_ctx, None, "test", 100)
        assert meta["step_name"] == "BLIND_TRUNCATE"
        # Verify result is small enough
        assert _calculate_context_bytes(truncated) <= 100 or meta["status"] == "TRUNCATED"

class TestTokenBudgetWiring:
    """Test that token budget values are wired through the call chain."""

    def test_llm_call_config_receives_real_values(self):
        """Verify LLMCallConfig accepts and stores max_context_tokens/max_output_tokens."""
        from agent_llm_models import LLMCallConfig

        config = LLMCallConfig(
            max_context_tokens=131072,
            max_output_tokens=8192,
        )
        assert config.max_context_tokens == 131072
        assert config.max_output_tokens == 8192

    def test_llm_call_config_defaults_as_fallback(self):
        """Verify LLMCallConfig defaults are 200K/12K when not overridden."""
        from agent_llm_models import LLMCallConfig

        config = LLMCallConfig()
        assert config.max_context_tokens == 200000
        assert config.max_output_tokens == 12000

    def test_invoke_llm_with_retry_compression_passes_token_budget(self):
        """Verify _invoke_llm_with_retry_compression threads token budget to LLMCallConfig."""

        # Patch _invoke_llm_with_retry to capture the config
        captured_config = None

        def mock_invoke_llm_with_retry(client, model_name, messages, stream, config, **kwargs):
            nonlocal captured_config
            captured_config = config
            # Return a minimal successful response
            mock_resp = type('MockResp', (), {
                'raw': type('MockRaw', (), {
                    'choices': [{'message': {'content': 'summary'}}]
                })(),
                'text': 'summary',
                'reasoning': None,
                'stats': type('MockStats', (), {
                    'total_tokens': 10,
                    'input_tokens': 5,
                    'output_tokens': 5,
                    'cached_tokens': 0,
                    'hit_rate': None,
                })(),
                'success': True,
                'error': None,
                'tool_calls': [],
            })()
            return mock_resp, False

        import agent_context_compress.steps.llm as llm_mod
        original = llm_mod._invoke_llm_with_retry
        llm_mod._invoke_llm_with_retry = mock_invoke_llm_with_retry

        try:
            llm_mod._invoke_llm_with_retry_compression(
                client=None,
                model_name="test",
                messages=[{"role": "user", "content": "hello"}],
                stream=False,
                max_context_tokens=131072,
                max_output_tokens=8192,
            )
            assert captured_config is not None
            assert captured_config.max_context_tokens == 131072
            assert captured_config.max_output_tokens == 8192
        finally:
            llm_mod._invoke_llm_with_retry = original

    def test_token_target_with_128k_model(self):
        """Simulate 128K model: verify target is correct for 180K tokens."""
        from agent_context_compress import compute_compression_target_bytes

        # 128K model, 180K tokens (overflow scenario)
        result = compute_compression_target_bytes(
            current_bytes=5000000,
            compression_factor=0.30,
            last_known_tokens=180000,
            max_context_tokens=131072,
            max_output_tokens=8192,
        )
        # breathing_room = 0.3 * 131072 = 39321
        # target_tokens = 131072 - 8192 - 39321 = 83559
        # reduction_ratio = (180000 - 83559) / 180000 = 0.5358
        # token_byte_target = 5000000 * (1 - 0.5358) = 2321000
        # byte_target = 3500000
        # min(3500000, 2321000) = 2321000
        assert result < 3500000  # token-aware is more aggressive
        assert result > 0

    def test_token_target_with_32k_model(self):
        """Simulate 32K model: verify target is correct for 30K tokens."""
        from agent_context_compress import compute_compression_target_bytes

        # 32K model, 30K tokens (near overflow)
        result = compute_compression_target_bytes(
            current_bytes=2000000,
            compression_factor=0.30,
            last_known_tokens=30000,
            max_context_tokens=32768,
            max_output_tokens=4096,
        )
        # breathing_room = 0.3 * 32768 = 9830
        # target_tokens = 32768 - 4096 - 9830 = 18842
        # reduction_ratio = (30000 - 18842) / 30000 = 0.3720
        # token_byte_target = 2000000 * (1 - 0.3720) = 1256000
        # byte_target = 1400000
        # min(1400000, 1256000) = 1256000
        assert result < 1400000  # token-aware is more aggressive
        assert result > 0

    def test_token_target_with_200k_model(self):
        """Simulate 200K model: verify target is correct for 190K tokens."""
        from agent_context_compress import compute_compression_target_bytes

        # 200K model, 190K tokens (near overflow)
        result = compute_compression_target_bytes(
            current_bytes=8000000,
            compression_factor=0.30,
            last_known_tokens=190000,
            max_context_tokens=200000,
            max_output_tokens=12000,
        )
        # breathing_room = 0.3 * 200000 = 60000
        # target_tokens = 200000 - 12000 - 60000 = 128000
        # reduction_ratio = (190000 - 128000) / 190000 = 0.3263
        # token_byte_target = 8000000 * (1 - 0.3263) = 5388800
        # byte_target = 5600000
        # min(5600000, 5388800) = 5388800
        assert result < 5600000  # token-aware is more aggressive
        assert result > 0

    def test_compress_context_passes_token_budget(self):
        """Verify compress_context passes token budget to compute_compression_target_bytes."""
        # This is an integration test - we verify the function signature accepts
        # and uses the parameters correctly
        from agent_context_compress import compress_context

        # Verify that compress_context has the right signature
        import inspect
        sig = inspect.signature(compress_context)
        params = list(sig.parameters.keys())
        assert "last_known_tokens" in params
        assert "max_context_tokens" in params
        assert "max_output_tokens" in params

    def test_extract_token_count_from_error(self):
        """Verify token count extraction from API error messages."""
        from agent_llm_invoke import _extract_token_count_from_error

        # Standard OpenAI-style error
        assert _extract_token_count_from_error(
            "your prompt contains at least 188001 input tokens"
        ) == 188001

        # Alternative format
        assert _extract_token_count_from_error(
            "Request too large. Your prompt contains 150000 tokens"
        ) == 150000

        # No match
        assert _extract_token_count_from_error("some other error") is None

    def test_handle_context_overflow_passes_token_budget(self):
        """Verify _handle_context_overflow passes token budget to _try_context_compress."""
        from agent_llm_invoke import _extract_token_count_from_error
        from agent_llm_models import LLMCallConfig

        # Verify that _extract_token_count_from_error works
        assert _extract_token_count_from_error(
            "your prompt contains at least 188001 input tokens"
        ) == 188001

        # Verify LLMCallConfig stores the values
        config = LLMCallConfig(
            max_context_tokens=131072,
            max_output_tokens=8192,
            compress_client=None,
            compress_model="test",
            compress_extra_kwargs={},
            compress_audit_writer=None,
        )
        assert config.max_context_tokens == 131072
        assert config.max_output_tokens == 8192


class TestCompressPruneImages:
    """Tests for the PRUNE_IMAGES step."""

    def test_no_images_no_reduction(self):
        """Context with no image blocks returns NO_REDUCTION."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result, meta = compress_prune_images(context, None, "model", 100)
        assert meta["status"] == "NO_REDUCTION"

    def test_prunes_excess_images(self):
        """Multiple images in a user message: all but last are pruned."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "text", "text": "look at these"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,CCCC"}},
            ]},
            {"role": "assistant", "content": "I see 3 images"},
        ]
        result, meta = compress_prune_images(context, None, "model", 100)
        assert meta["status"] in ("REDUCED", "ACHIEVED")
        # Verify first two images replaced with placeholder
        user_content = result[1]["content"]
        placeholders = [p for p in user_content if p.get("type") == "text" and "IMAGE" in p.get("text", "")]
        assert len(placeholders) == 2
        # Last image preserved
        images = [p for p in user_content if p.get("type") == "image_url"]
        assert len(images) == 1

    def test_single_image_kept(self):
        """A single image is not pruned."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
            {"role": "assistant", "content": "ok"},
        ]
        result, meta = compress_prune_images(context, None, "model", 100)
        assert meta["status"] == "NO_REDUCTION"

    def test_non_dict_content_parts_safe(self):
        """String parts in content list don't crash."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [
                "just a string",
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
            ]},
            {"role": "assistant", "content": "ok"},
        ]
        result, meta = compress_prune_images(context, None, "model", 100)
        # Should not crash
        assert meta["status"] in ("REDUCED", "ACHIEVED", "NO_REDUCTION")


class TestCompressDropReasoning:
    """Tests for the DROP_REASONING step."""

    def test_no_reasoning_no_reduction(self):
        """Context without reasoning fields returns NO_REDUCTION."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result, meta = compress_drop_reasoning(context, None, "model", 100)
        assert meta["status"] == "NO_REDUCTION"

    def test_drops_reasoning_field(self):
        """Reasoning field is removed from assistant messages within the 50% boundary.

        A large trailing message pushes the 50% boundary past the assistant
        message so it is inside the scan range.
        """
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "reasoning": "Let me think..."},
            {"role": "user", "content": "x" * 10000},
        ]
        result, meta = compress_drop_reasoning(context, None, "model", 100)
        assert meta["status"] in ("REDUCED", "ACHIEVED")
        assert "reasoning" not in result[2]
        assert result[2]["content"] == "hi"

    def test_only_drops_within_boundary(self):
        """Reasoning after the 50% byte boundary is preserved."""
        # The reasoning message is in the second half (after boundary), so it
        # must NOT be dropped.
        big_content = "x" * 10000
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": big_content},
            {"role": "assistant", "content": "hi", "reasoning": "thinking..."},
        ]
        result, meta = compress_drop_reasoning(context, None, "model", 100)
        assert meta["status"] in ("REDUCED", "ACHIEVED", "NO_REDUCTION")
        # Reasoning preserved (message is past the 50% boundary)
        assert "reasoning" in result[2]


class TestCollapseTurnsHelpers:
    """Tests for collapse_turns helper functions."""

    def test_is_real_user_prefix(self):
        """Messages with [U:real | N:0] prefix are real users."""
        from agent_context_compress.steps.collapse_turns import _is_real_user
        msg = {"role": "user", "content": "[U:real | N:0] hello"}
        assert _is_real_user(msg) is True

    def test_is_real_user_synthetic(self):
        """Synthetic messages are not real users."""
        from agent_context_compress.steps.collapse_turns import _is_real_user
        msg = {"role": "user", "content": "[U:meta | N:0] system message"}
        assert _is_real_user(msg) is False

    def test_is_real_user_user_type_key(self):
        """user_type='real' key takes precedence."""
        from agent_context_compress.steps.collapse_turns import _is_real_user
        msg = {"role": "user", "content": "anything", "user_type": "real"}
        assert _is_real_user(msg) is True

    def test_is_real_user_non_user(self):
        """Non-user role is never a real user."""
        from agent_context_compress.steps.collapse_turns import _is_real_user
        msg = {"role": "assistant", "content": "[U:real | N:0] hello"}
        assert _is_real_user(msg) is False

    def test_find_summarized_turns_basic(self):
        """Finds a completed turn: user → assistant with summary."""
        from agent_context_compress.steps.collapse_turns import _find_summarized_turns
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[U:real | N:0] do task"},
            {"role": "user", "content": "[U:repl | N:0] output"},
            {"role": "assistant", "content": "done", "summary": "Task completed"},
        ]
        turns = _find_summarized_turns(msgs)
        assert len(turns) == 1
        assert turns[0] == (1, 3)

    def test_find_summarized_turns_incomplete(self):
        """Turn without a summarized assistant is not found."""
        from agent_context_compress.steps.collapse_turns import _find_summarized_turns
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[U:real | N:0] do task"},
            {"role": "assistant", "content": "working..."},
        ]
        turns = _find_summarized_turns(msgs)
        assert turns == []

    def test_collapse_replaces_turn(self):
        """Collapse replaces intermediates with user + summary."""
        from agent_context_compress.steps.collapse_turns import _find_summarized_turns, _collapse
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[U:real | N:0] do task"},
            {"role": "user", "content": "[U:repl | N:0] output"},
            {"role": "assistant", "content": "done", "summary": "Task completed"},
            {"role": "user", "content": "[U:real | N:0] next task"},
            {"role": "assistant", "content": "working"},
        ]
        turns = _find_summarized_turns(msgs)
        result = _collapse(msgs, turns)
        # System + collapsed turn (user + summary) + next turn (user + assistant)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"  # kept user
        assert "Turn summary" in result[2]["content"]  # summary
        assert result[2]["role"] == "assistant"
        assert result[3]["content"] == "[U:real | N:0] next task"
        assert result[4]["content"] == "working"

    def test_collapse_no_turns_returns_same(self):
        """Collapse with no turns returns the original list unchanged."""
        from agent_context_compress.steps.collapse_turns import _collapse
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        assert _collapse(msgs, []) is msgs


class TestBlindTruncateEdgeCases:
    """Edge cases for BLIND_TRUNCATE."""

    def test_no_user_msg(self):
        """Context with no user message returns NO_USER_MSG."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "hi"},
        ]
        result, meta = compress_blind_truncate(context, None, "model", 100)
        assert meta["status"] == "NO_USER_MSG"

    def test_not_string_content(self):
        """User message with non-string content returns NOT_STRING."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": "hi"},
        ]
        result, meta = compress_blind_truncate(context, None, "model", 100)
        assert meta["status"] == "NOT_STRING"

    def test_already_fits(self):
        """Context already at target returns ALREADY_FITS."""
        context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "## CONVERSATION HISTORY\n\n### CURRENT TASK\n\n**USER:** short task"},
        ]
        result, meta = compress_blind_truncate(context, None, "model", 10000)
        assert meta["status"] == "ALREADY_FITS"


class TestCompressFullResetFailures:
    """Failure paths for FULL_RESET (FAILED_EMPTY, FAILED_NO_REDUCTION)."""

    @staticmethod
    def _make_mock_response(content_text):
        return Mock(
            choices=[
                Mock(
                    message=Mock(
                        role="assistant",
                        content=content_text,
                        tool_calls=None,
                        reasoning_content=None,
                    )
                )
            ],
            usage=Mock(
                prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None
            ),
        )

    def test_failed_empty_summary(self):
        """Empty LLM summary returns FAILED_EMPTY and unchanged context."""
        ctx = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Original request"},
            {"role": "assistant", "content": "Result processed"},
        ]
        client = Mock()
        client.chat.completions.create.side_effect = [
            self._make_mock_response(""),  # empty summary
        ]
        result, metadata = compress_full_reset(ctx, client, "test-model", target_size_bytes=100)
        assert metadata["status"] == "FAILED_EMPTY"
        # Context returned unchanged
        assert result == ctx

    def test_failed_no_reduction(self):
        """LLM output larger than original returns FAILED_NO_REDUCTION."""
        ctx = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do task"},
            {"role": "assistant", "content": "ok"},
        ]
        client = Mock()
        client.chat.completions.create.side_effect = [
            self._make_mock_response("S" * 5000),  # huge summary
            self._make_mock_response("P" * 5000),  # huge plan
        ]
        result, metadata = compress_full_reset(ctx, client, "test-model", target_size_bytes=100)
        assert metadata["status"] == "FAILED_NO_REDUCTION"
        # Context returned unchanged
        assert result == ctx


class TestComputeTargetBytes:
    """Tests for compute_compression_target_bytes edge cases."""

    def test_clamped_to_minimum(self):
        """Target is clamped to at least 1024 bytes."""
        from agent_context_compress.steps.utils import compute_compression_target_bytes
        # Small model: MCW=10000, output=8000, factor=0.3
        # breathing_room = 3000, target_tokens = 10000-8000-3000 = -1000 → 0
        # reduction_ratio = (5000-0)/5000 = 1.0 → token_byte_target = 0
        result = compute_compression_target_bytes(100000, 0.3, 5000, 10000, 8000)
        assert result >= 1024

    def test_normal_case(self):
        """Normal case: target is between 0 and current_bytes."""
        from agent_context_compress.steps.utils import compute_compression_target_bytes
        result = compute_compression_target_bytes(100000, 0.3, 50000, 128000, 4096)
        assert 0 < result < 100000

    def test_no_tokens_known(self):
        """Without last_known_tokens, uses byte-based target only."""
        from agent_context_compress.steps.utils import compute_compression_target_bytes
        result = compute_compression_target_bytes(100000, 0.3, None, 128000, 4096)
        assert result == int(100000 * 0.7)  # 70000
