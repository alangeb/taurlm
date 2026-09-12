"""LLM invocation with retry logic and context overflow handling.

Pure text LLM — no tool calling. The RLM agent communicates entirely through
Python code blocks, not tool calls.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any

from agent_console import (
    error_display,
    llm_timeout_message,
    verbose as _verbose,
    warning,
)
from agent_model_health import get_health_monitor
from agent_llm_models import (
    ALLOWED_MESSAGE_FIELDS,
    APIConnectionError,
    APIGatewayError,
    APITimeoutError,
    BadRequestError,
    CallStats,
    EmptyModelResponse,
    LLMCallConfig,
    LLMResponse,
    OPENAI_BODY_PARAMS,
    RateLimitError,
)
from agent_llm_client import _is_context_overflow
from agent_loop_detect import StreamAbortChecker


class StreamAbortedError(Exception):
    """Raised when a streaming LLM response is aborted mid-stream.

    Carries the partial content and reasoning received before the abort,
    so the caller can preserve them in context.
    """
    def __init__(self, reason: str, partial_content: str = "", partial_reasoning: str = ""):
        super().__init__(reason)
        self.partial_content = partial_content
        self.partial_reasoning = partial_reasoning


# ---------------------------------------------------------------------------
# Stats extraction
# ---------------------------------------------------------------------------

def _extract_call_stats(response: Any, response_text: str) -> CallStats:
    """Extract token usage and finish_reason from LLM response.

    Returns ``None`` for token fields when the API provides no usage data,
    so callers can distinguish "API returned 0" from "API returned no data".
    """
    usage = getattr(response, "usage", None)

    if usage:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        pt_details = getattr(usage, "prompt_tokens_details", None)
        # Distinguish "API returned 0 cached" from "API didn't report cached".
        cached_tokens = pt_details.get("cached_tokens", 0) if isinstance(pt_details, dict) else None
    else:
        prompt_tokens = completion_tokens = cached_tokens = None

    finish_reason = None
    choices = getattr(response, "choices", None)
    if choices:
        finish_reason = getattr(choices[0], "finish_reason", None)

    return CallStats(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        finish_reason=finish_reason,
    )


def _extract_token_usage(response: Any, response_text: str) -> tuple[int, int, int]:
    """Legacy wrapper: returns (prompt_tokens, completion_tokens, cached_tokens)."""
    stats = _extract_call_stats(response, response_text)
    return (stats.prompt_tokens, stats.completion_tokens, stats.cached_tokens)


# ---------------------------------------------------------------------------
# Internal helpers for _invoke_llm_with_retry
# ---------------------------------------------------------------------------

def _prepare_messages(messages: Any, preserve_thinking: bool = False) -> list[dict]:
    """Normalize messages for API call: strip reasoning (unless preserve_thinking),
    strip non-API fields.

    NEVER mutates the original context — creates new dicts for each message.
    """
    msg_list = messages.to_list() if hasattr(messages, "to_list") else messages

    result: list[dict] = []
    for msg in msg_list:
        new_msg = {k: v for k, v in msg.items() if k in ALLOWED_MESSAGE_FIELDS}
        if not preserve_thinking:
            new_msg.pop("reasoning", None)
        result.append(new_msg)

    return result


def _build_call_kwargs(
    model_name: str,
    messages: list[dict],
    stream: bool,
    extra_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Build the kwargs dict for client.chat.completions.create().

    Pure text LLM — no tools, no tool_choice.
    """
    call_kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": stream,
    }

    if not extra_kwargs:
        return call_kwargs

    # Split: standard params -> body, non-standard -> extra_body (+ body for llama.cpp compat).
    body_updates: dict[str, Any] = {}
    extra_body: dict[str, Any] = {}

    for k, v in extra_kwargs.items():
        if k == "_extra_body":
            extra_body = v
        elif k in OPENAI_BODY_PARAMS:
            body_updates[k] = v
        else:
            extra_body[k] = v
            body_updates[k] = v

    if "repetition_penalty" in extra_body:
        body_updates["repeat_penalty"] = extra_body["repetition_penalty"]

    call_kwargs.update(body_updates)
    if extra_body:
        call_kwargs["extra_body"] = extra_body

    return call_kwargs




# ---------------------------------------------------------------------------
# Streaming support
# ---------------------------------------------------------------------------

