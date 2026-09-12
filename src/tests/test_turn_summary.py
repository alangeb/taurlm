"""Tests for turn_summary fixes: fence stripping, quality gate, and audit records.

Mirrors test_audit_writer.py style (plain pytest + tmp_path / temp_dir fixture,
imports straight from the module). No live LLM — _invoke_llm_with_retry_compression
is monkeypatched.
"""

import types

import pytest

import rlm.turn_summary as ts
from rlm.turn_summary import _strip_code_blocks, _write_summary_audit, _summary_quality_ok
from agent_audit_writer import AuditWriter


# --------------------------------------------------------------------------
# FIX 3: _strip_code_blocks — paired AND dangling (unclosed) for every style
# --------------------------------------------------------------------------

class TestStripPaired:
    """Complete (paired) blocks are removed, prose preserved."""

    def test_backtick_paired(self):
        text = "before\n```py\nprint(1)\n```\nafter"
        out = _strip_code_blocks(text)
        assert "print(1)" not in out
        assert "before" in out and "after" in out

    def test_at_py_paired(self):
        out = _strip_code_blocks("keep @PY\nx=1\n@/PY keep2")
        assert "x=1" not in out
        assert "keep" in out and "keep2" in out

    def test_at_sh_paired(self):
        out = _strip_code_blocks("a @SH\nls\n@/SH b")
        assert "ls\n" not in out or "ls" not in out
        assert "a" in out and "b" in out

    def test_html_py_paired(self):
        out = _strip_code_blocks("p1 <py>x=1</py> p2")
        assert "x=1" not in out and "p1" in out and "p2" in out

    def test_html_sh_paired(self):
        out = _strip_code_blocks("p1 <sh>echo hi</sh> p2")
        assert "echo hi" not in out and "p1" in out and "p2" in out


class TestStripDangling:
    """Unclosed openers leak (the real 5%-failure bug) — must strip to end."""

    def test_dangling_at_py(self):
        # The real-world leaked summary shape from the bug report.
        text = "sanity check here\n@PY\nprint(40 + 2)\nsanitycheckdone"
        out = _strip_code_blocks(text)
        assert out == "sanity check here"

    def test_dangling_at_sh(self):
        out = _strip_code_blocks("done.\n@SH\nrm -rf /tmp/x")
        assert out == "done."

    def test_dangling_backtick(self):
        out = _strip_code_blocks("result is good\n```\nsecret code")
        assert out == "result is good"

    def test_dangling_html_py(self):
        # Own-line (not inline) unclosed opener strips to its prefix.
        out = _strip_code_blocks("summary text\n<py>oops unclosed")
        assert out == "summary text"

    def test_dangling_html_sh(self):
        out = _strip_code_blocks("summary text\n<sh>oops unclosed")
        assert out == "summary text"

    def test_paired_before_dangling_ordering(self):
        # A complete block then a dangling one: both gone, leading prose kept.
        text = "head\n@PY\nok=1\n@/PY\ntail\n@SH\nleak"
        out = _strip_code_blocks(text)
        assert "ok=1" not in out and "leak" not in out
        assert "head" in out and "tail" in out


# --------------------------------------------------------------------------
# FIX 4: quality gate — too short / echo rejection
# --------------------------------------------------------------------------

class TestQualityGate:
    def test_too_short_rejected(self):
        # The real "TAU-MANUAL-OK" stub (13 chars) must now fail.
        assert _summary_quality_ok("TAU-MANUAL-OK", "do the thing please") is False

    def test_threshold_boundary(self):
        assert ts._MIN_CLEAN_CHARS >= 40
        short = "x" * (ts._MIN_CLEAN_CHARS - 1)
        assert _summary_quality_ok(short, "unrelated request text here") is False

    def test_echo_rejected(self):
        # Echo must share >=0.9 of its tokens with the request at the 0.9 gate.
        req = "fix the fence strip gap and add tests and verify the summary system"
        echo = "fix the fence strip gap and add tests and verify the summary system"
        assert _summary_quality_ok(echo, req) is False

    def test_stopword_dominated_legit_summary_passes(self):
        # Regression: a legit >=40-char summary with a lot of shared
        # stopwords should NOT be false-rejected at the raised threshold.
        req = "the fix to strip the fences and add the tests"
        legit = ("The summary system now handles fence stripping correctly, "
                 "which was the fix, and the tests pass too today.")
        assert _summary_quality_ok(legit, req) is True

    def test_good_summary_accepted(self):
        req = "fix the fence strip gap and add tests and verify"
        good = ("Root cause found: _strip_code_blocks only handled paired fences. "
                "Added dangling-opener strips so unclosed blocks no longer leak.")
        assert _summary_quality_ok(good, req) is True


