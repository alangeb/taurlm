"""A2A sessions: scan log directories for session context files and build metadata."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from a2a_discovery import _socket_path_for_pid, get_agent_card
from a2a_transport import DEFAULT_CONNECT_TIMEOUT, _make_connection, _verify_socket
from agent_context_utils import _CONTEXT_FILE_CAPTURE_RE, read_context_metadata_for_a2a


def _is_sanity_session(log_dir: Path, agent_name: str | None = None) -> bool:
    """Return True for sessions that belong to sanity-test runs.

    A session is considered a sanity session when:
    - Its log directory path contains ``logtest`` (sanity.sh sets
      ``TAU_LOG_DIR`` to ``...logtest``), or
    - Its agent name follows the ``a2aname-`` pattern used by sanity.sh.
    """
    if "logtest" in str(log_dir).lower():
        return True
    if agent_name and agent_name.startswith("a2aname-"):
        return True
    return False


def _probe_session_socket(pid: int) -> tuple[str, dict | None]:
    """Probe the A2A socket for *pid*.

    Returns ``(status, agent_card_or_none)`` where *status* is one of
    ``active``, ``stale``, or ``unreachable``.
    """
    sock_path = _socket_path_for_pid(pid)

    if not Path(sock_path).exists():
        return ("stale", None)

    if not _verify_socket(sock_path):
        return ("unreachable", None)

    client = None
    try:
        client = _make_connection(sock_path, DEFAULT_CONNECT_TIMEOUT)
        card = get_agent_card(client)
        return ("active", card)
    except (FileNotFoundError, ConnectionError, TimeoutError, RuntimeError, OSError):
        return ("unreachable", None)
    finally:
        if client is not None:
            client.close()


def _build_session_info(prefix: str, pid: int, context_file: Path, log_dir: Path) -> dict:
    """Build a single session-metadata dict from a context file.

    The agent card (when reachable) takes priority over context-file
    metadata for shared fields; non-reachable sessions fall back to the
    metadata block written by TAU_005, or to ``None`` defaults.
    """
    metadata, message_count = read_context_metadata_for_a2a(context_file)
    # B15b: the context-file prefix encodes the *parent* pid (agent_session uses
    # os.getppid()), but the A2A server binds /tmp/taua2a-{os.getpid()}.sock.
    # Probe with the agent's own pid from the metadata block when available.
    agent_pid = metadata.get("pid") or pid
    status, card = _probe_session_socket(agent_pid)

    # Agent card fields take priority; fall back to context metadata.
    source = card or metadata

    agent_name = source.get("name") or source.get("agent_name")
    model = source.get("model")
    working_dir = source.get("working_dir")
    start_time = source.get("start_time")
    parent_pid = source.get("parent_pid") or (pid if agent_pid != pid else None)
    llm_group = source.get("llm_group")
    max_context_tokens = card.get("max_context_tokens") if card else None

    uptime = None
    if start_time is not None:
        try:
            uptime = int(time.time() - float(start_time))
        except (TypeError, ValueError):
            uptime = None

    socket_path = _socket_path_for_pid(agent_pid)

    return {
        "id": prefix,
        "pid": agent_pid,
        "status": status,
        "agent_name": agent_name,
        "model": model,
        "working_dir": working_dir,
        "context_length": message_count,
        "message_count": message_count,
        "start_time": start_time,
        "uptime": uptime,
        "parent_pid": parent_pid,
        "llm_group": llm_group,
        "max_context_tokens": max_context_tokens,
        "socket_path": socket_path,
        "context_file": str(context_file),
        "audit_file": str(log_dir / f"{prefix}.audit"),
        "is_sanity": _is_sanity_session(log_dir, agent_name),
    }


def _scan_sessions(log_dir: Path) -> list[dict]:
    r"""Scan *log_dir* for context files and return session-metadata dicts.

    Only files whose name matches ``^\d+_\d+_\d+\.context$`` are
    considered (the ``{ppid}_{timestamp}_{counter}`` convention).
    """
    sessions: list[dict] = []
    if not log_dir.exists():
        return sessions

    for ctx_file in sorted(log_dir.glob("*.context")):
        match = _CONTEXT_FILE_CAPTURE_RE.match(ctx_file.name)
        if not match:
            continue
        pid = int(match.group(1))
        sessions.append(_build_session_info(ctx_file.stem, pid, ctx_file, log_dir))

    return sessions


def _list_sessions_json() -> None:
    """Print all sessions in LOG_DIR as JSON, then exit.

    Short-circuits in :func:`a2a_cli_mode` like ``--list`` / ``--listjson``.
    """
    # Lazy import to avoid circular dependency at module load time.
    from agent_session import LOG_DIR

    sessions = _scan_sessions(LOG_DIR)
    print(json.dumps({"sessions": sessions}, indent=2))
    sys.exit(0)
