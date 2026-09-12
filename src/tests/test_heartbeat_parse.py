"""Tests for _parse_heartbeat_response parsing logic."""
import unittest
from agent_heartbeat import _parse_heartbeat_response


class TestParseHeartbeatResponse(unittest.TestCase):
    """Verify all 3 code paths: <PROMPT>, <NO_ACTION>, legacy PROMPT:."""

    # ── <PROMPT>...</PROMPT> path ──────────────────────────────────────
    def test_exact_prompt_tag(self):
        result = _parse_heartbeat_response("<PROMPT>do something</PROMPT>")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "prompt")
        self.assertEqual(result.task, "do something")

    def test_prompt_with_surrounding_text(self):
        raw = "Here is my reasoning...\n<PROMPT>check disk</PROMPT>\nThat's all."
        result = _parse_heartbeat_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "prompt")
        self.assertEqual(result.task, "check disk")

    def test_prompt_multiline_task(self):
        raw = "<PROMPT>line1\nline2</PROMPT>"
        result = _parse_heartbeat_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "prompt")
        self.assertEqual(result.task, "line1\nline2")

    # ── <NO_ACTION> path ───────────────────────────────────────────────
    def test_exact_no_action(self):
        result = _parse_heartbeat_response("<NO_ACTION>")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "no_action")
        self.assertIsNone(result.task)

    def test_no_action_with_surrounding_text(self):
        raw = "thinking...\n<NO_ACTION>\ndone."
        result = _parse_heartbeat_response(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "no_action")

    # ── Legacy PROMPT: path removed (Phase 7, cebdc3a) ─────────────────
    # Only <PROMPT>/<NO_ACTION> XML tags are valid exit states now.
    def test_legacy_prompt_prefix_not_parsed(self):
        """Legacy 'PROMPT:' prefix is no longer a valid exit state."""
        self.assertIsNone(_parse_heartbeat_response("PROMPT: legacy task"))
        self.assertIsNone(_parse_heartbeat_response("prompt: lower case"))

    # ── Unparseable → None ─────────────────────────────────────────────
    def test_unparseable_returns_none(self):
        result = _parse_heartbeat_response("just random text with no tags")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = _parse_heartbeat_response("")
        self.assertIsNone(result)

    def test_whitespace_only_returns_none(self):
        result = _parse_heartbeat_response("   \n  ")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
