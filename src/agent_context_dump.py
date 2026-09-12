"""Context dump/formatting functions extracted from TauContext.

Pure presentation functions — no state mutation. Each takes a TauContext
instance and returns a formatted string.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_context import TauContext

from agent_console import _role_color
from agent_models import Colors


def dump_context(
    ctx: "TauContext",
    mode: str = "summary",
    max_tokens: int = 200000,
    exact_tokens: int | None = None,
) -> str:
    """Dump the context as a formatted string for display or debugging.

    Modes: summary, full, user, assistant, short.
    """
    valid_modes = {"summary", "full", "user", "assistant", "short"}
    if mode not in valid_modes:
        modes_str = ", ".join(sorted(valid_modes))
        return (
            f"{Colors.RED}Invalid mode '{mode}'. "
            f"Valid modes: {modes_str}{Colors.RESET}"
        )

    if mode == "summary":
        return _dump_summary(ctx, max_tokens, exact_tokens)
    if mode == "short":
        return _dump_short(ctx)
    return _dump_detail(ctx, mode)


def dump_last(ctx: "TauContext", role: str | None = None) -> str:
    """Dump the last message, optionally filtered by role.

    Args:
        role: If set, find last message with this role (searching backward).

    Returns:
        Formatted string with the last matching message.
    """
    msgs = ctx.messages
    if not msgs:
        return f"{Colors.YELLOW}Context is empty.{Colors.RESET}"

    if role:
        # Search backward for last message with matching role
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            if msg.get("role") == role:
                return format_single_message(ctx, msg, show_index=True, msg_index=i)
        return f"{Colors.YELLOW}No '{role}' messages in context.{Colors.RESET}"

    # No filter: show last message
    return format_single_message(ctx, msgs[-1], show_index=True, msg_index=len(msgs) - 1)


def format_single_message(ctx: "TauContext", msg: dict, show_index: bool = False, msg_index: int | None = None) -> str:
    """Format a single message for display with role-based coloring."""
    role = msg.get("role", "unknown")
    content = msg.get("content") or ""

    # Handle list content (e.g. multimodal)
    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        content = "\n".join(text_parts) if text_parts else "(non-text content)"

    # Role-based coloring: assistant=green, everything else=white
    color = Colors.GREEN if role == "assistant" else Colors.WHITE
    role_label = role.upper()

    lines = []
    if show_index:
        if msg_index is not None:
            idx = msg_index + 1
            total = len(ctx.messages)
            lines.append(f"{Colors.CYAN}--- Message {idx}/{total} [{role_label}]{Colors.RESET}")
        else:
            lines.append(f"{Colors.CYAN}--- [{role_label}]{Colors.RESET}")
    else:
        lines.append(f"{Colors.CYAN}--- [{role_label}]{Colors.RESET}")

    lines.append(f"{color}{content}{Colors.RESET}")
    return "\n".join(lines)


def _dump_short(ctx: "TauContext") -> str:
    """Compact one-line-per-message view. 80 chars max, newlines collapsed."""
    lines = [f"\n{Colors.CYAN}CONTEXT SHORT ({len(ctx.messages)} messages){Colors.RESET}"]

    for i, msg in enumerate(ctx.messages):
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""

        # Handle list content
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = " ".join(text_parts) if text_parts else "(non-text)"

        # Collapse newlines, truncate to 80 chars
        collapsed = content.replace("\n", "\u2502").replace("\r", "")
        if len(collapsed) > 80:
            collapsed = collapsed[:77] + "..."

        color = _role_color(role)
        lines.append(f"{color}{i+1}. [{role.upper():9s}] {collapsed}{Colors.RESET}")

    return "\n".join(lines)


def _dump_summary(
    ctx: "TauContext",
    max_tokens: int = 200000,
    exact_tokens: int | None = None,
) -> str:
    """Generate a compact summary of the context with truncated content and usage stats."""
    lines = []
    token_count, percentage, byte_count, is_exact = ctx.get_usage_stats(
        max_tokens, exact_tokens
    )
    token_display = f"{token_count:,}" if is_exact else f"~{token_count:,}"

    sep = "=" * 60
    lines.append(f"\n{sep}")
    lines.append(f"CONTEXT SUMMARY ({len(ctx)} messages)")
    lines.append(
        f"Tokens: {token_display} ({percentage:.1%} of {max_tokens:,} max)"
    )
    lines.append(f"Bytes: {byte_count:,}")
    lines.append(f"{sep}")
    lines.append("")

    for i, msg in enumerate(ctx.messages):
        role = msg.get("role", "unknown")
        content_str = _format_content_summary(msg.get("content") or "")
        if len(content_str) > 100:
            content_str = content_str[:100] + "..."
        tool_calls = msg.get("tool_calls")
        tool_info = ""
        if tool_calls:
            tool_names = [
                tc.get("function", {}).get("name", "?") for tc in tool_calls
            ]
            tool_info = f" [tools: {', '.join(tool_names)}]"
        color = _role_color(role)
        lines.append(
            f"{color}{i + 1}. [{role}]{tool_info} {content_str}{Colors.RESET}"
        )
    lines.append(Colors.RESET)
    return "\n".join(lines)


def _dump_detail(ctx: "TauContext", mode: str) -> str:
    """Generate a detailed view of the context filtered by message role."""
    role_map = {
        "user": ("user",),
        "assistant": ("assistant",),
    }
    target = role_map.get(mode)
    if target:
        messages = [
            (i, m) for i, m in enumerate(ctx.messages)
            if m.get("role") in target
        ]
        title = f"{mode.upper()} MESSAGES ONLY ({len(messages)} messages)"
    else:
        messages = list(enumerate(ctx.messages))
        title = f"CONTEXT ({len(messages)} messages)"

    lines = [f"\n{Colors.CYAN}{title}{Colors.RESET}"]
    for idx, (_, msg) in enumerate(messages):
        role = msg.get("role", "unknown")
        content = str(msg.get("content") or "(none)")

        # Apply role-based coloring: assistant=green, everything else=white
        color = Colors.GREEN if role == "assistant" else Colors.WHITE

        lines.append(f"\n{Colors.CYAN}--- Message {idx + 1} [{role.upper()}]---{Colors.RESET}")
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            tc_lines = []
            for j, tc in enumerate(tool_calls):
                name = tc.get("function", {}).get("name", "?")
                args = tc.get("function", {}).get("arguments", "")
                args_display = args if mode == "full" else (
                    args[:100] + ("..." if len(str(args)) > 100 else "")
                )
                tc_lines.append(
                    f"    {j + 1}. {name}({args_display})"
                )
            tc_str = "".join(tc_lines)
            lines.append(
                f"{color}{content}{Colors.CYAN}\n{tc_str}{Colors.RESET}"
            )
        else:
            lines.append(f"{color}{content}{Colors.RESET}")
    lines.append(Colors.RESET)
    return "\n".join(lines)


def _dump_trace(ctx: "TauContext") -> str:
    """Generate a debug trace view of the context with detailed formatting."""
    lines = []
    reset = Colors.RESET
    white = reset  # SYST, USER
    green = Colors.GREEN  # ASSI
    cyan = Colors.CYAN  # API call blocks
    yellow = Colors.YELLOW  # Pending indicator

    total = len(ctx.messages)
    width = max(3, len(str(total)))

    sep = "=" * 60
    dash = "\u2500" * 60
    lines.append(f"\n{white}{sep}{reset}")
    lines.append(f"{white}CONTEXT TRACE (full){reset}")
    lines.append(f"{white}{sep}{reset}")

    # Show validation errors
    errors = ctx.validate()
    if errors:
        lines.append(f"{Colors.RED}VALIDATION ERRORS ({len(errors)}):{reset}")
        for err in errors:
            lines.append(f"  {Colors.RED}- {err}{reset}")

    lines.append(f"{dash}")

    # Build api_id -> api result lookup (for inline linking)
    tool_id_to_result = {}
    for msg in ctx.messages:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id", "")
            result_content = msg.get("content", "")
            tool_id_to_result[tid] = _truncate(str(result_content))

    # Show ALL messages from start to end
    for idx, msg in enumerate(ctx.messages):
        role = msg.get("role", "unknown")
        content_str = _format_content_summary(msg.get("content") or "")

        # Format message number: right-aligned, 3 characters minimum
        num_str = f"{idx + 1:>{width}}"

        if role == "system":
            lines.append(f"\n{white}{num_str} [SYST] {_truncate(content_str)}{reset}")

        elif role == "user":
            lines.append(f"\n{white}{num_str} [USER] {_truncate(content_str)}{reset}")

        elif role == "assistant":
            clean = _truncate(content_str)

            tc = msg.get("tool_calls")
            if tc:
                lines.append(f"\n{green}{num_str} [ASSI] {clean}{reset}")

                for tool_call in tc:
                    tc_id = tool_call.get("id", "NO-ID")
                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "unknown")
                    tool_args = func.get("arguments", "")

                    params = []
                    if tool_args:
                        try:
                            args_dict = json.loads(tool_args) if tool_args else {}
                            for k, v in args_dict.items():
                                v_str = str(v)[:80]
                                params.append(f"{k}={v_str}")
                        except (
                            json.JSONDecodeError,
                            ValueError,
                            TypeError,
                            KeyError,
                        ):
                            params.append(tool_args[:80])

                    param_str = ", ".join(params) if params else tool_args[:80]

                    lines.append(f"{cyan}\u2514\u2500 [{tool_name}] id={tc_id}{reset}")
                    if param_str:
                        lines.append(f"{cyan}    {param_str}{reset}")

                    if tc_id in tool_id_to_result:
                        result_preview = tool_id_to_result[tc_id]
                        lines.append(f"{cyan}    \u2192 result: {result_preview}{reset}")
                    else:
                        lines.append(f"{yellow}    \u2192 result: PENDING{reset}")
            else:
                lines.append(f"\n{green}{num_str} [ASSI] {clean}{reset}")

        elif role == "tool":
            tc_id = msg.get("tool_call_id", "NO-ID")
            lines.append(f"\n{cyan}{num_str} [TOOL] id={tc_id}{reset}")
            lines.append(f"{cyan}    {_truncate(content_str)}{reset}")

        else:
            lines.append(f"\n{white}{num_str} [{role.upper()}] {_truncate(content_str)}{reset}")

    lines.append(f"\n{dash}")
    lines.append(f"{white}END TRACE{reset}")
    return "\n".join(lines)


# --- Helpers (moved from TauContext staticmethods) ---

def _format_content_summary(content: Any) -> str:
    """Format message content for summary display (handles multimodal)."""
    if isinstance(content, list):
        image_count = sum(1 for p in content if p.get("type") == "image_url")
        text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        s = f"[{image_count} image(s), {len(text_parts)} text block(s)]"
        if text_parts:
            s += ": " + text_parts[0][:80]
        return s
    return str(content)


def _truncate(s: str, max_len: int = 120) -> str:
    """Replace newlines and truncate to max_len."""
    clean = s.replace("\n", " ").replace("\r", " ")
    return clean if len(clean) <= max_len else clean[:max_len] + "..."
