"""Step 8: Conversation Summary — deterministic conversation restructuring.

Condense entire conversation into a single summary user message without LLM calls.
Preserves ALL interaction history in a compact structured format.
Result is always OpenAI-alternation-compliant.
"""

from __future__ import annotations

from agent_message_utils import is_repl_message, is_synthetic_message

__all__ = ["compress_conversation_summary"]

from agent_context_compress.steps.framework import CompressionContext, make_compress_wrapper, _verbose
from agent_context_compress.steps.utils import (
    _calculate_context_bytes,
    _extract_text_from_content,
)

MAX_CONTENT_CHARS = 2000


def _extract_synthetic_category(msg: dict) -> str:
    """Extract category from synthetic message prefix.

    Handles both formats:
    - Legacy: [SYSTEM-SYNTHETIC: CATEGORY] content
    - New: [U:TYPE | N:stack] content (extracts TYPE)
    """
    content = msg.get("content", "")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                break

    # New format: [U:TYPE | N:stack]
    if text.startswith("[U:"):
        end = text.find("|", 3)
        if end > 0:
            return text[3:end].strip()
    # Legacy format: [SYSTEM-SYNTHETIC: CATEGORY]
    prefix = "[SYSTEM-SYNTHETIC: "
    if text.startswith(prefix):
        end = text.find("]", len(prefix))
        if end > 0:
            return text[len(prefix):end]
    return "unknown"


def _compress_conversation_summary_impl(ctx: CompressionContext, **kwargs) -> tuple[list[dict], str]:
    """Core conversation summary — condense entire conversation into a single summary message."""
    if ctx.at_target() or len(ctx.context) <= 2:
        return ctx.context, "NO_REDUCTION"

    original_size = ctx.current_bytes()

    # Extract system message
    system_msg = ctx.context[0] if ctx.context and ctx.context[0].get("role") == "system" else None

    # Build interaction pairs
    interactions: list[str] = []
    current_user_content: str | None = None
    current_assistant_contents: list[str] = []
    current_repl_lines: list[str] = []
    last_real_user_content: str | None = None
    interaction_num = 0

    i = 0
    # Skip system message
    if ctx.context and ctx.context[0].get("role") == "system":
        i = 1

    while i < len(ctx.context):
        msg = ctx.context[i]
        role = msg.get("role", "")

        if role == "user":
            # RLM: REPL output arrives as user messages with type 'repl'.
            # These are NOT synthetic (is_synthetic_message returns False)
            # but are also NOT real user requests — treat as REPL output,
            # not a new interaction.
            if is_repl_message(msg):
                content = _extract_text_from_content(msg.get("content", ""))
                # Strip the [U:repl | N:x] prefix if present
                if content.startswith("[U:"):
                    end = content.find("]")
                    if end > 0:
                        content = content[end + 1:].lstrip()
                if content:
                    # Truncate very long REPL outputs
                    if len(content) > 500:
                        current_repl_lines.append(content[:500] + " [...truncated]")
                    else:
                        current_repl_lines.append(content)
                    if len(current_repl_lines) > 10:
                        current_repl_lines = current_repl_lines[-10:]  # Keep most recent
                i += 1
                continue  # Don't start a new interaction

            # Save previous interaction if exists
            if current_user_content is not None:
                interaction_num += 1
                interaction_lines = [f"### INTERACTION {interaction_num}"]
                # Use the stored content (already extracted or marked as synthetic)
                interaction_lines.append(f"**USER:** {current_user_content}")
                for ac in current_assistant_contents:
                    interaction_lines.append(f"**ASSISTANT:** {ac}")
                if current_repl_lines:
                    interaction_lines.append("**REPL:**\n" + "\n".join(current_repl_lines))
                interactions.append("\n".join(interaction_lines))

            # Start new interaction
            if is_synthetic_message(msg):
                cat = _extract_synthetic_category(msg)
                current_user_content = f"[SYSTEM: {cat}]"
            else:
                current_user_content = _extract_text_from_content(msg.get("content", ""))
                if len(current_user_content) > MAX_CONTENT_CHARS:
                    current_user_content = current_user_content[:MAX_CONTENT_CHARS] + " [...truncated]"
                last_real_user_content = current_user_content
            current_assistant_contents = []
            current_repl_lines = []

        elif role == "assistant":
            content = _extract_text_from_content(msg.get("content", ""))
            if content:
                if len(content) > MAX_CONTENT_CHARS:
                    content = content[:MAX_CONTENT_CHARS] + " [...truncated]"
                current_assistant_contents.append(content)

        elif role == "tool":
            # RLM: REPL output comes as tool messages
            content = _extract_text_from_content(msg.get("content", ""))
            if content:
                # Truncate very long REPL outputs
                if len(content) > 500:
                    current_repl_lines.append(content[:500] + " [...truncated]")
                else:
                    current_repl_lines.append(content)
                if len(current_repl_lines) > 10:
                    current_repl_lines = current_repl_lines[-10:]  # Keep most recent

        i += 1

    # Build the summary content
    summary_parts = ["## CONVERSATION HISTORY\n", "The following is a compressed record of our conversation. Each pair shows a user request and the assistant's response.\n"]

    for interaction in interactions:
        summary_parts.append(interaction + "\n\n")

    # Add CURRENT TASK section
    summary_parts.append("### CURRENT TASK\n")
    if current_user_content is not None:
        # Use the last REAL user message if the current one is synthetic
        task_user = current_user_content
        if task_user.startswith("[SYSTEM:") and last_real_user_content is not None:
            task_user = last_real_user_content
        summary_parts.append(f"**USER:** {task_user}\n")
        for ac in current_assistant_contents:
            summary_parts.append(f"**ASSISTANT:** {ac}\n")
        if current_repl_lines:
            summary_parts.append("**REPL:**\n" + "\n".join(current_repl_lines) + "\n")
        last_role = ctx.context[-1].get("role") if ctx.context else None
        if last_role == "assistant":
            summary_parts.append("**STATUS:** completed\n")
        else:
            summary_parts.append("**STATUS:** in progress — continue the task above\n")
    else:
        summary_parts.append("**USER:** (no user message found)\n")
        summary_parts.append("**STATUS:** unknown\n")

    summary_content = "\n".join(summary_parts)

    # Build result context
    new_context: list[dict] = []
    if system_msg:
        new_context.append(system_msg)
    new_context.append({"role": "user", "content": summary_content})

    # NOTE: No synthetic assistant message is added. The context must end with
    # the USER message so the LLM naturally responds to it. A trailing assistant
    # message would make the LLM think it already responded, causing it to
    # generate a follow-up instead of continuing the user's task.

    new_bytes = _calculate_context_bytes(new_context)
    msgs_after = len(new_context)
    action_desc = f"conversation summary: {len(ctx.context)} msgs ({original_size}B) → {msgs_after} msgs ({new_bytes}B)"
    ctx.add_action(action_desc, action_type="conversation_summary")

    if ctx.verbose:
        _verbose(f"  :: CONVERSATION SUMMARY: {len(ctx.context)} msgs → {msgs_after} msgs, {original_size:,}B → {new_bytes:,}B")

    ctx.context = new_context
    return ctx.context, "SUMMARIZED"


compress_conversation_summary = make_compress_wrapper("CONVERSATION_SUMMARY", _compress_conversation_summary_impl)
