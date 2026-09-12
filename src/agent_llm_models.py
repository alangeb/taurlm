"""LLM data models, error classes, and configuration for TauErgon.

Extracted from agent_llm.py — contains only pure data structures and constants.
No I/O, no HTTP, no validation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_context import TauContext


# ---------------------------------------------------------------------------
# Response data models (wrappers around raw API response)
# ---------------------------------------------------------------------------

@dataclass
class Function:
    """Function definition for API communication."""
    name: str | None = None
    arguments: str | None = None

@dataclass
class ToolCall:
    """Single API call with function name and arguments."""
    id: str | None = None
    function: Function = field(default_factory=Function)
    type: str = "function"

@dataclass
class Message:
    """Chat message with content, API calls, and role."""
    content: str | None = None
    reasoning_content: str | None = None  # vLLM="reasoning", llama.cpp="reasoning_content"
    tool_calls: list[ToolCall] = field(default_factory=list)
    role: str | None = "assistant"

@dataclass
class Choice:
    """Completion choice with message and finish reason."""
    message: Message = field(default_factory=Message)
    finish_reason: str | None = None
    index: int = 0

@dataclass
class Usage:
    """Token usage statistics for an API request."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: dict[str, Any] | None = None

@dataclass
class Response:
    """Complete API response with choices and usage."""
    choices: list[Choice] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    id: str | None = None
    model: str | None = None
    created: int | None = None


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------

