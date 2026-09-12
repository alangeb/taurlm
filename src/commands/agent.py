"""Agent command — /agent for persistent agent management.

Provides interactive agent management via /agent command:
- /agent list — List all persistent agents
- /agent status <name> — Show agent status
- /agent send <name> <message> — Send message to agent
- /agent close <name> — Close agent
- /agent help — Show help

This command works with spawn(persistent=True) agents.
"""

from __future__ import annotations

from rlm.spawn import SpawnRegistry


def run(agent, args: list[str]) -> str:
    """Handle /agent command.

    Args:
        agent: TauErgon agent instance.
        args: Command arguments.

    Returns:
        Response string.
    """
    if not args:
        return _agent_list()

    subcmd = args[0].lower()

    if subcmd in ("help", "?"):
        return _agent_help()
    elif subcmd == "list":
        return _agent_list()
    elif subcmd == "status":
        name = args[1] if len(args) > 1 else None
        return _agent_status(name)
    elif subcmd == "send":
        if len(args) < 3:
            return "Usage: /agent send <name> <message>"
        name = args[1]
        message = " ".join(args[2:])
        return _agent_send(name, message)
    elif subcmd == "close":
        if len(args) < 2:
            return "Usage: /agent close <name>"
        return _agent_close(args[1])
    else:
        return f"Unknown subcommand: {subcmd}\nUse /agent help for usage."


def _agent_help() -> str:
    """Return help text for /agent command."""
    return (
        "AGENT COMMAND — Persistent Agent Management\n"
        "============================================\n\n"
        "  /agent list              — List all persistent agents\n"
        "  /agent status [name]     — Show agent status\n"
        "  /agent send <name> <msg> — Send message to agent\n"
        "  /agent close <name>      — Close agent\n"
        "  /agent help              — Show this help\n\n"
        "Persistent agents are created with spawn(persistent=True).\n"
        "They remain alive between send() calls for multi-turn steering."
    )


def _agent_list() -> str:
    """List all persistent agents."""
    registry = SpawnRegistry()
    agents = registry.list_active()

    if not agents:
        return "No persistent agents."

    lines = [f"PERSISTENT AGENTS ({len(agents)})\n"]
    for agent in agents:
        name = agent.name or agent.spawn_id
        lines.append(f"  {name:<20} status={agent.status:<10} id={agent.spawn_id}")

    return "\n".join(lines)


def _agent_status(name: str | None) -> str:
    """Show agent status."""
    registry = SpawnRegistry()
    agents = registry.list_active()

    if name is None:
        # Show all
        if not agents:
            return "No persistent agents."
        return _agent_list()

    # Find by name
    for handle in agents:
        if handle.name == name:
            info = handle.status_dict()
            lines = [
                f"AGENT STATUS\n",
                f"  Name:    {info.get('name', handle.spawn_id)}",
                f"  ID:      {info['spawn_id']}",
                f"  Status:  {info['status']}",
                f"  Messages: {info.get('message_count', 'N/A')}",
                f"  Context:  {info.get('context_tokens', 'N/A')} tokens ({f"{info['context_percentage']:.1f}" if isinstance(info.get('context_percentage'), (int, float)) else info.get('context_percentage', 'N/A')}%)",
                f"  Idle:    {info.get('idle', 'N/A')}",
            ]
            return "\n".join(lines)

    return f"Agent not found: {name}"


def _agent_send(name: str, message: str) -> str:
    """Send message to persistent agent."""
    registry = SpawnRegistry()
    agents = registry.list_active()

    for handle in agents:
        if handle.name == name:
            if handle.status == "closed":
                return f"Agent {name} is closed. Cannot send messages."
            if handle.status == "completed":
                return f"Agent {name} is completed. Cannot send messages."
            try:
                result = handle.send(message)
                return f"Response from {name}:\n{result}"
            except Exception as e:
                return f"Error sending to {name}: {type(e).__name__}: {e}"

    return f"Agent not found: {name}"


def _agent_close(name: str) -> str:
    """Close persistent agent."""
    registry = SpawnRegistry()
    agents = registry.list_active()

    for handle in agents:
        if handle.name == name:
            handle.close()
            return f"Agent {name} closed."

    return f"Agent not found: {name}"
