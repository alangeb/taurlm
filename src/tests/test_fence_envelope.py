"""Regression tests for P1 envelope-strip (tool-call tags around a valid fence).

Tokens are built with chr() so this source file carries NO literal fence/protocol
tokens at column 0 (keeps the agent REPL parser from tripping on it).
"""
from agent_fence_repair import repair_response, strip_envelopes
from agent_pipeline import extract_code

AT = chr(64)
Q = chr(34)
PYO, PYC = AT + "PY", AT + "/PY"
SHO, SHC = AT + "SH", AT + "/SH"


def _t(s):
    return chr(60) + s + chr(62)


TC = _t("tool_call")
FN_CODE = _t("function=code")
FN_PY = _t("function=Python")
FN_ATTR = _t("function name=" + Q + "code" + Q)
PARAM_CODE = _t("parameter=code")
PARAM_ATTR = _t("parameter name=" + Q + "code" + Q)
INVOKE = _t("invoke name=" + Q + "python" + Q)
C_FN = _t("/function")
C_TC = _t("/tool_call")
C_PARAM = _t("/parameter")
C_INV = _t("/invoke")


def _j(*lines):
    return chr(10).join(lines)


def test_full_envelope_stripped_body_kept():
    src = _j("", TC, FN_CODE, PYO, "x = 1", PYC, C_FN, C_TC)
    out, _ = repair_response(src, "at")
    assert "protocol-envelope-stripped" in repair_response(src, "at")[1]
    assert PYO in out and "x = 1" in out and PYC in out
    assert "tool_call" not in out and "function" not in out


def test_mismatched_close_stripped():
    src = _j(TC, FN_CODE, PYO, "x = 1", PYC, C_PARAM, C_FN, C_TC)
    out, _ = repair_response(src, "at")
    assert "parameter" not in out and "function" not in out and "tool_call" not in out


def test_open_side_only():
    src = _j(TC, FN_CODE, PYO, "y = 2", PYC)
    out, _ = repair_response(src, "at")
    assert "tool_call" not in out and PYO in out and "y = 2" in out


def test_close_side_only():
    src = _j(PYO, "z = 3", PYC, C_FN, C_TC)
    out, _ = repair_response(src, "at")
    assert "function" not in out and "tool_call" not in out and "z = 3" in out


def test_bash_envelope():
    src = _j(TC, FN_CODE, SHO, "echo hi", SHC, C_FN, C_TC)
    out, _ = repair_response(src, "at")
    assert "tool_call" not in out and SHO in out and "echo hi" in out


def test_attribute_style_open():
    src = _j(TC, FN_ATTR, PYO, "a = 1", PYC, C_FN, C_TC)
    out, _ = repair_response(src, "at")
    assert "tool_call" not in out and "function" not in out


def test_invoke_family():
    src = _j(INVOKE, PARAM_ATTR, PYO, "b = 2", PYC, C_INV)
    out, _ = repair_response(src, "at")
    assert "invoke" not in out and "parameter" not in out and PYO in out


def test_indented_envelope():
    src = _j("  " + TC, "  " + FN_CODE, PYO, "c = 3", PYC, "  " + C_FN, "  " + C_TC)
    out, _ = repair_response(src, "at")
    assert "tool_call" not in out


def test_tag_inside_body_preserved():
    src = _j(PYO, "  " + TC, "d = 4", PYC)
    out, _ = repair_response(src, "at")
    assert TC in out  # inside body -> untouched


def test_prose_separated_tag_preserved():
    src = _j(TC, "here is some prose", PYO, "e = 5", PYC)
    out, _ = repair_response(src, "at")
    assert TC in out  # separated by prose -> not an envelope


def test_idempotent():
    src = _j(TC, FN_CODE, PYO, "f = 6", PYC, C_FN, C_TC)
    once, _ = repair_response(src, "at")
    twice, rep2 = repair_response(once, "at")
    assert once == twice
    assert "protocol-envelope-stripped" not in rep2


def test_execution_byte_identical():
    src = _j(TC, FN_CODE, PYO, "g = 7", PYC, C_FN, C_TC)
    clean = _j(PYO, "g = 7", PYC)
    code_a, _ = extract_code(repair_response(src, "at")[0], fence_style="at")
    code_b, _ = extract_code(clean, fence_style="at")
    assert code_a == code_b == "g = 7"


def test_strip_envelopes_noop_on_clean():
    clean = _j("prose", PYO, "h = 8", PYC, "more prose")
    out, entries = strip_envelopes(clean, "at")
    assert out == clean and entries == []


# ---------------------------------------------------------------------------
# P1b: glue-opener pre-pass (tool-call open tag FUSED onto a fence-open token)
# ---------------------------------------------------------------------------

from agent_fence_repair import fix_glue_openers          # noqa: E402


def _blocks(text, style="at"):
    from agent_repl_parse import extract_code_blocks
    return [b.code for b in extract_code_blocks(text, fence_style=style)]


