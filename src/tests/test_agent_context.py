"""Tests for agent_context module.

Tests:
    test_calculate_context_tokens - Token estimation
    test_get_context_usage - Usage statistics
    test_clear_context - Context clearing
    test_load_save_context - File I/O
    test_context_methods - Context manipulation
    test_context_append_user - User message appending
    test_context_append_assistant - Assistant message appending
    test_context_append_tool - Tool message appending
    test_context_to_list - Context to list conversion
"""

import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_context import TauContext


class TestCalculateContextTokens:
    """Test TauContext.estimate_tokens method."""

    def test_empty_context(self):
        """Test with empty context."""
        context = TauContext()
        assert context.estimate_tokens() == 0

    def test_simple_context(self):
        """Test with simple messages."""
        context = TauContext(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )
        tokens = context.estimate_tokens()
        # "Hello" = 5 chars // 3 = 1 token + 15 overhead
        # "Hi there" = 8 chars // 3 = 2 tokens + 15 overhead
        # Total: 3 + 30 = 33 tokens
        assert tokens > 0
        assert tokens == 33  # (1 + 2) + 30 overhead (15 per message)

    def test_long_context(self):
        """Test with longer content."""
        context = TauContext([{"role": "user", "content": "x" * 3000}])
        tokens = context.estimate_tokens()
        # 3000 chars // 3 = 1000 tokens + 15 overhead
        assert tokens == 1015

    def test_unicode_content(self):
        """Test with Unicode content."""
        context = TauContext([{"role": "user", "content": "こんにちは"}])
        tokens = context.estimate_tokens()
        assert tokens > 0

    def test_multiline_content(self):
        """Test with multiline content."""
        content = "Line 1\nLine 2\nLine 3"
        context = TauContext([{"role": "user", "content": content}])
        tokens = context.estimate_tokens()
        assert tokens > 0


class TestGetContextUsage:
    """Test TauContext.get_usage_stats method."""

    def test_zero_context(self):
        """Test with empty context."""
        context = TauContext()
        tokens, percentage, byte_count, is_exact = context.get_usage_stats(1000)
        assert tokens == 0
        assert percentage == 0.0
        assert byte_count >= 0

    def test_partial_context(self):
        """Test with partial context usage."""
        context = TauContext([{"role": "user", "content": "x" * 4000}])  # ~1336 tokens
        tokens, percentage, byte_count, is_exact = context.get_usage_stats(10000)
        assert tokens > 0
        assert 0 < percentage < 1
        assert byte_count > 0

    def test_full_context(self):
        """Test with context at max."""
        context = TauContext(
            [{"role": "user", "content": "x" * 30000}]
        )  # ~10000 tokens
        tokens, percentage, byte_count, is_exact = context.get_usage_stats(10000)
        assert percentage >= 1.0
        assert byte_count > 0

    def test_over_max_context(self):
        """Test with context exceeding max."""
        context = TauContext(
            [{"role": "user", "content": "x" * 60000}]
        )  # ~20000 tokens
        tokens, percentage, byte_count, is_exact = context.get_usage_stats(10000)
        assert percentage >= 2.0  # 2x over max
        assert byte_count > 0


class TestClearContext:
    """Test clear method."""

    def test_returns_empty_list(self):
        """Test that clear clears the context."""
        context = TauContext([{"role": "user", "content": "test"}])
        assert len(context) == 1
        context.clear()
        assert len(context) == 0

    def test_clear_preserves_system_message(self):
        """Test that clear preserves system message."""
        context = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "test"},
            ]
        )
        context.clear()
        # System message should be preserved
        assert len(context) == 1
        assert context[0]["role"] == "system"


