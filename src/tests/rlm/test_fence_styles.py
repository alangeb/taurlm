"""Tests for all 4 fence styles in the strict extract_code_blocks.

NEW RULE (specs/fence-pipeline-v2.md): the extractor is style-strict. The old
cross-style fallback tests are inverted -> wrong-style content must yield
NOTHING from the extractor; agent_fence_repair converts it first.
"""

import pytest

from agent_repl_parse import extract_code_blocks, fence_tokens

STYLES = {
    "std": {
        "py_open": "```python",
        "py_close": "```",
        "sh_open": "```bash",
        "sh_close": "```",
        "wrong_py_open": "@PY",       # at-style opener
        "wrong_py_close": "@/PY",
        "wrong_sh_open": "<sh>",      # html-style opener
        "wrong_sh_close": "</sh>",
    },
    "quad": {
        "py_open": "``````python",
        "py_close": "``````",
        "sh_open": "``````bash",
        "sh_close": "``````",
        "wrong_py_open": "```python",  # std-style opener
        "wrong_py_close": "```",
        "wrong_sh_open": "@SH",
        "wrong_sh_close": "@/SH",
    },
    "html": {
        "py_open": "<py>",
        "py_close": "</py>",
        "sh_open": "<sh>",
        "sh_close": "</sh>",
        "wrong_py_open": "@PY",
        "wrong_py_close": "@/PY",
        "wrong_sh_open": "```bash",
        "wrong_sh_close": "```",
    },
    "at": {
        "py_open": "@PY",
        "py_close": "@/PY",
        "sh_open": "@SH",
        "sh_close": "@/SH",
        "wrong_py_open": "```python",
        "wrong_py_close": "```",
        "wrong_sh_open": "<sh>",
        "wrong_sh_close": "</sh>",
    },
}


# ---------------------------------------------------------------------------
# a. Extract a python block correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_extract_python_block(style):
    s = STYLES[style]
    text = f"Before\n{s['py_open']}\nx = 42\nprint(x)\n{s['py_close']}\nAfter"
    blocks = extract_code_blocks(text, fence_style=style)
    assert len(blocks) == 1
    assert blocks[0].language == "python"
    assert "x = 42" in blocks[0].code
    assert "print(x)" in blocks[0].code


# ---------------------------------------------------------------------------
# b. Extract a bash block correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_extract_bash_block(style):
    s = STYLES[style]
    text = f"Run:\n{s['sh_open']}\nls -la\n{s['sh_close']}\nDone"
    blocks = extract_code_blocks(text, fence_style=style)
    assert len(blocks) == 1
    assert blocks[0].language == "bash"
    assert "ls -la" in blocks[0].code


# ---------------------------------------------------------------------------
# c. Extract multiple blocks in order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_multiple_blocks_in_order(style):
    s = STYLES[style]
    text = (
        f"{s['py_open']}\na = 1\n{s['py_close']}\n"
        f"middle text\n"
        f"{s['sh_open']}\necho hi\n{s['sh_close']}\n"
        f"tail\n"
        f"{s['py_open']}\nb = 2\n{s['py_close']}"
    )
    blocks = extract_code_blocks(text, fence_style=style)
    assert len(blocks) == 3
    assert blocks[0].language == "python" and "a = 1" in blocks[0].code
    assert blocks[1].language == "bash" and "echo hi" in blocks[1].code
    assert blocks[2].language == "python" and "b = 2" in blocks[2].code


# ---------------------------------------------------------------------------
# d. NEW RULE: incomplete fence (no close) is NOT extracted
#    (was: code extended to end of text). repair owns auto-close.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_incomplete_fence_not_extracted(style):
    s = STYLES[style]
    text = f"Start\n{s['py_open']}\nx = 99\nprint(x)"
    blocks = extract_code_blocks(text, fence_style=style)
    assert blocks == []


# ---------------------------------------------------------------------------
# e. NEW RULE: wrong style is REJECTED (inverted from the old fallback test)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_no_fallback_wrong_style_python(style):
    s = STYLES[style]
    text = f"Before\n{s['wrong_py_open']}\nx = 1\n{s['wrong_py_close']}\nAfter"
    blocks = extract_code_blocks(text, fence_style=style)
    assert blocks == [], f"Style {style}: extractor must NOT fall back across styles"


@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_no_fallback_wrong_style_bash(style):
    s = STYLES[style]
    text = f"Before\n{s['wrong_sh_open']}\nls\n{s['wrong_sh_close']}\nAfter"
    assert extract_code_blocks(text, fence_style=style) == []


@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_repair_then_extract_roundtrip(style):
    """The repair ladder exists precisely to make wrong-style content extractable.

    P2 std-gating exception: when the WRONG style is the triple-backtick ("std")
    style and the active style is not "std", that block is inert prose and is
    deliberately NOT promoted (no note, byte-for-byte text). The gated branch is
    asserted explicitly here rather than deleted, so the two behaviours stay
    pinned in one place.
    """
    from agent_fence_repair import repair_response
    s = STYLES[style]
    text = f"Before\n{s['wrong_py_open']}\nx = 1\n{s['wrong_py_close']}\nAfter"
    assert extract_code_blocks(text, fence_style=style) == []      # strict: nothing
    out, notes = repair_response(text, style)
    if s["wrong_py_open"].startswith(chr(96) * 3) and style != "std":
        assert out == text and not notes
        assert extract_code_blocks(out, fence_style=style) == []
        return
    assert "other-style-enclosed" in notes
    blocks = extract_code_blocks(out, fence_style=style)
    assert len(blocks) == 1
    assert blocks[0].code.strip() == "x = 1"


# ---------------------------------------------------------------------------
# f. Trailing whitespace on closing fence is OK
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_trailing_whitespace_close(style):
    s = STYLES[style]
    text = f"{s['py_open']}\nx = 1\n{s['py_close']}   "
    blocks = extract_code_blocks(text, fence_style=style)
    assert len(blocks) == 1
    assert blocks[0].language == "python"
    assert "x = 1" in blocks[0].code


# ---------------------------------------------------------------------------
# Empty body rejected (NEW RULE)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_empty_block_rejected(style):
    s = STYLES[style]
    assert extract_code_blocks(f"{s['py_open']}\n\n{s['py_close']}", fence_style=style) == []


# ---------------------------------------------------------------------------
# Config: fence_style defaults to "std"
# ---------------------------------------------------------------------------

def test_default_style_is_std():
    blocks = extract_code_blocks("```python\nx = 1\n```")  # no fence_style arg
    assert len(blocks) == 1
    assert blocks[0].language == "python"


def test_invalid_style_falls_back_to_std(capsys):
    blocks = extract_code_blocks("```python\nx = 1\n```", fence_style="bogus")
    assert len(blocks) == 1
    assert blocks[0].language == "python"
    assert "Invalid fence_style" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# All styles round-trip through the (now deleted) helper's replacement:
# extract_code_blocks + join, which is what agent_pipeline.extract_code does.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
def test_all_styles_concatenation(style):
    s = STYLES[style]
    text = f"{s['py_open']}\nresult = 10\nprint(result)\n{s['py_close']}"
    code = "\n\n".join(b.code for b in extract_code_blocks(text, fence_style=style))
    assert "result = 10" in code
    assert "print(result)" in code


def test_fence_tokens_cover_all_styles():
    for style in ("std", "quad", "html", "at"):
        ft = fence_tokens(style)
        assert set(ft) >= {"py_open", "py_close", "sh_open", "sh_close", "py_label", "sh_label"}
