"""Tests for the Tier-2 LLM fence segmenter (specs/llm-fence-segmenter.md).

Zero network: everything above invoke_fn is pure; the transport seam is a fake.
"""
from __future__ import annotations

import json
import sys

import pytest
@pytest.fixture(autouse=True)
def _no_prod_telemetry(monkeypatch):
    # Unit tests must not write to the PRODUCTION fence_segmenter.jsonl.
    import agent_fence_segmenter as _seg
    monkeypatch.setattr(_seg, 'default_telemetry_writer', lambda rec: None)



from agent_fence_repair import repair_response, fence_like, RepairReport, Entry
from agent_fence_segmenter import (
    MAX_LINES, MAX_BYTES, PROMPT_VERSION,
    build_system_prompt, build_user_message, parse_fixer_answer,
    gate_partition, gate_drop_legality, gate_bash_witness, assemble, gate_assembly_proof,
    boundary_sanity, segment_and_repair,
)
from agent_repl_parse import fence_tokens, extract_code_blocks

NL = chr(10)
AT = "at"
FT = fence_tokens(AT)
OP, CL = FT["py_open"], FT["py_close"]


def _segs(*items):
    return {"segments": list(items)}


# ---------------------------------------------------------------------------
# Verdict on RepairReport (REQ-LFS-001/002)
# ---------------------------------------------------------------------------

def test_verdict_attribute_exists_defaults_ok():
    assert RepairReport().verdict == "ok"


def test_verdict_clean_for_prose():
    _, rep = repair_response("just prose no fences", AT)
    assert rep.verdict == "clean"
    assert fence_like("just prose no fences") is False


def test_verdict_ok_for_valid_block():
    _, rep = repair_response(OP + NL + "print(1)" + NL + CL, AT)
    assert rep.verdict == "ok"


def test_verdict_ok_for_half_open_clean_python():
    # B1: EOF auto-close of a python block that parses cleanly -> lost nothing.
    _, rep = repair_response(OP + NL + "x = 1 + 2", AT)
    assert rep.verdict == "ok"


def test_verdict_ambiguous_for_half_open_broken_python():
    _, rep = repair_response(OP + NL + "print(", AT)
    assert rep.verdict == "ambiguous"


def test_verdict_ambiguous_for_half_open_bash():
    _, rep = repair_response(FT["sh_open"] + NL + "ls -la", AT)
    assert rep.verdict == "ambiguous"


def test_verdict_ambiguous_for_unbalanced_refusal():
    _, rep = repair_response(OP + NL + OP + NL + "x = 1" + NL + CL, AT)
    assert rep.verdict == "ambiguous"
    assert "unbalanced-block" in rep


def test_verdict_ambiguous_for_mixed_style_megamerge():
    std_op, std_cl = "```python", "```"
    mixed = OP + NL + "x = 1" + NL + std_op + NL + "y = 2" + NL + std_cl + NL + "z = 3" + NL + CL
    _, rep = repair_response(mixed, AT)
    assert rep.verdict == "ambiguous"


def test_bash_fastpath_with_foreign_col0_stays_ok():
    # std-active bash block whose body contains a foreign @PY token at col 0.
    # ast.parse would fail on bash body, so the mixed-style probe MUST skip
    # non-python blocks (REQ-LFS-002: bash has no parse check).
    body = "```bash" + NL + "ls -la" + NL + OP + NL + "```"
    _, rep = repair_response(body, "std")
    assert rep.verdict == "ok"


def test_verdict_ok_for_fence_chars_inside_string():
    # A ``` inside a string literal parses cleanly -> stays ok (no false positive).
    _, rep = repair_response(OP + NL + "s = '```'" + NL + "print(s)" + NL + CL, AT)
    assert rep.verdict == "ok"


def test_verdict_ambiguous_for_h1b_protocol_autoclose():
    tco = "<" + "tool_call" + ">"
    _, rep = repair_response("text" + NL + tco + NL + "print(1)", AT)
    assert rep.verdict == "ambiguous"


# ---------------------------------------------------------------------------
# Gate G1 partition (REQ-LFS-010)
# ---------------------------------------------------------------------------

def test_gate_partition_ok():
    ok, _ = gate_partition([{"from": 0, "to": 3}, {"from": 3, "to": 5}], 5)
    assert ok


