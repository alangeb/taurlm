"""Test spawn() wiring into kernel namespace.

Verifies that spawn() is properly wired into the PythonKernel namespace
and routes to the correct underlying function based on parameters.
"""

import pytest
from unittest.mock import MagicMock, patch
from rlm.kernel import PythonKernel


class TestSpawnWiring:
    """Test spawn() wiring into kernel namespace."""

    def test_spawn_is_callable_with_config(self):
        """Test that spawn() is callable when config is provided."""
        config = MagicMock()
        config.max_turns = 1000

        kernel = PythonKernel(rlm_config=config)

        assert "spawn" in kernel.namespace
        assert callable(kernel.namespace["spawn"])

    def test_spawn_raises_without_parent_agent(self):
        """Test that spawn() raises RuntimeError without parent agent."""
        config = MagicMock()
        config.max_turns = 1000

        kernel = PythonKernel(rlm_config=config)

        with pytest.raises(RuntimeError, match="requires a parent agent"):
            kernel.namespace["spawn"]("test task")

    def test_spawn_callable_in_namespace(self):
        """Test that spawn() is accessible as 'spawn' in namespace."""
        config = MagicMock()
        config.max_turns = 1000

        kernel = PythonKernel(rlm_config=config)

        # Verify spawn is in namespace
        assert "spawn" in kernel.namespace

        # Verify it's callable
        assert callable(kernel.namespace["spawn"])

    def test_spawn_accepts_inherit_context_parameter(self):
        """Test that spawn() accepts inherit_context parameter."""
        from unittest.mock import patch, MagicMock
        parent = MagicMock()
        parent.nesting_count = 0
        parent.nesting_stack = ""
        parent.config = MagicMock()
        kernel = PythonKernel(rlm_config=MagicMock(), agent=parent)
        with patch("agent_core.TauErgon") as MockAgent:
            child = MagicMock()
            child.max_context_tokens = 128000
            child.context.get_usage_stats.return_value = (100, 0.001, 400, False)
            child.invoke.return_value = "isolated result"
            child.get_answer.return_value = MagicMock()
            setattr(child.get_answer.return_value, "yield", False)
            child.spawn_B = 0.69
            MockAgent.return_value = child
            with patch("rlm.spawn._with_real_stdout", side_effect=lambda f, *a: f(*a)):
                h = kernel.namespace["spawn"]("task", inherit_context=False)
        assert h.last_result == "isolated result"
        assert child.nesting_stack.endswith("S")

    def test_spawn_inherit_context_routes_to_fork(self):
        """Test that spawn() with inherit_context=True sets F in nesting_stack."""
        from unittest.mock import patch, MagicMock
        parent = MagicMock()
        parent.nesting_count = 0
        parent.nesting_stack = ""
        parent.config = MagicMock()
        kernel = PythonKernel(rlm_config=MagicMock(), agent=parent)
        with patch("agent_core.TauErgon") as MockAgent:
            child = MagicMock()
            child.max_context_tokens = 128000
            child.context.get_usage_stats.return_value = (100, 0.001, 400, False)
            child.context._messages = [{"role": "user", "content": "hi"}]
            child.invoke.return_value = "fork result"
            child.get_answer.return_value = MagicMock()
            setattr(child.get_answer.return_value, "yield", False)
            child.spawn_B = 0.69
            MockAgent.return_value = child
            with patch("rlm.spawn._with_real_stdout", side_effect=lambda f, *a: f(*a)):
                h = kernel.namespace["spawn"]("task", inherit_context=True)
        assert child.nesting_stack.endswith("F")
        assert h.last_result == "fork result"

    def test_spawn_accepts_name_parameter(self):
        """Test that spawn() accepts name parameter."""
        from unittest.mock import patch, MagicMock
        parent = MagicMock()
        parent.nesting_count = 0
        parent.nesting_stack = ""
        parent.config = MagicMock()
        kernel = PythonKernel(rlm_config=MagicMock(), agent=parent)
        with patch("agent_core.TauErgon") as MockAgent:
            child = MagicMock()
            child.max_context_tokens = 128000
            child.context.get_usage_stats.return_value = (100, 0.001, 400, False)
            child.invoke.return_value = "named result"
            child.get_answer.return_value = MagicMock()
            setattr(child.get_answer.return_value, "yield", False)
            child.spawn_B = 0.69
            MockAgent.return_value = child
            with patch("rlm.spawn._with_real_stdout", side_effect=lambda f, *a: f(*a)):
                h = kernel.namespace["spawn"]("task", name="myworker")
        assert h.name == "myworker"
        assert h.last_result == "named result"
