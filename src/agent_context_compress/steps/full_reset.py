"""Step 7: Full Reset — LLM generates summary + plan, replaces entire context.

Last resort compression. Makes two LLM calls: one for summary, one for next steps.
Replaces the entire context with a compact summary + plan + current user request.
"""

from __future__ import annotations
import logging

from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS

from agent_console import error
from agent_message_utils import is_real_user_request

from agent_context_compress.steps.framework import CompressionContext, make_compress_wrapper, _verbose
from agent_context_compress.steps.llm import _invoke_llm_with_retry_compression
from agent_context_compress.steps.utils import _calculate_context_bytes, _extract_text_from_content


__all__ = ["compress_full_reset", "SUMMARY_PROMPT", "PLAN_PROMPT"]

SUMMARY_PROMPT = """You are an expert conversation summarizer. Summarize everything that has been accomplished so far in this conversation.

The assistant communicates through Python code executed in a persistent REPL kernel.

Focus on:
- Key accomplishments and results
- Python code written and executed (especially important functions/classes)
- Files modified, created, or read (with paths)
- Shell commands executed (via bash blocks) and their outputs
- Technical decisions made and their rationale
- Current state of the work and what remains
- Variables, functions, or state that persists in the REPL kernel

Be comprehensive but concise. Include all critical information needed to continue this work."""


PLAN_PROMPT = """Based on the conversation summary, what are the next steps needed to complete the task?

The assistant communicates through Python code in a persistent REPL. Provide a clear plan
with concrete Python actions or bash blocks. Be specific about what code needs to be
written or what functions need to be called."""


def _validate_plan(plan: str) -> bool:
    """Validate the LLM 'next steps' plan.

    Mirrors the SUMMARY validation predicate (full_reset summary path): reject
    empty/None, too-short, and refusal/error-prefixed outputs. Returning False
    triggers a retry of the plan LLM call (see _compress_full_reset_impl).
    """
    if not plan or len(plan) < 50:
        return False
    if plan.startswith(("I cannot", "I'm sorry", "Error")):
        return False
    return True