# --------------------------------------------------------------------------
# FIX 1: AuditWriter.turn_summary emits a greppable typed record
# --------------------------------------------------------------------------

class TestAuditTurnSummary:
    def test_turn_summary_line_and_continuation(self, tmp_path):
        audit = tmp_path / "s.audit"
        writer = AuditWriter(audit)
        writer.turn_summary("Did the work and verified it.")
        writer.flush()
        text = audit.read_text()
        assert "TURN_SUMMARY" in text
        # summary appears as a continuation line
        assert "  | Did the work and verified it." in text

    def test_turn_summary_status_line(self, tmp_path):
        audit = tmp_path / "s.audit"
        writer = AuditWriter(audit)
        writer.turn_summary_status("skipped", "echo")
        writer.flush()
        text = audit.read_text()
        assert "TURN_SUMMARY" in text
        assert "status=skipped" in text and "reason=echo" in text


# --------------------------------------------------------------------------
# _write_summary_audit dispatches to turn_summary() on the writer
# --------------------------------------------------------------------------

class _FakeAudit:
    def __init__(self):
        self.calls = []

    def turn_summary(self, summary):
        self.calls.append(("turn_summary", summary))

    def flush(self):
        self.calls.append(("flush", None))


class _NoTurnSummaryAudit:
    def __init__(self):
        self.calls = []

    def user(self, *a, **k):
        self.calls.append(("user", a))

    def flush(self):
        self.calls.append(("flush", None))


def test_write_summary_audit_calls_turn_summary():
    fake = _FakeAudit()
    _write_summary_audit(fake, "Summary text here")
    assert ("turn_summary", "Summary text here") in fake.calls
    assert ("flush", None) in fake.calls
    # Must NOT fall back to bogus user()/assistant() writes
    assert not any(c[0] == "user" for c in fake.calls)


def test_write_summary_audit_no_turn_summary_attr_is_noop():
    # Legacy/fake writer without turn_summary must be a silent no-op (no crash).
    obj = _NoTurnSummaryAudit()
    _write_summary_audit(obj, "x")
    assert obj.calls == []


# --------------------------------------------------------------------------
# FIX 2: guard-skip path emits observability record WITHOUT calling the LLM
# --------------------------------------------------------------------------

def _make_agent(monkeypatch, request_text, substantive=True):
    """Build a minimal fake agent that passes guards 1-5."""
    agent = types.SimpleNamespace()
    agent.config = types.SimpleNamespace(
        rlm=types.SimpleNamespace(
            auto_summary=types.SimpleNamespace(enabled=True, model="m", max_output_tokens=64)))
    agent.client = object()
    agent.model_name = "m"
    agent.nesting_stack = ""
    agent.max_context_tokens = 200000

    def resolve_group_params():
        return {}
    agent.resolve_group_params = resolve_group_params

    user_msg = {"role": "user", "content": request_text}
    asst_msg = {"role": "assistant", "content": "x" * 100}
    if not substantive:
        asst_msg = {"role": "assistant", "content": "(none)"}
    agent.context = types.SimpleNamespace(_messages=[user_msg, asst_msg])

    fake_audit = _FakeAudit()
    agent._session = types.SimpleNamespace(
        audit_file=tmp_audit_path(), audit_writer=fake_audit)
    agent._fake_audit = fake_audit
    return agent


def tmp_audit_path():
    import tempfile, pathlib
    return pathlib.Path(tempfile.mktemp(suffix=".audit"))


