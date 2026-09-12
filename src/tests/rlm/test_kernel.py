"""Tests for RLM Python Kernel (rlm/kernel.py)

Tests cover:
- Namespace creation and persistence
- Variable persistence across executions
- Output capture and truncation
- Error handling
- Timeout enforcement
- Magic commands (%cd)
- Answer dict
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from rlm.kernel import PythonKernel, REPLResult
from agent_repl_parse import CodeBlock


class TestPythonKernel:
    """Tests for PythonKernel class."""

    def test_namespace_creation(self):
        """Test that kernel creates a clean namespace."""
        kernel = PythonKernel()
        assert kernel.namespace is not None
        assert "answer" in kernel.namespace
        assert "spawn" in kernel.namespace
        assert "Path" in kernel.namespace

    def test_variable_persistence(self):
        """Test that variables persist across executions."""
        kernel = PythonKernel()
        kernel.execute("x = 42")
        assert kernel.namespace.get("x") == 42
        kernel.execute("y = x + 1")
        assert kernel.namespace.get("y") == 43

    def test_import_persistence(self):
        """Test that imports persist across executions."""
        kernel = PythonKernel()
        result = kernel.execute("import json; data = json.dumps({'a': 1})")
        assert result.success
        result2 = kernel.execute("result = json.loads(data)")
        assert result2.success
        assert kernel.namespace.get("result") == {"a": 1}

    def test_output_capture(self):
        """Test that stdout is captured."""
        kernel = PythonKernel()
        result = kernel.execute("print('hello world')")
        assert result.success
        assert "hello world" in result.output

    def test_output_truncation(self):
        """Context copy is clamped to max_output_chars + an _output{seq} hint."""
        kernel = PythonKernel(max_output_chars=50)
        result = kernel.execute("print('x' * 1000)")
        assert result.success
        # Context copy: 50 chars of content + the truncation hint line.
        assert result.output.startswith("x" * 50)
        assert "full output in _output" in result.output
        # The FULL output survives in the namespace, uncapped by max_output_chars.
        stored = kernel.namespace[f"_output{result.code_seq}"]
        assert len(stored) == 1001  # 1000 chars + trailing newline from print()
        assert "truncated" in result.output

    def test_error_handling(self):
        """Test that errors are captured gracefully."""
        kernel = PythonKernel()
        result = kernel.execute("1/0")
        assert not result.success
        assert "ZeroDivisionError" in result.error
        assert result.exception_type == "ZeroDivisionError"
    def test_cd_magic(self):
        """Test %cd magic command."""
        kernel = PythonKernel()
        original_dir = Path.cwd()
        result = kernel.execute("%cd /tmp")
        assert result.success
        assert kernel.get_working_dir() == Path("/tmp")
        # Restore
        os.chdir(original_dir)

    def test_cd_magic_invalid(self):
        """Test %cd magic with invalid path."""
        kernel = PythonKernel()
        result = kernel.execute("%cd /nonexistent/path")
        assert "cd error" in result.error

    def test_state_reset(self):
        """Test that state can be reset."""
        kernel = PythonKernel()
        kernel.execute("x = 42")
        assert kernel.namespace.get("x") == 42
        kernel.clear_state()
        assert "x" not in kernel.namespace

    def test_answer_dict_available(self):
        """Test that answer dict is available in namespace."""
        kernel = PythonKernel()
        result = kernel.execute("answer['content'] = 'test answer'")
        assert result.success
        assert kernel.get_answer_content() == "test answer"

    def test_answer_ready(self):
        """Test that answer ready flag works."""
        kernel = PythonKernel()
        kernel.execute("answer['ready'] = True")
        assert kernel.is_answer_ready()

    def test_spawn_stub(self):
        """Test that spawn() raises RuntimeError without parent agent."""
        kernel = PythonKernel()
        result = kernel.execute("spawn('test')")
        assert not result.success
        assert "RuntimeError" in result.error or "requires a parent agent" in result.error

    def test_preloaded_modules(self):
        """Test that common modules are preloaded."""
        kernel = PythonKernel()
        result = kernel.execute("json.dumps({'test': 1})")
        assert result.success
        result2 = kernel.execute("result2 = math.sqrt(16)")
        assert result2.success
        assert kernel.namespace.get("result2") == 4.0

    def test_execution_count(self):
        """Test that execution count is tracked."""
        kernel = PythonKernel()
        assert kernel.total_executions == 0
        kernel.execute("x = 1")
        assert kernel.total_executions == 1
        kernel.execute("y = 2")
        assert kernel.total_executions == 2

    def test_duration_ms(self):
        """Test that duration is tracked."""
        kernel = PythonKernel()
        result = kernel.execute("time.sleep(0.1)")
        assert result.duration_ms >= 100  # At least 100ms

    def test_result_to_dict(self):
        """Test REPLResult serialization."""
        result = REPLResult(success=True, output="hello", duration_ms=10.0)
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "hello"
        assert d["duration_ms"] == 10.0

    def test_multiline_code(self):
        """Test multiline code execution."""
        kernel = PythonKernel()
        code = """
