"""Tests for agent lifecycle and context management.

Tests:
    test_agent_lifecycle - Verify agent can be created and started
    test_agent_context - Verify agent context management
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestAgentLifecycle:
    """Test agent creation and basic lifecycle."""

    def test_agent_creation_with_defaults(self, test_config):
        """Verify agent can be created with minimal parameters."""
        from agent_core import TauErgon

        agent = TauErgon(
            config=test_config,
            base_url="http://test:8000/v1",
            model="test-model",
        )
        assert agent is not None
        assert agent.base_url == "http://test:8000/v1"
        assert agent.model_name == "test-model"

    def test_agent_creation_with_custom_params(self, test_config):
        """Verify agent can be created with custom parameters."""
        from agent_core import TauErgon

        agent = TauErgon(
            config=test_config,
            base_url="http://custom:9000/v1",
            model="custom-model",
            max_context_tokens=100000,
            agent_name="custom-agent",
        )
        assert agent.base_url == "http://custom:9000/v1"
        assert agent.model_name == "custom-model"
        assert agent.max_context_tokens == 100000
        assert agent.agent_name == "custom-agent"

    def test_agent_has_required_attributes(self, test_config):
        """Verify agent has all required attributes."""
        from agent_core import TauErgon

        agent = TauErgon(
            config=test_config,
            base_url="http://test:8000/v1",
            model="test-model",
        )
        # Check required attributes exist
        assert hasattr(agent, "base_url")
        assert hasattr(agent, "model_name")
        assert hasattr(agent, "max_context_tokens")
        assert hasattr(agent, "context")
        assert hasattr(agent, "agent_name")
        assert hasattr(agent, "audit_writer")
        assert hasattr(agent, "audit_file")
        assert hasattr(agent, "context_file")


class TestAgentContext:
    """Test agent context management."""

    def test_agent_context_initialization(self, test_config):
        """Verify agent context is initialized correctly."""
        from agent_core import TauErgon
        from agent_context import TauContext

        agent = TauErgon(
            config=test_config,
            base_url="http://test:8000/v1",
            model="test-model",
        )
        assert isinstance(agent.context, TauContext)
        # Should have at least the system message
        assert len(agent.context) >= 1

    def test_agent_context_append(self, test_config):
        """Verify agent can append messages to context."""
        from agent_core import TauErgon

        agent = TauErgon(
            config=test_config,
            base_url="http://test:8000/v1",
            model="test-model",
        )
        initial_len = len(agent.context)
        agent.context.append_user("Test message")
        assert len(agent.context) == initial_len + 1
