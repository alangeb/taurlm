"""Wiki Command — /wiki

Persistent knowledge store commands.

Usage:
    /wiki                    — Show status + index
    /wiki search <query>     — Search wiki
    /wiki add <topic> <content> — Add entry
    /wiki get <topic>        — Retrieve latest for topic
    /wiki index              — Show index
    /wiki log <text>         — Append to log
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauErgon

__all__ = ["run"]


def run(agent: "TauErgon", args: list[str] | None = None) -> str:
    """Execute /wiki command."""
    args = args or []
    import wiki

    if not args:
        return wiki.status() + "\n\n" + wiki.idx()
    sub = args[0].lower()
    rest = " ".join(args[1:])
    if sub == "search" and rest:
        return wiki.search(rest)
    if sub == "add" and rest:
        parts = rest.split(" ", 1)
        topic = parts[0]
        content = parts[1] if len(parts) > 1 else ""
        return wiki.add(topic, content)
    if sub == "get" and rest:
        return wiki.retrieve(rest)
    if sub == "index":
        return wiki.idx()
    if sub == "log" and rest:
        return wiki.log(rest)
    if sub == "help":
        return (
            "WIKI COMMANDS\n"
            "  /wiki                    - Status + index\n"
            "  /wiki search <query>     - Search all wiki content\n"
            "  /wiki add <topic> <text> - Add entry to topic\n"
            "  /wiki get <topic>        - Retrieve latest for topic\n"
            "  /wiki index              - Show INDEX.md\n"
            "  /wiki log <text>         - Append to log.md\n"
            "  /wiki help               - This help"
        )
    return f"Usage: /wiki [search|add|get|index|log|help]"