class _SyntheticUsage:
    """Minimal usage object for streaming responses."""

    def __init__(self, data: dict) -> None:
        self.prompt_tokens = data.get("prompt_tokens", 0)
        self.completion_tokens = data.get("completion_tokens", 0)
        self.total_tokens = data.get("total_tokens", 0)
        self.prompt_tokens_details = data.get("prompt_tokens_details")


class _SyntheticChoice:
    """Minimal choice object for streaming responses."""

    def __init__(self, finish_reason: str | None) -> None:
        self.finish_reason = finish_reason


class _SyntheticStreamResponse:
    """Synthetic response object mimicking the non-streaming API shape.

    Used to feed ``_extract_call_stats`` and ``_build_llm_response``
    after a streaming call completes.
    """

    def __init__(self, usage_data: dict, finish_reason: str | None) -> None:
        self.usage = _SyntheticUsage(usage_data)
        self.choices = [_SyntheticChoice(finish_reason)]


def _invoke_llm_streaming(
    client: Any,
    model_name: str,
    messages: list[dict],
    config: LLMCallConfig,
    tokens_displayed: list[int],
    extra_kwargs: dict | None = None,
) -> LLMResponse:
    """Execute a streaming LLM call and return a fully-built LLMResponse.

    Iterates over SSE chunks, invoking ``config.on_token`` / ``config.on_reasoning``
    callbacks.  Feeds tokens to a ``StreamAbortChecker``; raises
    ``StreamAbortedError`` if the checker signals abort.

    Args:
        tokens_displayed: Single-element mutable list (``[0]``) used by the
            caller to track how many tokens have been displayed.  Updated
            in-place so the retry loop can decide whether to retry.
    """
    # L2: use the caller's (possibly mutated) extra_kwargs so disable_thinking_after
    # propagates to the streaming path; only fall back to a deep copy of the
    # config when the caller passes nothing.
    if extra_kwargs is not None:
        effective_extra = extra_kwargs
    else:
        effective_extra = copy.deepcopy(config.extra_kwargs) if config.extra_kwargs else {}
    call_kwargs = _build_call_kwargs(model_name, messages, True, effective_extra)

    t_start = time.monotonic()
    t_first_token: float | None = None
    t_end: float | None = None

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tokens_count = 0
    usage_data: dict = {}
    finish_reason: str | None = None

    # Abort checker — max_tokens from extra_kwargs if present
    max_tokens = effective_extra.get("max_tokens")
    abort_checker = StreamAbortChecker(max_tokens=max_tokens)

    for chunk in client.chat.completions.create_stream(**call_kwargs):
        # Extract usage (typically in the final chunk)
        if "usage" in chunk and isinstance(chunk["usage"], dict):
            usage_data = chunk["usage"]

        choices = chunk.get("choices")
        if not choices:
            continue

        delta = choices[0].get("delta", {})
        fr = choices[0].get("finish_reason")
        if fr:
            finish_reason = fr

        # Content delta
        content_delta = delta.get("content")
        if content_delta:
            if t_first_token is None:
                t_first_token = time.monotonic()
            content_parts.append(content_delta)
            tokens_count += 1
            tokens_displayed[0] += 1
            abort_checker.feed(content_delta)
            if abort_checker.should_abort:
                raise StreamAbortedError(
                    abort_checker.reason or "Stream aborted",
                    partial_content="".join(content_parts),
                    partial_reasoning="".join(reasoning_parts),
                )
            if config.on_token:
                config.on_token(content_delta)

        # Reasoning delta (varies by provider)
        reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning_delta:
            if t_first_token is None:
                t_first_token = time.monotonic()
            reasoning_parts.append(reasoning_delta)
            abort_checker.feed(reasoning_delta)
            if abort_checker.should_abort:
                raise StreamAbortedError(
                    abort_checker.reason or "Stream aborted",
                    partial_content="".join(content_parts),
                    partial_reasoning="".join(reasoning_parts),
                )
            if config.on_reasoning:
                config.on_reasoning(reasoning_delta)

    # DESIGN DECISION (warn-only, do NOT raise): Some providers (e.g. certain
    # vLLM builds, llama.cpp, Ollama) legitimately omit finish_reason in SSE
    # chunks even when the stream completed normally. Raising here would break
    # those providers; warning is sufficient because downstream token-count and
    # context-window logic uses the authoritative usage_data when available.
    if finish_reason is None:
        logging.warning("Stream ended without a finish_reason (possible truncation)")

    t_end = time.monotonic()
    stream_duration = t_end - t_start
    ttft = (t_first_token - t_start) if t_first_token is not None else 0.0

    # Authoritative token count from provider usage data (if available)
    completion_tokens = usage_data.get("completion_tokens") if usage_data else None
    effective_tokens = completion_tokens if completion_tokens else tokens_count
    tg_tps = (effective_tokens / (t_end - t_first_token)) if (t_first_token and t_end > t_first_token) else 0.0

    response_text = "".join(content_parts)
    reasoning_content = "".join(reasoning_parts) if reasoning_parts else None

    # Build synthetic response and extract stats
    synthetic = _SyntheticStreamResponse(usage_data, finish_reason)
    call_stats = _extract_call_stats(synthetic, response_text)

    # Set streaming-specific stats
    call_stats.stream_duration = stream_duration
    call_stats.first_token_time = ttft
    call_stats.tokens_generated = effective_tokens
    call_stats.tg_tps = tg_tps
    call_stats.ttft = ttft

    return _build_llm_response(
        synthetic,
        response_text,
        reasoning_content,
        call_stats,
        success=True,
    )


