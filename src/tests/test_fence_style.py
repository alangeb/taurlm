"""Focused tests for fence_tokens and fence-style propagation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_repl_parse import fence_tokens


class TestFenceTokens:
    """Verify fence_tokens returns correct delimiters for each style."""

    def test_at_style(self):
        ft = fence_tokens("at")
        assert ft["py_open"] == "@PY"
        assert ft["py_close"] == "@/PY"
        assert ft["sh_open"] == "@SH"
        assert ft["sh_close"] == "@/SH"

    def test_std_style(self):
        ft = fence_tokens("std")
        assert ft["py_open"] == "```python"
        assert ft["py_close"] == "```"
        assert ft["sh_open"] == "```bash"
        assert ft["sh_close"] == "```"

    def test_html_style(self):
        ft = fence_tokens("html")
        assert ft["py_open"] == "<py>"
        assert ft["py_close"] == "</py>"
        assert ft["sh_open"] == "<sh>"
        assert ft["sh_close"] == "</sh>"

    def test_quad_style(self):
        ft = fence_tokens("quad")
        assert ft["py_open"] == "``````python"
        assert ft["py_close"] == "``````"
        assert ft["sh_open"] == "``````bash"
        assert ft["sh_close"] == "``````"

    def test_invalid_falls_back_to_std(self):
        ft = fence_tokens("invalid")
        std = fence_tokens("std")
        assert ft == std


class TestReadSystemPromptFenceStyle:
    """Verify read_system_prompt applies fence-style replacements."""

    def test_at_style_contains_at_py(self):
        from agent_subsystems import read_system_prompt
        prompt = read_system_prompt(fence_style="at")
        assert "@PY" in prompt, "Expected @PY in system prompt for 'at' style"
        assert "@/PY" in prompt, "Expected @/PY in system prompt for 'at' style"

    def test_std_style_contains_triple_backtick_python(self):
        from agent_subsystems import read_system_prompt
        prompt = read_system_prompt(fence_style="std")
        assert "```python" in prompt, "Expected ```python in system prompt for 'std' style"


class TestNoCodeMessage:
    """Verify the NO CODE message in agent_loop uses correct style tokens."""

    def _get_no_code_snippet(self, fence_style):
        """Extract the no_code_msg construction from agent_loop.py source."""
        from agent_repl_parse import fence_tokens
        ft = fence_tokens(fence_style)
        # Replicate the exact message construction from agent_loop.py
        no_code_msg = (
            "[SYSTEM: NO CODE] Your response contains no code blocks. "
            "In RLM mode, you must execute Python code to make progress. "
            f"Options: (1) Write code in {ft['py_label']} or {ft['sh_label']} blocks, "
            "(2) Write a single code block setting answer['content'] and answer['ready'] = True, "
            "(3) Use available functions: host_request(), "
            "available_skills(), load_skill(), spawn()."
        )
        return no_code_msg

    def test_at_style_no_code(self):
        msg = self._get_no_code_snippet("at")
        assert "@PY" in msg
        assert "@SH" in msg

    def test_std_style_no_code(self):
        msg = self._get_no_code_snippet("std")
        assert "```python" in msg
        assert "```bash" in msg

    def test_agent_loop_source_uses_fence_tokens(self):
        """Verify agent_loop.py actually calls fence_tokens for the NO CODE msg."""
        src = Path(__file__).resolve().parent.parent / "agent_loop.py"
        content = src.read_text()
        # The NO CODE block should reference fence_tokens
        idx = content.find("NO CODE")
        chunk = content[max(0, idx - 200):idx + 400]
        assert "fence_tokens" in chunk, "NO CODE message should use fence_tokens()"
        assert "py_label" in chunk, "NO CODE message should use py_label"
        assert "sh_label" in chunk, "NO CODE message should use sh_label"
