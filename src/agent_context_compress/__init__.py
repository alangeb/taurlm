"""Context compression algorithms for LLM conversation management.

Nine pipeline steps applied until target size is reached.
One function implements both redact_blocks steps via the `use_boundary` parameter.

1. compress_prune_images — replace image content blocks with text placeholders
2. compress_drop_reasoning — strip reasoning fields from assistant messages
3. compress_collapse_turns_50 — collapse summarized turns in first 50%
4. compress_redact_blocks — strip intermediate messages from completed blocks (50% boundary)
5. compress_collapse_turns_full — collapse ALL summarized turns (no boundary)
6. compress_redact_blocks (use_boundary=False) — same as #4, scans entire context
7. compress_full_reset — full context rebuild (last resort)
8. compress_conversation_summary — deterministic conversation restructuring (fallback)
9. compress_blind_truncate — truncate summary from beginning (guaranteed fit)

ARCHITECTURE:

- Fixed 50% boundary: protects recent messages (current task state).
  Steps 3-4 operate within the boundary. Steps 5-6 ignore it.
- Parameter consistency: same model/params across compression calls.
- Compression prompt stability: prompts must not change between calls.
- COLLAPSE_TURNS uses pre-computed turn summaries (from task 009) to replace
  entire turns with user + summary. Much cheaper than REDACT_BLOCKS.

LOGGING:

- Console: One-liner per pipeline step via compression_step_summary().
- Audit: Per-action detail via audit_writer.compress_pipeline_start/compress_action/compress_step/compress_pipeline_end().
- Errors/warnings: Continue via _verbose() / warning() as before.

MODULAR DESIGN:

Each compression algorithm is extracted into its own module under steps/.
This module provides the orchestrator (compress_context). Pipeline registration
lives in pipeline.py to keep impl function imports out of this namespace.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

# Direct imports from focused source modules.

from agent_context_compress.steps.framework import (
    # Data classes
    CompressionContext,
    CompressionStep,
    # Wrapper
    _compress_wrapper,
)

from agent_context_compress.steps.utils import (
    # Constants
    MAX_ITERATIONS,
    MIN_BLOCK_SIZE,
    # Helpers used by compress_context orchestrator
    _calculate_context_bytes,
    _format_success,
    compute_compression_target_bytes,
)

# Console output
from agent_console import echo

# Individual compression algorithms (public wrappers for testing/composition)
from agent_context_compress.steps.prune_images import compress_prune_images
from agent_context_compress.steps.drop_reasoning import compress_drop_reasoning
from agent_context_compress.steps.collapse_turns import (
    compress_collapse_turns_50,
    compress_collapse_turns_full,
)
from agent_context_compress.steps.redact_blocks import compress_redact_blocks
from agent_context_compress.steps.full_reset import compress_full_reset
from agent_context_compress.steps.conversation_summary import compress_conversation_summary
from agent_context_compress.steps.blind_truncate import compress_blind_truncate

from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS


__all__ = [
    # Data classes
    "CompressionStep",
    "CompressionContext",
    # Constants
    "MAX_ITERATIONS",
    "MIN_BLOCK_SIZE",
    # Orchestrator
    "compress_context",
    # Helper (public for testing)
    "compute_compression_target_bytes",
    # Individual compression algorithms (public for testing/composition)
    "compress_prune_images",
    "compress_drop_reasoning",
    "compress_collapse_turns_50",
    "compress_collapse_turns_full",
    "compress_redact_blocks",
    "compress_full_reset",
    "compress_conversation_summary",
    "compress_blind_truncate",
    # Private helpers re-exported for testing only (prefix _ indicates internal)
    "_calculate_context_bytes",
    "_compress_wrapper",
]


# --- Pipeline Registry ---

# Pipeline definition moved to pipeline.py to keep impl function imports
# out of this module's namespace.  Only the pipeline tuple is imported here.
from agent_context_compress.pipeline import _COMPRESSION_PIPELINE


# --- Orchestrator ---

def compress_context(
    context: list[dict],
    client: Any,
    model_name: str,
    compression_factor: float,
    extra_kwargs: dict[str, Any] | None = None,
    verbose: bool = False,
    log_file: Path | None = None,
    audit_writer: Any = None,
    last_known_tokens: int | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> tuple[list[dict], str, dict[str, Any]]:
    """Run compression algorithms in sequence until target size is reached.

    RLM pipeline (primarily text; tool messages handled in conversation_summary).

    Pipeline (least to most aggressive):
    1.  PRUNE_IMAGES — replace image content blocks with text placeholders
    2.  DROP_REASONING — strip reasoning fields from assistant messages
    3.  COLLAPSE_TURNS_50 — collapse summarized turns in first 50%
    4.  REDACT_BLOCKS_50 — strip intermediate messages from completed blocks (50% boundary)
    5.  COLLAPSE_TURNS_FULL — collapse ALL summarized turns (no boundary)
    6.  REDACT_BLOCKS_FULL — same as #4 but scans entire context (no boundary)
    7.  FULL_RESET — full context rebuild (last resort)
    8.  CONVERSATION_SUMMARY — deterministic conversation restructuring (fallback)
    9.  BLIND_TRUNCATE — truncate summary from beginning (guaranteed fit)

    Stops early if target is reached.  Original context preserved if all fail.
    """
    t0 = time.time()

    original_size = _calculate_context_bytes(context)
    original_message_count = len(context)
    target_size = compute_compression_target_bytes(
        original_size,
        compression_factor,
        last_known_tokens,
        max_context_tokens,
        max_output_tokens,
    )

    if audit_writer is not None:
        audit_writer.compress_pipeline_start(original_size, target_size, compression_factor, last_known_tokens)

    # Console: START line (always)
    echo(f"[COMPRESS] {original_size:,}B / {original_message_count} msgs → target {target_size:,}B ({compression_factor*100:.0f}% reduction)")

    result = list(context)
    algorithms_used: list[str] = []
    bytes_per_algo: dict[str, int] = {}

    for step in _COMPRESSION_PIPELINE:
        result, step_metadata = _compress_wrapper(
            step.name, step.impl_fn, result, target_size, verbose, audit_writer,
            client=client, model_name=model_name,
            extra_kwargs=extra_kwargs, log_file=log_file,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            **step.kwargs,
        )
        size = step_metadata["bytes_after"]
        msg_count = step_metadata["msgs_after"]
        status = step_metadata["status"]

        # Console: per-step line (verbose OR step did work)
        if verbose or status not in ("NO_REDUCTION", "ALREADY_FITS", "NO_USER_MSG", "NOT_STRING", "NO_CURRENT_TASK", "NO_MORE_BLOCKS", "FAILED_NO_USER", "FAILED_EMPTY", "FAILED_NO_REDUCTION", "ITERATION_LIMIT"):
            saved = step_metadata["bytes_before"] - size
            echo(f"  {step.name}: {step_metadata['bytes_before']:,} → {size:,}B (saved {saved:,}B) [{status}]")

        algorithms_used.append(step.name)
        bytes_per_algo[step.name] = step_metadata["bytes_before"] - step_metadata["bytes_after"]

        if size <= target_size:
            duration = time.time() - t0
            reduction = (1 - size / original_size) * 100 if original_size > 0 else 0
            active_algos = [a for a, s in bytes_per_algo.items() if s > 0]
            echo(f"[COMPRESS] DONE: {size:,}B / {msg_count} msgs ({reduction:.1f}%) via {'+'.join(active_algos) or 'NONE'} [{duration:.1f}s]")
            summary, metadata = _format_success(
                step.name, size, original_size, compression_factor, msg_count,
                algorithms_used, verbose,
            )
            metadata["target_size"] = target_size
            metadata["bytes_per_algo"] = bytes_per_algo
            if audit_writer is not None:
                audit_writer.compress_pipeline_end(size, active_algos, sum(bytes_per_algo.values()), duration)
            return result, summary, metadata

    # All steps exhausted — target NOT met
    duration = time.time() - t0
    reduction = (1 - size / original_size) * 100 if original_size > 0 else 0
    active_algos = [a for a, s in bytes_per_algo.items() if s > 0]
    echo(f"[COMPRESS] FAIL: {size:,}B / {msg_count} msgs ({reduction:.1f}%, target {target_size:,}B) via {'+'.join(active_algos) or 'NONE'} [{duration:.1f}s] TARGET NOT MET")

    final_summary = f"FINAL: {algorithms_used[-1] if algorithms_used else 'NONE'} ({size:,} bytes)"

    metadata = {
        "bytes_before": original_size,
        "bytes_after": size,
        "target_size": target_size,
        "algorithms_used": algorithms_used,
        "bytes_per_algo": bytes_per_algo,
    }

    if audit_writer is not None:
        audit_writer.compress_pipeline_end(size, active_algos, sum(bytes_per_algo.values()), duration)

    return result, final_summary, metadata

# Standalone compression API (decoupled from TauContext)
from agent_context_compress.compress_api import compress, compress_to_target