def test_guard_skip_emits_record_without_llm(monkeypatch):
    """LLM returns empty -> status=skipped emitted, no summary attached."""
    called = {"llm": False}

    def fake_llm(*a, **k):
        called["llm"] = True
        return types.SimpleNamespace(text="   ")
    monkeypatch.setattr(
        "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
        fake_llm, raising=False)

    agent = _make_agent(monkeypatch, "please refactor module X and run tests")
    ok = ts.generate_turn_summary(agent)
    assert ok is False
    # LLM WAS called (we got past guards), but produced empty -> skipped record
    assert called["llm"] is True
    records = [c for c in agent._fake_audit.calls if c[0] == "turn_summary"]
    # Fake audit records turn_summary calls; skip uses turn_summary_status
    # which _FakeAudit lacks -> fallback path calls turn_summary(...)
    assert any("status=skipped" in str(r[1]) for r in records), agent._fake_audit.calls


def test_guard_4_root_only_does_not_emit(monkeypatch):
    """Non-root (nesting_stack set) is a trivially-false guard: no record, no LLM."""
    called = {"llm": False}

    def fake_llm(*a, **k):
        called["llm"] = True
        return types.SimpleNamespace(text="should not be used at all")
    monkeypatch.setattr(
        "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
        fake_llm, raising=False)

    agent = _make_agent(monkeypatch, "do something")
    agent.nesting_stack = "S"  # child agent -> guard 4 fails
    ok = ts.generate_turn_summary(agent)
    assert ok is False
    assert called["llm"] is False
    assert agent._fake_audit.calls == []  # no spam on trivial guards


# --------------------------------------------------------------------------
# REVIEW PASS: MAJOR — inline fence mentions must survive (anchored dangling)
# --------------------------------------------------------------------------

class TestInlineFenceProsePreserved:
    def test_inline_at_tokens_preserved(self):
        # The exact MAJOR repro: prose mentioning fence tokens must not be cut.
        out = _strip_code_blocks(
            "Fix added @PY and @SH dangling stripping.")
        assert out == "Fix added @PY and @SH dangling stripping."

    def test_inline_backtick_and_html_preserved(self):
        out = _strip_code_blocks(
            "mentions ``` fences and a <py> tag inline, all prose")
        assert out == "mentions ``` fences and a <py> tag inline, all prose"

    def test_inline_prose_survives(self):
        # Inline @PY mention must NOT truncate to 'Fix added' (the old bug).
        out = _strip_code_blocks(
            "Added @PY handling in the fixer.")
        assert "@PY" in out
        assert "Added" in out and "the fixer" in out

    def test_own_line_unclosed_still_strips(self):
        # A real own-line unclosed opener must still strip to its prefix.
        text = "real leak case\n@PY\nprint(40 + 2)\nleaked"
        assert _strip_code_blocks(text) == "real leak case"

    def test_indented_own_line_unclosed_strips(self):
        # Indented own-line opener (allowed by [ \t]*) still strips.
        text = "keep this\n    @PY\nleaked code here"
        assert _strip_code_blocks(text) == "keep this"

    def test_paired_fence_still_stripped(self):
        # Anchoring must not regress the paired path.
        text = "head\n```py\nprint(1)\n```\ntail"
        out = _strip_code_blocks(text)
        assert "print(1)" not in out and "head" in out and "tail" in out


# --------------------------------------------------------------------------
# REVIEW PASS: (a) SUCCESS PATH — one turn_summary call + summary attached
# --------------------------------------------------------------------------

