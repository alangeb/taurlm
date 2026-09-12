"""HTTP client for OpenAI-compatible LLM APIs.

Stdlib-only client (urllib + json) with cache tracking. Extracted from agent_llm.py.
"""

from __future__ import annotations

import json
import logging
import os
import random as _random
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Iterator

from agent_console import warning
from agent_llm_cache import PrefixCacheTracker
from agent_llm_models import (
    APIConnectionError,
    APIError,
    APIGatewayError,
    APITimeoutError,
    BadRequestError,
    Choice,
    Function,
    Message,
    RateLimitError,
    Response,
    ToolCall,
    UnauthorizedError,
    Usage,
    CONTEXT_OVERFLOW_INDICATORS,
)


def _is_context_overflow(error_str: str) -> bool:
    """Return True if *error_str* contains a known context overflow indicator."""
    return any(indicator in error_str for indicator in CONTEXT_OVERFLOW_INDICATORS)


# ---------------------------------------------------------------------------
# Retry backoff — configurable, jittered, interruptible
# ---------------------------------------------------------------------------

class RetryBackoff:
    """Configurable backoff with exponential growth, jitter, and interruptibility.

    Usage:
        backoff = RetryBackoff(base=5, max_wait=300, jitter=0.3)
        for attempt in range(max_retries):
            try:
                ...
            except TransientError:
                if attempt == max_retries - 1:
                    raise
                backoff.wait(attempt)
    """

    def __init__(
        self,
        base: float = 5.0,
        max_wait: float = 300.0,
        jitter: float = 0.3,
        multiplier: float = 2.0,
    ):
        self.base = base
        self.max_wait = max_wait
        self.jitter = jitter
        self.multiplier = multiplier

    def wait(self, attempt: int) -> None:
        """Sleep for the backoff interval, interruptible by SIGINT."""
        raw = min(self.base * (self.multiplier ** attempt), self.max_wait)
        # Apply jitter: ±jitter fraction
        if self.jitter > 0:
            swing = raw * self.jitter
            wait = raw + _random.uniform(-swing, swing)
        else:
            wait = raw
        wait = max(0.1, wait)  # Floor at 100ms

        # Interruptible sleep — check for SIGINT every second
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # M-L2: Check for exit request
            try:
                from agent_lifecycle import AgentLifecycle
                if AgentLifecycle.is_exit_requested():
                    break
            except ImportError:
                pass
            time.sleep(min(remaining, 1.0))

    def next_wait(self, attempt: int) -> float:
        """Return the wait time for *attempt* without sleeping."""
        raw = min(self.base * (self.multiplier ** attempt), self.max_wait)
        if self.jitter > 0:
            swing = raw * self.jitter
            return max(0.1, raw + _random.uniform(-swing, swing))
        return max(0.1, raw)


# ---------------------------------------------------------------------------
# Chat completion wrapper (provides chat.completions.create() interface)
# ---------------------------------------------------------------------------

class SimpleChatCompletion:
    """Wrapper providing chat.completions.create() interface."""

    def __init__(self, client: SimpleOpenAIClient):
        self._client = client

    def create(self, **kwargs) -> Any:
        return self._client.chat_completions_create(**kwargs)

    def create_stream(self, **kwargs) -> Iterator[dict]:
        return self._client.chat_completions_create_stream(**kwargs)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

# HTTP error code → exception mapping
_HTTP_ERROR_MAP = {
    400: BadRequestError,
    401: UnauthorizedError,
    429: RateLimitError,
    # 5xx gateway errors — transient, retry with backoff
    502: APIGatewayError,
    503: APIGatewayError,
    500: APIGatewayError,
    504: APIGatewayError,
}