# ---------------------------------------------------------------------------
# Public API — _invoke_llm_with_retry
# ---------------------------------------------------------------------------

def _build_llm_response(
    response: Any,
    response_text: str,
    reasoning_content: str | None,
    call_stats: CallStats,
    success: bool,
) -> LLMResponse:
    """Construct an LLMResponse from the common response components.

    Pure text LLM — no tool calls.
    """
    return LLMResponse(
        raw=response,
        text=response_text,
        reasoning=reasoning_content,
        stats=call_stats,
        success=success,
    )


# ---------------------------------------------------------------------------
# Extracted clusters (re-exported for backward compatibility).
#
# MONKEYPATCH SEAMS: the recovery helpers (_try_context_compress,
# _handle_context_overflow, _try_oversized_redaction, ...) live in llm_recovery,
# and the pre-send guard helpers (_pre_send_overflow_guard, _truncate_largest_user,
# _has_anchor, _persist_and_invalidate, _stub_newest_repl_feedback) live in
# llm_presend_guard. Their internal sibling calls resolve via THOSE modules'
# __dict__, NOT via this facade. So @patch("agent_llm_invoke._try_context_compress")
# or @patch("agent_llm_invoke._truncate_largest_user") is SILENTLY INERT (vacuous
# pass). Patch the real module path instead:
#     @patch("llm_recovery._try_context_compress")
#     @patch("llm_presend_guard._truncate_largest_user")
# Regression lock: tests/test_llm_invoke_seams.py
# ---------------------------------------------------------------------------
from llm_recovery import (  # noqa: F401  pylint: disable=unused-import
    _extract_token_count_from_error,
    _handle_context_overflow,
    _is_text_encode_error,
    _is_vision_error,
    _strip_image_blocks,
    _try_context_compress,
    _try_oversized_redaction,
)
from llm_presend_guard import (  # noqa: F401  pylint: disable=unused-import
    _PRE_SEND_GUARD_FRACTION,
    _PRE_SEND_PRUNE_CHARS,
    _has_anchor,
    _persist_and_invalidate,
    _pre_send_overflow_guard,
    _stub_newest_repl_feedback,
    _truncate_largest_user,
)

