"""Agent initialization config resolution.

Extracts all config-resolution logic from TauErgon.__init__ into a
single, testable, reusable module.  The public API is:

    from agent_init import resolve_agent_init, AgentInitConfig

    cfg = resolve_agent_init(
        config=config,
        base_url=base_url,
        model=model,
        max_context_tokens=max_context_tokens,
        agent_name=agent_name,
        llm_group_name=llm_group_name,
        heartbeat_seconds=heartbeat_seconds,
    )

All ``if config else default`` patterns live here.  TauErgon.__init__
becomes a pure wiring step that consumes the resolved config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

__all__ = [
    'AgentInitConfig',
    'resolve_agent_init',
]

from agent_config import (
    Config,
    LLMGroup,
    LoopDetectionConfig,
    MalformedConfig,
)

# ---------------------------------------------------------------------------
# Resolution primitives — 2 composable building blocks
# ---------------------------------------------------------------------------
#
# Instead of enumerating every combination of (arg-vs-config) × (not-None-vs-truthy),
# we provide 2 orthogonal primitives that compose to cover all cases:
#
#   _pick(a, b)      — None-aware: return a if a is not None, else b
#   _pick_or(a, b)   — truthy-aware: return a if truthy, else b
#   _cfgattr(c, a)   — safe getattr: return c.a if c is not None, else None
#
# Every resolution pattern composes from these:
#
#   arg > config > default (None-aware):  _pick(arg, _pick(_cfgattr(cfg, attr), default))
#   arg > config > default (truthy):      _pick_or(arg, _pick_or(_cfgattr(cfg, attr), default))
#   config > default (None-aware):       _pick(_cfgattr(cfg, attr), default)
#   config > default (truthy):            _pick_or(_cfgattr(cfg, attr), default)
#
# This eliminates the combinatorial explosion of near-duplicate functions.

T = TypeVar("T")


def _pick(a: T, b: T) -> T:
    """Return *a* if it is not None, else *b*.  (None-aware selection.)"""
    return a if a is not None else b


def _pick_or(a: T, b: T) -> T:
    """Return *a* if truthy, else *b*.  (Truthy selection.)"""
    return a if a else b


def _cfgattr(config: Config | None, attr: str) -> Any:
    """Return ``config.attr`` if *config* is not None, else None.

    Safe getattr that handles the ``config is None`` case without
    requiring an explicit None check at every call site.
    """
    return getattr(config, attr, None) if config is not None else None


# ---------------------------------------------------------------------------
# Resolved config dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentInitConfig:
    """Fully-resolved agent initialization parameters.

    Every field has a concrete value — no ``None`` fallbacks or
    ``if config else default`` logic remains in the caller.

    Invariants
    ----------
    - ``active_group`` is never ``None`` (raised during resolution).
    - ``llm_groups`` is never empty (raised during resolution).
    """

    # Identity
    agent_name: str

    # LLM groups (all groups from config, keyed by name)
    llm_groups: dict[str, LLMGroup]

    # Active group name and resolved values
    current_group_name: str

    # LLM overrides (may be None — used to detect cli vs group source)
    model_override: str | None
    base_url_override: str | None
    max_context_tokens_override: int | None

    # Resolved LLM values (from active group + overrides)
    model_name: str
    base_url: str
    api_key: str
    max_context_tokens: int
    max_tokens: int | None
    timeout: int  # from active group

    # Malformed response retry limits
    max_silent_retries: int
    max_enhanced_retries: int
    max_explicit_retries: int

    # Inference params (passthrough to API)
    inference_params: dict[str, Any] | None

    # Loop detection
    loop_detection_window_size: int
    loop_detection_repeat_threshold: int

    # Heartbeat
    heartbeat_enabled: bool
    heartbeat_interval: int | None


# ---------------------------------------------------------------------------
# Resolution function
# ---------------------------------------------------------------------------

def resolve_agent_init(
    config: Config | None = None,
    base_url: str | None = None,
    model: str | None = None,
    max_context_tokens: int | None = None,
    agent_name: str | None = None,
    llm_group_name: str | None = None,
    heartbeat_seconds: int | None = None,
) -> AgentInitConfig:
    """Resolve raw config + overrides into a fully-resolved init config.

    Resolution priority (highest → lowest):
        1. Explicit argument
        2. Config object value
        3. Default (from dataclass field defaults)

    Raises
    ------
    ValueError
        If no active LLM group can be resolved.
    """
    # ── Identity & LLM groups ──────────────────────────────────────────
    resolved_name = _pick(agent_name, _pick(_cfgattr(config, "agent_name"), "default"))
    llm_groups = _pick_or(_cfgattr(config, "llm_groups"), {})
    current_group_name = _pick_or(llm_group_name, _cfgattr(config, "llm_group_name"))

    # ── Active group resolution ─────────────────────────────────────────
    active_group = llm_groups.get(current_group_name)
    if not active_group:
        raise ValueError(
            f"No LLM group '{current_group_name}' found. "
            f"Available groups: {list(llm_groups.keys()) or '(none)'}"
        )

    # ── Resolved LLM values (from active group + overrides) ────────────
    resolved_model = model or active_group.model
    resolved_base_url = base_url or active_group.api_base
    resolved_api_key = active_group.api_key
    resolved_max_context = max_context_tokens if max_context_tokens is not None else active_group.max_context_tokens
    resolved_max_tokens = active_group.max_tokens
    resolved_timeout = active_group.timeout

    # ── Validate resolved values ────────────────────────────────────────
    if not resolved_base_url:
        raise ValueError(
            f"LLM group '{current_group_name}' has no valid api_base "
            f"(cli override: {base_url!r}, group.api_base: {active_group.api_base!r}). "
            "Cannot initialize agent without a base URL."
        )
    if not resolved_model:
        raise ValueError(
            f"LLM group '{current_group_name}' has no valid model "
            f"(cli override: {model!r}, group.model: {active_group.model!r}). "
            "Cannot initialize agent without a model name."
        )

    # ── Nested config values with defaults ──────────────────────────────
    # Malformed retry limits
    if config:
        malformed = config.malformed
    else:
        malformed = MalformedConfig()
    silent_retries = malformed.silent_retries
    enhanced_retries = malformed.enhanced_retries
    explicit_retries = malformed.explicit_retries

    # Inference params
    inference_params = _pick(_cfgattr(config, "inference_params"), None)

    # Loop detection
    if config:
        ld = config.loop_detection
    else:
        ld = LoopDetectionConfig()
    ld_window = ld.window_size
    ld_repeat = ld.repeat_threshold

    # Heartbeat
    hb_enabled = False
    hb_interval: int | None = None

    if heartbeat_seconds is not None and heartbeat_seconds > 0:
        hb_interval = heartbeat_seconds
        hb_enabled = True
    elif config and config.heartbeat_seconds and config.heartbeat_seconds > 0:
        hb_interval = config.heartbeat_seconds
        hb_enabled = True

    return AgentInitConfig(
        agent_name=resolved_name,
        llm_groups=llm_groups,
        current_group_name=current_group_name,
        model_override=model,
        base_url_override=base_url,
        max_context_tokens_override=max_context_tokens,
        model_name=resolved_model,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        max_context_tokens=resolved_max_context,
        max_tokens=resolved_max_tokens,
        timeout=resolved_timeout,
        max_silent_retries=silent_retries,
        max_enhanced_retries=enhanced_retries,
        max_explicit_retries=explicit_retries,
        inference_params=inference_params,
        loop_detection_window_size=ld_window,
        loop_detection_repeat_threshold=ld_repeat,
        heartbeat_enabled=hb_enabled,
        heartbeat_interval=hb_interval,
    )
