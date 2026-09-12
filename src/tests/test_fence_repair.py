"""Tests for in-place fence/protocol repair (agent_fence_repair).

Contract (specs/fence-pipeline-v2.md):
  repair_response(text, style) -> (text, RepairReport)
  - RepairReport iterates over Entry(step, lang, block) objects;
    .message / entry.message give the human-readable sentence.
  - Three stages, first stage with >=1 ACCEPTED block wins:
      1. active style  2. quad -> html -> std  3. protocol tags
  - Idempotent on TEXT (the report is not, by design).
  - Empty / whitespace-only body is rejected (no block).
  - Repair never blanks non-empty input.
"""
from __future__ import annotations

import pytest

from agent_fence_repair import repair_response
from agent_repl_parse import extract_code_blocks, fence_tokens

STYLES = ["std", "quad", "html", "at"]

TCO = "<" + "tool_call" + ">"
TCC = "<" + "/tool_call" + ">"
NL = chr(10)
FN_BASH = "<" + "function=Bash" + ">"
FN_READ = "<" + "function=Read" + ">"
FN_PY = "<" + "function=Python" + ">"
PR_CMD = "<" + "parameter=command" + ">"
PR_PATH = "<" + "parameter=path" + ">"
EPR = "<" + "/parameter" + ">"
EFN = "<" + "/function" + ">"

FULL = (
    "Let me run this:\n" + TCO + "\n" + FN_BASH + "\n" + PR_CMD + "\n"
    "ls -la\n" + EPR + "\n" + EFN + "\n" + TCC + "\nDone."
)


def _block(text, style):
    return extract_code_blocks(text, fence_style=style)


# ---------------------------------------------------------------------------
# Stage 3: protocol tags -> active fence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", STYLES)
def test_full_block_converted(style):
    ft = fence_tokens(style)
    out, notes = repair_response(FULL, style)
    assert ft["sh_open"] in out
    assert "ls -la" in out
    assert ft["sh_close"] in out
    assert "<" + "function=" not in out
    assert "<" + "parameter=" not in out
    assert "<" + "tool_call" not in out
    # prose around the group survives
    assert "Let me run this:" in out
    assert "Done." in out
    assert "protocol-tags" in notes
    assert "tool-call tags" in notes.message


@pytest.mark.parametrize("style", STYLES)
def test_converted_block_is_extractable(style):
    """Repair output must satisfy the strict extractor (they share one counter)."""
    out, _ = repair_response(FULL, style)
    blocks = _block(out, style)
    assert len(blocks) == 1
    assert blocks[0].language == "bash"
    assert blocks[0].code == "ls -la"


def test_protocol_block_without_closer_is_not_auto_closed():
    """Stage 3 requires an EXPLICIT closer - no EOF auto-close.

    Inverted from the original auto-close assertion. A lone protocol tag is far
    too weak an opener to justify extending to end-of-text: doing so turns the
    trailing prose of a reply into executed code. Fences still auto-close (see
    the half-open tests); tags do not.
    """
    src = "hi" + NL + TCO + NL + FN_BASH + NL + PR_CMD + NL + "ls -la"
    out, notes = repair_response(src, "at")
    assert out == src
    assert not notes
    assert extract_code_blocks(out, "at") == []


def test_protocol_block_closed_by_active_fence_is_converted():
    ft = fence_tokens("at")
    src = "hi" + NL + TCO + NL + FN_BASH + NL + PR_CMD + NL + "ls -la" + NL + ft["sh_close"]
    out, notes = repair_response(src, "at")
    assert ft["sh_open"] in out
    assert "protocol-tags" in notes
    blocks = extract_code_blocks(out, "at")
    assert len(blocks) == 1 and blocks[0].language == "bash"


def test_multi_param_preserves_bash_lang():
    """A nested parameter group inherits the enclosing function's language."""
    at = fence_tokens("at")
    src = (FN_BASH + "\n"
           + PR_CMD + "\nls\n" + EPR + "\n"
           + PR_CMD + "\ncd /tmp\n" + EPR + "\n"
           + EFN)
    out, _ = repair_response(src, "at")
    assert at["sh_open"] in out
    assert at["py_open"] not in out
    # each parameter group becomes its own block; both inherit bash from the function
    assert out.count(at["sh_open"]) == 2
    assert all(b.language == "bash" for b in _block(out, "at"))