def test_gate_partition_gap():
    ok, msg = gate_partition([{"from": 0, "to": 2}, {"from": 3, "to": 5}], 5)
    assert not ok and "gap at line 2" in msg


def test_gate_partition_overlap():
    ok, msg = gate_partition([{"from": 0, "to": 3}, {"from": 2, "to": 5}], 5)
    assert not ok and "overlap at line 2" in msg


def test_gate_partition_out_of_bounds():
    ok, msg = gate_partition([{"from": 0, "to": 6}], 5)
    assert not ok and "out of bounds" in msg


# ---------------------------------------------------------------------------
# Gate G2 drop legality (REQ-LFS-011)
# ---------------------------------------------------------------------------

def test_gate_drop_legality_ok_on_fence_drop():
    lines = [OP, "print(1)", CL, "prose"]
    segs = [{"type": "drop", "from": 0, "to": 1}, {"type": "code", "from": 1, "to": 2, "lang": "python"},
            {"type": "drop", "from": 2, "to": 3}, {"type": "prose", "from": 3, "to": 4}]
    ok, _ = gate_drop_legality(segs, lines)
    assert ok


def test_gate_drop_legality_illegal_content_drop():
    lines = ["print(1)", "secret = 42"]
    segs = [{"type": "prose", "from": 0, "to": 1}, {"type": "drop", "from": 1, "to": 2}]
    ok, msg = gate_drop_legality(segs, lines)
    assert not ok and "illegal drop at line 1" in msg


def test_gate_fence_line_in_prose_is_illegal():
    lines = ["intro", OP, "print(1)"]
    segs = [{"type": "prose", "from": 0, "to": 3}]
    ok, msg = gate_drop_legality(segs, lines)
    assert not ok and "fence line in prose at line 1" in msg


# ---------------------------------------------------------------------------
# Assembly + content-preservation proof (REQ-LFS-012)
# ---------------------------------------------------------------------------

def test_assemble_preserves_body_and_inserts_block():
    lines = [OP, "print(42)", CL]
    segs = [{"type": "drop", "from": 0, "to": 1}, {"type": "code", "from": 1, "to": 2, "lang": "python"},
            {"type": "drop", "from": 2, "to": 3}]
    out = assemble(segs, lines, AT)
    assert out == OP + NL + "print(42)" + NL + CL
    ok, msg = gate_assembly_proof(out, segs, lines, AT)
    assert ok, msg


def test_gate_assembly_proof_detects_body_drift():
    # #1 (self-reference) regression: proof must compare against the ORIGINAL
    # lines, not the assembler's own memory. Simulate an assembler that drops the
    # last body line (the silent-code-loss bug class) and prove G3 catches it.
    lines = ["print(42)", "answer['ready'] = True"]
    segs = [{"type": "code", "from": 0, "to": 2, "lang": "python"}]
    good = OP + NL + NL.join(lines) + NL + CL
    assert gate_assembly_proof(good, segs, lines, AT)[0]
    # buggy assembly dropped the final body line:
    buggy = OP + NL + "print(42)" + NL + CL
    ok, msg = gate_assembly_proof(buggy, segs, lines, AT)
    assert not ok and "body drift" in msg


# ---------------------------------------------------------------------------
# parse_fixer_answer (REQ-LFS-009)
# ---------------------------------------------------------------------------

def test_parse_valid():
    raw = json.dumps(_segs({"type": "prose", "from": 0, "to": 1},
                           {"type": "code", "from": 1, "to": 2, "lang": "python"}))
    segs, reason = parse_fixer_answer(raw, 2)
    assert segs is not None and reason == ""


def test_parse_strips_wrapping_prose():
    raw = "Here you go:\n" + json.dumps(_segs({"type": "code", "from": 0, "to": 1, "lang": "python"})) + "\nDone."
    segs, _ = parse_fixer_answer(raw, 1)
    assert segs is not None


def test_parse_missing_lang():
    raw = json.dumps(_segs({"type": "code", "from": 0, "to": 1}))
    segs, reason = parse_fixer_answer(raw, 1)
    assert segs is None and "lang" in reason