class APIError(Exception):
    """Base exception for all API-related errors.

    Attributes:
        status_code: HTTP status code if available (e.g., 400, 401, 429, 500).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

class APITimeoutError(APIError):
    """Raised when a request times out."""

    pass

class BadRequestError(APIError):
    """Raised for HTTP 400 Bad Request errors."""

    pass

class RateLimitError(APIError):
    """Raised for HTTP 429 Rate Limit errors."""

    pass

class UnauthorizedError(APIError):
    """Raised for HTTP 401 Unauthorized errors."""

    pass

class APIConnectionError(APIError):
    """Raised when the API endpoint is unreachable (connection refused, TCP RST)."""

    pass

class APIGatewayError(APIError):
    """Raised for HTTP 5xx gateway/proxy errors (502, 503, 504).

    These are transient infrastructure errors — the upstream model server
    is temporarily unavailable. Standard retry with backoff is appropriate.
    """

    pass


# ---------------------------------------------------------------------------
# Invocation data models
# ---------------------------------------------------------------------------

class EmptyModelResponse(ValueError):
    """Model returned no choices. Subclasses ValueError for backward compatibility."""

    pass

@dataclass
class CallStats:
    """Token usage statistics from a single LLM invocation.

    Token fields are ``None`` when the API does not provide usage data,
    allowing callers to distinguish "API returned 0" from "API returned no data".
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    finish_reason: str | None = None
    stream_duration: float = 0.0
    first_token_time: float = 0.0
    tokens_generated: int = 0
    tg_tps: float = 0.0
    ttft: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0–1.0), or 0.0 if no cache data."""
        if self.cached_tokens is None or not self.prompt_tokens:
            return 0.0
        return self.cached_tokens / self.prompt_tokens

@dataclass
class CacheTracker:
    """Session-wide cache hit rate statistics.

    Tracks cumulative, sliding-window (last 5 calls), and last-call hit rates.
    Records where ``cached_tokens is None`` are excluded from calculations.
    """

    _records: list[CallStats] = field(default_factory=list)

    def record(self, stats: CallStats) -> None:
        self._records.append(stats)

    def clear(self) -> None:
        self._records.clear()

    def _hit_rate(self, records: list[CallStats]) -> float | None:
        """Compute hit rate for a list of records, excluding None cache data."""
        valid = [r for r in records if r.cached_tokens is not None]
        total_prompt = sum(r.prompt_tokens or 0 for r in valid)
        total_cached = sum(r.cached_tokens or 0 for r in valid)
        return total_cached / total_prompt if total_prompt > 0 else None

    @property
    def cumulative_hit_rate(self) -> float | None:
        """Hit rate across all recorded calls."""
        return self._hit_rate(self._records)

    @property
    def sliding_hit_rate(self) -> float | None:
        """Hit rate over the last 5 calls."""
        return self._hit_rate(self._records[-5:])

    @property
    def last_hit_rate(self) -> float | None:
        """Hit rate of the most recent call with valid cache data."""
        for record in reversed(self._records):
            if record.cached_tokens is not None:
                return record.hit_rate
        return None

    @property
    def call_count(self) -> int:
        return len(self._records)

    @property
    def has_cache_data(self) -> bool:
        """True if any record has valid cache data (cached_tokens is not None)."""
        return any(r.cached_tokens is not None for r in self._records)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Allowed fields in messages sent to the LLM API.
ALLOWED_MESSAGE_FIELDS: frozenset[str] = frozenset(
    ["role", "content", "name", "tool_calls", "tool_call_id", "reasoning"]
)

# Allowed parameters in the OpenAI chat completion body.
OPENAI_BODY_PARAMS: frozenset[str] = frozenset(
    [
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repetition_penalty",
        "max_tokens",
        "seed",
    ]
)

# Allowed fields in tool_calls dicts sent to the API.
_ALLOWED_TOOL_CALL_FIELDS: frozenset[str] = frozenset(["id", "type", "function"])

# Context overflow error indicators — substrings matched against any APIError
CONTEXT_OVERFLOW_INDICATORS: tuple[str, ...] = (
    "exceed_context_size_error",
    "exceeds the available context size",
    "maximum context length",
    "context size has been exceeded",
)

# Default token budget values used across the codebase.
# Override via LLMGroup config in tau.json or agent overrides.
DEFAULT_MAX_CONTEXT_TOKENS: int = 200000
DEFAULT_MAX_OUTPUT_TOKENS: int = 12000

# Overflow compression thresholds
OVERSIZED_THRESHOLD = 0.20
COMPRESSION_FACTOR = 0.30


# ---------------------------------------------------------------------------
# Config / Response dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LLMCallConfig:
    """Configuration for an LLM invocation."""

    max_retries: int = 10
    disable_thinking_after: int = 5
    log_on_failure: bool = True
    log_file: Path | None = None
    # Default 10: reject empty/truncated responses (retry with backoff).
    # Safe for RLM mode — every valid response contains a code fence
    # (>>= 10 bytes). Matches turn_summary/compress call sites.
    min_response_bytes: int | None = 10
    context: "TauContext | None" = None
    extra_kwargs: dict[str, Any] | None = None
    compress_client: Any = None
    compress_model: str | None = None
    compress_extra_kwargs: dict | None = None
    compress_audit_writer: Any = None
    # Optional agent reference for vision error recovery.
    # If provided and a vision error occurs, the agent's
    # _recover_from_vision_error() method is called instead of
    # the default strip-and-retry behavior.
    agent: Any = None
    # Token budget for token-aware compression target calculation.
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    on_token: "Callable[[str], None] | None" = None
    on_reasoning: "Callable[[str], None] | None" = None
    hidden: bool = False

@dataclass
class LLMResponse:
    """Structured LLM response with text, stats, and API calls."""

    raw: Any
    text: str
    reasoning: str | None
    stats: CallStats
    success: bool = True
    error: Exception | None = None
    tool_calls: list[dict] = field(default_factory=list)


__all__ = [
    # Response data models
    "Function",
    "ToolCall",
    "Message",
    "Choice",
    "Usage",
    "Response",
    # Error classes
    "APIError",
    "APITimeoutError",
    "BadRequestError",
    "RateLimitError",
    "UnauthorizedError",
    "APIConnectionError",
    "APIGatewayError",
    # Invocation models
    "EmptyModelResponse",
    "CallStats",
    "CacheTracker",
    # Constants
    "ALLOWED_MESSAGE_FIELDS",
    "OPENAI_BODY_PARAMS",
    "_ALLOWED_TOOL_CALL_FIELDS",
    "CONTEXT_OVERFLOW_INDICATORS",
    "OVERSIZED_THRESHOLD",
    "COMPRESSION_FACTOR",
    # Config / Response
    "LLMCallConfig",
    "LLMResponse",
]
