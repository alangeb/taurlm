"""Unit tests for is_repl_message and is_real_user_request predicates.

These predicates are critical for the compression pipeline — they determine
which user messages are "real" prompts vs REPL output vs synthetic messages.
"""

from agent_message_utils import is_repl_message, is_real_user_request, is_synthetic_message


class TestIsReplMessage:
    """Tests for is_repl_message()."""

    def test_repl_prefix(self):
        msg = {"role": "user", "content": "[U:repl | N:0 | M:5 | C:10%] [REPL output]\n>>> 42"}
        assert is_repl_message(msg) is True

    def test_real_prefix(self):
        msg = {"role": "user", "content": "[U:real | N:0 | M:4 | C:9%] Do something"}
        assert is_repl_message(msg) is False

    def test_meta_prefix(self):
        msg = {"role": "user", "content": "[U:meta | N:0 | M:6 | C:11%] continuing"}
        assert is_repl_message(msg) is False

    def test_assistant_message(self):
        msg = {"role": "assistant", "content": "Done"}
        assert is_repl_message(msg) is False

    def test_system_message(self):
        msg = {"role": "system", "content": "You are helpful"}
        assert is_repl_message(msg) is False

    def test_unprefixed_user(self):
        msg = {"role": "user", "content": "bare message"}
        assert is_repl_message(msg) is False

    def test_repl_with_different_stack(self):
        msg = {"role": "user", "content": "[U:repl | N:2 | M:10 | C:50%] output"}
        assert is_repl_message(msg) is True

    def test_empty_content(self):
        msg = {"role": "user", "content": ""}
        assert is_repl_message(msg) is False


class TestIsRealUserRequest:
    """Tests for is_real_user_request()."""

    def test_real_prefix(self):
        msg = {"role": "user", "content": "[U:real | N:0 | M:0 | C:0%] Write code"}
        assert is_real_user_request(msg) is True

    def test_repl_prefix(self):
        msg = {"role": "user", "content": "[U:repl | N:0 | M:1 | C:1%] [REPL output]"}
        assert is_real_user_request(msg) is False

    def test_meta_prefix(self):
        msg = {"role": "user", "content": "[U:meta | N:0 | M:2 | C:2%] continuing"}
        assert is_real_user_request(msg) is False

    def test_confirm_prefix(self):
        msg = {"role": "user", "content": "[U:confirm | N:0 | M:3 | C:3%] yes"}
        assert is_real_user_request(msg) is False

    def test_inject_prefix(self):
        msg = {"role": "user", "content": "[U:inject | N:1 | M:4 | C:4%] task"}
        assert is_real_user_request(msg) is False

    def test_system_prefix(self):
        msg = {"role": "user", "content": "[U:system | N:0 | M:5 | C:5%] notice"}
        assert is_real_user_request(msg) is False

    def test_assistant_message(self):
        msg = {"role": "assistant", "content": "Done"}
        assert is_real_user_request(msg) is False

    def test_system_role(self):
        msg = {"role": "system", "content": "You are helpful"}
        assert is_real_user_request(msg) is False

    def test_unprefixed_user_is_real(self):
        """Defensive: unprefixed user messages are treated as real."""
        msg = {"role": "user", "content": "bare message no prefix"}
        assert is_real_user_request(msg) is True

    def test_real_with_deep_stack(self):
        msg = {"role": "user", "content": "[U:real | N:3 | M:20 | C:80%] Task"}
        assert is_real_user_request(msg) is True


class TestIsSyntheticMessage:
    """Tests for is_synthetic_message() (existing predicate)."""

    def test_meta_is_synthetic(self):
        msg = {"role": "user", "content": "[U:meta | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is True

    def test_confirm_is_synthetic(self):
        msg = {"role": "user", "content": "[U:confirm | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is True

    def test_inject_is_synthetic(self):
        msg = {"role": "user", "content": "[U:inject | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is True

    def test_system_type_is_synthetic(self):
        msg = {"role": "user", "content": "[U:system | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is True

    def test_repl_is_not_synthetic(self):
        """REPL is NOT in the synthetic set — it's handled separately."""
        msg = {"role": "user", "content": "[U:repl | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is False

    def test_real_is_not_synthetic(self):
        msg = {"role": "user", "content": "[U:real | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is False


class TestPredicateConsistency:
    """Verify the predicates are mutually consistent."""

    def test_real_is_not_repl_and_not_synthetic(self):
        msg = {"role": "user", "content": "[U:real | N:0 | M:0 | C:0%] x"}
        assert is_real_user_request(msg) is True
        assert is_repl_message(msg) is False
        assert is_synthetic_message(msg) is False

    def test_repl_is_repl_not_real_not_synthetic(self):
        msg = {"role": "user", "content": "[U:repl | N:0 | M:0 | C:0%] x"}
        assert is_repl_message(msg) is True
        assert is_real_user_request(msg) is False
        assert is_synthetic_message(msg) is False

    def test_meta_is_synthetic_not_real_not_repl(self):
        msg = {"role": "user", "content": "[U:meta | N:0 | M:0 | C:0%] x"}
        assert is_synthetic_message(msg) is True
        assert is_real_user_request(msg) is False
        assert is_repl_message(msg) is False

    def test_every_user_msg_is_at_most_one_category(self):
        """Every user message matches AT MOST one of: real, repl, synthetic.

        Note: fork/subagent/redirect types match NONE (they're a 4th implicit
        category: 'not a user turn'). Unprefixed messages default to 'real'.
        """
        messages = [
            {"role": "user", "content": "[U:real | N:0 | M:0 | C:0%] x"},
            {"role": "user", "content": "[U:repl | N:0 | M:1 | C:1%] x"},
            {"role": "user", "content": "[U:meta | N:0 | M:2 | C:2%] x"},
            {"role": "user", "content": "[U:confirm | N:0 | M:3 | C:3%] x"},
            {"role": "user", "content": "[U:inject | N:0 | M:4 | C:4%] x"},
            {"role": "user", "content": "[U:system | N:0 | M:5 | C:5%] x"},
            {"role": "user", "content": "[U:fork | N:1 | M:6 | C:6%] x"},
            {"role": "user", "content": "[U:subagent | N:1 | M:7 | C:7%] x"},
            {"role": "user", "content": "[U:redirect | N:0 | M:8 | C:8%] x"},
            {"role": "user", "content": "unprefixed"},
        ]
        for msg in messages:
            is_real = is_real_user_request(msg)
            is_repl = is_repl_message(msg)
            is_synth = is_synthetic_message(msg)
            # At most one should be True
            count = sum([is_real, is_repl, is_synth])
            assert count <= 1, f"Expected at most 1 True, got real={is_real} repl={is_repl} synth={is_synth} for {msg['content'][:30]}"
