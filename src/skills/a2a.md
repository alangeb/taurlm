---
name: a2a
description: 'Agent-to-Agent (A2A) protocol: discovery, transport, sessions, querying other agents. Use for finding running agents, sending prompts to other agent instances, checking agent status, managing multi-agent communication. Keywords: a2a, agent-to-agent, multi-agent, discovery, socket, transport, query, agent card, pid, session.'
category: infrastructure
keywords: 'a2a, agent-to-agent, multi-agent, discovery, socket, transport, query, agent card, pid, session'
---

# Agent-to-Agent (A2A) Protocol

## When to Use
- Querying another running agent instance
- Discovering available agents on the system
- Checking agent health/status
- Building multi-agent workflows

## Architecture

```
a2a_server.py      - Server: accepts connections, builds agent cards, handles queries
a2a_discovery.py   - Client: list_agents(), query_agent(), get_agent_card()
a2a_transport.py   - Low-level: connect_to_agent(pid), socket I/O, JSON stream
a2a_sessions.py    - Session scanning: _scan_sessions(), _list_sessions_json()
a2a_cli.py         - CLI entry point
agent_a2a.py       - Agent-side A2A integration
```

## Discovering Agents

```python
from a2a_discovery import list_agents

agents = list_agents()
for a in agents:
    print(f"PID {a['pid']}: {a.get('status', 'unknown')}")
```

## Querying an Agent

```python
from a2a_discovery import query_agent
from a2a_transport import connect_to_agent

client = connect_to_agent(pid=12345, timeout=5.0)
response = query_agent(client, "Summarize the last 5 commits")
print(response)
client.close()
```

## Agent Card

```python
from a2a_discovery import get_agent_card
from a2a_transport import connect_to_agent

client = connect_to_agent(pid=12345)
card = get_agent_card(client)
print(card)  # name, capabilities, version
```

## Key Points
- **Transport is Unix socket** - local-only by design
- **PID-based addressing** - connect to agent by process ID
- **Heartbeat idle timeout** — `HEARTBEAT_IDLE_TIMEOUT = 30.0`s: client gives up only if NO heartbeat for 30s, not on total query duration (a2a_transport.py:20); override per-call via `query_agent(client, prompt, idle_timeout=...)`
- `query_agent` returns a **dict** (parsed response), not a string (a2a_discovery.py:62)
- **Agent card** - sent only on request (`get_agent_card`, a2a_discovery.py:13; server dispatch a2a_server.py:142), not on connect
- **Imports are top-level** (`from a2a_discovery import ...`) — modules sit at `src/` root, so `PYTHONPATH=src`
- **JSON stream protocol** - responses are newline-delimited JSON
- **`list_agents()` returns DEAD sockets too** — it globs `/tmp/taua2a-*.sock` and includes `status` of `stale`/`unreachable` for dead PIDs (a2a_discovery.py:108-110, _get_agent_status:84-92). Filter `status=='active'` before querying.
- **`connect_to_agent` RAISES, never returns None** — `FileNotFoundError` if the socket file is gone, `ConnectionError` if stale/unresponsive (a2a_transport.py:52-58).

## Related Skills
- `spawn` - in-process delegation (preferred over A2A for subtasks)
- `wiki` - persistent knowledge shared across agents
- `context` - .context file format for reading session state directly
