"""Collapse summarized turns — replace completed turns with user + summary.

Two variants: COLLAPSE_TURNS_50 (only turns in the first 50% of context) and
COLLAPSE_TURNS_FULL (all summarized turns, no boundary).

A "completed turn" is: [user(real)] → ... → [assistant with "summary" metadata].
The intermediates (code, REPL output) are replaced with just the user message
and a synthetic assistant message containing the summary.

This is much cheaper than REDACT_BLOCKS because it uses pre-computed summaries
instead of stripping content.
"""

from __future__ import annotations

from typing import Any

from agent_context_compress.steps.framework import (
    CompressionContext,
    make_compress_wrapper,
)
from agent_context_compress.steps.utils import _calculate_context_bytes
from agent_message_utils import is_real_user_request


__all__ = [
    "compress_collapse_turns_50",
    "compress_collapse_turns_full",
    "_find_summarized_turns",
]


def _is_real_user(msg: dict) -> bool:
    """Check if a message is a real user message (not synthetic, not REPL)."""
    return is_real_user_request(msg)


def _find_summarized_turns(msgs: list[dict], boundary: int | None = None) -> list[tuple[int, int]]:
    """Find completed turns (user → assistant with summary).

    A turn is: [user(real)] → ... → [assistant with "summary"] where there is
    no other user message in between.

    Args:
        msgs: Context messages.
        boundary: If set, only search up to this index (exclusive).

    Returns:
        List of (start_idx, end_idx) tuples for each completed turn.
    """
    limit = boundary if boundary is not None else len(msgs)
    turns = []
    i = 0
    while i < limit:
        if _is_real_user(msgs[i]):
            start = i
            j = i + 1
            found = False
            while j < limit:
                # If we hit another REAL user message, this turn is incomplete.
                # Synthetic user messages (heartbeat, meta, confirm, inject)
                # don't break the turn scan.
                if _is_real_user(msgs[j]):
                    break
                if msgs[j].get("role") == "assistant" and msgs[j].get("summary"):
                    # Extend to last consecutive assistant (handles consecutive summarized assistants)
                    end = j
                    while end + 1 < limit and msgs[end + 1].get("role") == "assistant" and msgs[end + 1].get("summary"):
                        end += 1
                    turns.append((start, end))
                    i = end + 1
                    found = True
                    break
                j += 1
            if not found:
                i += 1
        else:
            i += 1
    return turns


def _collapse(msgs: list[dict], turns: list[tuple[int, int]]) -> list[dict]:
    """Replace completed turns with user + summary.

    Args:
        msgs: Full context messages.
        turns: List of (start, end) indices for turns to collapse.

    Returns:
        New message list with turns collapsed.
    """
    if not turns:
        return msgs

    # Build set of indices that belong to a turn
    turn_indices: dict[int, tuple[int, int]] = {}
    for start, end in turns:
        for k in range(start, end + 1):
            turn_indices[k] = (start, end)

    new_msgs = []
    i = 0
    while i < len(msgs):
        if i in turn_indices:
            start, end = turn_indices[i]
            if i == start:
                # Keep the user message
                new_msgs.append(msgs[i])
            elif i == end:
                # Replace with summary (preserve summary metadata)
                summary = msgs[end].get("summary", "")
                new_msgs.append({
                    "role": "assistant",
                    "content": f"[Turn summary]: {summary}",
                    "summary": summary,
                })
            # Skip intermediates
        else:
            new_msgs.append(msgs[i])
        i += 1

    return new_msgs


def _compress_collapse_turns_50_impl(
    ctx: CompressionContext,
    **kwargs: Any,
) -> tuple[list[dict], str]:
    """Collapse summarized turns in the first 50% of context (byte-based boundary)."""
    msgs = ctx.context
    if len(msgs) < 3:
        return msgs, "NO_REDUCTION"

    # Use byte-based boundary (consistent with REDACT_BLOCKS_50)
    _, boundary = ctx.split_point()
    turns = _find_summarized_turns(msgs, boundary)

    if not turns:
        return msgs, "NO_REDUCTION"

    new_msgs = _collapse(msgs, turns)
    ctx.add_action(f"collapsed {len(turns)} turn(s) in first 50%", "collapse_turns_50")
    status = "ACHIEVED" if _calculate_context_bytes(new_msgs) <= ctx.target_size_bytes else "REDUCED"
    return new_msgs, status


def _compress_collapse_turns_full_impl(
    ctx: CompressionContext,
    **kwargs: Any,
) -> tuple[list[dict], str]:
    """Collapse ALL summarized turns (no boundary)."""
    msgs = ctx.context
    if len(msgs) < 3:
        return msgs, "NO_REDUCTION"

    turns = _find_summarized_turns(msgs)

    if not turns:
        return msgs, "NO_REDUCTION"

    new_msgs = _collapse(msgs, turns)
    ctx.add_action(f"collapsed {len(turns)} turn(s) (full)", "collapse_turns_full")
    status = "ACHIEVED" if _calculate_context_bytes(new_msgs) <= ctx.target_size_bytes else "REDUCED"
    return new_msgs, status


# Public wrappers for testing/composition
compress_collapse_turns_50 = make_compress_wrapper("COLLAPSE_TURNS_50", _compress_collapse_turns_50_impl)
compress_collapse_turns_full = make_compress_wrapper("COLLAPSE_TURNS_FULL", _compress_collapse_turns_full_impl)