def _invoke_llm_with_retry(
    client: Any,
    model_name: str,
    messages: list,
    stream: bool = False,
    config: LLMCallConfig | None = None,
) -> tuple[LLMResponse, list[dict] | None]:
    """Invoke LLM with retry logic and error handling.

    Pure text LLM — no tool calling. The RLM agent communicates entirely
    through Python code blocks.

    Returns ``(LLMResponse, compressed_messages)``. The second element is
    ``None`` when no compression occurred, or the compressed message list
    when context overflow triggered compression. Caller must sync the
    compressed messages back to the agent context (e.g.
    ``context.set_messages(compressed)``) to make compression persistent.
    """
    if config is None:
        config = LLMCallConfig()

    # Deep-copy to prevent mutating the caller's config during retries.
    effective_extra = copy.deepcopy(config.extra_kwargs) if config.extra_kwargs else {}
    effective_max_retries = config.max_retries
    effective_disable_after = config.disable_thinking_after
    effective_min_bytes = config.min_response_bytes

    def _log_failure(exc: Exception) -> None:
        if config.log_on_failure:
            if call_kwargs is not None:  # C2: guard against unbound
                from agent_session import log_failed_api_request
                log_failed_api_request(
                    call_kwargs, config.log_file,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    status_code=getattr(exc, "status_code", None),
                )

    # Track compressed messages — None means no compression occurred.
    compressed_messages: list[dict] | None = None
    last_error: Exception | None = None
    text_encode_retry_done = False  # Track TextEncodeInput sanitization retry

    # Health monitoring — track connection health (advisory, not blocking)
    health_monitor = get_health_monitor()

    _consecutive_overflow_failures = 0  # H5: track consecutive compression failures
    for attempt in range(effective_max_retries + 1):
        # Advisory warning if circuit is open — don't block, let retry logic handle backoff
        if not health_monitor.is_healthy():
            status = health_monitor.get_status()
            warning(
                f"  :: LLM circuit OPEN (consecutive_failures={status.consecutive_failures}) — "
                f"proceeding with retry (existing backoff handles wait)"
            )

        # Prepare messages fresh each iteration so compressed context is used.
        # Extract preserve_thinking from chat_template_kwargs.
        preserve_thinking = False
        if effective_extra:
            chat_template = effective_extra.get("chat_template_kwargs", {})
            if isinstance(chat_template, dict):
                preserve_thinking = chat_template.get("preserve_thinking", False)
        msg_list = _prepare_messages(messages, preserve_thinking=preserve_thinking)

        # P4: synchronous hard guard on the exact list about to be sent.
        msg_list = _pre_send_overflow_guard(msg_list, config)

        # Disable thinking after N retries to force decisive responses.
        if (
            attempt >= effective_disable_after
            and effective_extra
            and effective_extra.get("chat_template_kwargs")
        ):
            effective_extra["chat_template_kwargs"]["enable_thinking"] = False

        call_kwargs = None  # C2: prevent UnboundLocalError in _log_failure
        tokens_displayed = [0]  # Mutable counter for streaming token tracking
        try:
            call_kwargs = _build_call_kwargs(
                model_name, msg_list, stream, effective_extra
            )

            # --- Streaming path: returns LLMResponse directly ---
            if stream:
                llm_response = _invoke_llm_streaming(
                    client, model_name, msg_list, config, tokens_displayed,
                    extra_kwargs=effective_extra,
                )

                # Check minimum response length.
                if effective_min_bytes is not None and len(llm_response.text) < effective_min_bytes:
                    last_error = Exception(
                        f"Response too short ({len(llm_response.text)} bytes < {effective_min_bytes} required)"
                    )
                    warning(
                        f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                        f"response too short (streaming)"
                    )
                    if attempt < effective_max_retries:  # L3: backoff before retry
                        from agent_llm_client import RetryBackoff as _RB_short_s

                        _RB_short_s(base=5, max_wait=120, jitter=0.3).wait(attempt)
                    continue

                # Successful streaming response
                health_monitor.record_success()
                return llm_response, compressed_messages

            # --- Non-streaming path ---
            response = client.chat.completions.create(**call_kwargs)
            if not response.choices:
                raise EmptyModelResponse("Empty response from model")

            choice = response.choices[0]
            # Validate the response is a well-formed assistant message with
            # non-null content. A null content (e.g. reasoning-only or
            # malformed response) is NOT silently coerced to "" — it is
            # rejected and retried (EmptyModelResponse → backoff loop).
            if choice.message.role != "assistant" or choice.message.content is None:
                raise EmptyModelResponse(
                    f"Malformed LLM response: role={choice.message.role!r}, "
                    "content=None"
                )
            response_text = choice.message.content
            reasoning_content = getattr(choice.message, "reasoning_content", None)
            call_stats = _extract_call_stats(response, response_text)

            # Validate finish_reason — None indicates possible truncation.
            if call_stats.finish_reason is None:
                raise EmptyModelResponse("Response missing finish_reason (possible truncation)")

            # Check minimum response length.
            if effective_min_bytes is not None and len(response_text) < effective_min_bytes:
                last_error = Exception(
                    f"Response too short ({len(response_text)} bytes < {effective_min_bytes} required)"
                )
                warning(
                    f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                    f"response too short"
                )
                if attempt < effective_max_retries:  # L3: backoff before retry
                    from agent_llm_client import RetryBackoff as _RB_short_n

                    _RB_short_n(base=5, max_wait=120, jitter=0.3).wait(attempt)
                continue

            # Successful response
            health_monitor.record_success()
            return _build_llm_response(
                response,
                response_text,
                reasoning_content,
                call_stats,
                success=True,
            ), compressed_messages

        except StreamAbortedError as e:
            # Stream aborted by checker — do NOT retry, re-raise immediately.
            error_display("STREAM ABORTED", str(e))
            raise

        except (APITimeoutError, TimeoutError, EmptyModelResponse) as e:
            last_error = e
            health_monitor.record_failure(str(e))

            # Streaming: if tokens were already displayed, warn and retry.
            if stream and tokens_displayed[0] > 0:
                print(f"\033[93m  :: RETRY (attempt {attempt + 1}/{effective_max_retries + 1}): previous response discarded - {type(e).__name__}: {e}\033[0m")

            if attempt >= effective_max_retries:
                error_display("MODEL ERROR", str(e))
                _log_failure(e)
                raise last_error from e

            llm_timeout_message(attempt, effective_max_retries)
            # L3: back off before retrying a timing-out/empty endpoint, for parity
            # with the Gateway/RateLimit/Connection branches (which all call
            # backoff.wait()). Without this the loop tight-hammers the endpoint.
            # L3: back off only on real TIMING-OUT requests, NOT on
            # EmptyModelResponse (a deterministic empty completion) — backing off
            # on empty just sleeps without helping and stalls retry loops.
            if not isinstance(e, EmptyModelResponse):
                from agent_llm_client import RetryBackoff as _RB_timeout
                _RB_timeout(base=5, max_wait=120, jitter=0.3).wait(attempt)

        except BadRequestError as e:
            error_str = str(e)

            if _is_context_overflow(error_str):
                if attempt >= effective_max_retries:
                    error_display("CONTEXT OVERFLOW", str(e))
                    raise

                recovered = _handle_context_overflow(messages, config, attempt, error_str)
                if recovered is not None:
                    messages = recovered
                    compressed_messages = recovered
                    _consecutive_overflow_failures = 0  # H5: reset on success
                    continue
                # Compression failed — fall through to error handling.
                # Retrying a few times unmodified is by design; later
                # attempts escalate from redaction to full compression.
                # P7-B2-01: only count the give-up counter once compression
                # has actually been attempted (attempt >= 3). Redaction
                # no-ops are not compression failures — counting them made
                # full compression unreachable (give-up fired first).
                if attempt >= 3:
                    _consecutive_overflow_failures += 1  # H5: track failure
                    if _consecutive_overflow_failures >= 3:
                        error_display("CONTEXT OVERFLOW", "3 consecutive compression failures, giving up")
                        raise
                _verbose(f"  :: Overflow retry {attempt+1}/{effective_max_retries}: no oversized message found, retrying")
                if attempt >= effective_max_retries:
                    error_display("CONTEXT OVERFLOW", str(e))
                    raise
            elif _is_vision_error(error_str):
                # Vision-incompatible model.
                # If agent is provided with _recover_from_vision_error,
                # use it (pops injected messages, marks image content as errors).
                # Otherwise, fall back to strip-and-retry.
                agent = config.agent
                if agent is not None and hasattr(agent, "_recover_from_vision_error"):
                    recovered = agent._recover_from_vision_error()
                    if recovered:
                        # Recovery succeeded — context cleaned, loop will continue.
                        # Signal caller via compressed_messages channel.
                        compressed_messages = agent.context.to_list()
                        warning(
                            "  :: Model does not support vision — recovered "
                            "context, marking image content as errors"
                        )
                        # Fall through to retry with cleaned context
                        messages = compressed_messages
                        continue
                    # Recovery failed — fall through to strip-and-retry
                recovered = _strip_image_blocks(messages)
                if recovered is not None:
                    messages = recovered
                    compressed_messages = recovered
                    warning(
                        "  :: Model does not support vision — stripped image "
                        "blocks from context, retrying"
                    )
                    continue
                # No images to strip — fatal error
                error_display("VISION ERROR", str(e))
                _log_failure(e)
                raise
            elif _is_text_encode_error(error_str):
                # TextEncodeInput error — context likely contains lone surrogates.
                # Sanitize and retry ONCE. Do NOT retry again on second failure.
                if not text_encode_retry_done:
                    text_encode_retry_done = True
                    from agent_message_utils import _sanitize_content, _sanitize_text

                    # Sanitize all messages in-place.
                    target_msgs = (
                        messages
                        if isinstance(messages, list)
                        else messages._messages
                        if hasattr(messages, "_messages")
                        else []
                    )
                    for msg in target_msgs:
                        if msg.get("content") is not None:
                            msg["content"] = _sanitize_content(msg["content"])
                        if msg.get("reasoning") is not None:
                            msg["reasoning"] = _sanitize_text(msg["reasoning"])
                    warning(
                        "  :: TextEncodeInput error — sanitized context "
                        "(stripped lone surrogates), retrying once"
                    )
                    continue
                else:
                    error_display(
                        "TEXT ENCODE ERROR",
                        "Context contains unfixable encoding errors.",
                    )
                    raise RuntimeError(
                        "Context contains unfixable encoding errors. Session terminated."
                    ) from e

            else:
                # Non-overflow, non-vision BadRequestError — no retry
                error_display("BAD REQUEST", str(e))
                _log_failure(e)
                raise

        except APIGatewayError as e:
            last_error = e
            health_monitor.record_failure(str(e))

            # Streaming: if tokens were already displayed, warn and retry.
            if stream and tokens_displayed[0] > 0:
                print(f"\033[93m  :: RETRY (attempt {attempt + 1}/{effective_max_retries + 1}): previous response discarded - {type(e).__name__}: {e}\033[0m")

            if attempt >= effective_max_retries:
                error_display("GATEWAY ERROR", str(e))
                _log_failure(e)
                raise last_error from e

            # 5xx errors need longer backoff — infrastructure recovery takes time
            from agent_llm_client import RetryBackoff

            backoff = RetryBackoff(base=30, max_wait=120, jitter=0.3)
            wait = backoff.next_wait(attempt)
            warning(
                f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                f"gateway error (HTTP {e.status_code}) — waiting {wait:.0f}s before retry"
            )
            backoff.wait(attempt)
            continue

        except RateLimitError as e:
            # HTTP 429: transient throttle — back off and retry (P7-B2-02).
            last_error = e
            health_monitor.record_failure(str(e))

            if stream and tokens_displayed[0] > 0:
                print(f"\033[93m  :: RETRY (attempt {attempt + 1}/{effective_max_retries + 1}): previous response discarded - {type(e).__name__}: {e}\033[0m")

            if attempt >= effective_max_retries:
                error_display("RATE LIMIT", str(e))
                _log_failure(e)
                raise last_error from e

            from agent_llm_client import RetryBackoff

            backoff = RetryBackoff(base=10, max_wait=120, jitter=0.3)
            wait = backoff.next_wait(attempt)
            warning(
                f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                f"rate limited (HTTP {e.status_code}) — waiting {wait:.0f}s before retry"
            )
            backoff.wait(attempt)
            continue

        except Exception as e:
            last_error = e

            # Streaming: if tokens were already displayed, warn and retry.
            if stream and tokens_displayed[0] > 0:
                print(f"\033[93m  :: RETRY (attempt {attempt + 1}/{effective_max_retries + 1}): previous response discarded - {type(e).__name__}: {e}\033[0m")

            # APIConnectionError: the client wraps socket-level failures
            # (urllib URLError / connection refused) as APIConnectionError, so it
            # must be retried like the raw socket errors below — otherwise a
            # transient socket blip raises instead of backing off (B4).
            if isinstance(e, (ConnectionRefusedError, BrokenPipeError, ConnectionResetError, APIConnectionError)):
                # Endpoint unreachable — backoff and retry
                health_monitor.record_failure(str(e))
                if attempt >= effective_max_retries:
                    error_display("CONNECTION ERROR", str(e))
                    _log_failure(e)
                    raise last_error from e

                from agent_llm_client import RetryBackoff

                backoff = RetryBackoff(base=5, max_wait=120, jitter=0.3)
                wait = backoff.next_wait(attempt)
                warning(
                    f"  :: LLM attempt {attempt + 1}/{effective_max_retries + 1}: "
                    f"endpoint unreachable — waiting {wait:.0f}s before retry"
                )
                backoff.wait(attempt)
                continue

            # Unexpected error — no retry
            error_display("UNEXPECTED ERROR", str(e))
            _log_failure(e)
            raise

    raise RuntimeError(
        f"_invoke_llm_with_retry exited loop without returning "
        f"(effective_max_retries={effective_max_retries}). This should not happen."
    )


__all__ = [
    "_extract_call_stats",
    "_extract_token_usage",
    "_prepare_messages",
    "_build_call_kwargs",
    "_build_llm_response",
    "_try_oversized_redaction",
    "_try_context_compress",
    "_handle_context_overflow",
    "_is_vision_error",
    "_is_text_encode_error",
    "_strip_image_blocks",
    "_pre_send_overflow_guard",
    "_invoke_llm_with_retry",
    "_invoke_llm_streaming",
    "StreamAbortedError",
]
