"""Tests for improved error handling in TauRLM."""

import pytest
from rlm.kernel import PythonKernel


class TestKernelErrorMessages:
    """Test that kernel error messages are directive and structured."""

    def test_error_has_severity_tag(self):
        """All errors include a severity tag."""
        kernel = PythonKernel()
        result = kernel.execute("print(undefined_variable)")
        assert not result.success
        assert "RECOVERABLE" in result.error or "RETRY" in result.error or "FATAL" in result.error

    def test_error_has_count(self):
        """Errors include a consecutive error count."""
        kernel = PythonKernel()
        result = kernel.execute("print(undefined_variable)")
        assert not result.success
        assert "error #1" in result.error

    def test_recoverable_error_has_fix_directive(self):
        """RECOVERABLE errors teach the _code{N} tail-exec recovery."""
        kernel = PythonKernel()
        result = kernel.execute("print(undefined_variable)")
        assert not result.success
        assert "run ONLY the tail" in result.error

    def test_name_error_shows_exception(self):
        """NameError includes the actual exception message."""
        kernel = PythonKernel()
        result = kernel.execute("print(undefined_variable)")
        assert not result.success
        assert "NameError" in result.error
        assert "undefined_variable" in result.error

    def test_syntax_error_shows_line(self):
        """SyntaxError includes line number and code context."""
        kernel = PythonKernel()
        result = kernel.execute("print(hello")
        assert not result.success
        assert "SyntaxError" in result.error
        assert "line" in result.error.lower()

    def test_fatal_error_has_strategy_change(self):
        """FATAL errors tell the model to change strategy."""
        kernel = PythonKernel()
        # RecursionError is FATAL
        result = kernel.execute("def f(): return f()\nf()")
        assert not result.success
        assert "FATAL" in result.error
        assert "Change strategy" in result.error

    def test_long_code_trimmed(self):
        """Errors in long code show trimmed context, not full dump."""
        kernel = PythonKernel()
        # 20-line code with error on line 15
        code = "\n".join([f"x{i} = {i}" for i in range(20)])
        code += "\nbad_syntax("
        result = kernel.execute(code)
        assert not result.success
        # Should have ellipsis for trimmed sections
        assert "..." in result.error

    def test_error_count_increments(self):
        """error_count parameter is reflected in the message."""
        kernel = PythonKernel()
        result = kernel.execute("print(undefined_variable)", error_count=3)
        assert not result.success
        assert "error #3" in result.error


class TestLoopErrorMessages:
    """Test that loop error messages are helpful."""

    def test_no_code_message_has_options(self):
        """No-CODE message lists options; body moved to _no_code_message
        (fence-aware refactor), so inspect the helper and confirm the loop
        still routes through it."""
        import inspect
        from agent_loop import run_rlm_loop, _no_code_message
        msg_source = inspect.getsource(_no_code_message)
        assert "host_request()" in msg_source
        assert "answer['content']" in msg_source
        loop_source = inspect.getsource(run_rlm_loop)
        assert "_no_code_message(" in loop_source

    def test_max_turns_message_has_suggestions(self):
        """Test max turns message includes suggestions."""
        import inspect
        from agent_loop import run_rlm_loop
        source = inspect.getsource(run_rlm_loop)
        assert "Breaking it into smaller" in source or "subtasks" in source

    def test_consecutive_errors_message_has_suggestions(self):
        """Test consecutive errors message includes suggestions."""
        import inspect
        from agent_loop import run_rlm_loop
        source = inspect.getsource(run_rlm_loop)
        assert "syntax errors" in source or "simpler" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
