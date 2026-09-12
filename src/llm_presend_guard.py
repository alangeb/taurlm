"""Pre-send context-size guard for the LLM call path.

Extracted verbatim from agent_llm_invoke.py; re-exported there for backward
compatibility. Reads context state only (no context-module edits).
"""

from __future__ import annotations

from agent_console import warning
from agent_llm_models import LLMCallConfig


# P4: pre-send hard guard threshold. The proactive 85% gate + reactive
# overflow handler can both miss a SILENT oversized streaming request (no
# provider 400). This synchronous guard is the last line before send.
_PRE_SEND_GUARD_FRACTION = 0.95
_PRE_SEND_PRUNE_CHARS = 10_000


def _truncate_largest_user(msg_list: list[dict]) -> tuple[list[dict], int]:
    """Truncate the single largest user message to _PRE_SEND_PRUNE_CHARS.

    Operates on the exact list about to be sent. Returns (new_list, freed_bytes).
    """
    best_i, best_len = -1, 0
    for i, msg in enumerate(msg_list):
        if msg.get("role") != "user":
            continue
        c = msg.get("content", "")
        if isinstance(c, str):
            ln = len(c)
        elif isinstance(c, list):
            ln = sum(len(p.get("text", "")) for p in c if p.get("type") == "text")
        else:
            continue
        if ln > best_len:
            best_i, best_len = i, ln
    if best_i < 0 or best_len <= _PRE_SEND_PRUNE_CHARS:
        return msg_list, 0
    msg = msg_list[best_i]
    c = msg.get("content", "")
    if isinstance(c, str):
        new_c = c[:_PRE_SEND_PRUNE_CHARS] + f"\n[ELIDED: original {best_len} chars]"
    else:  # multimodal list — truncate the largest text part
        new_c = []
        done = False
        for part in c:
            if not done and part.get("type") == "text" and len(part.get("text", "")) > _PRE_SEND_PRUNE_CHARS:
                orig = len(part["text"])
                new_c.append({**part, "text": part["text"][:_PRE_SEND_PRUNE_CHARS] + f"\n[ELIDED: original {orig} chars]"})
                done = True
            else:
                new_c.append(part)
    new_list = list(msg_list)
    new_list[best_i] = {**msg, "content": new_c}
    return new_list, best_len


def _stub_newest_repl_feedback(msg_list: list[dict]) -> tuple[list[dict], int]:
    """Replace the newest synthetic repl_feedback user message with a stub.

    Synthetic repl_feedback messages carry a "[U:repl" prefix. Returns
    (new_list, freed_bytes).
    """
    for i in range(len(msg_list) - 1, -1, -1):
        msg = msg_list[i]
        if msg.get("role") != "user":
            continue
        c = msg.get("content", "")
        text = c if isinstance(c, str) else " ".join(
            p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"
        ) if isinstance(c, list) else ""
        if text.startswith("[U:repl") or "\n[U:repl" in text[:80]:
            orig = len(text)
            stub = f"[U:repl] [output elided: {orig} bytes; full in _output{{N}}]"
            new_list = list(msg_list)
            new_list[i] = {**msg, "content": stub}
            return new_list, orig
    return msg_list, 0


def _has_anchor(sess, msg_list) -> bool:
    """True iff compute_live_tokens() will price msg_list with an EXACT anchor.

    The pre-send hard guard must only fire on a trustworthy count. compute_live_
    tokens falls back to a FULL estimate (biased HIGH: ~3 chars/token + 15/msg
    overhead) whenever there is no int anchor or the anchor no longer aligns
    with the list. Acting on that estimate forces spurious prunes / RuntimeErrors
    on a context that is really ~80% but estimates >95%. In the unanchored
    regime we are, by construction, far from the limit — the anchor is
    invalidated only right after a prune or compression, both of which just
    shrank the context — so the guard is skipped and the proactive 85% gate +
    reactive provider-overflow handler (which sees a REAL 400) do their jobs.
    """
    exact = getattr(sess, "last_exact_context_tokens", None) if sess else None
    anchor = getattr(sess, "last_exact_msg_count", None) if sess else None
    if not isinstance(exact, int) or not isinstance(anchor, int):
        return False
    try:
        n = len(msg_list)
    except TypeError:
        return False
    return anchor <= n


