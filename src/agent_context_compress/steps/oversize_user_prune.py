"""Step 1.5: Oversize User Prune — truncate oversized user messages.

Scan ALL messages (no boundary). For each user message with content longer
than 10,000 characters, truncate to 10,000 and append a truncation notice.
Deterministic — no LLM call.
"""

from __future__ import annotations

from agent_context_compress.steps.framework import CompressionContext, make_compress_wrapper, _verbose

__all__ = ["compress_oversize_user_prune"]

_MAX_USER_CHARS = 10_000


def _compress_oversize_user_prune_impl(ctx: CompressionContext, **kwargs) -> tuple[list[dict], str]:
    """Truncate user messages exceeding _MAX_USER_CHARS."""
    truncated = 0

    for i in range(len(ctx.context)):
        msg = ctx.context[i]
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            if isinstance(content, list):
                # P7-B3-01: never mutate the part list in place — upstream
                # passes a shallow copy, so the list object is shared with
                # the live context. Build a new list instead.
                new_content = None
                for j, part in enumerate(content):
                    if part.get("type") == "text" and len(part.get("text", "")) > _MAX_USER_CHARS:
                        if new_content is None:
                            new_content = list(content)
                        orig = len(part["text"])
                        new_content[j] = {**part, "text": part["text"][:_MAX_USER_CHARS] + f"\n[TRUNCATED: original was {orig} chars]"}
                        truncated += 1
                if new_content is not None:
                    ctx.context[i] = {**msg, "content": new_content}
            continue
        if len(content) <= _MAX_USER_CHARS:
            continue

        original_len = len(content)
        ctx.context[i] = {**msg, "content": content[:_MAX_USER_CHARS] + f"\n[TRUNCATED: original was {original_len} chars]"}
        truncated += 1
        ctx.add_action(
            f"truncated user msg {i}: {original_len} -> {_MAX_USER_CHARS} chars",
            action_type="oversize_user_prune",
        )
        if ctx.verbose:
            _verbose(f"  :: TRUNCATED user@{i}: {original_len:,} -> {_MAX_USER_CHARS:,} chars")
        if ctx.at_target():
            break

    if ctx.at_target():
        status = "ACHIEVED"
    elif truncated == 0:
        status = "NO_REDUCTION"
    else:
        status = "REDUCED"
    return ctx.context, status


compress_oversize_user_prune = make_compress_wrapper("OVERSIZE_USER_PRUNE", _compress_oversize_user_prune_impl)