@pytest.mark.parametrize("fn", [FN_READ, FN_PY])
def test_function_name_to_python(fn):
    at = fence_tokens("at")
    src = fn + "\n" + PR_PATH + "\nfoo\n" + EPR + "\n" + EFN
    out, _ = repair_response(src, "at")
    assert at["py_open"] in out


def test_function_name_to_bash():
    at = fence_tokens("at")
    src = FN_BASH + "\n" + PR_CMD + "\nls\n" + EPR + "\n" + EFN
    out, _ = repair_response(src, "at")
    assert at["sh_open"] in out


# ---------------------------------------------------------------------------
# Stage 1/2: fence styles
# ---------------------------------------------------------------------------

# P2 std-gating: a std (triple-backtick) block is inert prose under a NON-std
# active style, so the (active="at", wrong="std") pair is no longer a conversion
# case - it is covered by test_std_block_not_extracted_under_at. The surviving
# pairs still prove cross-style conversion works.
@pytest.mark.parametrize("active,wrong", [("std", "at"), ("html", "quad")])
def test_wrong_style_fence_normalized(active, wrong):
    ft_w = fence_tokens(wrong)
    ft_a = fence_tokens(active)
    src = "x\n" + ft_w["py_open"] + "\nprint(1)\n" + ft_w["py_close"]
    out, notes = repair_response(src, active)
    assert ft_a["py_open"] in out
    assert "print(1)" in out
    assert ft_w["py_open"] not in out
    assert "other-style-enclosed" in notes
    assert "different fence style" in notes.message


def test_half_open_active_auto_closed():
    at = fence_tokens("at")
    src = "hi\n" + at["py_open"] + "\nx = 1\nprint(x)"
    out, notes = repair_response(src, "at")
    assert out.endswith(at["py_close"])
    assert "half-open-active" in notes
    assert "never closed" in notes.message
    # and the repaired text is now extractable
    assert len(_block(out, "at")) == 1


def test_half_open_other_style():
    # P2 std-gating: a half-open STD block under "at" is inert prose, so the
    # half-open-conversion path is exercised with a NON-std foreign style (quad).
    at = fence_tokens("at")
    std = fence_tokens("quad")
    src = std["py_open"] + "\nx = 1"
    out, notes = repair_response(src, "at")
    assert at["py_open"] in out
    assert out.endswith(at["py_close"])
    assert "half-open-other-style" in notes


def test_clean_message_unchanged_and_silent():
    at = fence_tokens("at")
    clean = "hello\n" + at["py_open"] + "\nprint(2)\n" + at["py_close"] + "\nbye"
    out, notes = repair_response(clean, "at")
    assert out == clean
    assert not notes
    assert notes.message == ""


def test_active_enclosed_never_warns_even_with_orphan():
    """active-enclosed itself is silent; an orphan closer is still dropped + noted."""
    at = fence_tokens("at")
    src = at["py_open"] + "\nx = 1\n" + at["py_close"] + "\n" + at["py_close"]
    out, notes = repair_response(src, "at")
    assert out.count(at["py_close"]) == 1
    assert "orphan-close-dropped" in notes


# ---------------------------------------------------------------------------
# Idempotency on TEXT (report is NOT idempotent by design)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("src", [FULL, "hi\n@PY\nx = 1", "x\n```python\nprint(1)\n```",
                                 "just prose", "no code at all"])
def test_idempotent_on_text(style, src):
    once, _ = repair_response(src, style)
    twice, notes2 = repair_response(once, style)
    assert once == twice
    assert not notes2          # run 2 has nothing to fix


def test_multiple_blocks():
    at = fence_tokens("at")
    out, _ = repair_response(FULL + "\n" + FULL, "at")
    assert out.count(at["sh_open"]) == 2


