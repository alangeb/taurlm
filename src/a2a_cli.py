"""A2A CLI: command-line interface for agent listing, card retrieval, and queries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from a2a_discovery import get_agent_card, list_agents, query_agent
from a2a_transport import connect_to_agent
from a2a_sessions import _list_sessions_json
from agent_console import a2a_cli_error, agent_a2a_response, agent_card_json, agent_status_message, agents_json
from agent_console.primitives import display_info


def __agents_table_header() -> None:
    header = f"{'PID':<6} {'Status':<10} {'Tools':<6} {'Model':<25} {'Name':<15} {'Working Dir':<35}"
    display_info(header)
    display_info("-" * 105)


def _agents_table_row(agent: dict) -> None:
    row = (
        f"{agent['pid']:<6} "
        f"{agent['status']:<10} "
        f"{agent.get('tools_count', 'N/A'):<6} "
        f"{agent.get('model', 'N/A'):<25} "
        f"{agent.get('name', 'Unknown'):<15} "
        f"{agent.get('working_dir', 'N/A'):<35}"
    )
    display_info(row)


def _filter_active_agents(agents: list) -> list:
    """Return only agents with status 'active'."""
    return [a for a in agents if a.get("status") == "active"]


def _empty_agents_message(include_all: bool) -> str:
    """Return the appropriate empty-agents message."""
    return "No agents found." if include_all else "No active agents found."


def _print_agents_table(agents: list, include_all: bool = False) -> None:
    """Display agents in a formatted table (active only unless *include_all*)."""
    if not include_all:
        agents = _filter_active_agents(agents)
    if not agents:
        agent_status_message(_empty_agents_message(include_all))
        return
    __agents_table_header()
    for agent in agents:
        _agents_table_row(agent)


def _print_agents_json(agents: list, include_all: bool = False) -> None:
    """Display agents as JSON."""
    if not include_all:
        agents = _filter_active_agents(agents)
    if not agents:
        agent_status_message(_empty_agents_message(include_all))
        return
    agents_json(json.dumps(agents, indent=2))


_A2A_CLI_ERRORS = (FileNotFoundError, ConnectionError, TimeoutError, RuntimeError)


def _cli_connect_and_execute(pid: int, action):
    """Connect to agent, run action(client), close, handle errors."""
    try:
        client = connect_to_agent(pid)
        action(client)
        client.close()
    except _A2A_CLI_ERRORS as e:
        a2a_cli_error(f"Error: {e}")
        sys.exit(1)
    sys.exit(0)


def _action_card(c):
    """Fetch and display the agent card."""
    agent_card_json(json.dumps(get_agent_card(c), indent=2))


def _handle_cli_card(pid: int):
    """Fetch and display the agent card for the specified PID, then exit."""
    _cli_connect_and_execute(pid, _action_card)


def _action_query(c, query: str, idle_timeout: float):
    """Send a query and display the response."""
    resp = query_agent(c, query, idle_timeout).get("response")
    if resp is None:
        a2a_cli_error("Error: no response received")
        sys.exit(1)
    agent_a2a_response(resp)


def _handle_cli_query(pid: int, query: str, idle_timeout: float):
    """Send a query to the specified agent, display the response, then exit."""
    _cli_connect_and_execute(pid, lambda c: _action_query(c, query, idle_timeout))


_A2A_LIST_FLAGS = [
    ("list", False, False),
    ("list_all", False, True),
    ("listjson", True, False),
    ("listjson_all", True, True),
]


def a2a_cli_mode(args) -> None:
    """Handle A2A CLI mode: list agents, show card, or send query. Exits after."""
    # List mode (table or JSON, active or all)
    for list_flag, json_flag, include_all in _A2A_LIST_FLAGS:
        if getattr(args, list_flag):
            agents = list_agents()
            printer = _print_agents_json if json_flag else _print_agents_table
            printer(agents, include_all=include_all)
            sys.exit(0)

    # Session metadata mode (--list-sessions): scan LOG_DIR for all sessions.
    if getattr(args, "list_sessions", False):
        _list_sessions_json()

    # Resolve target PID (by --pid or --name)
    target_pid = args.pid
    if args.name and not args.pid:
        agents = list_agents()
        found = next((a for a in agents if a.get("name") == args.name), None)
        if not found:
            a2a_cli_error(f"Error: Agent with name '{args.name}' not found.")
            sys.exit(1)
        target_pid = found["pid"]

    # Card requires a target (resolved from --pid or --name)
    if args.card and not target_pid:
        a2a_cli_error("Error: --card requires --pid or --name")
        sys.exit(1)

    # Execute card or query
    if target_pid:
        query_value = args.inputs[0] if args.inputs else None
        if args.card:
            _handle_cli_card(target_pid)
        if query_value is not None:
            _handle_cli_query(target_pid, query_value, args.timeout)
        # B10: --pid/--name given but neither card nor query requested —
        # never fall through and START an agent.
        a2a_cli_error("Error: --pid/--name requires --card or a query argument")
        sys.exit(1)
    else:
        # B10: --card was already validated above; reaching here means a bare
        # --name resolved to nothing usable. Do not start an agent.
        a2a_cli_error("Error: no target agent resolved (--pid or --name required)")
        sys.exit(1)