def _persist_and_invalidate(config, sess, msg_list) -> None:
    """Persist a pruned list to the live context and drop the stale anchor.

    The guard runs on the PREPARED COPY (a fresh list built by
    _prepare_messages), so pruning it without writing back is a NOP: the next
    turn re-reads the unpruned store and re-prunes forever. Writing the pruned
    list back to config.context makes the prune durable; invalidating the anchor
    stops the next turn from trusting an exact count that predates the freed
    bytes (and, per _has_anchor, keeps the guard honest until a fresh call
    re-anchors). Mirrors the reactive compression path's set_messages persistence.
    """
    ctx = getattr(config, "context", None)
    if ctx is not None:
        try:
            ctx.set_messages(msg_list)
        except Exception:
            pass
    if sess is not None:
        try:
            sess.last_exact_context_tokens = None
            sess.last_exact_msg_count = None
        except Exception:
            pass


def _pre_send_overflow_guard(msg_list: list[dict], config: LLMCallConfig) -> list[dict]:
    """Synchronous hard guard on the EXACT list about to be sent.

    If the live token count is >= _PRE_SEND_GUARD_FRACTION of the window:
      1. force oversize prune on the FINAL list (truncate the largest user msg);
      2. if still >= threshold, stub the newest synthetic repl_feedback message;
      3. if STILL >= threshold, raise RuntimeError (loud death) instead of
         streaming an oversized request silently.

    Runs on the exact msg_list, independent of the compression cooldown path.
    """
    from agent_context_store import compute_live_tokens
    max_tokens = config.max_context_tokens
    if not max_tokens or max_tokens <= 0:
        return msg_list
    agent = getattr(config, "agent", None)
    sess = getattr(agent, "_session", None) if agent is not None else None

    def _live(mlist):
        return compute_live_tokens(
            mlist,
            getattr(sess, "last_exact_context_tokens", None) if sess else None,
            getattr(sess, "last_exact_msg_count", None) if sess else None,
            getattr(sess, "last_turn_output_tokens", 0) if sess else 0,
        )

    from agent_context_store import estimate_messages_tokens

    # Only trust the live count when an exact API anchor backs it. An
    # unanchored count is a full estimate that over-counts ~33%, so firing the
    # hard guard on it forces spurious prunes / RuntimeErrors on ~80% contexts.
    if not _has_anchor(sess, msg_list):
        return msg_list

    tokens = _live(msg_list)
    if tokens < _PRE_SEND_GUARD_FRACTION * max_tokens:
        return msg_list

    warning(
        f"  :: [PRE-SEND GUARD] context at {tokens/max_tokens*100:.1f}% "
        f">= {_PRE_SEND_GUARD_FRACTION*100:.0f}% — forcing prune"
    )
    # After the first prune, recompute with a FULL estimate: the anchored form
    # cannot see a prefix prune and would report the pre-prune number, causing a
    # spurious RuntimeError on a payload that is now physically under the window.
    pruned = False
    msg_list, freed = _truncate_largest_user(msg_list)
    if freed:
        pruned = True
        tokens = estimate_messages_tokens(msg_list)
        if tokens < _PRE_SEND_GUARD_FRACTION * max_tokens:
            _persist_and_invalidate(config, sess, msg_list)
            return msg_list

    msg_list, freed2 = _stub_newest_repl_feedback(msg_list)
    if freed2:
        pruned = True
        tokens = estimate_messages_tokens(msg_list)
        if tokens < _PRE_SEND_GUARD_FRACTION * max_tokens:
            _persist_and_invalidate(config, sess, msg_list)
            return msg_list

    if pruned:
        # Freed bytes but still over the hard threshold: persist the prune so
        # the next turn starts from the reduced list (not the original), drop
        # the stale anchor, THEN die loudly.
        _persist_and_invalidate(config, sess, msg_list)

    raise RuntimeError(
        f"context overflow guard: {tokens} tokens > "
        f"{_PRE_SEND_GUARD_FRACTION*100:.0f}% of {max_tokens}-token window"
    )