def test_parse_unknown_key():
    raw = json.dumps(_segs({"type": "prose", "from": 0, "to": 1, "extra": 1}))
    segs, reason = parse_fixer_answer(raw, 1)
    assert segs is None and "unknown keys" in reason


def test_parse_bad_json():
    segs, reason = parse_fixer_answer("{not json}", 1)
    assert segs is None and "JSON" in reason


# ---------------------------------------------------------------------------
# segment_and_repair outcomes
# ---------------------------------------------------------------------------

def _ok_invoke(segments):
    def _fn(messages, kwargs):
        return json.dumps(_segs(*segments)), True
    return _fn


def test_applied_end_to_end():
    text = OP + NL + "print(" + NL + "tail prose"
    inv = _ok_invoke([{"type": "drop", "from": 0, "to": 1},
                      {"type": "code", "from": 1, "to": 2, "lang": "python"},
                      {"type": "prose", "from": 2, "to": 3}])
    out, entries, verdict, tl = segment_and_repair(text, AT, invoke_fn=inv, model="m",
                                                   telemetry_writer=lambda r: None)
    assert verdict == "ok" and tl["outcome"] == "applied"
    assert entries[0].step == "llm-repair-applied"
    # code preserved verbatim and now extractable
    blocks = extract_code_blocks(out, fence_style=AT)
    assert [b.code for b in blocks] == ["print("]


def test_all_prose_terminates_clean():
    # REQ-LFS-016: a fence-LIKE reply the fixer classifies as pure prose (zero
    # code, zero drop) returns status-quo text as clean -> no-code feedback path.
    text = OP + NL + "print("
    # A drop of the stray opener is REQUIRED (col-0 fence line in prose is G2-illegal),
    # so a lone-opener input is NOT all-prose; the genuine all-prose case is a reply
    # with no col-0 fence content that the ladder still flagged ambiguous.
    def _all_prose(messages, kwargs):
        return json.dumps(_segs({"type": "prose", "from": 0, "to": 1})), True
    out, entries, verdict, tl = segment_and_repair("just prose", AT, invoke_fn=_all_prose,
                                                   model="m", telemetry_writer=lambda r: None)
    assert verdict == "clean" and tl["outcome"] == "all-prose" and out == "just prose"
    # and the drop-a-col-0-opener variant correctly does NOT count as all-prose:
    inv = _ok_invoke([{"type": "drop", "from": 0, "to": 1},
                      {"type": "prose", "from": 1, "to": 2}])
    _, _, v2, tl2 = segment_and_repair(text, AT, invoke_fn=inv, model="m",
                                       telemetry_writer=lambda r: None)
    assert tl2["outcome"] != "all-prose"  # zero code WITH drop -> G3 fail -> retry/give-up


def test_transport_error_gives_up():
    text = OP + NL + "print("
    def _boom(messages, kwargs):
        raise RuntimeError("connection reset")
    out, entries, verdict, tl = segment_and_repair(text, AT, invoke_fn=_boom, model="m",
                                                   telemetry_writer=lambda r: None)
    assert verdict == "ambiguous" and tl["outcome"] == "call-error"
    assert out == text
    assert entries[0].step == "llm-repair-failed"


def test_ok_false_transport_error():
    text = OP + NL + "print("
    def _falsey(messages, kwargs):
        return "", False
    out, entries, verdict, tl = segment_and_repair(text, AT, invoke_fn=_falsey, model="m",
                                                   telemetry_writer=lambda r: None)
    assert verdict == "ambiguous" and tl["outcome"] == "call-error"


def test_size_guard_too_large():
    text = "x" * (MAX_BYTES + 10)
    def _never(messages, kwargs):
        raise AssertionError("must not be called")
    out, entries, verdict, tl = segment_and_repair(text, AT, invoke_fn=_never, model="m",
                                                   telemetry_writer=lambda r: None)
    assert verdict == "ambiguous" and tl["outcome"] == "too-large"
    assert entries[0].step == "llm-repair-failed"


