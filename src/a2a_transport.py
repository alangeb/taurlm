"""A2A transport layer: socket connection and JSON stream decoding."""

from __future__ import annotations

import json
import socket
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_ACK_TIMEOUT = 5
DEFAULT_POLL_INTERVAL = 0.1
SOCKET_BUFFER = 4096

# Heartbeat protocol: server sends periodic heartbeats while processing.
# Client considers connection alive as long as heartbeats arrive within
# HEARTBEAT_IDLE_TIMEOUT seconds. No wall-clock timeout — slow agents are fine.
HEARTBEAT_INTERVAL = 5.0          # Server sends heartbeat every N seconds
HEARTBEAT_IDLE_TIMEOUT = 30.0     # Client gives up if no heartbeat for N seconds


# ── Client utilities ──────────────────────────────────────────────────────


def _verify_socket(sock_path: str) -> bool:
    """Check if a socket file exists and accepts connections."""
    if not Path(sock_path).exists():
        return False
    try:
        test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        test_sock.settimeout(1)
        test_sock.connect(sock_path)
        test_sock.close()
        return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def _make_connection(sock_path: str, timeout: float) -> socket.socket:
    """Create and connect a Unix domain socket with the given timeout."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(sock_path)
    return client


def connect_to_agent(pid: int, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> socket.socket:
    """Connect to an agent by PID via its Unix socket."""
    sock_path = f"/tmp/taua2a-{pid}.sock"

    if not Path(sock_path).exists():
        raise FileNotFoundError(f"Socket not found: {sock_path}")

    if not _verify_socket(sock_path):
        raise ConnectionError(f"Agent {pid} not responding (socket may be stale)")

    return _make_connection(sock_path, timeout)


def _decode_json_stream(client: socket.socket, timeout: float, initial_buffer: str = ""):
    """Decode one JSON object from a socket stream.

    Reads until a complete JSON object is available, then returns
    ``(obj, remainder_buffer)``.
    """
    client.settimeout(timeout)
    decoder = json.JSONDecoder()
    raw_buffer = initial_buffer.encode('utf-8') if initial_buffer else b''

    while True:
        try:
            chunk = client.recv(SOCKET_BUFFER)
            if not chunk:
                break
            raw_buffer += chunk
            buffer = raw_buffer.decode('utf-8', errors='replace')
            while buffer:
                try:
                    obj, idx = decoder.raw_decode(buffer)
                    remainder = buffer[idx:]
                    return obj, remainder
                except json.JSONDecodeError:
                    break
        except TimeoutError as exc:
            raise TimeoutError("Response timed out") from exc
    return None, raw_buffer.decode('utf-8', errors='replace') if raw_buffer else ""


def _read_json_response(client: socket.socket, timeout: float = DEFAULT_ACK_TIMEOUT):
    """Read a complete JSON object from a socket, returning (obj, remainder_buffer)."""
    return _decode_json_stream(client, timeout)
