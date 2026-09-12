"""Compression utilities — byte calculation, boundary detection.

This module provides helper functions used by compression step implementations:
- Byte calculation: _calculate_context_bytes, compute_compression_target_bytes
- Boundary detection: _compute_50_boundary, _find_50_boundary
- Text extraction: _extract_text_from_content
- Constants: MAX_ITERATIONS, MIN_BLOCK_SIZE
"""

from __future__ import annotations

import json


# --- Constants ---

__all__ = [
    'MAX_ITERATIONS',
    'MIN_BLOCK_SIZE',
    'compute_compression_target_bytes',
]

MAX_ITERATIONS = 100
MIN_BLOCK_SIZE = 300


# --- Byte calculation ---


def _calculate_context_bytes(context: list[dict]) -> int:
    """Total byte size of serialized context messages."""
    return sum(len(json.dumps(m).encode("utf-8")) for m in context)  # M-P1: byte-aware


def compute_compression_target_bytes(
    current_bytes: int,
    compression_factor: float,
    last_known_tokens: int | None,
    max_context_tokens: int,
    max_output_tokens: int,
) -> int:
    """Return the byte target for compression.

    Uses the MINIMUM of:
    1. Byte-based target: current_bytes × (1 - compression_factor)
    2. Token-derived target: current_bytes × (1 - reduction_ratio)

    The token-derived target is computed from the token budget:
        target_tokens = max_context_tokens - max_output_tokens - compression_factor × max_context_tokens
        reduction_ratio = (last_known_tokens - target_tokens) / last_known_tokens

    The compression_factor (typically 0.3) serves dual purpose:
    - Byte target: aims for 30% byte reduction
    - Token budget: reserves 30% of context window as breathing room for new content

    Assumes linear byte-to-token scaling. If compression removes byte-heavy,
    token-cheap content first (ratio collapse), multiple compression rounds
    may be needed.
    """
    # --- Byte-based target ---
    byte_target = int(current_bytes * (1 - compression_factor))

    # --- Token-derived target ---
    token_target = byte_target  # default fallback

    if last_known_tokens is not None and last_known_tokens > 0:
        # Token budget: reserve breathing room (compression_factor × MCW) + output tokens
        breathing_room = int(compression_factor * max_context_tokens)
        target_tokens = max_context_tokens - max_output_tokens - breathing_room
        target_tokens = max(0, target_tokens)  # Guard against negative

        if last_known_tokens > target_tokens:
            # Need to reduce tokens
            reduction_ratio = (last_known_tokens - target_tokens) / last_known_tokens

            # Apply reduction ratio directly to bytes (linear assumption)
            token_byte_target = int(current_bytes * (1 - reduction_ratio))

            # Use the more aggressive (lower) target
            token_target = min(byte_target, token_byte_target)

    return max(token_target, 1024)


# --- Boundary detection ---


def _compute_50_boundary(context: list[dict]) -> tuple[int, int]:
    """Return (boundary_50_bytes, boundary_idx) for the current context."""
    boundary_50_bytes = _calculate_context_bytes(context) // 2
    boundary_idx = _find_50_boundary(context, boundary_50_bytes)
    return boundary_50_bytes, boundary_idx


def _find_50_boundary(context: list[dict], boundary_bytes: int) -> int:
    """Return the index where cumulative bytes first exceed *boundary_bytes*."""
    cumulative = 0
    for i, msg in enumerate(context):
        cumulative += len(json.dumps(msg))
        if cumulative > boundary_bytes:
            return i
    return len(context)


# --- Text extraction ---


def _extract_text_from_content(content: str | list) -> str:
    """Extract text from content (str or multimodal list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return " ".join(parts)
    return ""


# --- Orchestrator helpers ---


def _format_success(
    name: str,
    size: int,
    original_size: int,
    compression_factor: float,
    msg_count: int,
    algorithms_used: list[str],
    verbose: bool,
) -> tuple[str, dict]:
    """Build success summary string and metadata dict."""
    from agent_console import echo
    from agent_models import Colors

    summary = f"✓ COMPRESSION ACHIEVED by {name} ({size:,} bytes)"
    if verbose:
        echo(f"\n{Colors.GREEN}{'='*70}{Colors.RESET}")
        echo(f"{Colors.GREEN}  COMPRESSION COMPLETE - EARLY SUCCESS!{Colors.RESET}")
        echo(f"{Colors.GREEN}{'='*70}{Colors.RESET}\n")
        echo(
            f"{Colors.GREEN}    Original: {original_size:,} bytes -> {size:,} bytes{Colors.RESET}"
        )
        echo(
            f"{Colors.GREEN}    Reduction: {(1 - size / original_size) * 100 if original_size > 0 else 0:.1f}% (target was {compression_factor*100:.0f}%) {Colors.RESET}"
        )
        echo(
            f"{Colors.GREEN}    Algorithms used: {' + '.join(algorithms_used)}{Colors.RESET}"
        )
        echo(
            f"{Colors.GREEN}    Final context: {size:,} bytes, {msg_count} messages{Colors.RESET}\n"
        )
    metadata = {
        "bytes_before": original_size,
        "bytes_after": size,
        "algorithms_used": algorithms_used,
    }
    return summary, metadata