class SimpleOpenAIClient:
    """Drop-in replacement for OpenAI client using stdlib only (urllib + json)."""

    @staticmethod
    def _safe_get(data: dict, *keys, expected_type=None, default=None):
        """Traverse nested dict keys with optional type validation."""
        if not isinstance(data, dict):
            return default
        current = data
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        if expected_type is not None:
            if current is None and type(None) in (
                expected_type if isinstance(expected_type, tuple) else (expected_type,)
            ):
                return current
            if not isinstance(current, expected_type):
                return default
        return current

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: int = 300,
        cache_tracker: PrefixCacheTracker | None = None,
    ):
        if not base_url:
            raise ValueError(
                f"SimpleOpenAIClient requires a valid base_url, got: {base_url!r}. "
                "Check LLM group configuration (api_base setting)."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("API_KEY", "")
        self.timeout = timeout
        self._cache_tracker = cache_tracker
        self.chat = type("Chat", (), {"completions": SimpleChatCompletion(self)})()

    def _log_request_audit(self, url: str, kwargs: dict, tag: str) -> None:
        """Write the full request body (.lr.json) + an audit one-liner (.audit).

        Shared by the non-stream and stream paths (I10 dedup). The .lr.json dump
        is intentional (bug reproduction) — see DESIGN DECISION note in caller.
        Best-effort: any failure is swallowed (logged at debug) so audit logging
        can never break a real LLM call. `tag` distinguishes LLM_CALL vs
        LLM_CALL_STREAM in the audit line.
        """
        try:
            import agent_session as _sess
            from datetime import datetime as _dt

            log_dir = getattr(_sess, "LOG_DIR", None)
            prefix = getattr(_sess, "SESSION_PREFIX", None)
            if log_dir and prefix:
                # (1) Full request body → {prefix}.lr.json (for full reproduction)
                lr_file = log_dir / f"{prefix}.lr.json"
                lr_body = {
                    "url": url,
                    "timeout": self.timeout,
                    "kwargs": kwargs,
                }
                lr_file.write_text(
                    json.dumps(lr_body, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                # (2) Audit one-liner → {prefix}.audit (params only, no context)
                audit_file = log_dir / f"{prefix}.audit"
                params = {k: kwargs[k] for k in (
                    "model", "max_tokens", "temperature", "top_p", "top_k",
                    "min_p", "presence_penalty", "frequency_penalty",
                    "repetition_penalty", "repeat_penalty", "seed",
                ) if k in kwargs}
                if "chat_template_kwargs" in kwargs:
                    params["chat_template_kwargs"] = kwargs["chat_template_kwargs"]
                if "extra_body" in kwargs:
                    params["extra_body"] = kwargs["extra_body"]
                params_str = " ".join(
                    f"{k}={json.dumps(v)}" for k, v in sorted(params.items())
                )
                ts = _dt.now().isoformat()
                with open(audit_file, "a", encoding="utf-8") as af:
                    af.write(f"[{ts}] {tag} {params_str}\n")
        except Exception as e:
            logging.debug("llm_client: audit logging failed: %s", e)

    def chat_completions_create(self, **kwargs) -> Any:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(kwargs, sort_keys=True).encode("utf-8")

        # Log full request body + audit one-liner (shared helper, I10).
        # DESIGN DECISION: the helper writes the FULL request body to .lr.json on
        # purpose — it is the bug-reproduction artifact. Do NOT redact/trim it.
        self._log_request_audit(url, kwargs, "LLM_CALL")

        tracker = self._cache_tracker
        expected_hit, hit_reason = 0.0, "no-tracker"
        if tracker is not None:
            expected_hit, hit_reason = tracker.compute_expected_hit(data)

        headers = {"Content-Type": "application/json", "X-Request-Id": str(uuid.uuid4())}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            # M-L5: DEFERRED - urllib.request.urlopen creates a new TCP connection per call.
            # Connection reuse (HTTP keep-alive) would reduce latency for retry sequences.
            # Would require switching to http.client.HTTPConnection or requests.Session.
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                try:
                    result = json.loads(response.read().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise APIError(f"Invalid JSON response from API: {e}")
                wrapped = self._wrap_response(result)
                self._report_cache_hit(wrapped, expected_hit, hit_reason, data)
                return wrapped

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except (OSError, ValueError):
                pass

            exc_class = _HTTP_ERROR_MAP.get(e.code, APIError)
            raise exc_class(f"{exc_class.__name__}: {error_body}", status_code=e.code) from e

        except urllib.error.URLError as e:
            if self._is_timeout(e):
                raise APITimeoutError("Request timed out") from e
            if self._is_connection_refused(e):
                raise APIConnectionError(f"Connection refused: {e}") from e
            if self._is_transient_socket_error(e):  # L1: DNS/reset/broken-pipe retry
                raise APIConnectionError(f"Transient socket error: {e}") from e
            raise APIError(f"Request failed: {e}") from e

    def chat_completions_create_stream(self, **kwargs) -> Iterator[dict]:
        """Streaming chat completion via SSE. Yields raw JSON chunk dicts."""
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        url = f"{self.base_url}/chat/completions"
        data = json.dumps(kwargs, sort_keys=True).encode("utf-8")

        # Log full request body + audit one-liner (shared helper, I10)
        self._log_request_audit(url, kwargs, "LLM_CALL_STREAM")

        tracker = self._cache_tracker
        expected_hit, hit_reason = 0.0, "no-tracker"
        if tracker is not None:
            expected_hit, hit_reason = tracker.compute_expected_hit(data)

        headers = {"Content-Type": "application/json", "X-Request-Id": str(uuid.uuid4())}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        usage_data = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" not in content_type:
                    error_body = response.read().decode("utf-8", errors="replace")
                    raise APIError(
                        f"Expected text/event-stream but got {content_type!r}: {error_body[:500]}"
                    )
                usage_data: dict = {}
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                    # Skip comments, keep-alives, and SSE metadata fields
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
                        continue
                    if not line:
                        continue

                    # Parse data: lines
                    if line.startswith("data: "):
                        payload = line[6:]
                    elif line.startswith("data:"):
                        payload = line[5:]
                    else:
                        continue

                    if payload.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    # Accumulate usage from any chunk that carries it
                    if "usage" in chunk and isinstance(chunk["usage"], dict):
                        usage_data = chunk["usage"]

                    yield chunk

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except (OSError, ValueError):
                pass
            exc_class = _HTTP_ERROR_MAP.get(e.code, APIError)
            raise exc_class(f"{exc_class.__name__}: {error_body}", status_code=e.code) from e

        except urllib.error.URLError as e:
            if self._is_timeout(e):
                raise APITimeoutError("Request timed out") from e
            if self._is_connection_refused(e):
                raise APIConnectionError(f"Connection refused: {e}") from e
            if self._is_transient_socket_error(e):  # L1: DNS/reset/broken-pipe retry
                raise APIConnectionError(f"Transient socket error: {e}") from e
            raise APIError(f"Request failed: {e}") from e

        finally:
            if usage_data:
                class _SyntheticResponse:
                    pass

                synthetic = _SyntheticResponse()
                synthetic.usage = Usage(
                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                    completion_tokens=usage_data.get("completion_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                    prompt_tokens_details=usage_data.get("prompt_tokens_details"),
                )
                self._report_cache_hit(synthetic, expected_hit, hit_reason, data)

    @staticmethod
    def _is_timeout(e: urllib.error.URLError) -> bool:
        """Check if a URLError represents a timeout."""
        exc_str = str(e).lower()
        reason_str = str(getattr(e, "reason", "")).lower()
        return (
            "timeout" in exc_str
            or "timed out" in exc_str
            or "timeout" in reason_str
            or "timed out" in reason_str
            or isinstance(getattr(e, "reason", None), socket.timeout)
        )

    @staticmethod
    def _is_connection_refused(e: urllib.error.URLError) -> bool:
        """Check if a URLError represents a connection refusal (endpoint unreachable)."""
        reason = getattr(e, "reason", None)
        if isinstance(reason, (ConnectionRefusedError, socket.error)):
            return True
        reason_str = str(reason).lower()
        return (
            "connection refused" in reason_str
            or "errno 111" in reason_str
            or "errno 61" in reason_str  # macOS connection refused
        )

    @staticmethod
    def _is_transient_socket_error(e: urllib.error.URLError) -> bool:
        """L1: transient socket-level failures that should RETRY, not raise.

        Covers DNS resolution (getaddrinfo), connection reset, and broken pipe —
        all transient. Mapped to APIConnectionError so the retry loop's
        isinstance tuple (agent_llm_invoke.py:856) backs off instead of raising.
        """
        reason = getattr(e, "reason", None)
        # socket.error covers ConnectionResetError / BrokenPipeError / OSError
        if isinstance(reason, (socket.gaierror, ConnectionResetError, BrokenPipeError, ConnectionError)):
            return True
        reason_str = str(reason).lower()
        return (
            "getaddrinfo" in reason_str
            or "temporary failure in name resolution" in reason_str
            or "name or service not known" in reason_str
            or "nodename nor servname" in reason_str
            or "connection reset" in reason_str
            or "broken pipe" in reason_str
            or "errno 104" in reason_str  # ECONNRESET
            or "errno 32" in reason_str  # EPIPE
        )

    def _report_cache_hit(
        self, response: Any, expected_hit: float, hit_reason: str, body_bytes: bytes
    ) -> None:
        """Report cache-hit anomalies, deduplicating repeated conditions.

        Each warning key (e.g. ``"gap:75%:0%"``, ``"low_act:0%"``) is emitted
        once via ``PrefixCacheTracker.should_warn``; persistent conditions are
        suppressed on subsequent calls so the log isn't flooded.  Distinct
        conditions (e.g. a hit-rate shift from 0% to 50%) produce a new key and
        are re-emitted.

        Passes *body_bytes* to ``diagnose_miss`` so it can compare the current
        request body against the previous one (``_prev_request_body``).
        """
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            pt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = 0
            if pt_details and isinstance(pt_details, dict):
                cached_tokens = pt_details.get("cached_tokens", 0) or 0
            if prompt_tokens == 0:
                return
            actual_hit = cached_tokens / prompt_tokens

            tracker = self._cache_tracker

            gap = expected_hit - actual_hit
            if gap >= 0.20:
                gap_key = f"gap:{expected_hit:.0%}:{actual_hit:.0%}"
                if tracker is None or tracker.should_warn(gap_key):
                    warning(
                        f":: cache: expected {expected_hit:.0%} -> actual {actual_hit:.0%} "
                        f"(gap {gap:.0%}, prefix cache MISS)"
                    )
                    # Diagnose miss with divergence context
                    if tracker is not None:
                        diag = tracker.diagnose_miss(body_bytes)
                        warning(f":: cache diag: {diag}")
            if "params changed" in hit_reason:
                if tracker is None or tracker.should_warn(hit_reason):
                    warning(f":: cache: invalidated — {hit_reason}")
            if expected_hit < 0.25:
                exp_key = f"low_exp:{expected_hit:.0%}"
                if tracker is None or tracker.should_warn(exp_key):
                    warning(f":: cache: low expected {expected_hit:.0%} ({hit_reason})")
                    if tracker is not None:
                        div_lines = tracker.format_divergence_lines(body_bytes)
                        if div_lines:
                            for line in div_lines.split("\n"):
                                warning(line)
            if actual_hit < 0.25:
                act_key = f"low_act:{actual_hit:.0%}"
                if tracker is None or tracker.should_warn(act_key):
                    warning(f":: cache: low actual {actual_hit:.0%} (cache underperforming)")
        except Exception as e:
            logging.debug("llm_client: cache tracking warning failed: %s", e)

    def _wrap_response(self, data: dict) -> Any:
        """Wrap raw API response dict into structured Response object."""
        if not isinstance(data, dict):
            return Response(choices=[], usage=Usage())

        choices = [self._wrap_choice(cd) for cd in self._safe_get(data, "choices", expected_type=list, default=[]) if isinstance(cd, dict)]

        usage_data = self._safe_get(data, "usage", expected_type=dict, default={})
        usage = Usage(
            prompt_tokens=self._safe_get(usage_data, "prompt_tokens", expected_type=int, default=0),
            completion_tokens=self._safe_get(usage_data, "completion_tokens", expected_type=int, default=0),
            total_tokens=self._safe_get(usage_data, "total_tokens", expected_type=int, default=0),
            prompt_tokens_details=self._safe_get(usage_data, "prompt_tokens_details", expected_type=dict, default=None),
        )

        return Response(
            choices=choices,
            usage=usage,
            id=self._safe_get(data, "id", expected_type=str),
            model=self._safe_get(data, "model", expected_type=str),
            created=self._safe_get(data, "created", expected_type=int),
        )

    def _wrap_choice(self, choice_data: dict) -> Choice:
        """Wrap a single choice dict into a Choice object."""
        message_data = self._safe_get(choice_data, "message", expected_type=dict, default={})
        tool_calls = [self._wrap_tool_call(tc) for tc in self._safe_get(message_data, "tool_calls", expected_type=list, default=[]) if isinstance(tc, dict)]

        reasoning_content = (
            self._safe_get(message_data, "reasoning", expected_type=str)
            or self._safe_get(message_data, "reasoning_content", expected_type=str)
        )

        return Choice(
            message=Message(
                content=self._safe_get(message_data, "content", expected_type=str),
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
                role=self._safe_get(message_data, "role", expected_type=str, default="assistant"),
            ),
            finish_reason=self._safe_get(choice_data, "finish_reason", expected_type=str),
            index=self._safe_get(choice_data, "index", expected_type=int, default=0),
        )

    def _wrap_tool_call(self, tc_data: dict) -> ToolCall:
        """Wrap a single API call dict into a ToolCall object."""
        func_data = self._safe_get(tc_data, "function", expected_type=dict, default={})
        func_obj = Function(
            name=self._safe_get(func_data, "name", expected_type=str),
            arguments=self._safe_get(func_data, "arguments", expected_type=str),
        )
        return ToolCall(
            id=self._safe_get(tc_data, "id", expected_type=str),
            function=func_obj,
            type=self._safe_get(tc_data, "type", expected_type=str, default="function"),
        )


__all__ = [
    "_is_context_overflow",
    "RetryBackoff",
    "SimpleChatCompletion",
    "SimpleOpenAIClient",
    "_HTTP_ERROR_MAP",
]
