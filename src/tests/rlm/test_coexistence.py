"""Coexistence Layer Tests for RLM

Tests the feature flag for dual-mode operation (RLM vs. tool-calling).
"""

from __future__ import annotations

from pathlib import Path


# Paths
SRC_DIR = Path(__file__).parent.parent.parent
AGENT_RLM_MD = SRC_DIR / "AGENT_RLM.md"


class TestSystemPromptLoading:
    """Test that AGENT_RLM.md is the only system prompt."""

    def test_agent_md_not_exists(self):
        """AGENT.md should NOT exist (deleted in RLM-only mode)."""
        agent_md = SRC_DIR / "AGENT.md"
        assert not agent_md.exists(), "AGENT.md should be deleted"

    def test_agent_rlm_md_exists(self):
        """AGENT_RLM.md should exist."""
        assert AGENT_RLM_MD.exists(), "AGENT_RLM.md not found"

    def test_agent_rlm_md_mentions_repl(self):
        """AGENT_RLM.md should mention REPL."""
        content = AGENT_RLM_MD.read_text()
        assert "REPL" in content

    def test_agent_rlm_md_mentions_no_tools(self):
        """AGENT_RLM.md should mention no tools."""
        content = AGENT_RLM_MD.read_text()
        content_lower = content.lower()
        # Check for any indication that agent has no tools
        assert (
            "no tools" in content_lower or
            "zero tools" in content_lower or
            "cannot call" in content_lower or
            "not a tool-calling agent" in content_lower
        )

    def test_agent_rlm_md_mentions_answer_dict(self):
        """AGENT_RLM.md should mention answer dict."""
        content = AGENT_RLM_MD.read_text()
        assert 'answer["content"]' in content or "answer['content']" in content


class TestReadSystemPrompt:
    """Test read_system_prompt function."""

    def test_read_system_prompt_loads_agent_rlm_md(self):
        """read_system_prompt should load AGENT_RLM.md."""
        from agent_subsystems import read_system_prompt

        prompt = read_system_prompt()
        # Should contain content from AGENT_RLM.md
        assert "TauRLM" in prompt or "REPL" in prompt

    def test_read_system_prompt_no_rlm_enabled_param(self):
        """read_system_prompt should not take rlm_enabled parameter."""
        from agent_subsystems import read_system_prompt
        import inspect
        sig = inspect.signature(read_system_prompt)
        assert "rlm_enabled" not in sig.parameters

    def test_read_system_prompt_rlm_has_repl_content(self):
        """RLM prompt should have REPL-specific content."""
        from agent_subsystems import read_system_prompt

        prompt = read_system_prompt()
        assert "REPL" in prompt
        assert "answer" in prompt.lower()


class TestConfigRLMEnabled:
    """Test RLM config."""

    def test_rlm_config_has_no_enabled_field(self):
        """RLMConfig should NOT have enabled field (RLM is always enabled)."""
        from agent_config import RLMConfig

        config = RLMConfig()
        assert not hasattr(config, "enabled")

    def test_rlm_config_defaults(self):
        """RLMConfig should have correct defaults."""
        from agent_config import RLMConfig

        config = RLMConfig()
        assert config.max_turns == 1000


class TestDispatchLogic:
    """Test that invoke_loop uses RLM loop."""

    def test_dispatch_logic_in_code(self):
        """Verify dispatch logic uses run_rlm_loop."""
        from pathlib import Path

        agent_core = Path(__file__).parent.parent.parent / "agent_core.py"
        content = agent_core.read_text()

        # Check that the dispatch logic uses run_rlm_loop
        assert "run_rlm_loop" in content
        assert "is_repl_enabled" not in content  # Removed

    def test_invoke_loop_exists(self):
        """TauErgon should have invoke_loop method."""
        from agent_core import TauErgon

        assert hasattr(TauErgon, "invoke_loop")

    def test_run_rlm_loop_exists_in_agent_loop(self):
        """run_rlm_loop should exist in agent_loop module."""
        from agent_loop import run_rlm_loop

        assert callable(run_rlm_loop)