def test_retry_with_feedback_after_gate_failure():
    text = OP + NL + "print("
    calls = {"n": 0}
    seen_feedback = {"has": False}

    def _flaky(messages, kwargs):
        calls["n"] += 1
        # first attempt: bad JSON -> G-parse failure; second: good
        if calls["n"] == 1:
            return "{ this is not json", True
        seen_feedback["has"] = any("Previous attempt failed" in m.get("content", "")
                                   for m in messages)
        return json.dumps(_segs({"type": "drop", "from": 0, "to": 1},
                                {"type": "code", "from": 1, "to": 2, "lang": "python"})), True

    out, entries, verdict, tl = segment_and_repair(text, AT, invoke_fn=_flaky, model="m",
                                                   telemetry_writer=lambda r: None, max_retries=3)
    assert verdict == "ok" and calls["n"] == 2
    assert seen_feedback["has"] is True   # REQ-LFS-007: retry input MUST change
    assert tl["retries_used"] == 1


def test_retries_bounded():
    text = OP + NL + "print("
    calls = {"n": 0}
    def _always_bad(messages, kwargs):
        calls["n"] += 1
        return "garbage", True
    out, entries, verdict, tl = segment_and_repair(text, AT, invoke_fn=_always_bad, model="m",
                                                   telemetry_writer=lambda r: None, max_retries=3)
    assert verdict == "ambiguous" and tl["outcome"] == "give-up"
    assert calls["n"] == 4   # 1 initial + 3 retries, never a 5th


def test_kwargs_are_isolated_thinking_off():
    text = OP + NL + "print("
    captured = {}
    def _cap(messages, kwargs):
        captured.update(kwargs)
        return json.dumps(_segs({"type": "drop", "from": 0, "to": 1},
                                {"type": "code", "from": 1, "to": 2, "lang": "python"})), True
    segment_and_repair(text, AT, invoke_fn=_cap, model="m", telemetry_writer=lambda r: None)
    assert captured["temperature"] == 0
    assert captured["max_tokens"]  # rides in kwargs (OPENAI_BODY_PARAMS path)
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}


def test_boundary_flag_when_parse_fails_and_hint_follows():
    # try:/x=1 has no except -> ast SyntaxError; following prose starts with
    # "except:" (a continuation hint) -> boundary uncertain flag (never a retry).
    flags = boundary_sanity(
        [{"type": "code", "from": 1, "to": 3, "lang": "python"},
         {"type": "prose", "from": 3, "to": 4}],
        [OP, "try:", "x = 1", "except:"])
    assert any("boundary uncertain" in f for f in flags)


def test_boundary_no_flag_when_body_parses():
    # A complete block that parses must NOT be flagged even if prose follows.
    flags = boundary_sanity(
        [{"type": "code", "from": 1, "to": 3, "lang": "python"},
         {"type": "prose", "from": 3, "to": 4}],
        [OP, "x = 1", "y = 2", "just prose"])
    assert flags == []


def test_prompt_has_no_col0_fence_token():
    prompt = build_system_prompt(AT)
    for ln in prompt.split(NL):
        # the raw active tokens must not appear alone at col 0
        assert ln.strip() not in (OP, CL, FT["sh_open"], FT["sh_close"])


# ---------------------------------------------------------------------------
# Regression tests for the second-round (FULL review) fixes D1/D2/D5.
# ---------------------------------------------------------------------------

def test_fence_like_ignores_tab_indented_tag():
    # D2: a TAB-indented lone envelope tag is legitimate prose, not fence-like.
    assert fence_like("\t<tool_call>") is False
    assert fence_like("<tool_call>") is True  # col-0 tag IS fence-like


def test_nonstr_never_raises_returns_clean():
    # D5: a truthy non-str must not raise in telemetry build; returns clean, no call.
    def _never(m, k):
        raise AssertionError("must not call the model")
    out, en, v, tl = segment_and_repair(["not", "a", "string"], AT,
                                        invoke_fn=_never, model="m",
                                        telemetry_writer=lambda r: None)
    assert v == "clean" and en == []


def test_applied_result_is_strip_envelopes_idempotent():
    # D1: segment_and_repair returns the SAME (stripped) form G3 proved, so the
    # stored text == the proven text; re-stripping is a no-op.
    def _inv(m, k):
        return json.dumps(_segs({"type": "drop", "from": 0, "to": 1},
                                {"type": "code", "from": 1, "to": 2, "lang": "bash"})), True
    out, en, v, tl = segment_and_repair(FT["sh_open"] + NL + "ls", AT,
                                        invoke_fn=_inv, model="m",
                                        telemetry_writer=lambda r: None)
    assert v == "ok" and tl["outcome"] == "applied"
    from agent_fence_repair import strip_envelopes
    again, _ = strip_envelopes(out, AT)
    assert out == again


