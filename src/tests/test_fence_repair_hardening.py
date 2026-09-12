# pylint: disable=unused-import,unused-variable
"""Tier-2 repair hardening tests (REQ-LFS-022): the holes found by live
black-box testing — std-only replies, glue openers, gate/segmenter tiling
pedantry, and silent give-ups. Includes seeded mutation fuzzing over the
whole ladder with universal invariants, so future regressions in ANY stage
are caught, not just the three that bit us."""
import random
from agent_fence_repair import repair_response
from agent_fence_segmenter import (assemble, gate_drop_legality, gate_partition,
                       normalize_segments, segment_and_repair)
from agent_repl_parse import extract_code_blocks


def _bodies(text, style="at"):
    rep, _notes = repair_response(text, style)
    return [b.code for b in extract_code_blocks(rep, fence_style=style)], rep


class TestStdUnderActive:
    def test_std_only_reply_is_ambiguous_not_blindly_converted(self):
        # REQ-LFS-022 DESIGN: tier-1 must NOT convert std under at-style even
        # when std-only — code-vs-prose is semantic. It says ambiguous, and
        # tier-2 (with tolerances) recovers it end-to-end below.
        src = "Here you go:\n```python\nprint(1)\n```\nDone."
        rep, notes = repair_response(src, "at")
        assert rep == src
        assert notes.verdict == "ambiguous"

    def test_std_only_recovers_via_tier2(self):
        import json as _json
        src = "Here you go:\n```python\nprint(1)\n```\nDone."
        good = _json.dumps({"segments": [
            {"type": "prose", "from": 0, "to": 1},
            {"type": "drop", "from": 1, "to": 2},
            {"type": "code", "from": 2, "to": 3, "lang": "python"},
            {"type": "drop", "from": 3, "to": 4},
            {"type": "prose", "from": 4, "to": 5}]})
        out, _en, v, tl = segment_and_repair(
            src, "at", invoke_fn=lambda m, k: (good, True), model="t",
            telemetry_writer=lambda d: None)
        assert v == "ok" and tl["outcome"] == "applied"
        from agent_repl_parse import extract_code_blocks as _x
        assert [b.code for b in _x(out, fence_style="at")] == ["print(1)"]

    def test_mixed_reply_keeps_std_guard(self):
        # real @PY token anywhere -> std pairs may be prose quotes: refuse
        src = "The @PY token opens blocks.\n```python\nprint(1)\n```"
        rep, notes = repair_response(src, "at")
        assert "```" in rep and rep == src  # std untouched
        assert notes.verdict == "ambiguous"

    def test_std_style_session_unchanged_behavior(self):
        src = "```python\nprint(2)\n```"
        rep, notes = repair_response(src, "std")
        bodies = [b.code for b in extract_code_blocks(rep, fence_style="std")]
        assert bodies == ["print(2)"]


class TestGlueOpeners:
    def test_bare_glue_splits(self):
        rep, notes = repair_response("@PY print(3)\n@/PY", "at")
        bodies = [b.code for b in extract_code_blocks(rep, fence_style="at")]
        assert bodies == ["print(3)"]
        assert any(e.step == "glue-opener-split" for e in notes.entries)

    def test_glue_without_closer_untouched(self):
        src = "  @PY print(4)"
        rep, notes = repair_response(src, "at")
        assert rep == src

    def test_token_in_string_never_split(self):
        src = "@PY\nx = '@PY abc'\nprint(x)\n@/PY"
        rep, notes = repair_response(src, "at")
        bodies = [b.code for b in extract_code_blocks(rep, fence_style="at")]
        assert bodies == ["x = '@PY abc'\nprint(x)"]


