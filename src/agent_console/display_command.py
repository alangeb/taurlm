"""Command display functions for TauRLM console.

Handles help display, command listings, and agent card display.
"""
from __future__ import annotations

import sys

from agent_console.primitives import (
    display_info,
    echo,
)

__all__ = [
    "help_display",
    "show_help",
    "show_commands",
    "show_command_help",
    "show_agent_card",
]


# ── Command Display ───────────────────────────────────────────────────────────


def help_display(title: str, width: int, body: str) -> None:
    bar = "=" * width
    display_info(bar)
    display_info(title)
    display_info(bar)
    sys.stdout.write(body)
    # Log to audit
    try:
        from agent_audit_bridge import console_info
        console_info(body)
    except Exception:
        pass


def show_help() -> None:
    """Display help information with all available commands."""
    from commands import COMMANDS, MD_COMMANDS

    # Build command list dynamically from COMMANDS and MD_COMMANDS
    cmd_lines = []
    for name, mod in sorted(COMMANDS.items()):
        desc = (mod.__doc__ or "").split("\n")[0].strip()
        cmd_lines.append(f"  /{name:<16} - {desc}")

    # Add .md commands
    for name in sorted(MD_COMMANDS.keys()):
        # Extract description from frontmatter
        desc = _get_md_description(MD_COMMANDS[name])
        cmd_lines.append(f"  /{name:<16} - {desc}")

    base_text = "HELP\n\nCOMMANDS\n"
    base_text += "\n".join(cmd_lines)
    base_text += "\n\nInput prefixes:\n"
    base_text += "  ! <command>        - Execute bash command\n"
    base_text += "\n"
    base_text += "  +message           - Turn steering (only during active turns):\n"
    base_text += "    +message         - Inject message into running turn\n"
    base_text += "    +stop            - End turn gracefully\n"
    base_text += "    +redirect task   - Clear context, start new task\n"
    base_text += "    +status          - Show diagnostics\n"
    base_text += "\n"
    base_text += "  #                  - Multiline block (supports #! or #+):\n"
    base_text += "    #!               - Start multiline block ('#!' continuation, blank lines end)\n"
    base_text += "    #+               - Turn steering from within multiline block (same as +)\n"

    help_display("HELP", 60, base_text)


def show_commands() -> None:
    """List all available commands including built-in and custom commands."""
    from commands import COMMANDS, MD_COMMANDS

    lines: list[str] = ["AVAILABLE COMMANDS\n"]
    for name, mod in sorted(COMMANDS.items()):
        desc = (mod.__doc__ or "").split("\n")[0].strip()
        lines.append(f"  /{name:<16} - {desc}")

    # Add .md commands
    for name in sorted(MD_COMMANDS.keys()):
        desc = _get_md_description(MD_COMMANDS[name])
        lines.append(f"  /{name:<16} - {desc}")

    output = "\n".join(lines)
    display_info(output)


def _get_md_description(md_path) -> str:
    """Extract description from .md command frontmatter."""
    try:
        content = md_path.read_text()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 2:
                for line in parts[1].strip().split("\n"):
                    if line.startswith("description:"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "Markdown command"


def show_command_help(cmd_name: str) -> None:
    """Display help for a specific command."""
    from commands import COMMANDS

    if cmd_name not in COMMANDS:
        return

    mod = COMMANDS[cmd_name]
    doc = mod.__doc__ or ""
    # Extract usage from docstring (lines starting with "    /")
    usage_lines = [l.strip() for l in doc.split("\n") if l.strip().startswith("/")]

    title = f"/{cmd_name} USAGE"
    lines = [f"{title}\n"]
    if usage_lines:
        lines.append("  Usage:")
        for ul in usage_lines[:5]:  # Limit to 5 usage lines
            lines.append(f"    {ul}")
    lines.append("")

    help_display(title, 60, "\n".join(lines))


def show_agent_card(status: object) -> dict:
    """Generate and return the agent card as a dictionary."""
    agent_card = {
        "name": status.agent_name,
        "description": "A helpful AI assistant with Python REPL access",
        "url": status.base_url,
        "model": status.model_name,
        "capabilities": {
            "repl": True,
            "skills": ["skill"],
            "commands": status.available_commands,
        },
        "context": {
            "messages": status.context_len,
            "tokens": status.token_count,
            "bytes": status.byte_count,
            "max_tokens": status.max_context_tokens,
            "is_exact": status.is_exact,
        },
    }
    return agent_card
