"""Tests for RLM main loop implementation.

Tests cover:
- RLM loop execution
- Answer ready detection
- Max turns protection
- Error recovery
- REPL output in context
- LLM without tools
- Context management (append_repl_output, append_repl_error)
"""
import inspect
from unittest.mock import MagicMock, patch

# Import RLM components
from rlm.kernel import PythonKernel
from rlm.answer import AnswerManager


from agent_fence_repair import repair_response
from agent_repl_parse import extract_code_blocks


def _codes(text, style='std'):
    """Pipeline path: repair the message, then strict-extract and join."""
    repaired, _notes = repair_response(text, style)
    return '\n\n'.join(b.code for b in extract_code_blocks(repaired, fence_style=style))


class TestAppendREPLOutput:
    """Test context.append_repl_output() method."""

    def test_append_repl_output_basic(self):
        """Test basic REPL output appending."""
        from agent_context import TauContext

        ctx = TauContext()
        ctx.set_system("test system")
        ctx.append_user("test", user_type="real")

        ctx.append_repl_output("print('hello')\nhello")

        # Check that synthetic user message was added
        messages = ctx.get_messages()
        # Messages: system, user, repl_output (merged with user if consecutive)
        assert len(messages) >= 2
        # Check that REPL output content is in the last message
        all_content = " ".join(m.get("content", "") for m in messages)
        assert "[REPL output]" in all_content
        assert "print('hello')" in all_content

    def test_append_repl_output_truncation(self):
        """Test REPL output truncation."""
        from agent_context import TauContext

        ctx = TauContext()
        ctx.append_user("test", user_type="real")

        long_output = "x" * 10000
        ctx.append_repl_output(long_output, max_chars=100)

        messages = ctx.get_messages()
        last_msg = messages[-1]
        assert "[Output truncated" in last_msg["content"]
        assert "10000 chars total" in last_msg["content"]

    def test_append_repl_output_empty(self):
        """Test empty REPL output."""
        from agent_context import TauContext

        ctx = TauContext()
        ctx.append_user("test", user_type="real")

        ctx.append_repl_output("")

        messages = ctx.get_messages()
        last_msg = messages[-1]
        assert "[REPL output]" in last_msg["content"]


class TestAppendREPLLError:
    """Test context.append_repl_error() method."""

    def test_append_repl_error_basic(self):
        """Test basic REPL error appending."""
        from agent_context import TauContext

        ctx = TauContext()
        ctx.append_user("test", user_type="real")

        ctx.append_repl_error("NameError: x is not defined")

        messages = ctx.get_messages()
        last_msg = messages[-1]
        assert last_msg["role"] == "user"
        assert "[REPL error]" in last_msg["content"]
        assert "NameError" in last_msg["content"]

    def test_append_repl_error_with_code(self):
        """Test REPL error with code context."""
        from agent_context import TauContext

        ctx = TauContext()
        ctx.append_user("test", user_type="real")

        ctx.append_repl_error("NameError: x is not defined", code="x = undefined_var")

        messages = ctx.get_messages()
        last_msg = messages[-1]
        assert "[REPL error]" in last_msg["content"]
        assert "Code that failed:" in last_msg["content"]
        assert "x = undefined_var" in last_msg["content"]

    def test_append_repl_error_code_truncation(self):
        """Test REPL error code truncation."""
        from agent_context import TauContext

        ctx = TauContext()
        ctx.append_user("test", user_type="real")

        long_code = "x = " + "y" * 3000
        ctx.append_repl_error("Error", code=long_code)

        messages = ctx.get_messages()
        last_msg = messages[-1]
        assert "[code truncated]" in last_msg["content"]


class TestRLMLoopDispatch:
    """Test RLM loop dispatch from agent_core."""

    def test_dispatch_to_rlm_loop_when_enabled(self):
        """Test that invoke_with_tools_loop dispatches to RLM loop when enabled."""
        from agent_config import Config, RLMConfig, REPLConfig

        # Create config with RLM (always enabled)
        rlm_cfg = RLMConfig(repl=REPLConfig())

        with patch('agent_core.SimpleOpenAIClient'):
            with patch('agent_core.TauContext'):
                mock_init = MagicMock()
                mock_init.agent_name = "test"
                mock_init.llm_groups = {"test": MagicMock()}
                mock_init.current_group_name = "test"
                mock_init.model_override = None
                mock_init.base_url_override = None
                mock_init.max_context_tokens_override = None
                mock_init.model_name = "test-model"
                mock_init.base_url = "http://test"
                mock_init.api_key = "test-key"
                mock_init.max_context_tokens = 10000
                mock_init.max_tokens = None
                mock_init.timeout = 180
                mock_init.max_silent_retries = 3
                mock_init.max_enhanced_retries = 3
                mock_init.max_explicit_retries = 3
                mock_init.inference_params = None
                mock_init.loop_detection_window_size = 5
                mock_init.loop_detection_repeat_threshold = 3
                mock_init.heartbeat_enabled = False
                mock_init.heartbeat_interval = None

                with patch('agent_core.resolve_agent_init', return_value=mock_init):
                    from agent_core import TauErgon
                    # Create config with RLM (always enabled)
                    config = Config(rlm=rlm_cfg)
                    agent = TauErgon(config=config)

                    # Verify REPL kernel is initialized
                    assert agent._repl_kernel is not None

    def test_dispatch_to_rlm_loop(self):
        """Test that invoke_loop uses RLM loop.

        RLM is the only mode - there is no tool-calling loop.
        """
        import agent_core
        source = inspect.getsource(agent_core.TauErgon.invoke_loop)
        assert "run_rlm_loop" in source
        # No tool-calling loop - RLM is the only mode
        assert "run_loop" not in source