def test_content_inside_fence_verbatim():
    at = fence_tokens("at")
    body = "x = \"<" + "function=Foo>\"\nprint(x)"
    src = at["py_open"] + "\n" + body + "\n" + at["py_close"]
    out, _ = repair_response(src, "at")
    assert body in out


def test_code_inside_real_fence_with_proto_close_preserved():
    at = fence_tokens("at")
    body = "x = \"<" + "/function>\"\nprint(x)"
    src = at["py_open"] + "\n" + body + "\n" + at["py_close"]
    out, _ = repair_response(src, "at")
    assert body in out
    assert out.count(at["py_close"]) == 1


def test_inline_prose_tag_not_consumed():
    at = fence_tokens("at")
    src = ("I will use <" + "function=Bash" + "> now\n"
           "real prose line\n"
           "more prose")
    out, notes = repair_response(src, "at")
    assert "I will use" in out
    assert "real prose line" in out
    assert "more prose" in out
    assert at["sh_open"] not in out
    assert not notes


# ---------------------------------------------------------------------------
# Empty body rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", STYLES)
def test_empty_block_body_rejected(style):
    ft = fence_tokens(style)
    src = ft["py_open"] + "\n   \n\n" + ft["py_close"]
    out, notes = repair_response(src, style)
    assert out == src                    # nothing to repair
    assert not notes
    assert _block(out, style) == []      # and the extractor rejects it too


@pytest.mark.parametrize("style", STYLES)
def test_empty_body_falls_through_to_good_block(style):
    """A stage whose candidates are all rejected must not lock out a good block."""
    ft = fence_tokens(style)
    src = (ft["py_open"] + "\n\n" + ft["py_close"] + "\n"
           + ft["py_open"] + "\nx = 1\n" + ft["py_close"])
    out, notes = repair_response(src, style)
    assert "x = 1" in out
    assert not notes                     # active-enclosed is silent
    assert len(_block(out, style)) == 1


# ---------------------------------------------------------------------------
# Never blank non-empty input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", STYLES)
def test_never_blanks_non_empty_input(style):
    ft = fence_tokens(style)
    src = ft["py_close"] + "\nsome prose"      # orphan closer + prose
    out, notes = repair_response(src, style)
    assert out.strip()
    assert "some prose" in out
    assert out == src                           # orphan-drop only runs in a winning stage
    assert not notes


def test_empty_and_none_safe():
    assert repair_response("", "at") == ("", [])
    assert repair_response(None, "at") == (None, [])


# ---------------------------------------------------------------------------
# NEW RULE (inverted from the old unconditional orphan-drop)
# ---------------------------------------------------------------------------

def test_close_only_prose_tags_left_in_place():
    """A reply with ONLY closing protocol tags and no opener is left alone.

    Orphan-drop now runs only inside a winning stage; stage 3 needs an opener
    group, so nothing wins and the text is untouched. Accepted regression
    (specs/fence-pipeline-v2.md, "Known trade-off accepted").
    """
    at = fence_tokens("at")
    src = "just prose\n" + EPR + "\n" + EFN + "\n" + TCC
    out, notes = repair_response(src, "at")
    assert out == src
    assert not notes
    assert at["sh_close"] not in out


# ---------------------------------------------------------------------------
# N1 REGRESSION: closer-inside-block-suspect must not fire on ordinary prose
# or on foreign-style closers the converting stage deliberately keeps.
# ---------------------------------------------------------------------------

def test_ordinary_prose_stray_closer_is_orphan_dropped_not_suspect():
    """Healthy block + ordinary prose + a stray ACTIVE closer: the stray is a
    trailing orphan, NOT a block cut short. Must be the quiet note."""
    at = fence_tokens("at")
    src = at["py_open"] + "\nx = 1\n" + at["py_close"] + "\nsome prose here\n" + at["py_close"]
    out, notes = repair_response(src, "at")
    assert "orphan-close-dropped" in notes
    assert "closer-inside-block-suspect" not in notes


