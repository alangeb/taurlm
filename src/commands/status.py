"""Status Command — /status

Display comprehensive agent status information.

Usage:
    /status              — Show full agent status
    /status short        — Show compact one-line status
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauErgon


__all__ = ["run"]


def run(agent: "TauErgon", args: list[str] | None = None) -> str:
    """Execute /status command.

    Args:
        agent: The current TauErgon agent instance.
        args: Command arguments list.

    Returns:
        Empty string (output displayed directly by display functions).
    """
    args = args or []
    from agent_console.display_status import agent_status, print_context_status

    arg_str = " ".join(args).strip().lower() if args else ""

    status = agent.get_status()

    if arg_str == "short":
        print_context_status(status)
    else:
        agent_status(status)

    return ""