class TestRLMLoopMaxTurns:
    """Test RLM loop max turns protection."""

    def test_max_turns_configuration(self):
        """Test that max turns is read from config."""
        from agent_config import RLMConfig

        # Default max turns
        config = RLMConfig()
        assert config.max_turns == 1000

        # Custom max turns
        config = RLMConfig(max_turns=100)
        assert config.max_turns == 100

    def test_max_turns_zero_means_unlimited(self):
        """Test that max_turns=0 means unlimited."""
        from agent_config import RLMConfig

        config = RLMConfig(max_turns=0)
        assert config.max_turns == 0


class TestRLMAnswerReady:
    """Test answer ready detection in RLM loop."""

    def test_answer_ready_ends_loop(self):
        """Test that answer ready ends the loop."""
        kernel = PythonKernel()
        answer_mgr = AnswerManager()
        kernel.namespace["answer"] = answer_mgr.get_dict()

        # Execute code that sets answer ready
        code = """
answer['content'] = 'The answer is 42'
answer['ready'] = True
"""
        result = kernel.execute(code)

        assert result.success
        assert result.answer_ready
        assert result.answer_content == "The answer is 42"

    def test_answer_not_ready_continues(self):
        """Test that answer not ready continues the loop."""
        kernel = PythonKernel()
        answer_mgr = AnswerManager()
        kernel.namespace["answer"] = answer_mgr.get_dict()

        # Execute code that doesn't set ready
        code = """
answer['content'] = 'Working on it...'
"""
        result = kernel.execute(code)

        assert result.success
        assert not result.answer_ready
        assert result.answer_content == "Working on it..."

    def test_answer_progressive_updates(self):
        """Test progressive answer updates."""
        kernel = PythonKernel()
        answer_mgr = AnswerManager()
        answer_dict = answer_mgr.get_dict()
        kernel.namespace["answer"] = answer_dict

        # First update
        kernel.execute("answer['content'] = 'Step 1'")
        # Sync from kernel namespace back to answer manager
        answer_mgr.update_content(answer_dict.get('content', ''))
        state = answer_mgr.get_state()
        assert state.content == "Step 1"
        assert not state.ready

        # Second update
        kernel.execute("answer['content'] = 'Step 1 and Step 2'")
        answer_mgr.update_content(answer_dict.get('content', ''))
        state = answer_mgr.get_state()
        assert state.content == "Step 1 and Step 2"
        assert not state.ready

        # Final update with ready
        kernel.execute("answer['ready'] = True")
        answer_mgr.set_ready(answer_dict.get('ready', False))
        state = answer_mgr.get_state()
        assert state.ready
        assert state.content == "Step 1 and Step 2"


class TestRLMErrorRecovery:
    """Test RLM loop error recovery."""

    def test_syntax_error_recovery(self):
        """Test that syntax errors are captured and reported."""
        kernel = PythonKernel()

        code = """
def broken(
    # Missing closing paren
"""
        result = kernel.execute(code)

        assert not result.success
        assert "SyntaxError" in (result.error or "")

    def test_name_error_recovery(self):
        """Test that NameError is captured and reported."""
        kernel = PythonKernel()

        code = "x = undefined_variable"
        result = kernel.execute(code)

        assert not result.success
        assert "NameError" in (result.error or "")

    def test_exception_in_code(self):
        """Test that exceptions in code are captured."""
        kernel = PythonKernel()

        code = """
raise ValueError("Test error")
"""
        result = kernel.execute(code)

        assert not result.success
        assert "ValueError" in (result.error or "")


