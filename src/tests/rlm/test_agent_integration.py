"""Phase 1.6: Agent Integration Tests

Tests for REPL kernel integration into agent_core.py.

Tests:
- RLM config dataclasses
- REPL integration methods (without full TauErgon instantiation)
- Kernel initialization logic
- Answer management integration
- REPLResult and AnswerState dataclasses
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent_config import Config, RLMConfig, REPLConfig, LLMGroup
from rlm.kernel import REPLResult, PythonKernel
from rlm.answer import AnswerManager, AnswerState


from agent_fence_repair import repair_response
from agent_repl_parse import extract_code_blocks


def _codes(text, style='std'):
    """Pipeline path: repair the message, then strict-extract and join."""
    repaired, _notes = repair_response(text, style)
    return '\n\n'.join(b.code for b in extract_code_blocks(repaired, fence_style=style))


class TestConfigRLM:
    """Test RLM config dataclasses."""

    def test_rlm_config_defaults(self):
        """Test RLMConfig default values."""
        config = RLMConfig()
        assert config.max_turns == 1000

    def test_repl_config_defaults(self):
        """Test REPLConfig default values."""
        config = REPLConfig()
        assert config.max_output_chars == 8192
        assert config.max_state_size_mb == 100.0
        assert config.bash_timeout_seconds == 180.0
        assert config.python_timeout_seconds == 180.0

    def test_config_has_rlm_field(self):
        """Test Config has RLM config field."""
        config = Config(
            llm_groups={"test": LLMGroup(
                name="test", model="test", api_base="http://test"
            )}
        )
        assert hasattr(config, "rlm")
        assert isinstance(config.rlm, RLMConfig)

    def test_config_rlm_repl(self):
        """Test Config has RLM REPL config."""
        config = Config(
            llm_groups={"test": LLMGroup(
                name="test", model="test", api_base="http://test"
            )}
        )
        assert hasattr(config.rlm, "repl")
        assert isinstance(config.rlm.repl, REPLConfig)

    def test_config_rlm_enabled(self):
        """Test Config with RLM enabled."""
        config = Config(
            llm_groups={"test": LLMGroup(
                name="test", model="test", api_base="http://test"
            )},
            rlm=RLMConfig()
        )
        assert config.rlm is not None


class TestKernelInitialization:
    """Test kernel initialization logic."""

    def test_kernel_creation_from_config(self):
        """Test kernel creation from RLM config."""
        rlm_config = RLMConfig()
        repl_config = rlm_config.repl

        kernel = PythonKernel(
            max_output_chars=repl_config.max_output_chars,
            max_state_size_mb=repl_config.max_state_size_mb,
            bash_timeout_seconds=repl_config.bash_timeout_seconds,
        )
        assert kernel.max_output_chars == 8192

    def test_kernel_with_answer_dict(self):
        """Test kernel with answer dict in namespace."""
        answer_mgr = AnswerManager()
        kernel = PythonKernel()
        kernel.namespace["answer"] = answer_mgr.get_dict()

        # Execute code that modifies answer
        kernel.execute("answer['content'] = 'Test'")
        assert kernel.namespace["answer"]["content"] == "Test"

    def test_kernel_answer_ready_detection(self):
        """Test kernel detects answer ready."""
        kernel = PythonKernel()
        result = kernel.execute(
            "answer['content'] = 'Hello'\n"
            "answer['ready'] = True"
        )
        assert result.answer_ready is True
        assert result.answer_content == "Hello"


class TestAnswerManagerIntegration:
    """Test AnswerManager integration with kernel."""

    def test_answer_manager_sync(self):
        """Test answer manager sync with kernel."""
        answer_mgr = AnswerManager()
        kernel = PythonKernel()
        kernel.namespace["answer"] = answer_mgr.get_dict()

        # Execute code that modifies answer
        kernel.execute("answer['content'] = 'Test'")

        # Sync answer manager
        kernel_answer = kernel.namespace["answer"]
        if isinstance(kernel_answer, dict):
            answer_mgr.update_content(kernel_answer.get("content", ""))
            if kernel_answer.get("ready", False):
                answer_mgr.set_ready(True)

        assert answer_mgr.get_content() == "Test"

    def test_answer_manager_reset(self):
        """Test answer manager reset."""
        answer_mgr = AnswerManager()
        answer_mgr.update_content("Test")
        answer_mgr.set_ready(True)

        answer_mgr.reset()
        assert answer_mgr.get_content() == ""
        assert answer_mgr.is_ready() is False

    def test_answer_state_from_manager(self):
        """Test AnswerState from AnswerManager."""
        answer_mgr = AnswerManager()
        answer_mgr.update_content("Hello")
        answer_mgr.set_ready(True)

        state = answer_mgr.get_state()
        assert isinstance(state, AnswerState)
        assert state.content == "Hello"
        assert state.ready is True


class TestCodeExtractionIntegration:
    """Test code extraction integration."""

    def test_extract_python_code_simple(self):
        """Test simple Python code extraction."""
        code = _codes  # local alias for the strict pipeline helper

        response = "```python\nx = 1 + 1\nprint(x)\n```"
        code = _codes(response)
        assert isinstance(code, str)
        assert "x = 1 + 1" in code
        assert "print(x)" in code

    def test_extract_python_code_multiple(self):
        """Test multiple Python code blocks."""
        code = _codes  # local alias for the strict pipeline helper

        response = "```python\nx = 1\n```\nSome text\n```python\ny = 2\n```"
        code = _codes(response)
        assert "x = 1" in code
        assert "y = 2" in code

    def test_extract_python_code_no_code(self):
        """Test no code extraction."""
        code = _codes  # local alias for the strict pipeline helper

        response = "No code here, just text."
        code = _codes(response)
        assert code == ""

    def test_extract_and_execute_pipeline(self):
        """Test full extract and execute pipeline."""
        code = _codes  # local alias for the strict pipeline helper

        response = "```python\nresult = 2 + 2\nanswer['content'] = f'Result: {result}'\nanswer['ready'] = True\n```"

        # Extract code (returns string, not list)
        code = _codes(response)
        assert len(code) > 0

        # Execute in kernel
        kernel = PythonKernel()
        result = kernel.execute(code)

        # Check result
        assert result.success
        assert result.answer_ready is True
        assert "Result: 4" == result.answer_content


class TestREPLResult:
    """Test REPLResult dataclass."""

    def test_repl_result_creation(self):
        """Test REPLResult creation."""
        result = REPLResult(success=True, output="test")
        assert result.success
        assert result.output == "test"
        assert result.error == ""
        assert result.answer_ready is False
        assert result.answer_content == ""

    def test_repl_result_to_dict(self):
        """Test REPLResult serialization."""
        result = REPLResult(
            success=True,
            output="test",
            answer_ready=True,
            answer_content="Hello"
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "test"
        assert d["answer_ready"] is True
        assert d["answer_content"] == "Hello"

    def test_repl_result_error(self):
        """Test REPLResult with error."""
        result = REPLResult(
            success=False,
            error="NameError: x is not defined",
            exception_type="NameError"
        )
        assert not result.success
        assert "NameError" in result.error


class TestAnswerState:
    """Test AnswerState dataclass."""

    def test_answer_state_creation(self):
        """Test AnswerState creation."""
        state = AnswerState(content="Hello", ready=True)
        assert state.content == "Hello"
        assert state.ready is True

    def test_answer_state_to_dict(self):
        """Test AnswerState serialization."""
        state = AnswerState(content="Hello", ready=True)
        d = state.to_dict()
        assert d["content"] == "Hello"
        assert d["ready"] is True

    def test_answer_state_from_dict(self):
        """Test AnswerState deserialization."""
        d = {"content": "Hello", "ready": True}
        state = AnswerState.from_dict(d)
        assert state.content == "Hello"
        assert state.ready is True

    def test_answer_state_clone(self):
        """Test AnswerState cloning."""
        state = AnswerState(content="Hello", ready=True)
        cloned = state.clone()
        assert cloned.content == state.content
        assert cloned.ready == state.ready
        assert cloned is not state


class TestKernelStatePersistence:
    """Test kernel state persistence across executions."""

    def test_variable_persistence(self):
        """Test variables persist across executions."""
        kernel = PythonKernel()
        kernel.execute("x = 42")
        result = kernel.execute("print(x)")
        assert result.success
        assert "42" in result.output

    def test_import_persistence(self):
        """Test imports persist across executions."""
        kernel = PythonKernel()
        kernel.execute("import json")
        result = kernel.execute("print(json.dumps({'key': 'value'}))")
        assert result.success
        assert '"key"' in result.output

    def test_function_definition_persistence(self):
        """Test function definitions persist."""
        kernel = PythonKernel()
        kernel.execute("def add(a, b): return a + b")
        result = kernel.execute("print(add(2, 3))")
        assert result.success
        assert "5" in result.output