class TestSegmenterTolerances:
    LINES = ["note", "@PY", "print(1)", "@/PY"]

    def test_gap_filled_only_when_fence_like(self):
        segs = [{"type": "prose", "from": 0, "to": 1},
                {"type": "code", "from": 2, "to": 3}]
        ns, stats = normalize_segments(segs, self.LINES, "at")
        assert stats["gap_fills"] == 2   # mid gap + trailing closer line
        ok1, _ = gate_partition(ns, len(self.LINES))
        ok2, _ = gate_drop_legality(ns, self.LINES, "at")
        assert ok1 and ok2
        assembled = assemble(ns, self.LINES, "at")
        assert "print(1)" in assembled and assembled.count("@PY") == 1

    def test_prose_gap_stays_fatal(self):
        lines = ["head", "REAL PROSE LINE", "@PY", "print(1)", "@/PY"]
        segs = [{"type": "prose", "from": 0, "to": 1},
                {"type": "code", "from": 2, "to": 4}]
        ns, stats = normalize_segments(segs, lines, "at")
        # exactly the trailing fence noise is absorbed; the REAL-PROSE gap is NOT
        assert stats["gap_fills"] == 1
        assert not any(s["from"] == 1 for s in ns)
        ok, why = gate_partition(ns, len(lines))
        assert not ok and "gap" in why

    def test_fence_inside_code_splits_not_fails(self):
        lines = ["@PY", "print(2)", "@/PY"]
        segs = [{"type": "code", "from": 0, "to": 3, "lang": "python"}]
        ns, stats = normalize_segments(segs, lines, "at")
        assert stats["fence_splits"] >= 1
        ok1, _ = gate_partition(ns, 3)
        ok2, _ = gate_drop_legality(ns, lines, "at")
        assert ok1 and ok2
        assembled = assemble(ns, lines, "at")
        from agent_fence_repair import extract_code_blocks
        assert [b.code for b in extract_code_blocks(assembled, fence_style="at")] == ["print(2)"]

    def test_content_line_never_dropped_by_normalizer(self):
        lines = ["x=1", "y=2", "@PY"]
        segs = [{"type": "code", "from": 0, "to": 2, "lang": "python"}]
        ns, stats = normalize_segments(segs, lines, "at")
        # trailing drop allowed (stray fence token), but it may never cover the
        # content range 0-2:
        for s in ns:
            if s["type"] == "drop":
                assert s["from"] >= 2
        assert stats["gap_fills"] == 1


class _Scripted:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
    def __call__(self, messages, kwargs):
        self.calls += 1
        return self.answers.pop(0) if self.answers else ('{"segments":[]}', True)


class TestGiveUpLesson:
    def test_giveup_carries_forensics(self):
        txt = "@PY\nprint(5)"
        inv = _Scripted([("garbage", True)] * 4)
        _, entries, verdict, tele = segment_and_repair(
            txt, "at", invoke_fn=inv, model="t", telemetry_writer=lambda d: None)
        assert verdict == "ambiguous"
        notes = [e.note for e in entries if e.step == "llm-repair-failed" and e.note]
        assert notes, "give-up must never be silent"
        assert "segmenter" in notes[0]

    def test_imperfect_but_recoverable_answer_applies(self):
        txt = "hi\n@PY\nprint(6)\n@/PY"
        good = '{"segments": [{"type":"prose","from":0,"to":1},' \
               '{"type":"code","from":2,"to":3,"lang":"python"}]}'
        inv = _Scripted([(good, True)])
        out, entries, verdict, tele = segment_and_repair(
            txt, "at", invoke_fn=inv, model="t", telemetry_writer=lambda d: None)
        assert verdict == "ok" and tele["outcome"] == "applied"
        assert tele["norm_gap_fills"] >= 1
        assert "print(6)" in out


class TestUniversalInvariants:
    """Seeded mutation fuzz: every mutation of a well-formed reply must either
    repair to bodies that are BYTE-IDENTICAL to original block bodies, or be
    refused (verdict != ok). This is the anti-'silent bad example' net."""

    def _base_replies(self):
        return [
            "note\n@PY\nx = 1\n@/PY\nafter",
            "@PY\nprint(1)\n@/PY\ntext\n@SH\necho hi\n@/SH",
            "```python\nprint(7)\n```",
            "intro\n```python\na = 1\nb = 2\n```\n```bash\nls\n```\nend",
            "@PY\n@PY\nprint(8)\n@/PY\n@/PY",
        ]

    MUTATIONS = ["drop_closer", "to_std", "glue", "stray_opener", "envelope", "blank", "none"]

    def test_envelope_with_foreign_fence_is_ambiguous_not_ok(self):
        # REGRESSION (fuzz-caught): tool_call envelope around md fence under
        # at-style: protocol stage used to render the body verbatim inside
        # @PY, smuggling ```python into the code and claiming verdict ok.
        text = "<tool_call>\n```python\nprint(7)\n```\n</tool_call>"
        rep, notes = repair_response(text, "at")
        assert notes.verdict == "ambiguous", notes.verdict
        # never render a body containing a foreign col0 fence as "ok"-quality
        from agent_fence_repair import extract_code_blocks
        for b in extract_code_blocks(rep, fence_style="at"):
            if not b.code: continue
            assert not b.code.lstrip().startswith("```") or notes.verdict == "ambiguous"

    def test_fuzz_never_raises_and_never_mangles(self):
        rng = random.Random(1337)
        for base in self._base_replies():
            for mut in self.MUTATIONS:
                for _ in range(6):
                    text = base
                    lines = text.split("\n")
                    if mut == "drop_closer" and len(lines) > 2:
                        k = [i for i, l in enumerate(lines) if l.strip().startswith("@/PY") or l.strip() == "```"]
                        if k: del lines[k[-1]]
                    elif mut == "to_std":
                        lines = ["```python" if l == "@PY" else ("```" if l == "@/PY" else l) for l in lines]
                    elif mut == "glue":
                        for i, l in enumerate(lines):
                            if l.strip() == "@PY" and i + 1 < len(lines):
                                lines[i] = "@PY " + lines[i + 1]; del lines[i + 1]; break
                    elif mut == "stray_opener" and len(lines) > 1:
                        lines.insert(rng.randrange(len(lines)), "@PY")
                    elif mut == "envelope":
                        lines = ["<tool_call>"] + lines + ["</tool_call>"]
                    elif mut == "blank":
                        lines.insert(rng.randrange(max(1, len(lines))), "")
                    text = "\n".join(lines)
                    rep, notes = repair_response(text, "at")   # invariant 1: never raises
                    bodies = [b.code for b in extract_code_blocks(rep, fence_style="at")]
                    if notes.verdict == "ok" and bodies:
                        originals = self._original_bodies(text)
                        for b in bodies:
                            assert any(b == o for o in originals), (
                                "silent content mutation on %r mutation of %r: %r not in %r" % (mut, text[:60], b[:60], [o[:60] for o in originals]))

    def _original_bodies(self, text):
        out = []
        for style in ("at", "std"):
            for b in extract_code_blocks(text, fence_style=style):
                out.append(b.code)
        # also: bodies under style the ladder converted FROM can differ by fence
        # strip only; recompute by scanning raw lines between fence tokens
        for op, cl in (("@PY", "@/PY"), ("@SH", "@/SH"), ("```python", "```"), ("```bash", "```")):
            cur, body = None, []
            for ln in text.split("\n"):
                if cur is None and ln.startswith(op + " "):   # glued opener counts
                    cur, body = True, [ln[len(op):].strip()]
                    continue
                if ln.strip() == op: cur = True; body = []; continue
                if cur and ln.strip() == cl: out.append("\n".join(body)); cur = None; continue
                if cur: body.append(ln)
        return out