def test_glue_parameter_attr_yields_extractable_block():
    src = _j(TC, PARAM_ATTR + PYO, "x = 1", PYC, C_PARAM, C_TC)
    out, rep = repair_response(src, "at")
    assert "glue-opener-split" in rep
    assert _blocks(out) == ["x = 1"]


def test_glue_invoke_line_above_yields_extractable_block():
    src = _j(INVOKE, PARAM_CODE + PYO, "y = 2", PYC, C_INV)
    out, rep = repair_response(src, "at")
    assert "glue-opener-split" in rep
    assert _blocks(out) == ["y = 2"]


def test_glue_body_byte_identical_to_clean():
    src = _j(TC, PARAM_ATTR + PYO, "a = 1", "b = 2", PYC, C_PARAM, C_TC)
    clean = _j(PYO, "a = 1", "b = 2", PYC)
    assert _blocks(repair_response(src, "at")[0]) == _blocks(clean)


def test_glue_is_idempotent():
    src = _j(TC, PARAM_ATTR + PYO, "c = 3", PYC, C_PARAM, C_TC)
    once, _ = repair_response(src, "at")
    twice, rep2 = repair_response(once, "at")
    assert once == twice
    assert "glue-opener-split" not in rep2


def test_glue_bash_token():
    src = _j(_t("function=Bash"), PARAM_CODE + SHO, "echo hi", SHC, _t("/function"))
    out, rep = repair_response(src, "at")
    assert "glue-opener-split" in rep
    assert _blocks(out) == ["echo hi"]


def test_glue_preserves_indent():
    src = _j("    " + PARAM_ATTR + PYO, "d = 4", "    " + PYC)
    out, _ = fix_glue_openers(src, "at")
    assert out == _j("    " + PYO, "d = 4", "    " + PYC)


def test_glue_does_not_touch_code_line():
    """A print statement whose string literal ends in tag+token must survive."""
    line = "print(" + Q + PARAM_ATTR[1:] + PYO + Q + ")"
    src = _j(PYO, "  " + line, "e = 5", PYC)
    out, rep = repair_response(src, "at")
    assert out == src                       # byte-for-byte untouched
    assert "glue-opener-split" not in rep
    assert _blocks(out) == ["  " + line + chr(10) + "e = 5"]


def test_glue_no_note_when_nothing_extractable():
    """An indented glue line is still inert -> text cleaned, but NO code note."""
    src = _j("  " + PARAM_ATTR + PYO, "f = 6")
    out, rep = repair_response(src, "at")
    assert _blocks(out) == []
    assert "glue-opener-split" not in rep   # note implies extractable code


def test_glue_other_styles():
    from agent_repl_parse import fence_tokens
    for style in ("std", "quad", "html"):
        ft = fence_tokens(style)
        src = _j(TC, PARAM_CODE + ft["py_open"], "g = 7", ft["py_close"], C_PARAM, C_TC)
        out, rep = repair_response(src, style)
        assert "glue-opener-split" in rep, style
        assert _blocks(out, style) == ["g = 7"], (style, out)


# ---------------------------------------------------------------------------
# P2: std-gating - a triple-backtick block is inert prose under a NON-std style
# ---------------------------------------------------------------------------

BT = chr(96) * 3
STD_PY = BT + "python"
STD_BASH = BT + "bash"


def test_std_block_not_extracted_under_at():
    src = _j(STD_PY, "x = 1", BT)
    out, rep = repair_response(src, "at")
    assert out == src                      # byte-for-byte untouched
    assert rep.summary() == ""             # no note -> fuzz invariant holds
    assert _blocks(out, "at") == []


def test_std_block_still_extracted_under_std():
    src = _j(STD_PY, "y = 2", BT)
    out, _ = repair_response(src, "std")
    assert _blocks(out, "std") == ["y = 2"]


def test_std_prose_example_not_executed_under_at():
    src = _j("Here is an example:", STD_PY, "print(1 + 1)", BT, "Hope that helps.")
    out, rep = repair_response(src, "at")
    assert out == src
    assert rep.summary() == ""
    assert _blocks(out, "at") == []


def test_std_gated_under_quad_and_html():
    for style in ("quad", "html"):
        src = _j(STD_PY, "z = 3", BT)
        out, rep = repair_response(src, style)
        assert out == src, style
        assert rep.summary() == "", (style, rep.summary())
        assert _blocks(out, style) == [], style


def test_std_gating_is_idempotent():
    src = _j(STD_PY, "w = 4", BT)
    once, _ = repair_response(src, "at")
    twice, rep2 = repair_response(once, "at")
    assert once == twice and rep2.summary() == ""


def test_active_style_still_promotes_under_at():
    """Regression: the ACTIVE fence is unaffected by std-gating."""
    src = _j(PYO, "v = 5", PYC)
    out, _ = repair_response(src, "at")
    assert _blocks(out, "at") == ["v = 5"]
