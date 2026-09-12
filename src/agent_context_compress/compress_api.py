"""Standalone compression API — decouples compression from TauContext.

These functions operate on a TauContext + TauErgon pair without requiring
the context class to know about the agent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import logging

if TYPE_CHECKING:
    from agent_context import TauContext
    from agent_core import TauErgon

from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from agent_audit_bridge import log_context_snapshot


def compress(
    context: TauContext,
    agent: TauErgon,
    target_percentage: float,
    last_known_tokens: int | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> bool:
    """Compress the context to the target percentage.

    Uses an LLM to summarize and compress the conversation history.

    Returns:
        True if compression succeeded AND target was met, False otherwise.
    """
    from agent_context_compress import compress_context
    try:
        resolved = agent.resolve_group_params()
        compressed_messages, summary, metadata = compress_context(
            context.messages,
            agent.client,
            agent.model_name,
            target_percentage,
            resolved,
            log_file=agent._session.audit_file,
            audit_writer=agent._session.audit_writer,
            last_known_tokens=last_known_tokens,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
        )
        context.set_messages(compressed_messages)
        log_context_snapshot(len(context.messages), context.bytes_size(), max_context_tokens)

        bytes_after = metadata.get("bytes_after", 0)
        target_size = metadata.get("target_size", float("inf"))
        return bytes_after <= target_size
    except (TypeError, ValueError, KeyError, RuntimeError, OSError):
        logging.exception('compression failed')
        return False


def compress_to_target(
    context: TauContext,
    agent: TauErgon,
    target_pct: int,
    current_tokens: int | None = None,
) -> bool:
    """Compress the context until it reaches the target percentage.

    Args:
        context: The TauContext to compress.
        agent: The TauErgon agent instance.
        target_pct: Target percentage of max context (e.g., 50 = 50% full).
        current_tokens: Live token count for the CURRENT context (prompt +
            reserved output). When supplied, it is authoritative and the stale
            ``last_exact_context_tokens`` (captured after the *previous* LLM
            response, before the newest tool result) is ignored. Without this,
            the early-return and factor would be computed from a count that
            predates the very delta that pushes a turn over the window.

    Returns:
        True if compression succeeded or was not needed, False on failure.
    """
    max_tokens = agent.max_context_tokens
    exact_tokens = getattr(agent._session, "last_exact_context_tokens", None)
    if current_tokens is not None:
        current_pct = current_tokens / max_tokens if max_tokens > 0 else 0.0
    else:
        current_tokens, current_pct, _, _ = context.get_usage_stats(max_tokens, exact_tokens)

    if current_pct <= target_pct / 100.0:
        return True

    factor = (current_pct - target_pct / 100.0) / current_pct
    return compress(
        context, agent,
        factor,
        last_known_tokens=current_tokens,
        max_context_tokens=max_tokens,
        max_output_tokens=agent.max_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
    )