def test_foreign_style_closer_kept_is_not_suspect():
    """A foreign-style closer kept as inert prose by a converting stage must
    not masquerade as lost code. std block, active=at: the std closer is not an
    active orphan, so no suspect note."""
    src = "@PY\nx = 1\n@/PY"
    out, notes = repair_response(src, "at")
    assert "closer-inside-block-suspect" not in notes


def test_genuine_triple_quote_stray_closer_still_suspect():
    """Guard: the real suspect (fence token inside a triple-quoted string)
    still gets the loud note."""
    at = fence_tokens("at")
    q = chr(34) * 3
    src = (at["py_open"] + "\nx = " + q + "\n" + at["py_close"]
           + "\ny = 2\n" + q + "\n" + at["py_close"])
    out, notes = repair_response(src, "at")
    assert "closer-inside-block-suspect" in notes


def test_report_shape():
    out, rep = repair_response(FULL, "at")
    assert out
    entries = rep.entries
    assert entries and entries[0].step == "protocol-tags"
    assert entries[0].lang == "bash"
    assert entries[0].block == ["ls -la"]
    assert "tool-call tags" in entries[0].message
    assert [e.step for e in rep] == ["protocol-tags"]   # L1: iterating yields Entry
    assert rep[0].step == "protocol-tags"               # rep[i] and iter agree


# ---------------------------------------------------------------------------
# REGRESSION A: protocol block closed by an ACTIVE-style closer
# ---------------------------------------------------------------------------

def test_protocol_block_closed_by_active_closer_does_not_raise():
    """Repro: repair_response("<function=bash>\nls\n@/PY", "at") used to TypeError.

    scan_proto treated the active-style closer as a protocol close tag and then
    indexed the result of _proto_kind() (None) -> TypeError. The active closer IS
    an allowed closer (design record, step-3 rule (b)); it must convert cleanly.
    """
    at = fence_tokens("at")
    src = "<" + "function=bash" + ">" + NL + "ls" + NL + at["py_close"]
    out, notes = repair_response(src, "at")           # must not raise
    assert at["sh_open"] in out
    assert "ls" in out
    assert "protocol-tags" in notes
    blocks = extract_code_blocks(out, "at")
    assert len(blocks) == 1 and blocks[0].language == "bash"


@pytest.mark.parametrize("style", ["std", "quad", "html"])
def test_protocol_block_closed_by_active_closer_other_styles(style):
    ft = fence_tokens(style)
    src = "<" + "function=bash" + ">" + NL + "ls -la" + NL + ft["py_close"]
    out, notes = repair_response(src, style)
    assert ft["sh_open"] in out
    assert "protocol-tags" in notes
    assert len(extract_code_blocks(out, style)) == 1


def test_protocol_closer_run_of_several_tags_is_one_closer():
    """</parameter> </function> </tool_call> in a row close ONE block."""
    at = fence_tokens("at")
    src = (TCO + NL + FN_BASH + NL + PR_CMD + NL + "ls" + NL
           + EPR + NL + EFN + NL + TCC)
    out, _ = repair_response(src, "at")
    assert out.count(at["sh_open"]) == 1
    assert len(extract_code_blocks(out, "at")) == 1


# ---------------------------------------------------------------------------
# H1b REGRESSION: lone <tool_call> at EOF must not lose its code
# ---------------------------------------------------------------------------

def test_lone_tool_call_at_eof_is_auto_closed():
    """Spec repro: repair_response("hi\n<tool_call>\nprint(1)", "at") gave 0 blocks.

    A truncated reply ending in a LONE tool_call tag is a pure truncation
    artifact: the code after it was silently dropped. It is now auto-closed.
    Envelope groups (function/parameter) still refuse to auto-close - see the
    next test and specs/fence-pipeline-v2.md "Step 3 closer rule".
    """
    at = fence_tokens("at")
    src = "hi" + NL + TCO + NL + "print(1)"
    out, notes = repair_response(src, "at")
    assert "protocol-tags" in notes
    blocks = extract_code_blocks(out, "at")
    assert [(b.language, b.code) for b in blocks] == [("python", "print(1)")]
    assert out.endswith(at["py_close"])
    assert "hi" in out
    twice, notes2 = repair_response(out, "at")
    assert twice == out and not notes2            # idempotent


