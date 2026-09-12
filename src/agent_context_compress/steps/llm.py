"""LLM invocation helper for compression calls.

This module provides a thin wrapper around ``_invoke_llm_with_retry`` for
compression-specific LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS, LLMCallConfig, LLMResponse
from agent_llm_invoke import _invoke_llm_with_retry


__all__ = ["_invoke_llm_with_retry_compression"]

def _invoke_llm_with_retry_compression(
    client: Any,
    model_name: str,
    messages: list[dict],
    stream: bool,
    max_retries: int = 5,
    min_response_bytes: int = 10,
    extra_kwargs: dict[str, Any] | None = None,
    log_file: Path | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> LLMResponse:
    """Thin wrapper around ``_invoke_llm_with_retry`` for compression calls.

    Pure text — no tools. RLM communicates via Python code blocks.
    """
    config = LLMCallConfig(
        max_retries=max_retries,
        min_response_bytes=min_response_bytes,
        log_on_failure=True,
        log_file=log_file,
        extra_kwargs=extra_kwargs,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
    )

    resp, _compressed = _invoke_llm_with_retry(
        client=client,
        model_name=model_name,
        messages=messages,
        stream=stream,
        config=config,
    )
    # Compression callers don't need the compressed messages — they pass
    # temporary lists, not the agent's persistent context.

    if not resp.success:
        raise resp.error or RuntimeError("Compression LLM call failed")
    return resp