class TestReviewFindingsRegression:
    """Locks findings from the two-spawn review of the Tier-2 segmenter.
    MED-1: bash witness must be an OPENER, not a stray closer.
    LOW-4: bare glue split needs a same-lang closer LATER in the text."""

    def test_bash_witness_rejects_closer_only(self):
        from agent_fence_segmenter import gate_bash_witness
        segs = [{"from": 0, "to": 1, "type": "prose"},
                {"from": 1, "to": 2, "type": "code", "lang": "bash"},
                {"from": 2, "to": 3, "type": "drop"}]
        ok, _msg = gate_bash_witness(segs, ["Run this", "ls -la", "@/SH"], "at")
        assert ok is False, "closer alone must not license bash execution"
        ok2, _ = gate_bash_witness(segs, ["@SH", "ls -la", "@/SH"], "at")
        assert ok2 is True, "a real opener must still witness"

    def test_glue_split_requires_later_same_lang_closer(self):
        from agent_fence_repair import fix_glue_openers
        # earlier foreign token must NOT vouch for the split
        t, e = fix_glue_openers("earlier token\n@/SH\n@PY x=1", "at")
        assert t == "earlier token\n@/SH\n@PY x=1" and not e
        # later same-lang closer licenses it
        t2, e2 = fix_glue_openers("@PY x=1\nprint(x)\n@/PY", "at")
        assert t2 == "@PY\nx=1\nprint(x)\n@/PY" and len(e2) == 1

class TestFixedPointVerdict:
    """ok must certify the text: verdicts never flip, ok output never drifts."""

    FLIP = (chr(64)+"SH ls"+chr(10)+chr(64)+"PY x=1"+chr(10)+"<invoke>"+chr(10)+"```"
            + chr(10)+"x=1"+chr(10)+"</parameter>"+chr(10)+"```java"+chr(10)
            + "print(1)"+chr(10)+chr(64)+"PY"+chr(10)+"print(1)"+chr(10)+"print(1)")

    def test_flip_case_now_stable(self):
        rep, notes = repair_response(self.FLIP, "at")
        rep2, notes2 = repair_response(rep, "at")
        assert (notes.verdict == "ok") == (notes2.verdict == "ok")
        if notes.verdict == "ok":
            assert rep2 == rep   # ok output is a fixed point

    def test_ok_verdict_idempotent_200_seeds(self):
        import random
        TOK = ["@PY","@/PY","@SH","@/SH","```python","```","```java",
               "<invoke>","</invoke>","<parameter=x>","</parameter>",
               "x=1","print(1)","ls -la","prose line","","    print(1)",
               "@PY x=1","@SH ls","x = " + "'''"]
        for seed in range(200):
            rng = random.Random(seed)
            s = chr(10).join(rng.choice(TOK) for _ in range(rng.randint(1,12)))
            a, na = repair_response(s, "at")
            b, nb = repair_response(a, "at")
            if na.verdict == "ok":
                assert nb.verdict == "ok", (seed, "flip")
                assert b == a, (seed, "drift")
            assert na.verdict in ("clean", "ok", "ambiguous")
