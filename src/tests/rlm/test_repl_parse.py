"""Tests for strict code-block extraction (agent_repl_parse.py).

Contract (specs/fence-pipeline-v2.md): extract_code_blocks accepts ONLY the
active fence style, ONLY fully-enclosed blocks, with NO cross-style fallback
and NO auto-close. agent_fence_repair owns every other case.

The old `extract_python_code` helper was deleted on purpose; the concatenation
it performed is now `agent_pipeline.extract_code`.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).parent.parent.parent / "src"))

import pytest

from agent_repl_parse import extract_code_blocks

BT3 = chr(96) * 3
DQ = chr(34) * 3


def _codes(text, style="std"):
    return [b.code for b in extract_code_blocks(text, fence_style=style)]


class TestCodeExtraction:
    """Strict python-block extraction."""

    def test_extract_single_code_block(self):
        assert _codes(f"{BT3}python\nx = 1\n{BT3}") == ["x = 1"]

    def test_extract_multiple_code_blocks(self):
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2\n{BT3}"
        assert _codes(text) == ["a = 1", "b = 2"]

    def test_no_code_block(self):
        assert _codes("Just text, no code") == []

    def test_mixed_content(self):
        text = f"Let me calculate:\n\n{BT3}python\nx = 1 + 2\nprint(x)\n{BT3}\n\nThe answer is 3."
        codes = _codes(text)
        assert "x = 1 + 2" in codes[0]
        assert "print(x)" in codes[0]

    def test_code_block_ordering(self):
        text = f"{BT3}python\nfirst\n{BT3}\n{BT3}python\nsecond\n{BT3}"
        blocks = extract_code_blocks(text)
        assert [b.code for b in blocks] == ["first", "second"]

    def test_empty_code_block_rejected(self):
        """NEW RULE: an empty body is clearly wrong -> no block."""
        assert _codes(f"{BT3}python\n{BT3}") == []

    def test_whitespace_only_code_rejected(self):
        assert _codes(f"{BT3}python\n   \n{BT3}") == []

    def test_code_with_special_chars(self):
        assert "hello" in _codes(BT3 + "python\nx = 'hello world'\n" + BT3)[0]

    def test_markdown_with_answer(self):
        body = "answer['content'] = 'test'\nanswer['ready'] = True"
        code = _codes(BT3 + "python\n" + body + "\n" + BT3)[0]
        assert "answer['content']" in code
        assert "answer['ready']" in code

    def test_multiline_string_preserved(self):
        text = f"{BT3}python\nx = {DQ}{DQ}{DQ}hello\nworld{DQ}{DQ}{DQ}\n{BT3}"
        code = _codes(text)[0]
        assert DQ + "hello" in code
        assert "world" + DQ in code

    def test_empty_response(self):
        assert _codes("") == []


class TestStrictness:
    """NEW RULES that invert the old fallback behaviour."""

    def test_four_backtick_fences_rejected_under_std(self):
        """OLD: 4+ backticks were tolerated. NEW: only the active token opens."""
        assert _codes("````python\nx = 1\n````") == []

    def test_bare_fence_rejected(self):
        assert _codes(f"{BT3}\nx = 1\n{BT3}") == []

    def test_language_alias_rejected(self):
        for alias in ("py", "python3", "python3.13"):
            assert _codes(f"{BT3}{alias}\nx = 1\n{BT3}") == []

    def test_opener_must_be_alone_on_line(self):
        """OLD: leading whitespace tolerated. NEW: strictly column 0."""
        assert _codes(f"  {BT3}python\nx = 1\n{BT3}") == []

    def test_opener_with_trailing_text_rejected(self):
        assert _codes(f"{BT3}python x = 1\n{BT3}") == []

    def test_closer_may_have_trailing_text(self):
        """Closer: token at column 0, trailing content allowed."""
        assert _codes(f"{BT3}python\nx = 1\n{BT3}   ") == ["x = 1"]

    def test_closer_is_token_boundary_not_substring(self):
        """`@/PYX` must NOT close an at-style block."""
        from agent_repl_parse import fence_tokens
        at = fence_tokens("at")
        po, pc = at["py_open"], at["py_close"]
        text = po + "\nx = 1\n" + pc + "X\nmore"
        assert extract_code_blocks(text, fence_style="at") == []

    def test_inline_code_never_extracted(self):
        assert _codes("Use `x = 1 + 2` for calculation") == []
        assert _codes("Call `print('hello')` to output") == []

    def test_inline_never_added_when_blocks_exist(self):
        text = f"Use `x = 1` or this:\n{BT3}python\ny = 2\n{BT3}"
        assert _codes(text) == ["y = 2"]

    def test_no_cross_style_fallback(self):
        """OLD: extractor fell back across styles. NEW: active style only."""
        from agent_repl_parse import fence_tokens
        at = fence_tokens("at")
        text = at["py_open"] + "\nx = 1\n" + at["py_close"]
        assert extract_code_blocks(text, fence_style="std") == []

    def test_unclosed_trailing_block_not_extracted(self):
        """OLD: code ran to end of text. NEW: repair owns auto-close; extractor is strict."""
        assert _codes(f"{BT3}python\nx = 1") == []

    def test_unclosed_trailing_after_complete_block(self):
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2"
        assert _codes(text) == ["a = 1"]          # only the enclosed one

    def test_only_python_and_bash_fences_parsed(self):
        text = f"{BT3}python\nx = 1\n{BT3}\n{BT3}javascript\nconsole.log(1)\n{BT3}"
        assert _codes(text) == ["x = 1"]

    def test_invalid_style_falls_back_to_std(self, capsys):
        blocks = extract_code_blocks(f"{BT3}python\nx = 1\n{BT3}", fence_style="bogus")
        assert len(blocks) == 1
        assert "Invalid fence_style" in capsys.readouterr().out


class TestDepthCounter:
    """The counter is shared by repair and the extractor (specs/fence-pipeline-v2.md)."""

    def test_open_open_close_is_one_block(self):
        """Inner opener stays literal code; depth 1 at EOF -> nothing enclosed."""
        text = f"{BT3}python\n{BT3}python\nx = 1\n{BT3}"
        assert extract_code_blocks(text) == []

    def test_open_close_close_emits_one_block(self):
        text = f"{BT3}python\nx = 1\n{BT3}\n{BT3}"
        assert _codes(text) == ["x = 1"]                # second closer is an orphan

    def test_backtick_run_inside_body(self):
        text = BT3 + "python\nprint('" + BT3 + "')\nx = 1\n" + BT3
        assert "print(" in _codes(text)[0]


class TestBashBlockExtraction:
    def test_extract_bash_block(self):
        blocks = extract_code_blocks(f"{BT3}bash\nls -la\n{BT3}")
        assert len(blocks) == 1
        assert blocks[0].language == "bash"
        assert blocks[0].code.strip() == "ls -la"

    def test_empty_bash_block_rejected(self):
        """NEW RULE: empty body -> no block (was: a block with empty code)."""
        assert extract_code_blocks(f"{BT3}bash\n{BT3}") == []

    def test_mixed_python_bash_blocks(self):
        text = "\n".join([
            BT3 + "python", "x = 1", BT3, "",
            BT3 + "bash", "echo hello", BT3, "",
            BT3 + "python", "print(x)", BT3,
        ])
        blocks = extract_code_blocks(text)
        assert [b.language for b in blocks] == ["python", "bash", "python"]

    def test_bash_block_extracted_as_bash(self):
        """NEW RULE: bash fences ARE extracted (old helper dropped them silently)."""
        assert _codes(f"{BT3}bash\necho hi\n{BT3}") == ["echo hi"]


class TestPositions:
    def test_start_end_positions(self):
        text = f"intro\n{BT3}python\nx = 1\n{BT3}\ntail"
        b = extract_code_blocks(text)[0]
        assert text[b.start_pos:].startswith(BT3 + "python")
        assert b.end_pos <= len(text)


# ---------------------------------------------------------------------------
# REGRESSION E (extractor side): CRLF tolerance is NOT the extractor\'s job,
# but trailing spaces/tabs on opener lines ARE tolerated. agent_repl_parse and
# agent_fence_repair are mirrored copies of the counter, so the rule must match
# in both files or repair and extraction disagree.
# ---------------------------------------------------------------------------

class TestOpenerTrailingBlanks:
    """Opener: token at column 0, ALONE except for trailing spaces/tabs."""

    @pytest.mark.parametrize("pad", ["  ", "\t", " \t "])
    def test_opener_trailing_blanks_accepted(self, pad):
        assert _codes(f"{BT3}python{pad}\nx = 1\n{BT3}") == ["x = 1"]

    @pytest.mark.parametrize("pad", ["  ", "\t"])
    def test_bash_opener_trailing_blanks_accepted(self, pad):
        blocks = extract_code_blocks(f"{BT3}bash{pad}\nls\n{BT3}")
        assert [b.code for b in blocks] == ["ls"]

    @pytest.mark.parametrize("pad", ["  ", "\t"])
    def test_at_style_opener_trailing_blanks(self, pad):
        from agent_repl_parse import fence_tokens
        at = fence_tokens("at")
        text = at["py_open"] + pad + "\nx = 1\n" + at["py_close"]
        assert extract_code_blocks(text, fence_style="at")

    def test_opener_trailing_nonblank_rejected(self):
        """Trailing TEXT (not blanks) still kills the opener."""
        assert _codes(f"{BT3}python x = 1\nx = 1\n{BT3}") == []

    def test_opener_leading_space_still_rejected(self):
        """The tolerance is TRAILING only; column 0 is still required."""
        assert _codes(f" {BT3}python\nx = 1\n{BT3}") == []

    def test_closer_trailing_tabs_accepted(self):
        assert _codes(f"{BT3}python\nx = 1\n{BT3}\t") == ["x = 1"]


class TestCrlfIsRepairJob:
    """The extractor does NOT normalize CRLF - repair_response owns that.

    Documented so nobody adds a second, divergent normalization here.
    """

    def test_crlf_opener_not_matched_by_extractor(self):
        """A \r at the end of an opener line is not a trailing blank."""
        assert _codes(f"{BT3}python\rx = 1\r{BT3}") == []

    def test_crlf_after_repair_is_extractable(self):
        """The supported path: repair first (it strips \r), then extract."""
        from agent_fence_repair import repair_response
        repaired, _notes = repair_response(f"{BT3}python\r\nx = 1\r\n{BT3}", "std")
        assert "\r" not in repaired
        assert _codes(repaired) == ["x = 1"]
