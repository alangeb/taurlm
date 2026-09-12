"""Tests for agent_heartbeat.py — HeartbeatManager and response parsing."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from agent_heartbeat import (
    HeartbeatManager,
    HeartbeatResponse,
    _parse_heartbeat_response,
    _load_heartbeat_prompt,
)


class TestParseHeartbeatResponse:
    """Tests for _parse_heartbeat_response()."""

    def test_prompt_tag(self):
        raw = "I think we should continue with the task.\n<PROMPT>Fix the bug in main.py</PROMPT>"
        result = _parse_heartbeat_response(raw)
        assert result is not None
        assert result.action == "prompt"
        assert result.task == "Fix the bug in main.py"

    def test_no_action_tag(self):
        raw = "Everything looks fine.\n<NO_ACTION>"
        result = _parse_heartbeat_response(raw)
        assert result is not None
        assert result.action == "no_action"
        assert result.task is None

    def test_prompt_with_surrounding_text(self):
        raw = "Let me think about this... <PROMPT>Review the code</PROMPT> That's what I'd do."
        result = _parse_heartbeat_response(raw)
        assert result is not None
        assert result.action == "prompt"
        assert result.task == "Review the code"

    def test_no_match(self):
        raw = "I don't know what to do."
        result = _parse_heartbeat_response(raw)
        assert result is None

    def test_none_input(self):
        result = _parse_heartbeat_response(None)
        assert result is None

    def test_empty_string(self):
        result = _parse_heartbeat_response("")
        assert result is None

    def test_prompt_multiline(self):
        raw = "<PROMPT>\nLine 1\nLine 2\n</PROMPT>"
        result = _parse_heartbeat_response(raw)
        assert result is not None
        assert result.action == "prompt"
        assert "Line 1" in result.task
        assert "Line 2" in result.task


class TestLoadHeartbeatPrompt:
    """Tests for _load_heartbeat_prompt()."""

    def test_returns_none_if_file_missing(self, tmp_path, monkeypatch):
        from pathlib import Path
        import agent_heartbeat

        monkeypatch.setattr(agent_heartbeat, "_HEARTBEAT_MD", tmp_path / "nonexistent.md")
        result = _load_heartbeat_prompt()
        assert result is None

    def test_strips_frontmatter(self, tmp_path, monkeypatch):
        from pathlib import Path
        import agent_heartbeat

        md_file = tmp_path / "heartbeat.md"
        md_file.write_text(
            "---\ndescription: Test heartbeat\n---\n\n"
            "HEARTBEAT: It is ${time}.\n"
        )
        monkeypatch.setattr(agent_heartbeat, "_HEARTBEAT_MD", md_file)
        result = _load_heartbeat_prompt()
        assert result is not None
        assert "description" not in result
        assert "HEARTBEAT:" in result

    def test_substitutes_dynamic_placeholders(self, tmp_path, monkeypatch):
        from pathlib import Path
        import agent_heartbeat

        md_file = tmp_path / "heartbeat.md"
        md_file.write_text(
            "---\ndescription: Test\n---\n\n"
            "Time: ${time}\nDate: ${date}\n"
        )
        monkeypatch.setattr(agent_heartbeat, "_HEARTBEAT_MD", md_file)
        result = _load_heartbeat_prompt()
        assert result is not None
        assert "${time}" not in result
        assert "${date}" not in result
        # Should contain actual time/date values
        assert len(result) > 10


class TestHeartbeatManager:
    """Tests for HeartbeatManager."""

    def _make_agent(self):
        """Create a mock agent with the required attributes."""
        agent = MagicMock()
        agent.context = []  # Empty context
        agent.model_name = "test-model"
        agent.client = MagicMock()
        return agent

    def test_initial_state(self):
        hb = HeartbeatManager(enabled=False, interval_seconds=None)
        assert hb.enabled is False
        assert hb.interval_seconds is None
        assert hb.last_activity_time > 0

    def test_touch_activity(self):
        hb = HeartbeatManager()
        initial = hb.last_activity_time
        time.sleep(0.01)
        hb.touch_activity()
        assert hb.last_activity_time > initial

    def test_should_check_disabled(self):
        hb = HeartbeatManager(enabled=False, interval_seconds=60)
        assert hb.should_check() is False

    def test_should_check_no_interval(self):
        hb = HeartbeatManager(enabled=True, interval_seconds=None)
        assert hb.should_check() is False

    def test_should_check_not_idle(self):
        hb = HeartbeatManager(enabled=True, interval_seconds=60)
        # Just touched, not idle yet
        assert hb.should_check() is False

    def test_should_check_idle(self):
        hb = HeartbeatManager(enabled=True, interval_seconds=1)
        # Simulate idle
        hb.last_activity_time = time.time() - 2
        assert hb.should_check() is True

    def test_run_heartbeat_disabled(self):
        hb = HeartbeatManager(enabled=False, interval_seconds=60)
        assert hb.run_heartbeat() is None

    def test_run_heartbeat_not_idle(self):
        hb = HeartbeatManager(enabled=True, interval_seconds=60)
        # Just touched, not idle yet
        assert hb.run_heartbeat() is None

    def test_run_heartbeat_no_agent(self):
        hb = HeartbeatManager(enabled=True, interval_seconds=1)
        hb.last_activity_time = time.time() - 2
        # No agent set
        assert hb.run_heartbeat() is None

    def test_run_heartbeat_with_mock_llm(self):
        agent = self._make_agent()
        # Mock LLM response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "<NO_ACTION>"
        agent.client.chat.completions.create.return_value = mock_response

        hb = HeartbeatManager(enabled=True, interval_seconds=1, agent=agent)
        hb.last_activity_time = time.time() - 2

        result = hb.run_heartbeat()
        assert result is not None
        assert result.action == "no_action"

    def test_run_heartbeat_prompt_response(self):
        agent = self._make_agent()
        # Mock LLM response with prompt
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "<PROMPT>Continue with the task</PROMPT>"
        agent.client.chat.completions.create.return_value = mock_response

        hb = HeartbeatManager(enabled=True, interval_seconds=1, agent=agent)
        hb.last_activity_time = time.time() - 2

        result = hb.run_heartbeat()
        assert result is not None
        assert result.action == "prompt"
        assert result.task == "Continue with the task"

    def test_run_heartbeat_llm_error(self):
        agent = self._make_agent()
        agent.client.chat.completions.create.side_effect = Exception("API error")

        hb = HeartbeatManager(enabled=True, interval_seconds=1, agent=agent)
        hb.last_activity_time = time.time() - 2

        result = hb.run_heartbeat()
        assert result is None
