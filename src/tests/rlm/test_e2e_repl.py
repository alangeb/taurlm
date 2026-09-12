"""E2E Tests for RLM REPL — full loop tests.

These tests verify the full RLM loop works correctly with various inputs:
- Multi-turn conversation handling
- File read/write via REPL
- Shell commands via REPL
- Answer mechanism
- Mixed markdown and code handling
"""

import pytest
from agent_fence_repair import repair_response
from agent_repl_parse import extract_code_blocks

BT3 = chr(96) * 3


def _codes(text, style="std"):
    """Pipeline path: repair then strict-extract, joined (replaces extract_python_code)."""
    repaired, _ = repair_response(text, style)
    return "\n\n".join(b.code for b in extract_code_blocks(repaired, fence_style=style))



class TestE2EMultiTurn:
    """Test multi-turn conversation handling."""

    def test_multi_code_block_extraction(self):
        """Test extracting multiple code blocks from a response."""
        response = """First, let me set up the data:
```python
x = [1, 2, 3, 4, 5]
```

Now let me process it:
```python
result = sum(x)
print(result)
```

The answer is 15."""
        blocks = extract_code_blocks(response)
        assert len(blocks) == 2
        assert "x = [1, 2, 3, 4, 5]" in blocks[0].code
        assert "result = sum(x)" in blocks[1].code

    def test_code_then_answer(self):
        """Test code followed by answer dict."""
        response = """```python
import os
files = os.listdir('.')
answer['content'] = str(len(files))
answer['ready'] = True
```"""
        code = _codes(response)
        assert "import os" in code
        assert "answer['content']" in code
        assert "answer['ready']" in code

    def test_concatenated_python_blocks(self):
        """Test that multiple python blocks are concatenated."""
        response = """```python
x = 1
```

```python
y = 2
```"""
        code = _codes(response)
        assert "x = 1" in code
        assert "y = 2" in code


class TestE2EFileOps:
    """Test file operations via REPL."""

    def test_file_read_write_roundtrip(self, tmp_path):
        """Test reading and writing files."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        # Simulate REPL code for reading
        code = f"_f = open('{test_file}'); content = _f.read(); _f.close()"
        ns = {}
        exec(code, ns)
        assert ns["content"] == "Hello, World!"

        # Simulate REPL code for writing
        write_file = tmp_path / "output.txt"
        code = f"with open('{write_file}', 'w') as _f: _f.write('New content')"
        exec(code, ns)
        assert write_file.read_text() == "New content"

    def test_json_read_write(self, tmp_path):
        """Test JSON file operations."""
        import json
        test_file = tmp_path / "data.json"

        # Write JSON
        data = {"key": "value", "number": 42}
        code = f"import json; _f = open('{test_file}', 'w'); json.dump({data}, _f); _f.close()"
        exec(code, {"json": json})

        # Read JSON
        code = f"_f = open('{test_file}'); result = json.load(_f); _f.close()"
        ns = {"json": json}
        exec(code, ns)
        assert ns["result"] == data

    def test_pathlib_operations(self, tmp_path):
        """Test pathlib operations."""
        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        # Simulate pathlib listing
        code = f"from pathlib import Path; files = list(Path('{tmp_path}').glob('*.py'))"
        ns = {}
        exec(code, ns)
        assert len(ns["files"]) >= 1
        assert any("test.py" in str(f) for f in ns["files"])


class TestE2EShellCommands:
    def test_shell_via_subprocess(self):
        """Test shell execution via subprocess."""
        import subprocess
        result = subprocess.run(["echo", "hello"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_bash_fence_extracted_as_bash(self):
        """NEW RULE: bash fences ARE extracted (as bash) — the kernel runs them.

        Inverts the old assertion, which expected the deleted extract_python_code
        helper to silently drop bash blocks.
        """
        response = "```bash\nls -la\n```"
        blocks = extract_code_blocks(response)
        assert [b.language for b in blocks] == ["bash"]
        assert blocks[0].code == "ls -la"
        assert "subprocess" not in blocks[0].code   # no more bash->python rewriting


class TestE2EAnswerMechanism:
    """Test answer mechanism."""

    def test_answer_ready(self):
        """Test answer ready detection."""
        ns = {"answer": {"content": "", "ready": False}}
        code = """
answer['content'] = "The result is 42"
answer['ready'] = True
"""
        exec(code, ns)
        assert ns["answer"]["ready"] is True
        assert ns["answer"]["content"] == "The result is 42"

    def test_answer_not_ready(self):
        """Test answer not ready."""
        ns = {"answer": {"content": "", "ready": False}}
        code = """
x = 1 + 2
print(x)
"""
        exec(code, ns)
        assert ns["answer"]["ready"] is False

    def test_answer_update(self):
        """Test answer content update."""
        ns = {"answer": {"content": "initial", "ready": False}}
        code = """
answer['content'] = "updated"
"""
        exec(code, ns)
        assert ns["answer"]["content"] == "updated"
        assert ns["answer"]["ready"] is False


class TestE2EMixedMarkdown:
    """Test mixed markdown and code handling."""

    def test_markdown_with_code_blocks(self):
        """Test extracting code from markdown."""
        response = """# Analysis

Here's what I found:

1. First, I'll read the file
2. Then process it
3. Finally, return the answer

```python
with open('data.txt') as f:
    lines = f.readlines()
result = len(lines)
answer['content'] = f"Found {result} lines"
answer['ready'] = True
```

That's the complete analysis."""
        code = _codes(response)
        assert "open('data.txt')" in code
        assert "answer['ready']" in code

    def test_only_python_blocks_extracted(self):
        """Test that only python fences are extracted (strict rules)."""
        response = "```bash\nls -la\n```\n\n```python\nimport os\nfiles = os.listdir('.')\n```\n\n```json\n\"files\": 42\n```"
        blocks = extract_code_blocks(response)
        # Only python block extracted
        assert len(blocks) == 2
        assert blocks[0].language == "bash"
        assert blocks[1].language == "python"
        assert "import os" in blocks[1].code

    def test_bare_fence_rejected(self):
        """Test that bare fence (no language) is not a valid opening."""
        response = "```\nx = 1 + 2\nprint(x)\n```"
        blocks = extract_code_blocks(response)
        assert len(blocks) == 0  # bare fence not a valid opening

    def test_inline_code_ignored(self):
        """Test that inline code is not extracted as blocks."""
        response = "Use `x = 1` to set the value."
        blocks = extract_code_blocks(response)
        assert len(blocks) == 0  # No code blocks


class TestE2EEdgeCases:
    """Test edge cases."""

    def test_empty_response(self):
        """Test empty response."""
        code = _codes("")
        assert code == ""

    def test_no_code_response(self):
        """Test response with no code."""
        code = _codes("Hello, how can I help you?")
        assert code == ""

    def test_code_with_triple_quotes(self):
        """Test code with triple quotes inside."""
        response = """```python
docstring = '''
This is a docstring.
'''
print(docstring)
```"""
        code = _codes(response)
        assert "docstring" in code
        assert "This is a docstring" in code

    def test_nested_backticks(self):
        """Test nested backticks."""
        response = """````markdown
Here's some code:
```python
x = 1
```
````"""
        blocks = extract_code_blocks(response)
        # Should handle nested backticks
        assert len(blocks) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