class TestLoadSaveContext:
    """Test context file operations."""

    def test_save_and_load_context(self, temp_dir):
        """Test saving and loading context."""
        context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]
        context_file = temp_dir / "test_context.json"

        # Save using file I/O
        with open(context_file, "w") as f:
            json.dump(context, f)

        # Load using context method
        loaded_context = TauContext()
        result = loaded_context.load_from_file(context_file)

        assert result is True
        assert loaded_context.to_list() == context
        assert len(loaded_context) == 2

    def test_load_nonexistent_file(self, temp_dir):
        """Test loading nonexistent file returns False."""
        context_file = temp_dir / "nonexistent.json"
        context = TauContext()
        result = context.load_from_file(context_file)
        assert result is False

    def test_save_to_file_creates_directory(self, temp_dir):
        """Test saving to a path with parent directory."""
        context_file = temp_dir / "subdir" / "test_context.json"
        # Create parent directory first (save_to_file doesn't auto-create)
        context_file.parent.mkdir(parents=True, exist_ok=True)
        context = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )
        context.save_to_file(context_file)
        assert context_file.exists()

    def test_load_invalid_json(self, temp_dir):
        """Test loading invalid JSON file."""
        context_file = temp_dir / "invalid.json"
        with open(context_file, "w") as f:
            f.write("not valid json")
        context = TauContext()
        result = context.load_from_file(context_file)
        assert result is False

    def test_load_metadata_wrapped_format(self, temp_dir):
        """Test loading new metadata-wrapped context file format."""
        context_file = temp_dir / "metadata_context.json"
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]
        metadata = {
            "pid": 12345,
            "working_dir": "/tmp/test",
            "start_time": "2026-09-12T14:57:03+00:00",
            "model": "test-model",
        }
        # Write new format
        with open(context_file, "w") as f:
            json.dump({"metadata": metadata, "messages": messages}, f)

        # Load
        loaded_context = TauContext()
        result = loaded_context.load_from_file(context_file)

        assert result is True
        assert loaded_context.to_list() == messages
        assert len(loaded_context) == 2
        assert loaded_context._metadata == metadata

    def test_load_bare_array_format(self, temp_dir):
        """Test loading legacy bare-array context file format."""
        context_file = temp_dir / "bare_context.json"
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]
        # Write bare array format
        with open(context_file, "w") as f:
            json.dump(messages, f)

        # Load
        loaded_context = TauContext()
        result = loaded_context.load_from_file(context_file)

        assert result is True
        assert loaded_context.to_list() == messages
        assert len(loaded_context) == 2
        assert loaded_context._metadata == {}

    def test_save_and_load_roundtrip_with_metadata(self, temp_dir):
        """Test saving and loading preserves metadata."""
        context_file = temp_dir / "roundtrip_context.json"
        context = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )
        context.set_metadata(pid=99999, model="test-model")

        # Save
        context.save_to_file(context_file)

        # Load
        loaded_context = TauContext()
        result = loaded_context.load_from_file(context_file)

        assert result is True
        assert loaded_context.to_list() == context.to_list()
        assert loaded_context._metadata["pid"] == 99999
        assert loaded_context._metadata["model"] == "test-model"

    def test_save_to_file_small_context_returns_false(self, temp_dir):
        """Test that saving < 3 messages returns False (silent skip)."""
        context_file = temp_dir / "small_context.json"
        context = TauContext([
            {"role": "user", "content": "Hello"},
        ])
        result = context.save_to_file(context_file)
        assert result is False
        assert not context_file.exists()

    def test_save_to_file_short_context_returns_false(self, temp_dir):
        """save_to_file returns False (and writes nothing) for a <3-message context.

        NOTE: the old pending-tool-calls guard was removed in the RLM redesign
        (tool calls no longer exist); the surviving False-contract is the
        minimum-message guard.
        """
        context_file = temp_dir / "short_context.json"
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ])
        result = context.save_to_file(context_file)
        assert result is False
        assert not context_file.exists()

    def test_save_to_file_force_with_pending_tools(self, temp_dir):
        """Test that force=True allows saving with pending tool calls."""
        context_file = temp_dir / "force_context.json"
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Let me help", "tool_calls": [
                {"id": "call_1", "function": {"name": "tool", "arguments": "{}"}}
            ]},
        ])
        result = context.save_to_file(context_file, force=True)
        assert result is True
        assert context_file.exists()

    def test_load_malformed_dict_returns_false(self, temp_dir):
        """Test loading a dict with 'messages' that is not a list."""
        context_file = temp_dir / "malformed.json"
        with open(context_file, "w") as f:
            json.dump({"messages": "not a list"}, f)
        context = TauContext()
        result = context.load_from_file(context_file)
        assert result is False

    def test_load_unrecognized_format_returns_false(self, temp_dir):
        """Test loading a JSON string (neither list nor dict with messages)."""
        context_file = temp_dir / "string.json"
        with open(context_file, "w") as f:
            f.write('"just a string"')
        context = TauContext()
        result = context.load_from_file(context_file)
        assert result is False


