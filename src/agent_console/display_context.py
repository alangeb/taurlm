"""Context display functions for TauErgon console.

Handles context validation warnings, context lists, previews, and dumps.
"""
from __future__ import annotations

import sys

from agent_console.primitives import (
    _role_color,
    blank_line,
    display_error,
    display_info,
)
from agent_console.audit import _log_audit, display_warn_audit as display_warn


__all__ = [
    "context_validation_warning",
    "context_append_warning",
    "context_list_display",
    "context_preview_display",
    "context_validation_display",
    "context_dump_with_json",
]


# ── Context Display ───────────────────────────────────────────────────────────


def context_validation_warning(error_lines: list[str]) -> None:
    sep = "=" * 60
    display_error(sep)
    display_error("⚠️  CRITICAL CONTEXT VALIDATION WARNING")
    display_error(sep)
    for line in error_lines:
        display_error(f"  {line}")
    display_error(sep)
    audit_lines = ["CRITICAL CONTEXT VALIDATION WARNING"]
    audit_lines.extend(error_lines)
    _log_audit("error", " | ".join(audit_lines))


def context_append_warning(errors: list[str]) -> None:
    display_error("⚠ Context validation warning:")
    for err in errors:
        display_error(f"  - {err}")
    for err in errors:
        _log_audit("warning", f"Context validation: {err}")


def context_list_display(contexts: list[dict]) -> None:
    if not contexts:
        display_warn("[No context files found]")
        return
    display_info("Available context files (use /continue <n> to load):")
    header = f"{'ID':<5}{'Age':<10}{'Msgs':<6}{'File':<45}Last User Message"
    display_info(header)
    sep = f"{'─'*4:<5}{'─'*9:<10}{'─'*5:<6}{'─'*44:<45}{'─'*40}"
    display_info(sep)
    for ctx in contexts:
        last_user = ctx.get("last_user", "") or "(no user message)"
        name = ctx.get('name', 'unknown')
        if len(name) > 42:
            name = name[:39] + "..."
        line = (
            f"{ctx.get('id', '?'):<5}"
            f"{ctx.get('age', '?'):<10}"
            f"{ctx.get('msg_count', '?'):<6}"
            f"{name:<45}"
            f"{last_user}\n"
        )
        sys.stdout.write(line)
        try:
            from agent_audit_bridge import console_info
            console_info(line.rstrip())
        except Exception:
            pass
    sys.stdout.write("\n")
    try:
        from agent_audit_bridge import console_info
        console_info("")
    except Exception:
        pass


def context_preview_display(context_file: str, messages: list[dict]) -> None:
    from agent_models import Colors

    if not messages:
        display_warn(f"[Preview empty or unreadable: {context_file}]")
        return
    display_info(f"Preview of {context_file} (last {len(messages)} messages):")
    display_info("─" * 60)
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            image_count = sum(1 for p in content if p.get("type") == "image_url")
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = f"[{image_count} image(s)]"
            if text_parts:
                content += ": " + text_parts[0][:200]
        color = _role_color(role)
        line = f"{color}[{role.upper()}] {content}{Colors.RESET}\n"
        sys.stdout.write(line)
        try:
            from agent_audit_bridge import console_info
            console_info(f"[{role.upper()}] {content}")
        except Exception:
            pass
    sys.stdout.write("\n")
    try:
        from agent_audit_bridge import console_info
        console_info("")
    except Exception:
        pass


def context_validation_display(errors: list[str], context_len: int | None = None,
                                last_role: str | None = None) -> None:
    display_error("Context validation errors detected:")
    if context_len is not None or last_role is not None:
        display_error(f"  Context state: {context_len} messages, last role: {last_role}")
    for error in errors:
        display_error(f"  {error}")
    blank_line()
    state = ""
    if context_len is not None and last_role is not None:
        state = f" ({context_len} msgs, last={last_role}): {len(errors)} error(s)"
    _log_audit("warning", f"Context validation{state}")
    for err in errors:
        _log_audit("warning", f"Context validation: {err}")


def context_dump_with_json(json_str: str) -> None:
    sys.stdout.write(json_str)
    try:
        from agent_audit_bridge import console_info
        console_info(json_str)
    except Exception:
        pass
