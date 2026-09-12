"""P1: exact-anchor live token estimator (compute_live_tokens + TokenTracker).

The exact prompt_tokens from the last API call covers only the messages that
were SENT (messages[:anchor]). Anything appended after (the assistant response
+ newest tool result) is the delta the exact count predates — the thing that
can silently overflow the window. The live helper prices the anchored prefix
exactly and the post-anchor delta by estimate.
"""
from agent_context_store import compute_live_tokens, estimate_messages_tokens
from agent_token_tracker import TokenTracker


def _msg(text):
    return {"role": "user", "content": text}


def test_exact_wins_when_nothing_appended_after_anchor():
    # anchor == len(messages): nothing appended -> live == exact. The last-turn
    # output_tokens is NOT added: the assistant reply lives in the slice (it is
    # appended at index anchor_count), so adding it here would double-count.
    msgs = [_msg("hello"), _msg("world")]
    live = compute_live_tokens(msgs, exact_tokens=5000, anchor_count=2, output_tokens=100)
    assert live == 5000


def test_estimate_kicks_in_for_post_anchor_message():
    # A big synthetic message appended AFTER the anchor must be priced.
    msgs = [_msg("x"), _msg("y"), _msg("z" * 3000)]
    base = compute_live_tokens(msgs[:2], exact_tokens=1000, anchor_count=2, output_tokens=50)
    with_new = compute_live_tokens(msgs, exact_tokens=1000, anchor_count=2, output_tokens=50)
    # The 3000-char message adds ~1000 estimate tokens; live must grow.
    assert with_new > base
    # exact prefix + estimated delta only; output_tokens is deliberately ignored.
    assert with_new == 1000 + estimate_messages_tokens([msgs[2]])


def test_fallback_to_estimate_when_exact_is_none():
    msgs = [_msg("a" * 300)]
    live = compute_live_tokens(msgs, exact_tokens=None, anchor_count=None, output_tokens=99)
    assert live == estimate_messages_tokens(msgs)


def test_rebaseline_when_context_replaced(anchor_shrink=True):
    # anchor > len(messages) => list was replaced/shrunk by compression; the
    # anchor is stale, so fall back to a full estimate (re-baseline).
    msgs = [_msg("short")]
    live = compute_live_tokens(msgs, exact_tokens=999999, anchor_count=50, output_tokens=0)
    assert live == estimate_messages_tokens(msgs)


def test_huge_reply_at_anchor_not_double_counted():
    """Regression: the assistant reply appended AT messages[anchor_count] must be
    priced once (by the slice), not again via output_tokens. A big reply must not
    inflate the live count by its full size and spuriously trip the 95% guard."""
    prefix = [_msg("x" * 40) for _ in range(5)]
    big_reply = {"role": "assistant", "content": "code\n" * 40000}  # ~big
    msgs = prefix + [big_reply]
    # exact anchor measured on the 5-message prefix; the big reply is the delta.
    live = compute_live_tokens(msgs, exact_tokens=1000, anchor_count=5,
                               output_tokens=10000)
    single = 1000 + estimate_messages_tokens([big_reply])
    assert live == single
    # The buggy behavior (double count) would be ~ single + 10000.
    assert live < single + 10000


def test_tracker_live_tokens_method():
    tt = TokenTracker()
    msgs = [_msg("hello"), _msg("world")]
    # No anchor yet -> full estimate.
    assert tt.live_tokens(msgs) == estimate_messages_tokens(msgs)
    # Anchor at full length -> exact only (reply priced by slice, not added).
    tt.last_exact_context_tokens = 4000
    tt.last_exact_msg_count = 2
    tt.last_turn_output_tokens = 200
    assert tt.live_tokens(msgs) == 4000