for i in range(3):
    print(i)
"""
        result = kernel.execute(code)
        assert result.success
        assert "0" in result.output
        assert "1" in result.output
        assert "2" in result.output

    def test_function_definition(self):
        """Test function definition and persistence."""
        kernel = PythonKernel()
        kernel.execute("def add(a, b): return a + b")
        result = kernel.execute("result = add(2, 3)")
        assert result.success
        assert kernel.namespace.get("result") == 5

    def test_class_definition(self):
        """Test class definition and persistence."""
        kernel = PythonKernel()
        kernel.execute("class Counter:\n    def __init__(self):\n        self.count = 0\n    def increment(self):\n        self.count += 1")
        result = kernel.execute("c = Counter(); c.increment(); print(c.count)")
        assert result.success
        assert "1" in result.output
    def test_empty_code_execution(self):
        """Test that empty code executes without error."""
        kernel = PythonKernel()
        result = kernel.execute("")
        assert result.success
        assert result.output == ""

    def test_whitespace_only_code(self):
        """Test that whitespace-only code executes without error."""
        kernel = PythonKernel()
        result = kernel.execute("   \n\n   ")
        assert result.success
        assert result.output == ""

    def test_answer_dict_reset_on_clear_state(self):
        """Test that answer dict is reset when state is cleared."""
        kernel = PythonKernel()
        kernel.execute("answer['content'] = 'test'")
        kernel.execute("answer['ready'] = True")
        assert kernel.is_answer_ready()
        kernel.clear_state()
        assert not kernel.is_answer_ready()
        assert kernel.get_answer_content() == ""
    def test_working_dir_persistence(self):
        """Test that working directory persists across executions."""
        kernel = PythonKernel()
        original_dir = Path.cwd()
        try:
            kernel.execute("%cd /tmp")
            assert kernel.get_working_dir() == Path("/tmp")
            # Next execution should still be in /tmp
            result = kernel.execute("import os; print(os.getcwd())")
            assert result.success
            assert "/tmp" in result.output
        finally:
            os.chdir(original_dir)


# Import os for test_cd_magic
import os


class TestNestedErrorDetection:
    """Tests for nested code error detection (ast.parse, compile, eval)."""

    def setup_method(self):
        self.kernel = PythonKernel()

    def test_ast_parse_string_error_detected_as_nested(self):
        r = self.kernel.execute("import ast\nbad = 'def f():\\ndef g():\\n    pass'\nast.parse(bad)")
        assert not r.success
        assert "NOT in your executed code" in r.error
        assert "def g():" in r.error  # problematic line content shown

    def test_ast_parse_file_error_detected_as_nested(self):
        # Write a broken file
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def broken():\ndef next():\n    pass\n")
            tmp = f.name
        try:
            r = self.kernel.execute(f"import ast\n_f = open({tmp!r}); _src = _f.read(); _f.close()\nast.parse(_src)")
            assert not r.success
            assert "NOT in your executed code" in r.error
        finally:
            os.unlink(tmp)

    def test_compile_with_filename_shows_filename(self):
        r = self.kernel.execute("bad = 'def f():\\n'\ncompile(bad, 'myfile.py', 'exec')")
        assert not r.success
        assert "myfile.py" in r.error
        assert "NOT in your executed code" in r.error

    def test_eval_error_detected_as_nested(self):
        r = self.kernel.execute("eval('x +')")
        assert not r.success
        assert "NOT in your executed code" in r.error

    def test_normal_syntax_error_not_flagged_nested(self):
        r = self.kernel.execute("x = 1\ny = x +\nz = 3")
        assert not r.success
        assert "NOT in your executed code" not in r.error
        assert "run ONLY the tail" in r.error

    def test_name_error_not_flagged_nested(self):
        r = self.kernel.execute("print(undefined_var)")
        assert not r.success
        assert "NOT in your executed code" not in r.error
        assert "run ONLY the tail" in r.error

    def test_nested_error_includes_hint(self):
        r = self.kernel.execute("import ast\nbad = 'def f():\\ndef g():\\n    pass'\nast.parse(bad)")
        assert not r.success
        assert "NOT in your executed code" in r.error


class TestBashBlockExecution:
    """Tests for bash block execution in execute_blocks."""

    def test_bash_block_runs(self):
        kernel = PythonKernel()
        result = kernel.execute_blocks([CodeBlock(language="bash", code="echo hello")])
        assert result.success
        assert "hello" in result.output

    def test_bash_nonzero_exit_continues(self):
        kernel = PythonKernel()
        result = kernel.execute_blocks([
            CodeBlock(language="bash", code="exit 1"),
            CodeBlock(language="python", code="x = 42"),
        ])
        # Non-zero exit is a warning, execution continues
        assert result.success
        assert kernel.namespace.get("x") == 42

    def test_python_bash_python_order(self):
        kernel = PythonKernel()
        result = kernel.execute_blocks([
            CodeBlock(language="python", code="a = 1"),
            CodeBlock(language="bash", code="echo mid"),
            CodeBlock(language="python", code="b = a + 1"),
        ])
        assert result.success
        assert kernel.namespace.get("a") == 1
        assert kernel.namespace.get("b") == 2
        assert "mid" in result.output

    def test_empty_bash_block_skipped(self):
        kernel = PythonKernel()
        result = kernel.execute_blocks([
            CodeBlock(language="bash", code=""),
            CodeBlock(language="python", code="y = 99"),
        ])
        assert result.success
        assert kernel.namespace.get("y") == 99

    def test_bash_timeout_is_fatal(self):
        """Bash timeout should stop remaining blocks."""
        import subprocess as sp
        kernel = PythonKernel()
        kernel.bash_timeout_seconds = 0.1  # very short timeout
        original_run = sp.run
        def mock_run(*args, **kwargs):
            raise sp.TimeoutExpired(cmd="sleep", timeout=0.1)
        sp.run = mock_run
        try:
            result = kernel.execute_blocks([
                CodeBlock(language="bash", code="sleep 10"),
                CodeBlock(language="python", code="z = 123"),
            ])
            assert not result.success
            assert "timeout" in result.error.lower()
            # z should NOT be set (python block didn't run)
            assert kernel.namespace.get("z") is None
        finally:
            sp.run = original_run


class TestFormatExecutionError:
    """Tests for PythonKernel._format_execution_error extracted method."""

    def _make_kernel(self):
        from rlm.kernel import PythonKernel
        return PythonKernel(max_output_chars=100)

    def _make_syntax_error(self, lineno=2, filename="<string>", text="  bad indent", offset=3):
        """Create a SyntaxError with proper attributes for testing."""
        e = SyntaxError("invalid syntax")
        e.lineno = lineno
        e.filename = filename
        e.text = text
        e.offset = offset
        return e

    def test_direct_error_with_caret(self):
        """Error in executed code produces caret pointer and fix directive."""
        k = self._make_kernel()
        code = "x = 1\n  bad indent\n"
        e = self._make_syntax_error(lineno=2, filename="<string>", text="  bad indent", offset=3)
        result = k._format_execution_error(e, code)
        assert "SyntaxError" in result
        assert "line 2" in result
        assert "^" in result
        assert "Do not re-emit" in result

    def test_name_error_no_lineno(self):
        """NameError without lineno produces a simpler message."""
        k = self._make_kernel()
        code = "x = undefined_var\n"
        e = NameError("name 'undefined_var' is not defined")
        result = k._format_execution_error(e, code)
        assert "NameError" in result
        assert "Do not re-emit" in result

    def test_prefix_parameter(self):
        """Prefix is prepended to the error type line."""
        k = self._make_kernel()
        code = "x = 1\n"
        e = NameError("name 'x' is not defined")
        result = k._format_execution_error(e, code, prefix="Block 1 of 2, ")
        assert result.startswith("Block 1 of 2, NameError")

    def test_nested_error_detection(self):
        """Error with filename not in _NESTED_MARKERS is classified as nested."""
        k = self._make_kernel()
        code = "import ast\nast.parse(open('bad.py').read())\n"
        e = self._make_syntax_error(lineno=1, filename="bad.py", text="x =", offset=1)
        result = k._format_execution_error(e, code)
        assert "NOTE: This error is NOT in your executed code" in result
        assert "bad.py" in result

    def test_indentation_error_type(self):
        """IndentationError is reported with its type name."""
        k = self._make_kernel()
        code = "if True:\n    pass\n  bad\n"
        e = IndentationError("unexpected indent")
        e.lineno = 3
        e.filename = "<string>"
        e.text = "  bad"
        e.offset = 2
        result = k._format_execution_error(e, code)
        assert "IndentationError" in result
        assert "3 |" in result  # line number in code context