def _merge_consecutive(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages to ensure OpenAI alternation."""
    if not messages:
        return messages
    result = [messages[0]]
    for msg in messages[1:]:
        if msg.get("role") == result[-1].get("role"):
            prev = result[-1]
            prev_content = prev.get("content", "")
            curr_content = msg.get("content", "")
            # Merge content
            if isinstance(prev_content, str) and isinstance(curr_content, str):
                merged_content = prev_content + "\n" + curr_content
            elif isinstance(prev_content, list) and isinstance(curr_content, list):
                merged_content = prev_content + curr_content
            elif isinstance(prev_content, str):
                merged_content = [{"type": "text", "text": prev_content}] + curr_content
            elif isinstance(curr_content, str):
                merged_content = prev_content + [{"type": "text", "text": curr_content}]
            else:
                merged_content = curr_content
            # Merge tool_calls if both have them
            merged = {**prev, "content": merged_content}
            if prev.get("tool_calls") and msg.get("tool_calls"):
                merged["tool_calls"] = prev["tool_calls"] + msg["tool_calls"]
            elif msg.get("tool_calls"):
                merged["tool_calls"] = msg["tool_calls"]
            result[-1] = merged
        else:
            result.append(msg)
    return result


def _compress_full_reset_impl(ctx: CompressionContext, **kwargs) -> tuple[list[dict], str]:
    """Core full reset — LLM generates summary + plan, replaces entire context."""
    client = kwargs.get("client")
    model_name = kwargs.get("model_name")
    extra_kwargs = kwargs.get("extra_kwargs")
    log_file = kwargs.get("log_file")
    max_context_tokens = kwargs.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)
    max_output_tokens = kwargs.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

    original_size = ctx.current_bytes()

    system_msg = ctx.context[0] if ctx.context and ctx.context[0].get("role") == "system" else None

    # Find the last real user message (type='real') — the most recent turn's request
    last_real_user_idx = None
    for i in range(len(ctx.context) - 1, -1, -1):
        if is_real_user_request(ctx.context[i]):
            last_real_user_idx = i
            break

    if last_real_user_idx is None:
        if ctx.verbose:
            error("  :: No real user prompt found! Returning unchanged context.")
        return ctx.context, "FAILED_NO_USER"

    last_real_user_content = _extract_text_from_content(ctx.context[last_real_user_idx].get("content", ""))
    if len(last_real_user_content) > 4000:
        last_real_user_content = last_real_user_content[:4000] + "\n[... truncated ...]"

    # LLM Request 1: summary — include ALL messages after the system prompt
    # (including in-progress work after the last real user message). The
    # summarization instruction is folded into the system prompt to avoid
    # consecutive user messages in the LLM request.
    body = ctx.context[1:] if system_msg else ctx.context
    # Ensure OpenAI alternation: if the first body message is an assistant
    # message (no system prompt to anchor the turn), prepend a minimal user
    # message so the request starts with user.
    if body and body[0].get("role") == "assistant":
        body = [{"role": "user", "content": "(conversation start)"}] + list(body)
    # Cap the body to avoid overflow in the summary call itself.
    # Keep the first 2 messages (context anchor) + last 40 messages (recent work).
    # Truncate individual messages > 4000 chars.
    MAX_BODY_MSGS = 40
    MAX_MSG_CHARS = 4000
    if len(body) > MAX_BODY_MSGS:
        head = body[:2]
        tail = body[-MAX_BODY_MSGS:]
        # Ensure we don't duplicate if context is small
        if len(head) + len(tail) < len(body):
            body = head + [{"role": "user", "content": f"[... {len(body) - len(head) - len(tail)} messages omitted ...]"}] + tail
    # Truncate long messages
    capped_body = []
    for msg in body:
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) > MAX_MSG_CHARS:
            capped_body.append({**msg, "content": content[:MAX_MSG_CHARS] + "\n[... truncated ...]"})
        else:
            capped_body.append(msg)
    body = capped_body
    context_for_summary = (
        [{"role": "system", "content": SUMMARY_PROMPT + "\n\nSummarize everything the agent has done so far."}]
        + _merge_consecutive(body)
    )

    try:
        resp = _invoke_llm_with_retry_compression(
            client, model_name, context_for_summary,
            stream=False, extra_kwargs=extra_kwargs, log_file=log_file,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
        )
        summary = resp.text.strip() if resp.text else ""
        # H11: Validate LLM summary
        if not summary or len(summary) < 50 or summary.startswith(("I cannot", "I'm sorry", "Error")):
        # Handles: None, '', 0, False — all falsy values skip the summary
            summary = ""  # Trigger fallback
        if ctx.verbose:
            _verbose(f"  :: LLM SUMMARY received ({len(summary):,} bytes)")
    except Exception as e:
        if ctx.verbose:
            error(f"  :: LLM SUMMARY FAILED: {e}")
        else:
            logging.warning("full_reset: LLM summary failed: %s", e)  # M-P3
        summary = ""

    if not summary:
        if ctx.verbose:
            error("  :: FULL_RESET FAILED - Summary empty. Returning unchanged context.")
        return ctx.context, "FAILED_EMPTY"

    # LLM Request 2: next steps plan
    context_for_plan = [
        {"role": "system", "content": PLAN_PROMPT},
        {
            "role": "user",
            "content": f"Current state summary:\n\n{summary}\n\nTell me about next steps to finish.",
        },
    ]

    # Validate the plan like the summary is validated (D2); on validation
    # FAILURE retry the LLM call once before falling back to an empty plan.
    MAX_PLAN_ATTEMPTS = 2
    plan = ""
    for attempt in range(MAX_PLAN_ATTEMPTS):
        try:
            resp = _invoke_llm_with_retry_compression(
                client, model_name, context_for_plan,
                stream=False, extra_kwargs=extra_kwargs, log_file=log_file,
                max_context_tokens=max_context_tokens,
                max_output_tokens=max_output_tokens,
            )
            plan = resp.text.strip() if resp.text else ""
        except Exception as e:
            if ctx.verbose:
                error(f"  :: LLM NEXT STEPS FAILED: {e}")
            else:
                logging.warning("full_reset: LLM next steps failed: %s", e)  # M-P3
            plan = ""
        if _validate_plan(plan):
            if ctx.verbose:
                _verbose(f"  :: LLM NEXT STEPS received ({len(plan):,} bytes)")
            break
        # Invalid plan — retry (unless this was the last attempt).
        if attempt < MAX_PLAN_ATTEMPTS - 1 and ctx.verbose:
            _verbose("  :: LLM NEXT STEPS invalid — retrying")
    if not _validate_plan(plan):
        # Still invalid after retries — fall back to empty plan (as before).
        plan = ""

    new_content = f"""# COMPREHENSION SUMMARY

{summary}

# NEXT STEPS & PLAN

{plan}

# CURRENT USER REQUEST

{last_real_user_content}"""

    new_context = []
    if system_msg:
        new_context.append(system_msg)
    # D5 REVERTED (2026-09-12): end on USER (align to conversation_summary's
    # user-end convention). A trailing assistant would make the model prefill
    # instead of answering the pending request.
    new_context.append({"role": "user", "content": new_content})

    # Size guard: if LLM output is not smaller than the original, don't use it
    new_bytes = _calculate_context_bytes(new_context)
    if new_bytes >= original_size:
        if ctx.verbose:
            error(f"  :: FULL_RESET produced no reduction ({new_bytes:,}B >= {original_size:,}B). Returning unchanged context.")
        return ctx.context, "FAILED_NO_REDUCTION"

    msgs_after = len(new_context)
    action_desc = f"full reset: {len(ctx.context)} msgs ({original_size}B) → {msgs_after} msgs ({new_bytes}B)"
    ctx.add_action(action_desc, action_type="full_reset")

    if ctx.verbose:
        _verbose(f"  :: NEW CONTEXT: {len(new_context)} msgs, {new_bytes:,} bytes (from {len(ctx.context)} msgs)")

    ctx.context = new_context
    return ctx.context, "RESET"


compress_full_reset = make_compress_wrapper("FULL_RESET", _compress_full_reset_impl)