class TestSuccessPath:
    def _fake_writer(self):
        class _Writer:
            def __init__(self):
                self.turn_summary_calls = []
                self.turn_summary_status_calls = []
                self.flushed = 0
            def turn_summary(self, summary):
                self.turn_summary_calls.append(summary)
            def turn_summary_status(self, status, reason=""):
                self.turn_summary_status_calls.append((status, reason))
            def flush(self):
                self.flushed += 1
        return _Writer()

    def test_success_emits_exactly_one_record_and_attaches(self, monkeypatch):
        # NOTE: real code imports _invoke_llm_with_retry_compression
        # function-locally (from agent_context_compress.steps.llm import ...),
        # so we MUST patch that module attribute, not the turn_summary global.
        def fake_llm(*a, **k):
            return types.SimpleNamespace(
                text=("Done: refactored module X, added 3 tests, all green. "
                      "Touched files: x.py and tests/test_x.py."))
        monkeypatch.setattr(
            "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
            fake_llm, raising=False)

        agent = _make_agent(monkeypatch, "refactor module X and run the test suite")
        writer = self._fake_writer()
        agent._session.audit_writer = writer

        ok = ts.generate_turn_summary(agent)

        assert ok is True
        # EXACTLY ONE turn_summary() call, zero skip records.
        assert len(writer.turn_summary_calls) == 1, writer.turn_summary_calls
        assert writer.turn_summary_status_calls == []
        assert writer.flushed >= 1
        # Summary attached to the LAST assistant message.
        last_asst = agent.context._messages[-1]
        assert last_asst.get("summary") == writer.turn_summary_calls[0]


# --------------------------------------------------------------------------
# REVIEW PASS: (b) SKIP-PATH — pin the reason value for too_short & exception
# --------------------------------------------------------------------------

class TestSkipReasons:
    def _status_writer(self):
        class _Writer:
            def __init__(self):
                self.turn_summary_calls = []
                self.status_calls = []
            def turn_summary(self, summary):
                self.turn_summary_calls.append(summary)
            def turn_summary_status(self, status, reason=""):
                self.status_calls.append((status, reason))
            def flush(self):
                pass
        return _Writer()

    def test_too_short_reason_pinned(self, monkeypatch):
        # LLM returns a short stub (< _MIN_CLEAN_CHARS) -> reason=too_short.
        def fake_llm(*a, **k):
            return types.SimpleNamespace(text="TAU-MANUAL-OK")
        monkeypatch.setattr(
            "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
            fake_llm, raising=False)
        agent = _make_agent(monkeypatch, "please refactor module X and run tests")
        writer = self._status_writer()
        agent._session.audit_writer = writer

        ok = ts.generate_turn_summary(agent)
        assert ok is False
        assert ("skipped", "too_short") in writer.status_calls, writer.status_calls

    def test_exception_reason_pinned(self, monkeypatch):
        # LLM raises -> generic except -> reason=exception.
        def boom(*a, **k):
            raise RuntimeError("llm exploded")
        monkeypatch.setattr(
            "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
            boom, raising=False)
        agent = _make_agent(monkeypatch, "please refactor module X and run tests")
        writer = self._status_writer()
        agent._session.audit_writer = writer

        ok = ts.generate_turn_summary(agent)
        assert ok is False
        assert ("skipped", "exception") in writer.status_calls, writer.status_calls

    def test_llm_empty_reason_pinned(self, monkeypatch):
        def fake_llm(*a, **k):
            return types.SimpleNamespace(text="   ")
        monkeypatch.setattr(
            "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
            fake_llm, raising=False)
        agent = _make_agent(monkeypatch, "please refactor module X and run tests")
        writer = self._status_writer()
        agent._session.audit_writer = writer
        ok = ts.generate_turn_summary(agent)
        assert ok is False
        assert ("skipped", "llm_empty") in writer.status_calls

    def test_no_writer_no_turn_summary_warns_not_crashes(self, monkeypatch, caplog):
        # MINOR 1: a writer without turn_summary must warn (not silently no-op).
        def fake_llm(*a, **k):
            return types.SimpleNamespace(
                text=("Completed refactor of module X; 5 tests added and all pass; "
                      "files edited were x.py and tests/test_x.py."))
        monkeypatch.setattr(
            "agent_context_compress.steps.llm._invoke_llm_with_retry_compression",
            fake_llm, raising=False)
        agent = _make_agent(monkeypatch, "refactor module X and run tests")
        # audit_writer present but WITHOUT turn_summary attr.
        agent._session.audit_writer = object()

        ok = ts.generate_turn_summary(agent)
        # Still produces the summary metadata even though observability is lost.
        assert ok is True
        assert "observability lost" in caplog.text
