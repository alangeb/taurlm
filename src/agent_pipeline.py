"""RLM pipeline: extracted stages of the agent loop.

Each function is a self-contained stage of the run_rlm_loop iteration.
Extracted for testability and readability — no behavioral change.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauErgon

__all__ = [
    "check_work_budget",
    "validate_and_compress",
    "call_llm",
    "extract_code",
    "validate_eot",
    "build_synthetic_feedback",
]


def check_work_budget(agent: "TauErgon") -> str | None:
    """Check spawn work budget. Returns early-exit string if budget exhausted, else None.

    S3: only spawned children carry a work budget (identified by a non-empty
    ``spawn_id``). Root agents default to ``spawn_B == 0.0`` and must NOT be
    capped. A 1-turn spawn legitimately has ``B == 0`` and MUST still be capped,
    so the guard keys on ``spawn_id`` presence (not ``spawn_B > 0``); the
    ``spawn_B <= 0`` check below then treats B==0 as capped.
    """
    if getattr(agent, "spawn_id", "") != "":
        # P1: live exact-anchored count, not a pure estimate (and not the stale
        # scalar exact either — the anchor+delta helper prices both exactly).
        from agent_context_store import compute_live_tokens
        _sess = getattr(agent, "_session", None)
        _ctx = agent.context
        _msgs = _ctx.get_messages() if hasattr(_ctx, "get_messages") else _ctx._messages
        _live = compute_live_tokens(
            _msgs,
            getattr(_sess, "last_exact_context_tokens", None) if _sess else None,
            getattr(_sess, "last_exact_msg_count", None) if _sess else None,
            getattr(_sess, "last_turn_output_tokens", 0) if _sess else 0,
        )
        C_now = _live / agent.max_context_tokens if agent.max_context_tokens > 0 else 0.0
        delta = C_now - agent.spawn_C_last
        if delta > 0:
            agent.spawn_B = max(0.0, agent.spawn_B - delta)
        agent.spawn_C_last = C_now
        agent.context.spawn_B = agent.spawn_B
        if hasattr(agent, "spawn_min_B"):
            agent.spawn_min_B = min(agent.spawn_min_B, agent.spawn_B)

        if agent.spawn_B <= 0:
            agent._cleanup_pending = True
            answer = agent.get_answer()
            answer_str = _answer_to_str(answer) if answer and answer.content else ""
            if not answer_str:
                answer_str = "[Stopped: work budget exhausted]"
            agent.context.close_turn(answer_str, skip_cleanup=True)
            return answer_str

        if agent.spawn_B < 0.10 and not getattr(agent, "_budget_warned", False):
            agent._budget_warned = True
            agent._budget_warning_text = (
                f"[BUDGET] B:{agent.spawn_B*100:.1f}% remaining. "
                f"Consider yielding: set answer['yield']=True with status summary."
            )
    return None


def validate_and_compress(agent: "TauErgon", turn_count: int) -> None:
    """Validate context, print status bar, and trigger proactive compression if needed."""
    errors = agent.context.validate()
    if errors:
        from agent_console import context_validation_display
        last = agent.context[-1] if agent.context else None
        context_validation_display(
            errors,
            context_len=len(agent.context),
            last_role=last.get("role", "empty") if last else "empty",
        )

    # Print context status bar before each LLM call
    try:
        from agent_console.display_status import print_context_status
        print_context_status(agent.get_status())
    except Exception:
        pass

    # Compression gate: single unconditional 85% boundary.
    #
    # Design decision: if live context usage is >= 85% of the window, compress
    # every turn until it is not. There is deliberately NO cooldown and NO
    # separate 95% hard gate -- an unconditional 85% gate already covers the
    # >=95% case (it would have fired at 85% last turn), so a second gate is
    # dead weight. Re-running compression on consecutive turns is a signal that
    # something is wrong (one oversized tool result, or a system-prompt floor
    # above 85%), but we still run: correctness beats saving a compress call,
    # and the "no progress" warning below makes the pathological case loud.
    #
    # The gate MUST read a LIVE estimate, not the cached exact_tokens from the
    # previous LLM response (that value predates the newest tool result, which
    # is exactly the delta that can push a turn over the window). We estimate
    # the current context and reserve the output budget so the window accounts
    # for prompt + max_output together.
    try:
        from agent_llm_models import DEFAULT_MAX_OUTPUT_TOKENS
        from agent_context_store import compute_live_tokens
        max_tokens = agent.max_context_tokens
        reserve = getattr(agent, "max_tokens", 0) or DEFAULT_MAX_OUTPUT_TOKENS
        # prompt_tokens is reserve-free (authoritative for the compressor's
        # target math, which subtracts the output budget itself). live_tokens
        # adds the reserve back ONLY for the gate decision, so we ask "does
        # prompt + a full response fit?" without double-counting the reserve.
        #
        # P1: prompt_tokens is the LIVE exact-anchored count (exact prompt of
        # the last call + estimate of anything appended after the anchor, the
        # assistant reply included), NOT a pure estimate. This catches the newest tool result —
        # the exact delta that can silently push a turn over the window — while
        # still pricing the anchored prefix exactly. Falls back to a full
        # estimate when no anchor exists or the list was replaced.
        _sess = getattr(agent, "_session", None)
        _ctx = agent.context
        _msgs = _ctx.get_messages() if hasattr(_ctx, "get_messages") else _ctx._messages
        prompt_tokens = compute_live_tokens(
            _msgs,
            getattr(_sess, "last_exact_context_tokens", None) if _sess else None,
            getattr(_sess, "last_exact_msg_count", None) if _sess else None,
            getattr(_sess, "last_turn_output_tokens", 0) if _sess else 0,
        )
        live_tokens = prompt_tokens + reserve
        pct = live_tokens / max_tokens if max_tokens > 0 else 0.0
        if pct >= 0.85:
            from agent_console import echo
            from agent_context_compress import compress_to_target
            echo(f"[COMPRESS] Trigger: context at {pct*100:.1f}% >= 85% (live estimate)")
            before = len(agent.context)
            ok = compress_to_target(agent.context, agent, 50, current_tokens=prompt_tokens)
            if len(agent.context) == before:
                echo(f"[COMPRESS] No progress at turn {turn_count} "
                     f"({pct*100:.1f}%) -- context may exceed the compression floor "
                     f"(system prompt / oversized block). ok={ok}")
    except Exception as e:
        logging.warning("Compression gate failed: %s", e)




class _StreamDisplay:
    """Manages streaming display with proper colors and line buffering."""

    def __init__(self, hidden: bool = False) -> None:
        self.hidden = hidden
        self._reasoning_buf = ""
        self._content_buf = ""
        self._phase = "idle"  # idle -> reasoning -> content
        self._full_reasoning = ""
        self._full_content = ""

    def on_reasoning(self, token: str) -> None:
        if self.hidden:
            self._full_reasoning += token
            return
        if self._phase == "idle":
            self._phase = "reasoning"
        self._reasoning_buf += token
        self._full_reasoning += token
        self._flush_reasoning()

    def on_token(self, token: str) -> None:
        if self.hidden:
            self._full_content += token
            return
        if self._phase == "reasoning":
            self._flush_reasoning_final()
            self._phase = "content"
        elif self._phase == "idle":
            self._phase = "content"
        self._content_buf += token
        self._full_content += token
        self._flush_content()

    def finalize(self) -> None:
        if self.hidden:
            return
        if self._phase == "reasoning":
            self._flush_reasoning_final()
        elif self._phase == "content":
            self._flush_content_final()
        # Close the streamed message at column 0 so the next thing written
        # (e.g. the [REPL CODE] header) starts on its own line. Guarded so an
        # empty turn emits nothing (no stray blank line).
        if self._full_reasoning or self._full_content:
            from agent_console.primitives import ensure_newline
            ensure_newline()
        from agent_console.primitives import stream_audit
        if self._full_reasoning:
            stream_audit(self._full_reasoning)
        if self._full_content:
            stream_audit(self._full_content)

    def _flush_reasoning(self) -> None:
        from agent_models import Colors
        from agent_console.primitives import stream_write_raw
        if self._reasoning_buf:
            stream_write_raw(self._reasoning_buf, Colors.REASONING)
            self._reasoning_buf = ""
        import sys
        sys.stdout.flush()

    def _flush_reasoning_final(self) -> None:
        # Flush any remaining reasoning buffer WITHOUT a trailing newline: the
        # reasoning->content transition must stay on the same line (tested). The
        # newline that closes the whole streamed message is emitted by finalize().
        from agent_models import Colors
        from agent_console.primitives import stream_write_raw
        if self._reasoning_buf:
            stream_write_raw(self._reasoning_buf, Colors.REASONING)
            self._reasoning_buf = ""
        import sys
        sys.stdout.flush()

    def _flush_content(self) -> None:
        from agent_models import Colors
        from agent_console.primitives import stream_write_raw
        if self._content_buf:
            stream_write_raw(self._content_buf, Colors.GREEN)
            self._content_buf = ""
        import sys
        sys.stdout.flush()

    def _flush_content_final(self) -> None:
        # Same as _flush_reasoning_final: no trailing newline here; finalize() closes the line.
        from agent_models import Colors
        from agent_console.primitives import stream_write_raw
        if self._content_buf:
            stream_write_raw(self._content_buf, Colors.GREEN)
            self._content_buf = ""


def build_segmenter_invoke_fn(client, model_name, group_extra_kwargs):
    """Transport adapter for the Tier-2 fence segmenter (REQ-LFS-021).

    Returns ``invoke_fn(messages, kwargs) -> (text, ok)`` -- the segmenter's
    single injection seam. Contract, verified against the real stack:
      * ``_invoke_llm_with_retry`` RAISES on transport/circuit/provider failure
        (no ``success=False`` return path exists), so ANY exception here means a
        call failure -> ``("", False)``. A ``success=False`` response (if one were
        ever constructed) is also treated as failure -- auxiliary only.
      * The transport's OWN retry loop is disabled (``max_retries=0``): retries
        are owned by REQ-LFS-007's gate-feedback loop.
      * ``min_response_bytes=0``: a terse JSON answer is valid, not "too short".
      * ``kwargs`` is the caller's FRESH per-call dict (temperature/max_tokens/
        chat_template_kwargs) -- deliberately NOT merged with group params.
      * Timeout is client-level (baked at client construction); there is NO
        per-call knob, so a stalled fixer inherits the GROUP timeout.
    ``group_extra_kwargs`` is accepted for signature symmetry / future use but is
    intentionally NOT merged in (REQ-LFS-021: wholesale kwargs, drop preserve_thinking).
    """
    from agent_llm_invoke import _invoke_llm_with_retry
    from agent_llm_models import LLMCallConfig

    def invoke_fn(messages, kwargs):
        try:
            config = LLMCallConfig(
                extra_kwargs=dict(kwargs),   # fresh per call
                max_retries=0,              # retries owned by the segmenter
                min_response_bytes=0,       # terse JSON is acceptable
                log_on_failure=False,       # never write to the main audit
                context=None,               # fully context-free
            )
            resp, _compressed = _invoke_llm_with_retry(
                client, model_name, messages, stream=False, config=config,
            )
        except Exception:
            return "", False
        if not getattr(resp, "success", True):
            return "", False
        return (resp.text or ""), True

    return invoke_fn


def call_llm(agent: "TauErgon") -> tuple[str, str]:
    """Invoke the LLM. Returns (response_text, reasoning_content)."""
    from agent_llm_invoke import _invoke_llm_with_retry
    from agent_llm_models import DEFAULT_MAX_OUTPUT_TOKENS, LLMCallConfig

    extra_kwargs = agent.resolve_group_params()

    config = LLMCallConfig(
        log_on_failure=True,
        log_file=agent._session.audit_file,
        context=agent.context,
        extra_kwargs=extra_kwargs,
        compress_client=agent.client,
        compress_model=agent.model_name,
        compress_extra_kwargs=extra_kwargs,
        compress_audit_writer=agent._session.audit_writer,
        agent=agent,
        max_context_tokens=agent.max_context_tokens,
        max_output_tokens=agent.max_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        min_response_bytes=1,
    )

    if agent.client is None:
        from agent_console import error
        error("LLM client is None — cannot invoke LLM")
        raise ValueError("agent.client is None")

    display = _StreamDisplay(hidden=False)
    config.on_token = display.on_token
    config.on_reasoning = display.on_reasoning

    try:
        resp, compressed = _invoke_llm_with_retry(
            agent.client,
            agent.model_name,
            agent.context,
            stream=True,
            config=config,
        )
    finally:
        display.finalize()

    # Anchor the exact count to the message list that was SENT (prompt_tokens
    # covers messages[:len]); the assistant response is appended later, so
    # len(context) here is exactly the prompt's message count.
    agent._session.record_call_stats(resp.stats, context_msg_count=len(agent.context))

    if compressed is not None:
        agent.context.set_messages(compressed)

    if getattr(agent, "_cleanup_pending", False):
        agent.context.cleanup_synthetic()
        agent._cleanup_pending = False

    response_text = resp.text or ""
    reasoning_content = resp.reasoning
    return response_text, reasoning_content


def extract_code(response_text: str, fence_style: str = "std") -> tuple[str, list]:
    """Extract code blocks from response. Returns (code_string, code_block_list)."""
    from agent_repl_parse import extract_code_blocks

    code = ""
    code_blocks = []
    try:
        blocks = extract_code_blocks(response_text, fence_style=fence_style)
        code_blocks = [b for b in blocks if b.language in ("python", "bash")]
        code = "\n\n".join(b.code for b in code_blocks)
    except Exception as parse_err:
        from agent_console import warning
        warning(f"RLM: code extraction failed: {parse_err}")
    return code, code_blocks


def validate_eot(agent: "TauErgon", response_text: str, turn_count: int) -> list[str]:
    """Validate EOT (end-of-turn) conditions. Returns list of rejection feedback parts.

    Checks: multi-block EOT, empty content, oversized content.
    Modifies agent._repl_answer state on rejection.
    """
    parts: list[str] = []
    answer = agent.get_answer()

    # Reject EOT with multiple code blocks
    if answer and answer.ready:
        from agent_repl_parse import scan_style
        _repl_cfg = getattr(getattr(agent.config, "rlm", None), "repl", None)
        _fence_style = getattr(_repl_cfg, "fence_style", "std") if _repl_cfg else "std"
        # Count BLOCKS via the shared counter, not opener lines: fence-like text
        # inside prose or strings must not reject a healthy EOT (design record).
        # Auto-closed blocks count here even though the strict extractor refuses
        # them for execution - otherwise "two blocks, second one unclosed" would
        # slip past the one-block EOT rule by accident.
        # Count only blocks with a NON-EMPTY body. A block whose body is blank
        # (e.g. fence-repair residue, or a stray ATPY/ATSLASHPY pair with nothing
        # between) carries no code and must not, on its own, make a healthy
        # single-block EOT look "multi-block".
        _fence_count = len([b for b in scan_style(response_text.split(chr(10)), _fence_style).blocks
                            if "".join(b[3]).strip()])
        if _fence_count > 1:
            logging.warning("EOT rejected: multi-block (turn %d, %d blocks)", turn_count, _fence_count)
            parts.append(
                "[SYSTEM: MULTI-BLOCK EOT] answer['ready'] was set but your response "
                f"contains {_fence_count} code blocks. EOT requires exactly ONE code block. "
                "Complete your work, then send a final single-block response with "
                "answer['content'] and answer['ready'] = True."
            )
            agent._repl_answer.set_ready(False)
            if agent._repl_kernel:
                agent._repl_kernel.namespace["answer"]["ready"] = False

    # Reject EOT with empty content
    if answer and answer.ready:
        _preview = _answer_to_str(answer)
        if not _preview.strip():
            logging.warning("EOT rejected: empty content (turn %d)", turn_count)
            parts.append(
                "[SYSTEM: EMPTY ANSWER] answer['ready'] was set but "
                "answer['content'] is empty. Provide non-empty content "
                "and set answer['ready'] = True again."
            )
            agent._repl_answer.set_ready(False)
            if agent._repl_kernel:
                agent._repl_kernel.namespace["answer"]["ready"] = False

    # Reject EOT with oversized content
    if answer and answer.ready:
        _preview = _answer_to_str(answer)
        if len(_preview) > 100_000:
            logging.warning("EOT content truncated: %d chars (turn %d)", len(_preview), turn_count)
            _truncated = _preview[:100_000] + "\n[TRUNCATED: answer exceeded 100000 chars]"
            agent._repl_answer.update_content(_truncated)
            if agent._repl_kernel:
                agent._repl_kernel.namespace["answer"]["content"] = _truncated

    return parts


def build_synthetic_feedback(agent: "TauErgon", parts: list[str]) -> None:
    """Build and append the single synthetic user message containing all feedback."""
    # Guarantee: always have something in the synthetic message (rebind, dont mutate caller)
    if not parts:
        parts = ["[Code executed: no output]"]

    # ONE synthetic user message with all feedback
    msg_count = len(agent.context._messages)
    _exact = getattr(agent, "_session", None)
    _exact_tokens = _exact.last_exact_context_tokens if _exact else None
    pct = agent.context.get_usage_stats(getattr(agent, "max_context_tokens", 200000), _exact_tokens)[1]
    # Full display for console output: no truncation, newlines preserved.
    # The header prefix stays on line 1; feedback flows below it.
    feedback_display = "\n\n".join(parts)
    display_line = f"[M:{msg_count} C:{int(pct*100)}%] {feedback_display}"

    # Build content: multimodal if images are queued, text-only otherwise
    if agent._queued_images:
        image_descs = [desc for _, _, desc in agent._queued_images if desc]
        if image_descs:
            parts = list(parts) + image_descs
        text_content = "\n\n".join(parts)
        content_blocks: list[dict] = [{"type": "text", "text": text_content}]
        for data_uri, _mime, _desc in agent._queued_images:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": data_uri},
            })
        agent._queued_images.clear()
        agent.context.append_synthetic_user(
            "repl_feedback", content_blocks, msg_count=msg_count, context_pct=pct, display=display_line
        )
    else:
        agent.context.append_synthetic_user(
            "repl_feedback", "\n\n".join(parts), msg_count=msg_count, context_pct=pct, display=display_line
        )


def _answer_to_str(answer) -> str:
    """Convert answer content to string.

    Intentionally duplicated from agent_loop.py to avoid circular import
    (agent_pipeline cannot import from agent_loop). Keep in sync if changed.
    """
    if answer is None:
        return ""
    content = answer.content
    if isinstance(content, str):
        return content
    try:
        import json
        return json.dumps(content, indent=2, default=str)
    except (TypeError, ValueError):
        return str(content)
        import sys
        sys.stdout.flush()