class TestContextMethods:
    """Test TauContext class methods."""

    def test_estimate_tokens(self):
        """Test estimate_tokens method."""
        context = TauContext([{"role": "user", "content": "Hello"}])
        tokens = context.estimate_tokens()
        assert tokens > 0

    def test_load_from_file_success(self, temp_dir):
        """Test successful file load."""
        context_file = temp_dir / "test_context.json"
        context_data = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]

        # Save first
        with open(context_file, "w") as f:
            json.dump(context_data, f)

        # Load
        context = TauContext()
        result = context.load_from_file(context_file)

        assert result is True
        assert len(context) == 2
        assert context[0]["content"] == "Hello"

    def test_load_from_file_invalid(self, temp_dir):
        """Test loading invalid JSON file."""
        context_file = temp_dir / "invalid.json"

        # Write invalid JSON
        with open(context_file, "w") as f:
            f.write("not valid json")

        context = TauContext()
        result = context.load_from_file(context_file)

        assert result is False
        assert len(context) == 0

    def test_save_to_file(self, temp_dir):
        """Test saving context to file."""
        context_file = temp_dir / "test_save.json"
        context = TauContext(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )

        # Should not save (less than 3 messages)
        context.save_to_file(context_file)
        assert not context_file.exists()

        # Add more messages
        context.append_user("Test")
        context.save_to_file(context_file)
        assert context_file.exists()

        # Verify content
        with open(context_file, "r") as f:
            saved = json.load(f)
        assert "metadata" in saved
        assert "messages" in saved
        assert len(saved["messages"]) == 3

    def test_to_list(self):
        """Test to_list method."""
        context = TauContext(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )
        result = context.to_list()
        assert isinstance(result, list)
        assert len(result) == 2
        assert result == context.to_list()

    def test_len(self):
        """Test __len__ method."""
        context = TauContext(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )
        assert len(context) == 2

    def test_getitem(self):
        """Test __getitem__ method."""
        context = TauContext(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )
        assert context[0]["role"] == "user"
        assert context[1]["role"] == "assistant"

    def test_iter(self):
        """Test __iter__ method."""
        context = TauContext(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        )
        roles = [msg["role"] for msg in context]
        assert roles == ["user", "assistant"]


class TestContextAppend:
    """Test context append methods."""

    def test_append_user(self):
        """Test append_user method with system message first."""
        context = TauContext([{"role": "system", "content": "You are helpful"}])
        context.append_user("Hello")
        assert len(context) == 2
        assert context[1]["role"] == "user"
        import re as _re
        assert _re.match(r"^\[U:real \| N:\d+ \| M:\d+ \| C:\d+%\] Hello$", context[1]["content"])

    def test_append_assistant(self):
        """Test append_assistant method after user message."""
        context = TauContext(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
            ]
        )
        context.append_assistant("Hi there")
        assert len(context) == 3
        assert context[2]["role"] == "assistant"
        assert context[2]["content"] == "Hi there"

    def test_append_multiple_messages(self):
        """Test appending multiple messages in sequence."""
        context = TauContext([{"role": "system", "content": "You are helpful"}])
        context.append_user("Hello")
        context.append_assistant("Hi")
        context.append_user("How are you?")
        assert len(context) == 4
        assert context[1]["role"] == "user"
        assert context[2]["role"] == "assistant"
        assert context[3]["role"] == "user"


