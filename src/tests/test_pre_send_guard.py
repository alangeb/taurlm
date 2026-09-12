"""P4: pre-send hard overflow guard.

Two regimes:
  * UNANCHORED (no exact API anchor): the live count is a full estimate that
    over-counts ~33%, so the guard is a NO-OP (the proactive 85% gate + the
    reactive provider-overflow handler cover the real cases). Regression: the
    guard used to fire on this estimate and force spurious prunes / loud death
    on contexts that were really ~80%.
  * ANCHORED (exact count backs the live number): the guard fires, prunes the
    prepared copy AND persists the prune to the live context (so it is not a
    NOP on a copy), then invalidates the stale anchor.
"""
from agent_llm_models import LLMCallConfig
from agent_llm_invoke import _pre_send_overflow_guard


class _Sess:
    def __init__(self, exact=9000, anchor=1, out=0):
        self.last_exact_context_tokens = exact
        self.last_exact_msg_count = anchor
        self.last_turn_output_tokens = out


class _Agent:
    def __init__(self, sess):
        self._session = sess


class _Ctx:
    """Fake context store capturing set_messages persistence."""
    def __init__(self, msgs=None):
        self._m = msgs or []
        self.set_called_with = None
    def to_list(self):
        return self._m
    def set_messages(self, msgs):
        self.set_called_with = msgs
        self._m = msgs


def _cfg(max_ctx, sess=None, ctx=None):
    c = LLMCallConfig()
    c.max_context_tokens = max_ctx
    c.agent = _Agent(sess) if sess is not None else None
    c.context = ctx
    return c


def test_unanchored_guard_is_noop():
    """No anchor -> full estimate over-counts; guard must NOT prune/raise."""
    big = "[U:repl] " + ("x" * 900_000)  # ~300k estimated tokens >> 95% of 200k
    msgs = [{"role": "user", "content": big}]
    out = _pre_send_overflow_guard(msgs, _cfg(200_000, sess=None))
    assert out is msgs  # returned unchanged, untouched
    assert "ELIDED" not in out[0]["content"]


def test_anchor_invalidated_guard_is_noop():
    """Anchor present but exact is None (post-prune/compression) -> no-op."""
    big = "x" * 900_000
    msgs = [{"role": "user", "content": big}]
    sess = _Sess(exact=None, anchor=None)
    out = _pre_send_overflow_guard(msgs, _cfg(200_000, sess=sess))
    assert "ELIDED" not in out[0]["content"]


def test_anchored_oversize_pruned_and_persisted():
    """Anchored + over threshold + prunable -> prune, persist, invalidate."""
    ctx = _Ctx()
    sess = _Sess(exact=9000, anchor=1)
    c = _cfg(10_000, sess=sess, ctx=ctx)  # 95% threshold = 9500
    # msg[0] anchored prefix (~9000 via exact scalar); msg[1] pushes over.
    msgs = [{"role": "user", "content": "x" * 27_000},
            {"role": "user", "content": "y" * 3_000}]
    out = _pre_send_overflow_guard(msgs, c)
    assert "ELIDED" in out[0]["content"]
    # FIX B: the prune is persisted to the live context (not a NOP on a copy).
    assert ctx.set_called_with is not None
    assert "ELIDED" in ctx.set_called_with[0]["content"]
    # Anchor invalidated so the next turn re-anchors instead of trusting stale.
    assert sess.last_exact_context_tokens is None
    assert sess.last_exact_msg_count is None


def test_anchored_unprunable_raises():
    """Anchored + over threshold + nothing prunable -> loud RuntimeError."""
    sess = _Sess(exact=200_000, anchor=1)
    c = _cfg(10_000, sess=sess)
    # Huge SYSTEM message: guard only prunes user/repl, so it cannot recover.
    msgs = [{"role": "system", "content": "s" * 2_000_000},
            {"role": "user", "content": "hi"}]
    raised = False
    try:
        _pre_send_overflow_guard(msgs, c)
    except RuntimeError as e:
        raised = True
        assert "context overflow guard" in str(e)
    assert raised, "expected RuntimeError for unprunable oversized anchored context"


def test_prefix_prune_recomputes_with_full_estimate():
    """Regression: oversize lives in the ANCHORED PREFIX (index < anchor).

    With an exact anchor, _live() prices the prefix by the frozen exact_tokens
    scalar and ignores mlist[:anchor]. A prune at index<anchor must therefore be
    re-measured with a FULL estimate, or the guard raises on a payload that is
    now physically under the window.
    """
    sess = _Sess(exact=9000, anchor=1)
    c = _cfg(10_000, sess=sess)  # 95% threshold = 9500
    msgs = [{"role": "user", "content": "x" * 27_000},
            {"role": "user", "content": "y" * 3_000}]
    # live = exact(9000) + estimate(msg[1:]~1015) = ~10015 >= 9500 -> triggers.
    out = _pre_send_overflow_guard(msgs, c)
    assert "ELIDED" in out[0]["content"]
    assert sess.last_exact_context_tokens is None
    assert sess.last_exact_msg_count is None
