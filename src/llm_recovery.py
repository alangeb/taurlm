"""Context-overflow and vision-error recovery helpers for the LLM call path.

Extracted verbatim from agent_llm_invoke.py; re-exported there for backward
compatibility. Pure helpers — no state, no circular import of the facade.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_llm_models import (
    COMPRESSION_FACTOR,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    LLMCallConfig,
    OVERSIZED_THRESHOLD,
)


def _try_oversized_redaction(msg_list: list[dict]) -> list[dict] | None:
    """Redact oversized messages that exceed OVERSIZED_THRESHOLD of total context bytes.

    Returns a NEW list with redacted content if any redaction occurred, else None.
    Uses json.dumps for accurate byte counting (handles multimodal list content).
    """
    total_bytes = sum(len(json.dumps(m.get("content", ""))) for m in msg_list)
    threshold = int(total_bytes * OVERSIZED_THRESHOLD)
    redacted = False
    result: list[dict] = []

    for msg in msg_list:
        content = msg.get("content", "")
        content_bytes = len(json.dumps(content))
        if content_bytes > threshold:
            result.append({
                **msg,
                "content": f"[redacted: {content_bytes} bytes]",
            })
            redacted = True
            continue
        result.append(msg)

    return result if redacted else None


def _try_context_compress(
    msg_list: list[dict],
    client: Any,
    model_name: str,
    extra_kwargs: dict | None,
    log_file: Path | None,
    audit_writer: Any,
    last_known_tokens: int | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> list[dict] | None:
    """Invoke the full compression pipeline on the message list.

    Returns compressed messages, or None on failure (best-effort fallback).
    """
    if client is None:
        return None

    try:
        from agent_context_compress import compress_context
        from agent_console import echo
        echo(f"[COMPRESS] Trigger: reactive (API context overflow)")

        compressed, _summary, _meta = compress_context(
            msg_list,
            client,
            model_name,
            COMPRESSION_FACTOR,
            extra_kwargs,
            log_file=log_file,
            audit_writer=audit_writer,
            last_known_tokens=last_known_tokens,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
        )
        return compressed
    except Exception:
        return None


def _extract_token_count_from_error(error_str: str) -> int | None:
    """Extract token count from API error messages.

    Parses patterns like:
    - "your prompt contains at least 188001 input tokens"
    - "Request too large. Your prompt contains X tokens"
    """
    match = re.search(r"(\d+)\s*(?:input\s*)?tokens?", error_str, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _handle_context_overflow(
    msg_list: list[dict],
    config: LLMCallConfig,
    attempt: int,
    error_str: str | None = None,
) -> list[dict] | None:
    """Handle context overflow by escalating: redaction → compression → give up.

    Returns compressed message list if recovery succeeded, None to abort.
    Returns a new message list when compression succeeds.
    """
    if not msg_list:
        return None

    # Extract token count from error message for token-aware compression
    last_known_tokens = _extract_token_count_from_error(error_str or "")

    # Escalating overflow strategy:
    #   attempts 0-2: oversized message redaction (cheap)
    #   attempts 3+: full compress via context (uses LLM)
    if attempt <= 2:
        return _try_oversized_redaction(msg_list)
    return _try_context_compress(
        msg_list,
        config.compress_client,
        config.compress_model or "",
        config.compress_extra_kwargs,
        config.log_file,
        config.compress_audit_writer,
        last_known_tokens=last_known_tokens,
        max_context_tokens=config.max_context_tokens,
        max_output_tokens=config.max_output_tokens,
    )


def _is_text_encode_error(error_str: str) -> bool:
    """Detect TextEncodeInput / tokenizer encoding errors.

    These errors mean the context contains invalid characters (e.g., lone
    UTF-16 surrogates) that the tokenizer cannot process. Unlike network
    errors, blind retries are useless — the context must be sanitized.
    """
    return "TextEncodeInput" in error_str or "TextInputSequence" in error_str


def _is_vision_error(error_str: str) -> bool:
    """Detect vision-incompatible model errors.

    Matches vLLM: 'At most 0 image(s) may be provided in one prompt.'
    Also matches generic patterns containing 'image' or 'vision' combined
    with capability keywords (not supported, cannot, may be provided, etc.).
    """
    lower = error_str.lower()
    return ("image" in lower or "vision" in lower) and any(
        kw in lower
        for kw in (
            "may be provided", "not supported", "cannot", "can't",
            "unable", "does not support", "do not support", "required",
        )
    )


def _strip_image_blocks(msg_list: list[dict]) -> list[dict] | None:
    """Strip image_url blocks from multimodal content.

    Returns a NEW list with stripped content if any images were removed,
    else None (no images present).

    Aligns with design 3.10 (pre-API field stripping) and 3.18 (in-place
    compression pattern). Messages that become empty after stripping are
    replaced with a text placeholder to preserve OpenAI alternation
    (design 18.6).
    """
    # Handle TauContext or plain list
    if hasattr(msg_list, "to_list"):
        msg_list = msg_list.to_list()
    msg_list = list(msg_list)

    stripped = False
    result: list[dict] = []

    for msg in msg_list:
        content = msg.get("content")
        if isinstance(content, list):
            # Multimodal content — filter out image_url blocks
            text_blocks = [b for b in content if b.get("type") != "image_url"]
            if len(text_blocks) < len(content):
                stripped = True
                if text_blocks:
                    # Keep remaining text blocks
                    result.append({**msg, "content": text_blocks})
                else:
                    # Pure-image message: replace with placeholder.
                    # Preserves OpenAI alternation (18.6).
                    result.append(
                        {**msg, "content": "[image: model does not support vision]"}
                    )
                continue
        result.append(msg)

    return result if stripped else None
