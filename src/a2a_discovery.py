"""A2A discovery: agent card retrieval, query protocol, and agent listing."""

from __future__ import annotations

import json
import socket
import time
import uuid
from pathlib import Path

from a2a_transport import (DEFAULT_ACK_TIMEOUT, DEFAULT_CONNECT_TIMEOUT, HEARTBEAT_IDLE_TIMEOUT, SOCKET_BUFFER, _make_connection, _read_json_response, _verify_socket)

def get_agent_card(client: socket.socket) -> dict:
    """Fetch the agent card from a connected socket (synchronous)."""
    client.send(json.dumps({"type": "agent_card"}).encode())
    data, _ = _read_json_response(client, DEFAULT_ACK_TIMEOUT)
    if data.get("type") == "agent_card":
        return data
    raise RuntimeError(f"Unexpected response: {data}")


def _wait_for_response(client: socket.socket, request_id: str, initial_buffer: str = "", idle_timeout: float = HEARTBEAT_IDLE_TIMEOUT) -> dict:
    """Wait for a response matching *request_id*, accepting heartbeats.

    Times out only if no data (response or heartbeat) arrives for *idle_timeout* seconds.
    As long as heartbeats keep flowing, the connection stays alive indefinitely — no wall-clock timeout.
    """
    decoder = json.JSONDecoder()
    last_activity = time.time()
    buffer = initial_buffer

    while True:
        try:
            client.settimeout(1.0)
            chunk = client.recv(SOCKET_BUFFER).decode()
            if not chunk:
                break
            buffer += chunk
            last_activity = time.time()
            while buffer:
                start_idx = buffer.find("{")
                if start_idx == -1:
                    break
                try:
                    obj, end_idx = decoder.raw_decode(buffer[start_idx :])
                    buffer = buffer[start_idx + end_idx :]
                    if obj.get("type") == "error":
                        raise RuntimeError(obj.get("message", "A2A error"))
                    if obj.get("id") == request_id and obj.get("type") == "response":
                        return obj
                except json.JSONDecodeError:
                    break
        except TimeoutError:
            if time.time() - last_activity >= idle_timeout:
                raise TimeoutError(f"Agent stopped responding (no heartbeat for {idle_timeout}s)") from None
            continue
    # Defensive fallback: unreachable in practice (while True never breaks),
    # but guards against future refactoring that might add a break.
    raise TimeoutError(f"Agent did not respond within {idle_timeout}s")


def query_agent(client: socket.socket, prompt: str, idle_timeout: float = HEARTBEAT_IDLE_TIMEOUT) -> dict:
    """Send a query to an agent and wait for the result (async protocol).

    No wall-clock timeout — waits indefinitely as long as the server sends heartbeats.
    Times out only if no heartbeat arrives for *idle_timeout* seconds.
    """
    request_id = str(uuid.uuid4())
    client.send(json.dumps({"type": "query", "id": request_id, "query": prompt}).encode())
    ack, ack_remainder = _read_json_response(client, DEFAULT_ACK_TIMEOUT)
    if ack.get("type") != "queued":
        raise RuntimeError(f"Unexpected acknowledgment: {ack}")
    return _wait_for_response(client, request_id, ack_remainder, idle_timeout)


def _get_agent_status(sock_path: str) -> dict:
    """Probe a socket and return an agent info dict (or None if PID unparseable)."""
    pid_match = Path(sock_path).stem.split("-")
    try:
        pid = int(pid_match[1])
    except (ValueError, IndexError):
        return None
    if not Path(sock_path).exists():
        return {"pid": pid, "sock_path": sock_path, "status": "stale"}
    if not _verify_socket(sock_path):
        return {"pid": pid, "sock_path": sock_path, "status": "unreachable"}
    client = None
    try:
        client = _make_connection(sock_path, 2)
        card = get_agent_card(client)
    except (FileNotFoundError, ConnectionError, TimeoutError, RuntimeError, OSError):
        return {"pid": pid, "sock_path": sock_path, "status": "unreachable"}
    finally:
        if client is not None:
            client.close()
    return {
        "pid": pid,
        "sock_path": sock_path,
        "name": card.get("name", "Unknown"),
        "tools_count": len(card.get("tools", [])),
        "working_dir": card.get("working_dir", "Unknown"),
        "model": card.get("model", "Unknown"),
        "file": card.get("file", "Unknown"),
        "status": "active",
    }


def list_agents() -> list:
    """Scan /tmp for active agent sockets and return their info dicts."""
    return [info for sock_file in Path("/tmp").glob("taua2a-*.sock") if (info := _get_agent_status(str(sock_file)))]


def _socket_path_for_pid(pid: int) -> str:
    """Return the Unix socket path for a given agent PID."""
    return f"/tmp/taua2a-{pid}.sock"
