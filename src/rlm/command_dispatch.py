"""RLM Command Dispatch — Slash command handling.

This module provides command dispatch functionality for RLM mode.
It handles slash commands by routing them to the appropriate
command modules in commands/*.py and command files in commands/*.md.

Key Functions
-------------
handle_command(agent, cmd_name, cmd_full) — Dispatch a slash command
get_available_commands() — Discover available commands
strip_frontmatter(content) — Remove YAML frontmatter from .md files
substitute_placeholders(content, args) — Replace $1, $2, $*, $1+
parse_multi_prompt(content, args) — Split on ---, substitute placeholders
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent_core import TauErgon
    from agent_models import InputMessage

__all__ = [
    "handle_command",
    "get_available_commands",
    "strip_frontmatter",
    "substitute_placeholders",
    "parse_multi_prompt",
]

# Maximum recursion depth for .md command dispatch.
# NOTE: Recursion limit enforcement is tested via sanity.sh (Test 8) which
# exercises the full dispatch pipeline. Unit tests for MAX_MD_RECURSION are
# intentionally omitted — the limit is a safety guard, not core functionality.
# The try/finally depth counter in agent_core._dispatch_md() guarantees correctness.
MAX_MD_RECURSION = 5


# ─── Markdown command helpers ─────────────────────────────────────────────


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from .md command content."""
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:  # L-Pa7: no closing ---, skip
        return content
    if len(parts) >= 3:
        return parts[2].strip()
    return content


def substitute_placeholders(content: str, args: list[str]) -> str:
    """Replace $N, $N+, and $* placeholders with argument values.

    Uses word-boundary-aware replacement to avoid substring collisions
    (e.g., $10 is not corrupted by $1 replacement).
    """
    if not args:
        args = []

    # PB1: SINGLE-PASS substitution over the ORIGINAL string. The previous
    # implementation ran several re.sub passes over the accumulated ``result``,
    # so a value substituted by an earlier pass (e.g. an arg whose value itself
    # contains "$*" or "$1") was RE-expanded by a later pass — cross-arg
    # injection. A single pass with one callback never re-scans substituted text.
    all_args = " ".join(args)

    def _repl(m: "re.Match[str]") -> str:
        rng, pos = m.group(1), m.group(2)
        if rng is not None:  # $N+ range
            i = int(rng)
            return " ".join(args[i - 1:]) if i <= len(args) else ""
        if pos is not None:  # $N positional (negative lookahead already applied)
            i = int(pos)
            return args[i - 1] if i <= len(args) else ""
        return all_args  # $* collective

    # One alternation: $N+ first, then $N (not followed by another digit), then $*.
    return re.sub(r"\$(\d+)\+|\$(\d+)(?!\d)|\$\*", _repl, content)


def _substitute_dynamic_placeholders(content: str) -> str:
    """Replace ${time}, ${date}, ${datetime} with current values."""
    now = datetime.now()
    for placeholder, formatter in [
        ("${time}", lambda n: n.strftime("%H:%M:%S")),
        ("${date}", lambda n: n.strftime("%A, %B %d, %Y")),
        ("${datetime}", lambda n: n.strftime("%Y-%m-%d %H:%M:%S")),
    ]:
        content = content.replace(placeholder, formatter(now))
    return content


def parse_multi_prompt(content: str, args: list[str]) -> list[str]:
    """Split content on --- delimiters and substitute placeholders in each part."""
    if not content.strip():
        return []

    parts = re.split(r'^\s*---\s*$', content, flags=re.MULTILINE)
    parts = [part for part in parts if part.strip()]

    return [
        substitute_placeholders(
            _substitute_dynamic_placeholders(part.strip()), args
        )
        for part in parts
    ]


# ─── Help ──────────────────────────────────────────────────────────────────


def _show_help() -> None:
    """Display help information with all available commands."""
    from agent_console.display_command import show_help
    show_help()


def get_available_commands() -> dict[str, Any]:
    """Discover and return available markdown commands dynamically.

    Returns:
        dict: Mapping of command names to CommandInfo objects.
    """
    try:
        from commands import COMMANDS
        return COMMANDS
    except ImportError:
        return {}


def handle_command(
    agent: "TauErgon",
    cmd_name: str,
    cmd_full: str,
    msg: Optional["InputMessage"] = None,
) -> None:
    """Dispatch a slash command to the appropriate handler.

    In RLM mode, commands are handled via commands/*.py modules directly.

    Args:
        agent: TauErgon instance.
        cmd_name: Command name without slash (e.g., "goal").
        cmd_full: Full command string (e.g., "/goal set Test").
        msg: Optional input message.
    """
    from agent_audit_bridge import console_info
    from agent_console.primitives import echo, display_error
    import difflib  # L-Pa6
    from commands import COMMANDS
    _sugg = [s for s in difflib.get_close_matches(cmd_name, list(COMMANDS.keys()), n=3) if s != cmd_name]
    if _sugg:
        display_error('Did you mean: ' + ', '.join(_sugg) + '?')
    from agent_console import unknown_command_error

    # Log command invocation
    try:
        console_info(f"COMMAND_INVOKE: /{cmd_name} {cmd_full}")
    except Exception:
        pass  # Audit failures must never suppress command execution

    # Help aliases: /, /help, /?
    if cmd_full in ("", "/") or cmd_name in ("help", "?"):
        _show_help()
        try:
            console_info("COMMAND_RESULT: help displayed")
        except Exception:
            pass
        return

    # RLM mode: use commands/*.py directly
    try:
        from commands import COMMANDS

        if cmd_name in COMMANDS:
            cmd_module = COMMANDS[cmd_name]
            cmd_body = cmd_full.lstrip("/").strip()
            args = cmd_body[len(cmd_name):].strip() if len(cmd_body) > len(cmd_name) else ""
            arg_list = args.split() if args else []
            if cmd_name in ("goal", "refine", "autonomous"):
                result = cmd_module.run(args)
            else:
                result = cmd_module.run(agent, arg_list)
            if result:
                echo(result)
            try:
                console_info(f"COMMAND_RESULT: /{cmd_name} completed")
            except Exception:
                pass
            return
    except ImportError:
        pass
    except Exception as e:
        import traceback as _tb
        _tb_short = _tb.format_exc().strip().split(chr(10))[-1]
        display_error(f"Command error: {e} | {_tb_short}")
        try:
            console_info(f"COMMAND_ERROR: /{cmd_name} failed: {e}")
        except Exception:
            pass
        return

    unknown_command_error(cmd_name)
    try:
        console_info(f"COMMAND_UNKNOWN: /{cmd_name}")
    except Exception:
        pass