class TestRLMCodeExtraction:
    """Test Python code extraction for RLM loop."""

    def test_extract_single_code_block(self):
        """Test extracting a single Python code block."""
        code = _codes  # local alias for the strict pipeline helper

        response = """Here's the code:

```python
x = 2 + 2
print(x)
```

The result should be 4."""

        code = _codes(response)
        assert "x = 2 + 2" in code
        assert "print(x)" in code

    def test_extract_multiple_code_blocks(self):
        """Test extracting multiple Python code blocks."""
        code = _codes  # local alias for the strict pipeline helper

        response = """First block:
```python
x = 1
```

Second block:
```python
y = 2
```
"""
        code = _codes(response)
        # Multiple blocks are concatenated
        assert "x = 1" in code
        assert "y = 2" in code

    def test_no_code_blocks(self):
        """Test response with no code blocks."""
        code = _codes  # local alias for the strict pipeline helper

        response = "This is just text with no code."
        code = _codes(response)
        assert code == ""

    def test_code_block_with_answer(self):
        """Test code block that sets answer."""
        code = _codes  # local alias for the strict pipeline helper

        response = """Let me calculate that:

```python
result = 42
answer['content'] = f'The answer is {result}'
answer['ready'] = True
```
"""
        code = _codes(response)
        assert "answer['content']" in code
        assert "answer['ready']" in code


class TestRLMIntegration:
    """Integration tests for RLM loop components."""

    def test_full_pipeline_extract_execute_answer(self):
        """Test full pipeline: extract code, execute, check answer."""
        code = _codes  # local alias for the strict pipeline helper
        from rlm.kernel import PythonKernel
        from rlm.answer import AnswerManager

        # Simulate LLM response
        llm_response = """Let me solve this:

```python
x = 2 + 2
answer['content'] = f'The result is {x}'
answer['ready'] = True
```
"""
        # Extract code (returns concatenated string)
        code = _codes(llm_response)
        assert "x = 2 + 2" in code
        assert "answer['content']" in code

        # Create kernel with answer
        kernel = PythonKernel()
        answer_mgr = AnswerManager()
        answer_dict = answer_mgr.get_dict()
        kernel.namespace["answer"] = answer_dict

        # Execute
        result = kernel.execute(code)
        assert result.success
        assert result.answer_ready
        assert result.answer_content == "The result is 4"

        # Verify answer manager state (sync from kernel namespace)
        answer_mgr.update_content(answer_dict.get('content', ''))
        answer_mgr.set_ready(answer_dict.get('ready', False))
        state = answer_mgr.get_state()
        assert state.ready
        assert state.content == "The result is 4"

    def test_kernel_state_persistence(self):
        """Test that kernel state persists across executions."""
        kernel = PythonKernel()

        # First execution
        kernel.execute("x = 10")

        # Second execution uses x
        result = kernel.execute("y = x * 2\nprint(y)")
        assert result.success
        assert "20" in result.output

    def test_kernel_import_persistence(self):
        """Test that imports persist across executions."""
        kernel = PythonKernel()

        # First execution: import
        kernel.execute("import json")

        # Second execution: use import
        result = kernel.execute("data = json.dumps({'key': 'value'})\nprint(data)")
        assert result.success
        assert '"key": "value"' in result.output or '"value": "key"' in result.output.replace(' ', '')


class TestRLMConfig:
    """Test RLM configuration."""

    def test_rlm_config_defaults(self):
        """Test RLM config default values."""
        from agent_config import RLMConfig

        config = RLMConfig()
        assert config.max_turns == 1000

    def test_rlm_config_custom(self):
        """Test RLM config with custom values."""
        from agent_config import RLMConfig, REPLConfig

        config = RLMConfig(
            max_turns=100,
            repl=REPLConfig(
                max_output_chars=4096,
            ),
        )
        assert config.max_turns == 100
        assert config.repl.max_output_chars == 4096


class TestNoCodeMessageFenceAwareness:
    """Fix 1: the generic NO-CODE options list must not bury a precise
    fence-refusal note that already names the exact fix."""

    def test_generic_options_kept_when_not_a_fence_failure(self):
        from agent_loop import _no_code_message
        msg = _no_code_message("at", ambiguous_fences=False, streak=1, force_at=3)
        assert "Options:" in msg and "spawn()" in msg
        assert "fence-fix" not in msg

    def test_options_suppressed_when_fences_blamed(self):
        from agent_loop import _no_code_message
        msg = _no_code_message("at", ambiguous_fences=True, streak=1, force_at=3)
        assert "Options:" not in msg and "spawn()" not in msg
        assert "ambiguous" in msg and "fence-fix note" in msg

    def test_streak_pressure_survives_the_fence_aware_form(self):
        from agent_loop import _no_code_message
        m1 = _no_code_message("at", ambiguous_fences=True, streak=1, force_at=3)
        m2 = _no_code_message("at", ambiguous_fences=True, streak=2, force_at=3)
        assert "(attempt" not in m1
        assert "(attempt 2 of 3)" in m2