# --- third-round improvements: D4 indent hint + D6 banner count ---------------

def test_boundary_sanity_flags_greater_indent():
    # D4: prose tail MORE-indented than the block min indent == dangling body.
    flags = boundary_sanity(
        [{"type": "code", "from": 1, "to": 3, "lang": "python"},
         {"type": "prose", "from": 3, "to": 4}],
        [OP, "try:", "    x = 1", "        y = 2"])
    assert any("boundary uncertain" in f for f in flags)


def test_boundary_sanity_no_false_positive_on_plain_prose():
    # col-0 prose with no continuation keyword must NOT flag.
    flags = boundary_sanity(
        [{"type": "code", "from": 1, "to": 3, "lang": "python"},
         {"type": "prose", "from": 3, "to": 4}],
        [OP, "try:", "    x = 1", "all done here"])
    assert flags == []


def test_applied_banner_reports_dropped_count():
    # REQ-LFS-015: the user-facing note names the dropped fence/tag line count.
    def _inv(m, k):
        return json.dumps(_segs({"type": "drop", "from": 0, "to": 1},
                                {"type": "code", "from": 1, "to": 2, "lang": "bash"})), True
    _, en, v, tl = segment_and_repair(FT["sh_open"] + NL + "ls", AT,
                                      invoke_fn=_inv, model="m", telemetry_writer=lambda r: None)
    assert v == "ok"
    assert "dropped 1 fence/tag line" in en[0].note


# --- fourth-round adversarial fixes: D7-1 (code-range fence) + D7-3 (md head) --

def test_gate_rejects_active_fence_inside_code_range():
    # D7-1: a code range containing an active col-0 fence line splits the block
    # and silently drops the tail -> G2 must reject it.
    lines = [OP, "print(1)", CL, "print(2)", CL]
    segs = [{"type": "drop", "from": 0, "to": 1},
            {"type": "code", "from": 1, "to": 4, "lang": "python"},
            {"type": "drop", "from": 4, "to": 5}]
    ok, msg = gate_drop_legality(segs, lines, AT)
    assert not ok and "active fence line in code" in msg


def test_segmenter_gives_up_not_silently_losing_code():
    # End-to-end: the same broken input must NOT be "applied" with print(2) lost.
    def _inv(m, k):
        return json.dumps(_segs({"type": "drop", "from": 0, "to": 1},
                                {"type": "code", "from": 1, "to": 4, "lang": "python"},
                                {"type": "drop", "from": 4, "to": 5})), True
    text = OP + NL + "print(1)" + NL + CL + NL + "print(2)" + NL + CL
    out, en, v, tl = segment_and_repair(text, AT, invoke_fn=_inv, model="m",
                                        telemetry_writer=lambda r: None)
    # REQ-LFS-022: the stray col-0 closer INSIDE the code range is now
    # normalized (fence_splits) instead of failing G2 — the split is loss-FREE:
    # both print(1) and print(2) survive as separate blocks. The invariant
    # this test pins is "never silently lose code": applied is fine, loss is not.
    assert tl["norm_fence_splits"] >= 1
    assert "print(1)" in out and "print(2)" in out, out
    # loss-FREE re-assembly: extracted bodies are byte-identical to originals
    from agent_repl_parse import extract_code_blocks as _x
    assert [b.code for b in _x(out, fence_style=AT)] == ["print(1)", "print(2)"]


def test_boundary_sanity_skips_markdown_quote_head():
    # D7-3: an indented markdown/quote prose tail is legitimate prose, not a
    # stranded body line -> must NOT flag "boundary uncertain".
    flags = boundary_sanity(
        [{"type": "code", "from": 1, "to": 3, "lang": "python"},
         {"type": "prose", "from": 3, "to": 4}],
        [OP, "try:", "    x = 1", "  - keep going"])
    assert flags == []


# --- fifth-round context-review test gaps: ladder-stability + multi-block ------

