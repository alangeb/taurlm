"""Integration tests for agent_core and agent_subsystems initialization.

Tests the full initialization flow:
1. agent_subsystems.init_subsystems -> SubsystemBundle
2. agent_subsystems.read_system_prompt -> system prompt string
3. agent_core.TauErgon.__init__ -> agent with config, subsystems, session
"""

from pathlib import Path
from unittest.mock import MagicMock, patch


# Ensure src/ is on path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_init_config():
    """Create a minimal AgentInitConfig for testing."""
    from agent_init import AgentInitConfig

    return AgentInitConfig(
        agent_name="test-agent",
        llm_groups={},
        current_group_name="test",
        model_override=None,
        base_url_override=None,
        max_context_tokens_override=None,
        model_name="test-model",
        base_url="http://test",
        api_key="test-key",
        max_context_tokens=10000,
        max_tokens=None,
        timeout=180,
        max_silent_retries=3,
        max_enhanced_retries=3,
        max_explicit_retries=3,
        inference_params=None,
        loop_detection_window_size=5,
        loop_detection_repeat_threshold=3,
        heartbeat_enabled=False,
        heartbeat_interval=None,
    )


def _make_mock_agent():
    """Create a minimal mock TauErgon for testing."""
    agent = MagicMock()
    agent.config = MagicMock()
    return agent


class TestInitSubsystems:
    """Test agent_subsystems.init_subsystems()."""

    def test_returns_subsystem_bundle(self):
        from agent_subsystems import init_subsystems, SubsystemBundle
        bundle = init_subsystems(_make_mock_agent(), _make_init_config())
        assert isinstance(bundle, SubsystemBundle)

    def test_has_session_manager(self):
        from agent_subsystems import init_subsystems
        bundle = init_subsystems(_make_mock_agent(), _make_init_config())
        assert bundle.session is not None
        # AgentSessionManager has audit_writer, not messages
        assert hasattr(bundle.session, "audit_writer")

    def test_has_loop_detector(self):
        from agent_subsystems import init_subsystems
        bundle = init_subsystems(_make_mock_agent(), _make_init_config())
        assert bundle.loop_detector is not None


class TestReadSystemPrompt:
    """Test agent_subsystems.read_system_prompt()."""

    def test_returns_non_empty_string(self):
        from agent_subsystems import read_system_prompt
        prompt = read_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_returns_default_when_no_file(self):
        from agent_subsystems import read_system_prompt
        with patch("pathlib.Path.exists", return_value=False):
            prompt = read_system_prompt()
            assert "helpful AI assistant" in prompt

    def test_loads_agent_rlm_md(self):
        from agent_subsystems import read_system_prompt
        prompt = read_system_prompt()
        # Should contain content from AGENT_RLM.md
        assert len(prompt) > 100  # File is substantial


class TestTauErgonInit:
    """Test agent_core.TauErgon.__init__() integration."""

    def test_init_creates_agent(self):
        from agent_core import TauErgon
        from agent_config import Config
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(Config())
        assert agent is not None

    def test_init_sets_config(self):
        from agent_core import TauErgon
        from agent_config import Config
        cfg = Config(agent_name="test-agent")
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(cfg)
        assert agent.config.agent_name == "test-agent"

    def test_init_sets_session(self):
        from agent_core import TauErgon
        from agent_config import Config
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(Config())
        assert agent._session is not None

    def test_init_sets_loop_detector(self):
        from agent_core import TauErgon
        from agent_config import Config
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(Config())
        assert agent.loop_detector is not None

    def test_init_sets_system_prompt_in_context(self):
        from agent_core import TauErgon
        from agent_config import Config
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(Config())
        # System prompt is set in context, not as agent.system_prompt
        system = agent.context.get_system()
        assert system is not None
        assert len(system) > 0

    def test_init_with_custom_config(self):
        from agent_core import TauErgon
        from agent_config import Config
        cfg = Config(agent_name='test', timeout=300)
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(cfg)
        assert agent.config.timeout == 300
        assert agent.config.agent_name == 'test'

    def test_init_sets_key_attributes(self):
        from agent_core import TauErgon
        from agent_config import Config
        with patch('agent_core.resolve_agent_init', return_value=_make_init_config()):
            agent = TauErgon(Config())
        # Check key attributes that exist on TauErgon
        assert hasattr(agent, "_session")
        assert hasattr(agent, "loop_detector")
        assert hasattr(agent, "context")
        assert hasattr(agent, "client")
        assert hasattr(agent, "config")
        assert hasattr(agent, "agent_name")
        assert hasattr(agent, "model_name")
        assert hasattr(agent, "base_url")