class TestCleanupSynthetic:
    """Test cleanup_synthetic() removes synthetic messages WITHOUT merging."""

    def test_removes_synthetic_messages(self):
        """Test that synthetic messages are removed but NOT merged."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "First part"},
            {"role": "user", "content": "[SYSTEM-SYNTHETIC: bridge] synthetic"},
            {"role": "assistant", "content": "Second part"},
        ])
        context.cleanup_synthetic()
        msgs = context.get_messages()
        # Should have: system, assistant, assistant (synthetic removed, NO merge)
        assert len(msgs) == 3
        assert msgs[0]["content"] == "You are helpful"
        assert msgs[1]["content"] == "First part"
        assert msgs[2]["content"] == "Second part"

    def test_preserves_non_synthetic_messages(self):
        """Test that non-synthetic messages are preserved."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ])
        context.cleanup_synthetic()
        msgs = context.get_messages()
        assert len(msgs) == 3

    def test_removes_multiple_synthetic_messages(self):
        """Test that multiple synthetic messages are all removed."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "[SYSTEM-SYNTHETIC: bridge] synthetic1"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "[SYSTEM-SYNTHETIC: bridge] synthetic2"},
        ])
        context.cleanup_synthetic()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "assistant"

    def test_empty_context(self):
        """Test cleanup on empty context."""
        context = TauContext()
        context.cleanup_synthetic()
        assert len(context) == 0

    def test_no_synthetic_messages(self):
        """Test cleanup when there are no synthetic messages."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ])
        context.cleanup_synthetic()
        msgs = context.get_messages()
        assert len(msgs) == 3
        assert msgs[1]["content"] == "Hello"
        assert msgs[2]["content"] == "Hi"


