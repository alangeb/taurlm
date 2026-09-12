"""Integration tests for TauRLM RLM components.

Tests cover:
- Full RLM loop (code extraction → REPL execution → answer check)
- Kernel timeout handling
- Host request integration (host_request in kernel namespace)
- Agent message integration (agent_message in kernel namespace)
- Skills integration (available_skills, load_skill in kernel namespace)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from rlm.kernel import PythonKernel
from rlm.answer import AnswerManager


from agent_fence_repair import repair_response
from agent_repl_parse import extract_code_blocks


def _codes(text, style='std'):
    """Pipeline path: repair the message, then strict-extract and join."""
    repaired, _notes = repair_response(text, style)
    return '\n\n'.join(b.code for b in extract_code_blocks(repaired, fence_style=style))


class TestKernelNamespaceFunctions:
    """Test that all expected functions are available in the kernel namespace."""

    def test_host_request_available(self):
        """Test that host_request is available in kernel namespace."""
        kernel = PythonKernel()
        assert "host_request" in kernel.namespace
        assert callable(kernel.namespace["host_request"])

    def test_list_spawns_available(self):
        """Test that list_spawns and get_spawn are available in kernel namespace."""
        kernel = PythonKernel()
        assert "list_spawns" in kernel.namespace
        assert callable(kernel.namespace["list_spawns"])
        assert "get_spawn" in kernel.namespace
        assert callable(kernel.namespace["get_spawn"])


    def test_available_skills_available(self):
        """Test that available_skills is available in kernel namespace."""
        kernel = PythonKernel()
        assert "available_skills" in kernel.namespace
        assert callable(kernel.namespace["available_skills"])

    def test_load_skill_available(self):
        """Test that load_skill is available in kernel namespace."""
        kernel = PythonKernel()
        assert "load_skill" in kernel.namespace
        assert callable(kernel.namespace["load_skill"])

    def test_spawn_available(self):
        """Test that spawn is available in kernel namespace."""
        kernel = PythonKernel()
        assert "spawn" in kernel.namespace
        assert callable(kernel.namespace["spawn"])

    def test_answer_available(self):
        """Test that answer dict is available in kernel namespace."""
        kernel = PythonKernel()
        assert "answer" in kernel.namespace
        assert isinstance(kernel.namespace["answer"], dict)
        assert "content" in kernel.namespace["answer"]
        assert "ready" in kernel.namespace["answer"]


class TestCodeExtractionIntegration:
    """Test code extraction from LLM responses."""

    def test_extract_single_code_block(self):
        """Test extracting a single code block."""
        code = _codes  # local alias for the strict pipeline helper

        response = """Here's the code:

```python
x = 42
print(x)
```

Done!"""
        code = _codes(response)
        assert "x = 42" in code
        assert "print(x)" in code

    def test_extract_multiple_code_blocks(self):
        """Test extracting multiple code blocks."""
        code = _codes  # local alias for the strict pipeline helper

        response = """First:
```python
x = 1
```

Second:
```python
y = 2
```"""
        code = _codes(response)
        assert "x = 1" in code
        assert "y = 2" in code

    def test_extract_no_code_block(self):
        """Test response with no code block."""
        code = _codes  # local alias for the strict pipeline helper

        response = "Just text, no code here."
        code = _codes(response)
        assert code == ""

    def test_extract_with_answer_ready(self):
        """Test code with answer['ready'] = True."""
        code = _codes  # local alias for the strict pipeline helper

        response = """```python
answer['content'] = 'done'
answer['ready'] = True
```"""
        code = _codes(response)
        assert "answer['ready'] = True" in code


class TestAnswerMechanismIntegration:
    """Test answer mechanism integration."""

    def test_answer_set_and_check(self):
        """Test setting and checking answer."""
        manager = AnswerManager()
        manager.update_content("test answer")
        manager.set_ready(True)
        assert manager.is_ready() is True
        assert manager.get_content() == "test answer"

    def test_answer_not_ready_initially(self):
        """Test that answer is not ready initially."""
        manager = AnswerManager()
        assert manager.is_ready() is False

    def test_answer_reset(self):
        """Test resetting answer."""
        manager = AnswerManager()
        answer_dict = manager.get_dict()
        answer_dict["content"] = "test"
        answer_dict["ready"] = True
        manager.reset()
        assert manager.is_ready() is False
        assert manager.get_content() == ""


class TestFullREPLFlow:
    """Test full REPL flow: code execution → answer check."""

    def test_repl_compute_and_answer(self):
        """Test computing a value and setting answer."""
        kernel = PythonKernel()
        # Execute computation
        result = kernel.execute("result = 2 + 2")
        assert result.success
        # Set answer
        result = kernel.execute(
            "answer['content'] = str(result)\nanswer['ready'] = True"
        )
        assert result.success
        assert result.answer_ready is True
        assert result.answer_content == "4"

    def test_repl_file_read_and_answer(self):
        """Test reading a file and setting answer."""
        kernel = PythonKernel()
        # Write a test file
        kernel.execute(
            "Path('/tmp/test_repl_flow.txt').write_text('hello world')"
        )
        # Read it back
        result = kernel.execute(
            "content = Path('/tmp/test_repl_flow.txt').read_text()\n"
            "answer['content'] = content\n"
            "answer['ready'] = True"
        )
        assert result.success
        assert result.answer_content == "hello world"

    def test_repl_error_recovery(self):
        """Test that errors don't break subsequent executions."""
        kernel = PythonKernel()
        # Cause an error
        result = kernel.execute("1/0")
        assert not result.success
        # Should still work
        result = kernel.execute("x = 42")
        assert result.success
        assert kernel.namespace.get("x") == 42

    def test_repl_variable_persistence(self):
        """Test that variables persist across executions."""
        kernel = PythonKernel()
        kernel.execute("x = 10")
        kernel.execute("y = x * 2")
        kernel.execute("z = y + 5")
        assert kernel.namespace.get("z") == 25
