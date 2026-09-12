"""Compression pipeline definition.

Declarative pipeline of compression steps. Each step references an impl function
directly — the orchestrator calls ``_compress_wrapper`` with the impl function.

This module isolates the impl function imports so that ``__init__.py`` only
exposes the public wrapper functions (used by tests) and the pipeline tuple.
"""

from __future__ import annotations

from agent_context_compress.steps.framework import CompressionStep

# Import impl functions — used ONLY for pipeline registration.
# Tests use the public wrapper functions (compress_*) instead.
from agent_context_compress.steps.prune_images import _compress_prune_images_impl
from agent_context_compress.steps.oversize_user_prune import _compress_oversize_user_prune_impl
from agent_context_compress.steps.drop_reasoning import _compress_drop_reasoning_impl
from agent_context_compress.steps.collapse_turns import (
    _compress_collapse_turns_50_impl,
    _compress_collapse_turns_full_impl,
)
from agent_context_compress.steps.redact_blocks import _compress_redact_blocks_impl
from agent_context_compress.steps.full_reset import _compress_full_reset_impl
from agent_context_compress.steps.conversation_summary import _compress_conversation_summary_impl
from agent_context_compress.steps.blind_truncate import _compress_blind_truncate_impl


__all__ = ["_COMPRESSION_PIPELINE"]


# Pipeline references impl functions directly.  ``_compress_wrapper`` is called
# in the orchestrator loop, forwarding ``step.kwargs`` to each impl.  The public
# wrapper functions (``compress_*``) are kept for backward-compatible testing.
#
# Order: 50% boundary steps first (protect recent context), then full context,
# then LLM-based, then brute force.

# Step execution order (earliest to last):
# 1. PRUNE_IMAGES, 2. OVERSIZE_USER_PRUNE, 3. DROP_REASONING,
# 4. COLLAPSE_TURNS_50, 5. REDACT_BLOCKS_50, 6. COLLAPSE_TURNS_FULL,
# 7. REDACT_BLOCKS_FULL, 8. FULL_RESET, 9. CONVERSATION_SUMMARY, 10. BLIND_TRUNCATE
_COMPRESSION_PIPELINE: tuple[CompressionStep, ...] = (
    CompressionStep("PRUNE_IMAGES", _compress_prune_images_impl),
    CompressionStep("OVERSIZE_USER_PRUNE", _compress_oversize_user_prune_impl),
    CompressionStep("DROP_REASONING", _compress_drop_reasoning_impl),
    CompressionStep("COLLAPSE_TURNS_50", _compress_collapse_turns_50_impl),
    CompressionStep("REDACT_BLOCKS_50", _compress_redact_blocks_impl, kwargs={"use_boundary": True}),
    CompressionStep("COLLAPSE_TURNS_FULL", _compress_collapse_turns_full_impl),
    CompressionStep("REDACT_BLOCKS_FULL", _compress_redact_blocks_impl, kwargs={"use_boundary": False}),
    CompressionStep("FULL_RESET", _compress_full_reset_impl),
    CompressionStep("CONVERSATION_SUMMARY", _compress_conversation_summary_impl),
    CompressionStep("BLIND_TRUNCATE", _compress_blind_truncate_impl),
)