def test_tier2_applied_text_is_ladder_stable():
    # The applied (stripped) text must re-run through the PURE ladder unchanged
    # with a non-ambiguous verdict, so tier-2 never re-fires every turn.
    def _inv(m, k):
        return json.dumps(_segs({"type": "drop", "from": 0, "to": 1},
                                {"type": "code", "from": 1, "to": 2, "lang": "python"},
                                {"type": "prose", "from": 2, "to": 3})), True
    out, en, v, tl = segment_and_repair(OP + NL + "print(1)" + NL + "tail prose", AT,
                                        invoke_fn=_inv, model="m", telemetry_writer=lambda r: None)
    assert v == "ok"
    from agent_fence_repair import _repair_core
    again, rep = _repair_core(out, AT)
    assert again == out and rep.verdict != "ambiguous"


def test_multi_block_segmentation_passes_gates():
    # Two python code ranges separated by prose must tile/assemble/prove cleanly
    # (guards against future gate tightening silently rejecting valid 2-block answers).
    lines = [OP, "a = 1", CL, "between", OP, "b = 2", CL]
    segs = [{"type": "drop", "from": 0, "to": 1},
            {"type": "code", "from": 1, "to": 2, "lang": "python"},
            {"type": "drop", "from": 2, "to": 3},
            {"type": "prose", "from": 3, "to": 4},
            {"type": "drop", "from": 4, "to": 5},
            {"type": "code", "from": 5, "to": 6, "lang": "python"},
            {"type": "drop", "from": 6, "to": 7}]
    assert gate_partition(segs, len(lines))[0]
    assert gate_drop_legality(segs, lines, AT)[0]
    asm = assemble(segs, lines, AT)
    ok, msg = gate_assembly_proof(asm, segs, lines, AT)
    assert ok, msg
    blocks = extract_code_blocks(asm, fence_style=AT)
    assert [b.code for b in blocks] == ["a = 1", "b = 2"]


# ---------------------------------------------------------------------------
# Fresh/context-review regressions: gate-internal failures the earlier
# end-to-end tests did NOT exercise (silent code loss, prose->bash, never-raise).
# ---------------------------------------------------------------------------

def test_gate_partition_rejects_bool_bounds():
    # #4: gate is the hard gate; bool must not masquerade as an int bound.
    ok, _ = gate_partition([{"type": "code", "from": False, "to": True, "lang": "python"}], 1)
    assert not ok


def test_bash_witness_rejects_fabricated_bash_without_sh_fence():
    # #2: prose typed as bash with NO active SH fence token must be rejected.
    lines = ["just a note", "rm -rf ~/notes", "more prose"]
    segs = [{"type": "code", "from": 0, "to": 3, "lang": "bash"}]
    ok, msg = gate_bash_witness(segs, lines, AT)
    assert not ok and "SH" in msg   # wording changed with credentialed-witness rule; rejection is the invariant
    assert gate_bash_witness([{"type": "code", "from": 0, "to": 3, "lang": "python"}], lines, AT)[0]


def test_bash_witness_allows_bash_when_input_has_sh_token():
    sh = FT["sh_open"]
    lines = [sh, "echo hi", FT["sh_close"]]
    segs = [{"type": "drop", "from": 0, "to": 1},
            {"type": "code", "from": 1, "to": 2, "lang": "bash"},
            {"type": "drop", "from": 2, "to": 3}]
    assert gate_bash_witness(segs, lines, AT)[0]


def test_segmenter_refuses_fabricated_bash_end_to_end():
    # end-to-end: a fabricated bash-only answer (no SH token in input) gives up,
    # status quo preserved -- tier-2 never turns prose into executed shell.
    def _inv(m, k):
        return json.dumps(_segs({"type": "code", "from": 0, "to": 3, "lang": "bash"})), True
    text = "just a note\nrmtmp x\nmore prose"   # no fence at all -> tier1 clean, but
    # force tier-2 path directly through segment_and_repair regardless of verdict:
    out, en, v, tl = segment_and_repair(text, AT, invoke_fn=_inv, model="m",
                                        telemetry_writer=lambda r: None)
    assert tl["outcome"] != "applied"
    assert out == text  # unchanged