class TestMergeConsecutiveAssistants:
    """Test explicit merge_consecutive_assistants() merges consecutive assistant messages only."""

    def test_merge_two_consecutive_assistants(self):
        """Test merging two consecutive assistant messages."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "First part"},
            {"role": "assistant", "content": "Second part"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["content"] == "You are helpful"
        assert "First part" in msgs[1]["content"]
        assert "Second part" in msgs[1]["content"]

    def test_merge_three_consecutive_assistants(self):
        """Test merging 3+ consecutive assistant messages."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Part 1"},
            {"role": "assistant", "content": "Part 2"},
            {"role": "assistant", "content": "Part 3"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert "Part 1" in msgs[1]["content"]
        assert "Part 2" in msgs[1]["content"]
        assert "Part 3" in msgs[1]["content"]

    def test_merge_deduplicates_tool_calls(self):
        """Test that duplicate tool_calls are deduplicated by ID."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Part 1", "tool_calls": [
                {"id": "call_1", "function": {"name": "test_tool", "arguments": "{}"}}
            ]},
            {"role": "assistant", "content": "Part 2", "tool_calls": [
                {"id": "call_1", "function": {"name": "test_tool", "arguments": "{}"}}
            ]},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert len(msgs[1]["tool_calls"]) == 1

    def test_merge_none_content(self):
        """Test merging messages where content is None."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "test_tool", "arguments": "{}"}}
            ]},
            {"role": "assistant", "content": "Result"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert msgs[1]["content"] == "Result"
        assert len(msgs[1]["tool_calls"]) == 1

    def test_merge_reasoning(self):
        """Test that reasoning fields are merged."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Part 1", "reasoning": "Thinking 1"},
            {"role": "assistant", "content": "Part 2", "reasoning": "Thinking 2"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert "Thinking 1" in msgs[1]["reasoning"]
        assert "Thinking 2" in msgs[1]["reasoning"]

    def test_merge_refusal(self):
        """Test that refusal fields are merged."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Part 1", "refusal": "Refusal 1"},
            {"role": "assistant", "content": "Part 2", "refusal": "Refusal 2"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert "Refusal 1" in msgs[1]["refusal"]
        assert "Refusal 2" in msgs[1]["refusal"]

    def test_consecutive_tool_messages_not_merged(self):
        """Test that consecutive tool messages are NOT merged (batched tool calls).

        Consecutive tool messages are valid (one assistant message with N tool_calls
        produces N tool results). They are preserved as-is without merging.
        """
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "tool", "tool_call_id": "call_1", "name": "tool_1", "content": "Result 1"},
            {"role": "tool", "tool_call_id": "call_2", "name": "tool_2", "content": "Result 2"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        # Tool messages should NOT be merged — preserved as separate messages
        assert len(msgs) == 3
        assert msgs[1]["tool_call_id"] == "call_1"
        assert msgs[2]["tool_call_id"] == "call_2"

    def test_consecutive_user_messages_merged_gracefully(self):
        """Test that consecutive user messages are merged gracefully with a warning.

        Consecutive user messages can appear from tool-result / post-parse
        recovery edge cases. They are merged (content concatenated) and a
        warning is logged — NOT a RuntimeError.
        """
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Message 1"},
            {"role": "user", "content": "Message 2"},
        ])
        # Should NOT raise — merges gracefully
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        # System + merged user (2 messages merged into 1)
        assert len(msgs) == 2
        assert msgs[1]["role"] == "user"
        assert "Message 1" in msgs[1]["content"]
        assert "Message 2" in msgs[1]["content"]

    def test_no_consecutive_messages_is_noop(self):
        """Test merge_consecutive_assistants() is a no-op when there are no consecutive same-role messages."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 3

    def test_merge_assistant_with_tool_calls_and_without(self):
        """Test merging assistant with tool_calls and assistant without tool_calls.

        This is the real-world scenario: assistant sends tool calls, then sends
        a follow-up message without tool calls (e.g., after synthetic bridge removal).
        """
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "test_tool", "arguments": "{}"}}
            ]},
            {"role": "assistant", "content": "Done with tool call"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert msgs[1]["content"] == "Done with tool call"
        assert len(msgs[1]["tool_calls"]) == 1
        assert msgs[1]["tool_calls"][0]["id"] == "call_1"

    def test_merge_preserves_all_fields_combined(self):
        """Merge concatenates content/reasoning/refusal (RLM: no tool_calls/usage_metadata)."""
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Part 1", "reasoning": "Thinking 1", "refusal": "Refuse 1"},
            {"role": "assistant", "content": "Part 2", "reasoning": "Thinking 2", "refusal": "Refuse 2"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        merged = msgs[1]
        assert "Part 1" in merged["content"]
        assert "Part 2" in merged["content"]
        assert "Thinking 1" in merged["reasoning"]
        assert "Thinking 2" in merged["reasoning"]
        assert "Refuse 1" in merged["refusal"]
        assert "Refuse 2" in merged["refusal"]

    def test_close_turn_merges_consecutive_assistants(self):
        """Test that close_turn() properly merges consecutive assistant messages.

        Integration test: close_turn() calls cleanup_synthetic() then merge_consecutive_assistants().
        When synthetic bridges are removed, consecutive assistant messages should be merged.
        """
        # Simulate: assistant → synthetic_user → assistant
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "[SYSTEM-SYNTHETIC: continuation] Continuing conversation."},
            {"role": "assistant", "content": "Second response"},
        ])
        context.close_turn("turn complete")
        msgs = context.get_messages()
        # After merge: system, user, merged_assistant (content includes both responses)
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1, f"Expected 1 merged assistant, got {len(assistant_msgs)}"
        assert "First response" in assistant_msgs[0]["content"]
        assert "Second response" in assistant_msgs[0]["content"]

    def test_merge_empty_string_content(self):
        """Test merging when one assistant has empty string content.

        _merge_content uses str(a) + "\n" + str(b), so empty string + "Real content"
        produces "\nReal content".
        """
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Real content"},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert msgs[1]["content"] == "\nReal content"

    def test_merge_both_empty_content(self):
        """Test merging when both assistants have empty content.

        _merge_content uses str(a) + "\n" + str(b), so "" + "\n" + "" = "\n".
        """
        context = TauContext([
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": ""},
        ])
        context.merge_consecutive_assistants()
        msgs = context.get_messages()
        assert len(msgs) == 2
        assert msgs[1]["content"] == "\n"