def test_lone_tool_call_at_eof_only_for_lone_tag():
    """The EOF exception is ONLY a lone tool_call - an envelope still needs a closer."""
    src = "hi" + NL + TCO + NL + FN_BASH + NL + PR_CMD + NL + "ls -la"
    out, notes = repair_response(src, "at")
    assert out == src and not notes               # documented behaviour, unchanged


def test_lone_tool_call_groups_at_eof_are_all_closed():
    """Two consecutive lone tool_call groups at EOF: both are recovered."""
    at = fence_tokens("at")
    src = "hi" + NL + TCO + NL + "print(1)" + NL + TCO + NL + "print(2)"
    out, notes = repair_response(src, "at")
    blocks = extract_code_blocks(out, "at")
    assert [b.code for b in blocks] == ["print(1)", "print(2)"]
    assert out.count(at["py_open"]) == 2


# ---------------------------------------------------------------------------
# REGRESSION B: _renders_extractable gate (never silently drop code)
# ---------------------------------------------------------------------------

def test_gate_refuses_rewrite_that_would_drop_code():
    """Repro: repair_response("```python\n@/PY\nprint(1)", "at").

    The std stage sees an opener, but the embedded ACTIVE closer truncates the
    body to nothing useful: rendering it would leave text the strict extractor
    discards, i.e. code silently dropped. The gate must refuse the stage, so
    repair reports NO step and returns the text byte-for-byte unchanged.
    """
    src = "```python" + NL + "@/PY" + NL + "print(1)"
    out, notes = repair_response(src, "at")
    assert out == src
    assert not notes
    assert notes.message == ""


@pytest.mark.parametrize("style", STYLES)
def test_gate_refuses_when_render_would_be_unclosed(style):
    """A body containing a net-positive run of active openers must not commit."""
    ft = fence_tokens(style)
    other = "at" if style != "at" else "std"
    fo = fence_tokens(other)
    # other-style opener whose body contains an ACTIVE opener and no closer:
    # rendering would leave the text unclosed -> gate refuses -> unchanged.
    src = fo["py_open"] + NL + ft["py_open"] + NL + "x = 1"
    out, notes = repair_response(src, style)
    if notes:                                   # committed -> must be extractable
        assert extract_code_blocks(out, style)
    else:
        assert out == src


def test_gate_allows_legitimate_conversion_and_extra_closers():
    """The gate is not over-strict: a real conversion still commits."""
    # P2 std-gating: use a NON-std foreign style so the conversion path is real.
    std = fence_tokens("quad")
    at = fence_tokens("at")
    src = std["py_open"] + NL + "x = 1" + NL + std["py_close"]
    out, notes = repair_response(src, "at")
    assert "other-style-enclosed" in notes
    assert extract_code_blocks(out, "at")[0].code == "x = 1"
    assert at["py_open"] in out


# ---------------------------------------------------------------------------
# REGRESSION C: _finalize strips ACTIVE orphans -> idempotence
# ---------------------------------------------------------------------------

def test_finalize_strips_orphans_left_by_converting_stage():
    """Repro: repair_response("@/PY\n@/PY\n```python\n@PY", "at").

    The std-converting stage copies the leading ACTIVE closers verbatim; in the
    output they are orphans, so a SECOND repair pass would strip them and break
    repair(repair(x))[0] == repair(x)[0]. _finalize removes them up front.
    """
    at = fence_tokens("at")
    # NB: the std block body is net-CLOSED (plain code). A body holding an
    # ACTIVE opener used to be "rescued" by appending pad closers; that pad is
    # gone (H1) - see test_net_open_block_is_refused_not_padded.
    # P2 std-gating: the converting stage must be a NON-std style, so the
    # triple-backtick pair is replaced by the quad style (same shape).
    q = chr(96) * 6
    src = "@/PY" + NL + "@/PY" + NL + q + "python" + NL + "x = 1" + NL + q
    once, notes1 = repair_response(src, "at")
    assert notes1                                   # something was repaired
    assert once.count(at["py_close"]) == once.count(at["py_open"])
    twice, notes2 = repair_response(once, "at")
    assert twice == once
    assert not notes2