def test_never_raises_on_lone_surrogate():
    # #3: surrogate encode path must not raise; degrade to a give-up telemetry row.
    def _inv(m, k):
        return json.dumps(_segs({"type": "prose", "from": 0, "to": 2})), True
    text = "x=1 " + chr(0xD800) + "\nmore"  # lone surrogate -> utf-8 would raise
    tl = {}
    def _cap(r):
        tl.update(r)
    out, en, v, tel = segment_and_repair(text, AT, invoke_fn=_inv, model="m",
                                         telemetry_writer=_cap)
    assert out is text or out == text  # never crashed
    assert tel.get("outcome") is not None


def test_empty_input_still_emits_consistent_telemetry():
    # #5: empty/non-str still writes a schema-complete telemetry row (REQ-LFS-017).
    rows = []
    segment_and_repair("", AT, invoke_fn=lambda m,k: ("", True), model="m",
                       telemetry_writer=rows.append)
    segment_and_repair("   \n  ", AT, invoke_fn=lambda m,k: ("", True), model="m",
                       telemetry_writer=rows.append)
    assert len(rows) == 2
    for r in rows:
        assert set(["prompt_version","model","sha256","in_lines","in_bytes","outcome"]) <= set(r)
        assert r["outcome"] == "all-prose"


class TestChainEndToEnd:
    """Ladder-ambiguous -> tier-2 applied -> correct executable bodies (stub)."""

    def test_envelope_input_full_chain(self):
        NL = chr(10); AT = chr(64); SL = chr(47)
        L = chr(60); G = chr(62); BT = chr(96)*3
        from agent_fence_segmenter import segment_and_repair
        from agent_fence_repair import repair_response, extract_code_blocks
        env = (L+"invoke"+G + NL + BT+"python" + NL + "print(7)"
               + NL + BT + NL + L+SL+"invoke"+G)
        rep, notes = repair_response(env, "at")
        assert notes.verdict == "ambiguous"
        def seg_json():
            s = '{"type":"%s","from":%d,"to":%d}'
            return ('{"segments":[' + ",".join([
                s % ("drop",0,1), s % ("drop",1,2),
                '{"type":"code","from":2,"to":3,"lang":"python"}',
                s % ("drop",3,4), s % ("drop",4,5)]) + "]}")
        out, ents, v, tele = segment_and_repair(
            rep, "at", invoke_fn=lambda m,k: (seg_json(), True), model="m")
        assert v == "ok" and tele["outcome"] == "applied"
        assert [b.code for b in extract_code_blocks(out, fence_style="at")] == ["print(7)"]
def test_keep_set_couples_to_step_messages():
    """agent_loop._KEEP is a hardcoded allowlist; membership is deliberate.

    Adding a lesson to STEP_MESSAGES (or a typo in _KEEP) must fail HERE,
    not silently drop a true lesson from context after a tier-2 success.
    """
    import ast
    from pathlib import Path
    from agent_fence_repair import STEP_MESSAGES
    src = Path(__file__).resolve().parents[1].joinpath("agent_loop.py").read_text()
    tree = ast.parse(src)
    keep = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_KEEP":
                    keep = ast.literal_eval(node.value)
    assert keep is not None, "_KEEP allowlist vanished from agent_loop"
    assert keep <= set(STEP_MESSAGES)
    # The applied lesson arrives via _t2_entries, never via _KEEP:
    assert "llm-repair-applied" not in keep and "llm-repair-failed" not in keep
    # Exact membership is a deliberate decision; adding a lesson must update
    # BOTH places and this assertion forces that review:
    assert keep == {"other-style-enclosed", "half-open-other-style",
                    "protocol-envelope-stripped", "protocol-tags",
                    "glue-opener-split"}, keep

def test_prompt_example_not_glued():
    # Reviewer-found regression: a missing comma implicit-concatenated the
    # Example label onto the JSON example line in the live prompt.
    from agent_fence_segmenter import build_system_prompt
    pl = build_system_prompt("at").split(chr(10))
    assert not any(l.startswith("Example") and "segments" in l for l in pl)
    i = next(k for k, l in enumerate(pl) if l.startswith("Example"))
    assert pl[i-1].strip() == "", "blank separator lost (implicit concat)"
