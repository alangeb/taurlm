"""E2E-style tests for the strict extractor + repair ladder.

Rewritten for the v2 contract (specs/fence-pipeline-v2.md). The old
`extract_python_code` helper was deleted on purpose; the pipeline path is now
`repair_response` -> `extract_code_blocks` -> join (agent_pipeline.extract_code).
"""
from agent_fence_repair import repair_response
from agent_repl_parse import extract_code_blocks

BT3 = chr(96) * 3


def _pipeline(text, style="std"):
    """What the loop actually does: repair, then extract strictly."""
    repaired, _notes = repair_response(text, style)
    blocks = extract_code_blocks(repaired, fence_style=style)
    return "\n\n".join(b.code for b in blocks), blocks


class TestLanguageAliases:
    """Only the exact active opener is accepted (strict rules)."""

    def test_py_alias_rejected(self):
        assert _pipeline(f"{BT3}py\nx = 1\n{BT3}")[0] == ""

    def test_python3_alias_rejected(self):
        assert _pipeline(f"{BT3}python3\ny = 2\n{BT3}")[0] == ""

    def test_python313_alias_rejected(self):
        assert _pipeline(f"{BT3}python3.13\nz = 3\n{BT3}")[0] == ""

    def test_empty_language_rejected(self):
        assert _pipeline(f"{BT3}\na = 42\n{BT3}")[0] == ""

    def test_extract_code_blocks_strict(self):
        assert extract_code_blocks(f"{BT3}py\nx = 1\n{BT3}") == []


class TestVariableBackticks:
    """Under a given active style, only that style's token opens a block."""

    def test_four_backticks_rejected_under_std(self):
        assert _pipeline("````\nx = 1\n````")[0] == ""

    def test_five_backticks_rejected_under_std(self):
        assert _pipeline("`````\ny = 2\n`````")[0] == ""

    def test_quad_style_is_active_for_six_backticks(self):
        code, _ = _pipeline("``````python\nx = 1\n``````", style="quad")
        assert code == "x = 1"


class TestMultiBlock:
    def test_two_python_blocks(self):
        text = f"{BT3}python\na = 1\n{BT3}\nText.\n{BT3}python\nb = 2\n{BT3}"
        code, blocks = _pipeline(text)
        assert "a = 1" in code and "b = 2" in code
        assert len(blocks) == 2

    def test_python_and_bash_blocks(self):
        text = f"{BT3}python\nx = 1\n{BT3}\n{BT3}bash\necho hello\n{BT3}"
        code, blocks = _pipeline(text)
        assert "x = 1" in code
        # NEW RULE: bash blocks ARE extracted (as bash), not silently dropped
        assert [b.language for b in blocks] == ["python", "bash"]
        assert "echo hello" in code

    def test_mixed_language_blocks(self):
        text = f"{BT3}python\nimport os\n{BT3}\n{BT3}json\n{{\"key\": \"value\"}}\n{BT3}"
        code, _ = _pipeline(text)
        assert "import os" in code
        assert '"key"' not in code


class TestInlineNotExtracted:
    """Inline backtick code is NEVER extracted. Prose yields no executable code."""

    def test_inline_assignment(self):
        assert _pipeline("Try `x = 1 + 2` to calculate.")[0] == ""

    def test_inline_import(self):
        assert _pipeline("Use `import os` for file operations.")[0] == ""

    def test_inline_function_call(self):
        assert _pipeline('Call `print("hello")` to output.')[0] == ""

    def test_inline_dotted_call(self):
        assert _pipeline("We call `subprocess.run(...)` to do it.")[0] == ""

    def test_inline_keyword_fragment(self):
        assert _pipeline("Then `if x:` branch runs.")[0] == ""

    def test_inline_symbol_in_prose(self):
        assert _pipeline("causing `release_kv_cache(..., is_insert=False)` and skipping.")[0] == ""

    def test_inline_non_code_ignored(self):
        assert _pipeline("The word `hello` is not code.")[0] == ""

    def test_inline_never_added_when_blocks_exist(self):
        text = f"{BT3}python\nx = 1\n{BT3}\nAlso `y = 2`"
        code, _ = _pipeline(text)
        assert "x = 1" in code
        assert "y = 2" not in code


class TestIncompleteFences:
    """NEW RULES: the extractor refuses unclosed blocks; repair auto-closes the LAST one."""

    def test_incomplete_fence_at_end_is_not_extracted_directly(self):
        assert extract_code_blocks(f"{BT3}python\nx = 1") == []

    def test_incomplete_fence_at_end_is_repaired_then_extracted(self):
        code, notes = repair_response(f"{BT3}python\nx = 1", "std")
        assert code.endswith("```")
        assert "half-open-active" in notes
        assert extract_code_blocks(code)[0].code == "x = 1"

    def test_incomplete_fence_after_complete(self):
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2"
        code, _ = _pipeline(text)
        assert "a = 1" in code
        assert "b = 2" in code          # repair auto-closed the trailing block

    def test_only_last_block_may_extend_to_eof(self):
        """An auto-closed block whose BODY holds an inner opener is refused.

        H1: the counter reads this as one block with the inner opener as literal
        code, but the strict extractor is non-greedy, so the two disagree. The
        old net-pad "fixed" that by appending a closer INTO the body, which made
        the model receive a SyntaxError for a fence line it never wrote. The pad
        is gone: the stage is refused, nothing is executed, and the model is told
        to indent the inner fence.
        """
        text = f"{BT3}python\na = 1\n{BT3}python\nb = 2"
        code, _ = _pipeline(text)
        repaired, notes = repair_response(text, "std")
        assert repaired == text                            # untouched: no pad injected
        assert code == ""                                  # nothing half-baked runs
        assert "unbalanced-block" in notes
        assert "column 0" in notes.message


class TestBashExtraction:
    """NEW RULE: bash fences are extracted as bash (old helper dropped them)."""

    def test_bash_fence_extracted(self):
        _, blocks = _pipeline(f"{BT3}bash\nls -la\n{BT3}")
        assert len(blocks) == 1 and blocks[0].language == "bash"

    def test_bash_fence_not_converted_to_python(self):
        code, blocks = _pipeline(f"{BT3}bash\ngrep -r TODO src/\n{BT3}")
        assert "grep -r" in code
        assert "subprocess" not in code


class TestToolArtifactConversion:
    """Protocol tags are converted by repair (stage 3), not stripped by the extractor."""

    def test_xml_tool_call_converted(self):
        text = ("Let me do it\n<" + "tool_call>\n<" + "function=Bash>\n<"
                + "parameter=command>\nls\n<" + "/parameter>\n<" + "/function>\n<"
                + "/tool_call>\nDone")
        code, _ = _pipeline(text)
        assert "ls" in code
        assert "function=" not in code

    def test_prose_tool_name_not_stripped(self):
        """NEW RULE: the extractor never rewrites prose. `file_read(...)` in text stays."""
        text = f'file_read(path="test.py")\n{BT3}python\nimport os\n{BT3}'
        code, _ = _pipeline(text)
        assert "import os" in code
        assert "file_read" not in code       # only the fenced block is executed
        repaired, notes = repair_response(text, "std")
        assert "file_read" in repaired       # prose preserved verbatim in the text
        assert not notes


class TestIdempotence:
    def test_pipeline_is_idempotent_on_text(self):
        text = f"hi\n{BT3}python\nprint(1)\n{BT3}"
        once, _ = repair_response(text, "std")
        twice, notes = repair_response(once, "std")
        assert once == twice
        assert not notes
