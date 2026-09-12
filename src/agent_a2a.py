"""A2A (Agent-to-Agent) interface for inter-agent communication via Unix domain sockets.

Agents communicate through ``/tmp/taua2a-{PID}.sock`` using JSON messages.

Protocol:
- Agent card request (sync): ``{"type": "agent_card"}`` → agent metadata
- Status request (sync): ``{"type": "status"}`` → running/idle info
- Query request (async): ``{"type": "query", "id": <uuid>, "query": <prompt>}`` → ``{"type": "queued", "id": <request_id>}`` → ``{"type": "response", "id": <request_id>, "response": <result>}``

Key components:
- A2AServer: Handles incoming connections (agent_card, status, query) in a daemon thread
- connect_to_agent, get_agent_card, query_agent, list_agents: Client utilities
"""

from __future__ import annotations

# Re-export the focused A2A modules
from a2a_transport import connect_to_agent
from a2a_discovery import get_agent_card, list_agents, query_agent
from a2a_sessions import _list_sessions_json, _scan_sessions
from a2a_server import A2AServer
from a2a_cli import a2a_cli_mode

__all__ = [
    "A2AServer",
    "connect_to_agent",
    "get_agent_card",
    "list_agents",
    "query_agent",
    "a2a_cli_mode",
]