def test_finalize_orphan_stripping_is_harmless():
    """An orphan closer carries no content, so stripping loses nothing."""
    at = fence_tokens("at")
    q = chr(96) * 6
    src = "prose line" + NL + at["py_close"] + NL + q + "python" + NL + "y = 2" + NL + q
    once, _ = repair_response(src, "at")
    twice, notes2 = repair_response(once, "at")
    assert twice == once
    assert not notes2
    assert "prose line" in once
    assert extract_code_blocks(once, "at")[0].code == "y = 2"


@pytest.mark.parametrize("style", STYLES)
def test_idempotence_after_protocol_conversion(style):
    """repair(repair(x))[0] == repair(x)[0] for the protocol path too."""
    ft = fence_tokens(style)
    src = "hi" + NL + TCO + NL + FN_BASH + NL + PR_CMD + NL + "ls" + NL + ft["py_close"]
    once, _ = repair_response(src, style)
    twice, notes2 = repair_response(once, style)
    assert twice == once
    assert not notes2


# ---------------------------------------------------------------------------
# REGRESSION E: CRLF normalization + trailing spaces/tabs on openers (repair side)
# ---------------------------------------------------------------------------

def test_crlf_input_is_normalized_and_repaired():
    at = fence_tokens("at")
    src = "hi\r\n" + at["py_open"] + "\r\nx = 1\r\nprint(x)"
    out, notes = repair_response(src, "at")
    assert "\r" not in out
    assert "half-open-active" in notes
    assert out.endswith(at["py_close"])
    assert len(extract_code_blocks(out, "at")) == 1


def test_crlf_protocol_block_repaired():
    at = fence_tokens("at")
    src = "<" + "function=bash" + ">" + "\r\nls\r\n" + at["sh_close"]
    out, notes = repair_response(src, "at")
    assert "\r" not in out
    assert at["sh_open"] in out
    assert "protocol-tags" in notes


def test_crlf_only_prose_is_normalized_silently():
    out, notes = repair_response("a\r\nb\r\n", "at")
    assert out == "a\nb\n"
    assert not notes


@pytest.mark.parametrize("pad", ["  ", "\t", " \t "])
def test_opener_tolerates_trailing_spaces_and_tabs_repair(pad):
    """Mirror of the extractor rule: openers may carry trailing blanks."""
    at = fence_tokens("at")
    src = "hi\n" + at["py_open"] + pad + "\nx = 1\n" + at["py_close"]
    out, notes = repair_response(src, "at")
    assert out == src                       # already well formed -> silent
    assert not notes
    assert len(extract_code_blocks(src, "at")) == 1


@pytest.mark.parametrize("pad", ["  ", "\t"])
def test_half_open_opener_with_trailing_blanks_is_closed(pad):
    at = fence_tokens("at")
    src = at["py_open"] + pad + "\nx = 1"
    out, notes = repair_response(src, "at")
    assert "half-open-active" in notes
    assert out.endswith(at["py_close"])


def test_opener_with_trailing_NON_blank_text_is_not_an_opener():
    at = fence_tokens("at")
    src = at["py_open"] + " x = 1" + "\nprint(1)"
    out, notes = repair_response(src, "at")
    assert out == src
    assert not notes


# ---------------------------------------------------------------------------
# H1 REGRESSION: net-pad injected fence lines into EXECUTED code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", STYLES)
def test_net_open_block_is_refused_not_padded(style):
    """A body with a net-positive run of active openers must NOT be pad-fixed.

    Repro (style="at"): "@PY\nx = 1\n@PY\ny = 2\n@/PY". The old net-pad
    appended a closer to the BODY, so the extracted code ended with a literal
    fence line -> SyntaxError on code the model never wrote. The pad is gone:
    the stage is refused, the text is returned byte-for-byte, and the model is
    told to fix the inner fence.
    """
    ft = fence_tokens(style)
    src = (ft["py_open"] + NL + "x = 1" + NL + ft["py_open"] + NL + "y = 2"
           + NL + ft["py_close"])
    out, notes = repair_response(src, style)
    assert out == src                                  # nothing executed, nothing rewritten
    assert notes and all(e.step == "unbalanced-block" for e in notes.entries)
    assert "unbalanced" in notes.message
    assert ft["py_close"] + NL + ft["py_close"] not in out   # no pad emitted
    assert ft["py_open"] + NL + ft["py_close"] not in out    # no auto-close either


