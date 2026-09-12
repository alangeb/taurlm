"""RLM Host Bridge

This module implements the host bridge that allows Python skills to make
typed requests to the TypeScript host for capabilities whose authoritative
state belongs outside the kernel.

Architecture
------------
The host bridge keeps:
- Credentials and provider execution
- Transcript writes
- Worker routing
- Scheduling
- Goal state management
- Agent message delivery

...out of Python while retaining a programmatic model interface.

Python skills use `rlm.host_request(...)` to make typed requests.
The host validates the request and owns the state transition.

Key Classes
-----------
HostBridge    : Host bridge implementation

Example
-------
    # In Python skill:
    result = await rlm.host_request("goal", action="set", content="Complete the review")
    result = await rlm.host_request("heartbeat", interval=300)

See Also
--------
- rlm/skills.py: Python-backed skills
- prime-agent docs: Host bridge architecture
"""

from __future__ import annotations
from uuid import uuid4

try:
    import fcntl
except ImportError:
    fcntl = None
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

__all__ = [
    "HostResponse",
    "HostBridge",
]


@dataclass
class HostResponse:
    """Response from host.

    Attributes:
        success: Whether the request succeeded
        data: Response data
        error: Error message if failed
    """
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class HostBridge:
    """Host bridge for external capabilities.

    Allows Python skills to make typed requests to the host
    for capabilities that must stay outside the kernel.

    Attributes:
        config: RLM configuration
        _agent: Parent agent instance (for context access)
    """

    def __init__(self, config: Any | None = None, agent: Any | None = None):
        """Initialize host bridge.

        Args:
            config: RLM configuration (optional)
            agent: Parent agent instance for context access (optional)
        """
        # L-A8: agent param reserved for future use
        self._config = config
        self._agent = agent

    def request(self, req_type: str, action: str, **params: Any) -> HostResponse:
        """Make a typed host request.

        Args:
            req_type: Request type (goal, heartbeat, agent_message)
            action: Action to perform
            **params: Request parameters

        Returns:
            HostResponse with success status and data

        Raises:
            ValueError: If request type is not supported
        """
        handler = {
            "goal": self._handle_goal,
            "heartbeat": self._handle_heartbeat,
            "agent_message": self._handle_agent_message,
        }.get(req_type)

        if handler is None:
            return HostResponse(
                success=False,
                error=f"Unknown request type: {req_type}",
            )

        try:
            return handler(action, **params)
        except Exception as e:
            return HostResponse(
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _handle_goal(self, action: str, **params: Any) -> HostResponse:
        """Handle goal-related requests with file-based persistence.

        Args:
            action: set, get, update, clear
            **params: Goal parameters (content, status, progress)

        Returns:
            HostResponse with goal data
        """
        goal_file = self._get_data_path("rlm_goal.json")

        if action == "set":
            goal = {
                "content": params.get("content", ""),
                "status": params.get("status", "active"),
                "created_at": time.time(),
                "updated_at": time.time(),
                "progress": params.get("progress", 0),
            }
            # HB1: serialize with 'update' under the same fcntl lock so a
            # concurrent set/update can't interleave and corrupt the goal file.
            lock_file = goal_file.with_suffix(".lock")
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_file, "w") as lf:
                if fcntl is not None:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    # L-A9: _save_json uses atomic write (temp+rename)
                    self._save_json(goal_file, goal)
                finally:
                    if fcntl is not None:
                        fcntl.flock(lf, fcntl.LOCK_UN)
            return HostResponse(success=True, data=goal)

        elif action == "get":
            if not goal_file.exists():
                return HostResponse(success=True, data={})
            goal = self._load_json(goal_file)
            return HostResponse(success=True, data=goal)

        elif action == "update":
            # L-A9: File lock for atomic read-modify-write
            lock_file = goal_file.with_suffix(".lock")
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_file, "w") as lf:
                if fcntl is not None:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    if not goal_file.exists():
                        return HostResponse(success=False, error="No goal set")
                    goal = self._load_json(goal_file)
                    if "content" in params:
                        goal["content"] = params["content"]
                    if "status" in params:
                        goal["status"] = params["status"]
                    if "progress" in params:
                        goal["progress"] = params["progress"]
                    goal["updated_at"] = time.time()
                    self._save_json(goal_file, goal)
                finally:
                    if fcntl is not None:
                        fcntl.flock(lf, fcntl.LOCK_UN)
            return HostResponse(success=True, data=goal)

        elif action == "clear":
            if goal_file.exists():
                goal_file.unlink()
            return HostResponse(success=True, data={"cleared": True})

        else:
            return HostResponse(success=False, error=f"Unknown goal action: {action}")

    def _handle_heartbeat(self, action: str, **params: Any) -> HostResponse:
        """Handle heartbeat-related requests with file-based persistence.

        Args:
            action: set, get, clear
            **params: Heartbeat parameters (enabled, interval_seconds)

        Returns:
            HostResponse with heartbeat config
        """
        heartbeat_file = self._get_data_path("rlm_heartbeat.json")

        if action == "set":
            config = {
                "enabled": params.get("enabled", True),
                "interval_seconds": params.get("interval_seconds", 300),
                "next_beat": time.time() + params.get("interval_seconds", 300),
                "scheduled_time": time.time(),
            }
            self._save_json(heartbeat_file, config)
            return HostResponse(success=True, data=config)

        elif action == "get":
            if not heartbeat_file.exists():
                return HostResponse(success=True, data={})
            config = self._load_json(heartbeat_file)
            return HostResponse(success=True, data=config)

        elif action == "clear":
            if heartbeat_file.exists():
                heartbeat_file.unlink()
            return HostResponse(success=True, data={"cleared": True})

        else:
            return HostResponse(success=False, error=f"Unknown heartbeat action: {action}")

    def _handle_agent_message(self, action: str, **params: Any) -> HostResponse:
        """Handle agent message requests with file-based message queue.

        Args:
            action: send, receive
            **params: Message parameters (content, sender, receiver)

        Returns:
            HostResponse with message data
        """
        msg_dir = self._get_data_path("rlm_messages")
        msg_dir.mkdir(parents=True, exist_ok=True)

        if action == "send":
            message = {
                "content": params.get("content", ""),
                "sender": params.get("sender", "unknown"),
                "receiver": params.get("receiver", "all"),
                "timestamp": time.time(),
            }
            msg_file = msg_dir / f"msg_{uuid4().hex[:8]}.json"  # H21
            self._save_json(msg_file, message)
            return HostResponse(success=True, data={"sent": True, "file": str(msg_file)})

        elif action == "receive":
            messages = []
            for msg_file in sorted(msg_dir.glob("msg_*.json")):
                # H22: Atomic claim via rename to prevent concurrent reads
                claimed = msg_file.with_suffix(".json.processing")
                try:
                    os.rename(msg_file, claimed)
                except FileNotFoundError:
                    continue  # Another thread claimed it
                try:
                    msg = self._load_json(claimed)
                    messages.append(msg)
                finally:
                    claimed.unlink(missing_ok=True)
            return HostResponse(success=True, data={"messages": messages, "count": len(messages)})

        else:
            return HostResponse(success=False, error=f"Unknown message action: {action}")

    def _get_data_path(self, filename: str) -> Path:
        """Get path to data file in ~/.local/taurlm/ directory."""
        data_dir = Path.home() / ".local" / "taurlm"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / filename

    def _save_json(self, path: Path, data: dict) -> None:
        """Save data as JSON to file (B9: tmp + os.replace — a crash mid-write
        can no longer truncate the file and poison _load_json)."""
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _load_json(self, path: Path) -> dict:
        """Load JSON data from file."""
        with open(path) as f:
            return json.load(f)

    def get_initial_namespace(self) -> dict[str, Any]:
        """Get initial namespace with host_request function.

        Returns:
            Dict with host_request key containing callable
        """
        return {
            "host_request": self.request,
        }
