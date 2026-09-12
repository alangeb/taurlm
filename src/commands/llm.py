"""LLM command — /llm

Show or switch the active LLM group.

Usage:
    /llm              — Show current LLM group
    /llm help         — Show this help
    /llm list         — Show all available LLM groups
    /llm <name>       — Switch to the named LLM group
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from agent_core import TauErgon


def run(agent: "TauErgon", args: List[str] | None = None) -> str:
    """Run the /llm command."""
    args = args or []
    parts = [a.strip() for a in args if a.strip()]

    if not parts:
        return _show_current(agent)

    if parts[0].lower() == "help":
        return (
            "LLM COMMANDS\n"
            "  /llm              — Show current LLM group\n"
            "  /llm help         — Show this help\n"
            "  /llm list         — Show all available LLM groups\n"
            "  /llm <name>       — Switch to the named LLM group\n"
        )

    if parts[0].lower() == "list":
        return _list_groups(agent)

    return _switch_group(agent, parts[0])


def _show_current(agent: "TauErgon") -> str:
    """Show the current LLM group info."""
    name = agent.current_group_name
    group = agent.llm_groups.get(name)
    if not group:
        return f"Unknown LLM group: {name}"
    return (
        f"LLM: {name} | model: {group.model} | api: {group.api_base} "
        f"| max_tokens: {group.max_tokens} | ctx: {group.max_context_tokens}"
    )


def _list_groups(agent: "TauErgon") -> str:
    """List all available LLM groups."""
    if not agent.llm_groups:
        return "No LLM groups configured."

    lines = []
    lines.append(f"AVAILABLE LLM GROUPS ({len(agent.llm_groups)})")
    lines.append("  Name         Model    API Base                MaxTok   Ctx")
    lines.append("  -----------  -------  ----------------------  -------  ------")
    for name, group in agent.llm_groups.items():
        marker = " <- current" if name == agent.current_group_name else ""
        lines.append(
            f"  {name:<12}  {group.model:<8}  {group.api_base:<20}  "
            f"{group.max_tokens:<7}  {group.max_context_tokens}{marker}"
        )
    lines.append("")
    lines.append("  Use /llm <name> to switch.")
    return "\n".join(lines)


def _switch_group(agent: "TauErgon", name: str) -> str:
    """Switch to the named LLM group."""
    if name not in agent.llm_groups:
        available = ", ".join(agent.llm_groups.keys())
        return f"Unknown LLM group: {name}\n  Available: {available}"

    if name == agent.current_group_name:
        return f"Already on group: {name}"

    old_name = agent.current_group_name
    agent.current_group_name = name
    try:
        agent._rebuild_client(clear_overrides=True)
    except ValueError as e:
        agent.current_group_name = old_name
        return f"Failed to switch to {name!r}: {e}"

    group = agent.llm_groups[name]
    return f"Switched to LLM group: {name} (model: {group.model}, api: {group.api_base})"