def test_net_open_triple_quoted_string_not_padded():
    """Spec repro: a triple-quoted string containing an active opener."""
    at = fence_tokens("at")
    q3 = chr(39) * 3
    src = at["py_open"] + NL + "s = " + q3 + NL + at["py_open"] + NL + q3 + NL + at["py_close"]
    out, notes = repair_response(src, "at")
    assert out == src
    assert notes.summary() == "unbalanced-block"
    assert not out.endswith(at["py_close"] + NL + at["py_close"])


def test_net_open_refusal_does_not_destroy_a_good_sibling_block():
    """Refusing a stage must leave the already-valid block intact and extractable."""
    at = fence_tokens("at")
    # block 1 is well formed; block 2 (auto-closed at EOF) is net-open.
    src = (at["py_open"] + NL + "x = 1" + NL + at["py_close"] + NL
           + at["py_open"] + NL + "y = 2" + NL + at["py_open"] + NL + "z = 3")
    out, notes = repair_response(src, "at")
    assert out == src                                    # no pad, no rewrite
    assert notes.summary() == "unbalanced-block"
    blocks = extract_code_blocks(out, "at")              # strict extractor still sees block 1
    assert [b.code for b in blocks] == ["x = 1"]


# ---------------------------------------------------------------------------
# DETERMINISTIC FUZZ: repair never raises, never drops code, is idempotent
# ---------------------------------------------------------------------------

_FUZZ_TOKS = None


def _fuzz_tokens():
    """Token soup: every style\'s openers/closers, near-miss variants, prose."""
    global _FUZZ_TOKS
    if _FUZZ_TOKS is None:
        toks = []
        for st in STYLES:
            ft = fence_tokens(st)
            toks += [ft["py_open"], ft["py_close"], ft["sh_open"], ft["sh_close"],
                     " " + ft["py_open"], ft["py_open"] + "  ", ft["py_close"] + "\t",
                     ft["py_close"] + "X"]
        toks += [TCO, TCC, FN_BASH, FN_READ, PR_CMD, PR_PATH, EPR, EFN,
                 "print(1)", "x = 1", "", "   ", "some prose", "  indented"]
        _FUZZ_TOKS = toks
    return _FUZZ_TOKS


def test_fuzz_repair_never_raises_never_drops_is_idempotent():
    """1000 generated inputs x 4 styles = 4000 cases, seeded (deterministic)."""
    import random
    import time
    rng = random.Random(20260902)
    toks = _fuzz_tokens()
    t0 = time.time()
    committed = 0
    for _ in range(1000):
        src = "\n".join(rng.choice(toks) for _ in range(rng.randint(0, 8)))
        for style in STYLES:
            out1, notes = repair_response(src, style)          # (1) never raises
            diagnosed = notes and all(
                e.step == "unbalanced-block" for e in notes.entries)
            if notes and not diagnosed:
                committed += 1
                # (2) a reported step must leave extractable code behind
                assert extract_code_blocks(out1, style), (src, style, out1)
            out2, notes2 = repair_response(out1, style)
            assert out2 == out1, (src, style, out1, out2)      # (3) idempotent
            if diagnosed:
                # A refusal is a property of the (untouched) text, so run 2
                # re-diagnoses it identically - it is not a repair.
                assert out1 == src and notes2.summary() == notes.summary()
            else:
                assert not notes2                              # run 2 has nothing to fix
    assert time.time() - t0 < 10, "fuzz must stay under ~10s"
    assert committed > 100, committed                          # the fuzz really bites
