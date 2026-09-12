"""Compression framework — classes and wrapper.

This module provides the core infrastructure for the compression pipeline:
- CompressionStep: Declarative step registration
- CompressionContext: Shared context passed to each impl function
- _compress_wrapper: Common boilerplate handler for all compression algorithms
- _make_metadata: Metadata dict builder
- make_compress_wrapper: Factory for public wrapper functions

All compression step implementations import from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    'CompressionContext',
    'CompressionStep',
    'make_compress_wrapper',
]

from agent_console import (
    verbose as _verbose,
)

from agent_context_compress.steps.utils import (
    _calculate_context_bytes,
    _compute_50_boundary,
)

# --- Data classes ---


@dataclass
class CompressionStep:
    """Declarative registration for a compression pipeline step.

    Stores the *impl_fn* (the core algorithm) and optional *kwargs* to forward
    to the impl (e.g. ``use_boundary``).  The pipeline calls ``_compress_wrapper``
    directly — no intermediate wrapper function needed.
    """
    name: str
    impl_fn: Callable[..., tuple[list[dict], str]]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompressionContext:
    """Shared context for compression algorithm implementations.

    Bundles state and helpers that every ``_compress_*_impl`` function needs,
    eliminating repeated parameter passing and boilerplate calls.
    """
    context: list[dict]
    target_size_bytes: int
    verbose: bool
    audit_writer: Any
    actions: list[str]
    step_name: str

    def current_bytes(self) -> int:
        """Current serialized byte size of the context."""
        return _calculate_context_bytes(self.context)

    def at_target(self) -> bool:
        """True if context is at or below the target size."""
        if self.target_size_bytes <= 0:  # M-P2: guard against zero/negative target
            return True
        return self.current_bytes() <= self.target_size_bytes

    def split_point(self) -> tuple[int, int]:  # M-P5: renamed from boundary
        """Return (split_point_50_bytes, split_point_idx) for the current context."""
        return _compute_50_boundary(self.context)

    def add_action(self, description: str, action_type: str | None = None) -> None:
        """Record an action and optionally log it to the audit writer."""
        self.actions.append(description)
        if action_type and self.audit_writer is not None:
            self.audit_writer.compress_action(self.step_name, action_type, description)


# --- Metadata helper ---


def _make_metadata(
    step_name: str,
    bytes_before: int,
    bytes_after: int,
    msgs_before: int,
    msgs_after: int,
    actions: list[str],
    status: str,
) -> dict:
    return {
        "step_name": step_name,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "msgs_before": msgs_before,
        "msgs_after": msgs_after,
        "actions": actions,
        "status": status,
    }


# --- Wrapper ---


def _compress_wrapper(
    step_name: str,
    impl_fn: Callable[..., tuple[list[dict], str]],
    context: list[dict],
    target_size_bytes: int,
    verbose: bool = False,
    audit_writer: Any = None,
    **impl_kwargs,
) -> tuple[list[dict], dict]:
    """Handle common boilerplate for compression algorithms.

    The implementation function (impl_fn) should accept:
        (ctx: CompressionContext, **kwargs)
    And return:
        (context, status)

    The wrapper handles:
        - original_size / msgs_before calculation
        - final_size / msgs_after calculation
        - audit logging (compress_step)
        - metadata generation
        - system message preservation
        - CompressionContext creation

    *impl_kwargs* are forwarded to *impl_fn* to avoid closure boilerplate.
    """
    original_size = _calculate_context_bytes(context)
    msgs_before = len(context)
    current_context = list(context)
    actions: list[str] = []

    # Preserve system message for restoration if compression loses it
    system_msg = (
        context[0] if context and context[0].get("role") == "system" else None
    )

    ctx = CompressionContext(
        context=current_context,
        target_size_bytes=target_size_bytes,
        verbose=verbose,
        audit_writer=audit_writer,
        actions=actions,
        step_name=step_name,
    )

    try:
        new_context, status = impl_fn(ctx, **impl_kwargs)
    except Exception as e:
        if verbose:
            _verbose(f"  :: {step_name}: ERROR {type(e).__name__}: {e}")
        new_context, status = current_context, 'ERROR'

    # Ensure system message is preserved after compression
    if system_msg:
        has_system = any(
            isinstance(m, dict) and m.get("role") == "system"
            for m in new_context
        )
        if not has_system:
            if verbose:
                _verbose(f"  :: {step_name}: restoring lost system message")
            new_context.insert(0, system_msg)
            # Defensive: if first non-system message is assistant, insert
            # a minimal user message to maintain OpenAI alternation.
            if len(new_context) > 1 and new_context[1].get("role") == "assistant":
                new_context.insert(1, {"role": "user", "content": "(context restored)"})

    # D5 REVERTED (2026-09-12): compressed context MUST end on USER, not
    # assistant. Compression is triggered by an overflow error MID-INVOKE and the
    # compressed list is RE-SENT AS-IS to _invoke_llm_streaming (invoke.py:703-704),
    # so it is the LIVE prompt and must end on the user's pending request; a
    # trailing assistant makes the model prefill instead of answering (the exact
    # failure conversation_summary.py:184-187 warns about). No synthetic-assistant
    # append here.

    final_size = _calculate_context_bytes(new_context)
    msgs_after = len(new_context)

    if audit_writer is not None:
        actions_str = "; ".join(actions[:3]) if actions else ""
        audit_writer.compress_step(step_name, original_size, final_size, msgs_before, msgs_after, status, actions_str)

    metadata = _make_metadata(step_name, original_size, final_size, msgs_before, msgs_after, actions, status)
    return new_context, metadata


# --- Factory for public wrapper functions ---


def make_compress_wrapper(
    step_name: str | Callable[[dict[str, Any]], str],
    impl_fn: Callable[..., tuple[list[dict], str]],
) -> Callable[..., tuple[list[dict], dict]]:
    """Factory that generates a public wrapper function for a compression step.

    Eliminates boilerplate by generating the standard ``compress_*`` function
    from an impl function. Each step module uses this instead of writing ~12
    lines of identical wrapper code.

    Usage (fixed step name)::

        compress_prune_images = make_compress_wrapper("PRUNE_IMAGES", _compress_prune_images_impl)

    Usage (dynamic step name based on kwargs)::

        compress_redact_blocks = make_compress_wrapper(
            lambda kw: "REDACT_BLOCKS_50" if kw.get("use_boundary") else "REDACT_BLOCKS_FULL",
            _compress_redact_blocks_impl,
        )

    The generated wrapper has the signature::

        compress_*(context, client, model_name, target_size_bytes,
                   verbose=False, audit_writer=None, **kwargs)

    Args:
        step_name: A fixed step name string, or a callable that receives the
            merged kwargs (including client, model_name, and any extra params)
            and returns the step name.
        impl_fn: The implementation function (e.g. ``_compress_prune_images_impl``).

    Returns:
        A callable wrapper with proper ``__name__``, ``__qualname__``,
        ``__module__``, and ``__doc__`` attributes.
    """
    def wrapper(
        context: list[dict],
        client: Any,
        model_name: str,
        target_size_bytes: int,
        verbose: bool = False,
        audit_writer: Any = None,
        **kwargs: Any,
    ) -> tuple[list[dict], dict]:
        all_kwargs = {"client": client, "model_name": model_name, **kwargs}
        resolved_name = step_name(all_kwargs) if callable(step_name) else step_name
        return _compress_wrapper(
            resolved_name, impl_fn, context, target_size_bytes, verbose, audit_writer, **all_kwargs
        )

    if callable(step_name):
        wrapper.__name__ = impl_fn.__name__.replace("_impl", "").lstrip("_")
        wrapper.__qualname__ = wrapper.__name__
        wrapper.__doc__ = "Public wrapper for a dynamic compression step."
    else:
        wrapper.__name__ = f"compress_{step_name.lower()}"
        wrapper.__qualname__ = wrapper.__name__
        wrapper.__doc__ = f"Public wrapper for the {step_name} compression step."
    wrapper.__module__ = impl_fn.__module__
    wrapper.__wrapped__ = impl_fn  # type: ignore[attr-defined]
    return wrapper