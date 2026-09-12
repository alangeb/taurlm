"""External control queue: typed command protocol between controllers and agent.

This module defines the control command protocol used by external processes
(user steering, parent agents) to communicate with a running agent instance.

Command types:
- inject: Append synthetic user message to context
- terminate: Graceful (finish turn), forceful (exit), or force_kill (SIGKILL)
- redirect: Clear context and stale state, start new task
- status: Log current status to audit

Usage:
    from agent_control import ControlQueue, process_control_queue

    # Producer (e.g., input handler):
    cq = agent.control_queue
    cq.send_terminate(graceful=True, source="user")

    # Consumer (e.g., agent loop, at turn boundaries):
    process_control_queue(agent)
"""
from __future__ import annotations

import json
import logging
import os
import queue
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ControlCommand",
    "ControlQueue",
    "process_control_queue",
]


# ── Typed Command ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ControlCommand:
    """A single control command. Serialized to JSON for queue transport."""

    type: str  # "inject" | "terminate" | "redirect" | "status"
    # inject fields
    content: str = ""
    role: str = "user"
    # terminate fields
    graceful: bool = True
    source: str = "parent"  # "parent" | "user"
    force_kill: bool = False
    # redirect fields
    task: str = ""

    def to_json(self) -> str:
        """Serialize to JSON string for queue transport."""
        d: dict[str, Any] = {"type": self.type}
        if self.type == "inject":
            d["content"] = self.content
            d["role"] = self.role
        elif self.type == "terminate":
            d["graceful"] = self.graceful
            d["source"] = self.source
            d["force_kill"] = self.force_kill
        elif self.type == "redirect":
            d["task"] = self.task
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> ControlCommand | None:
        """Deserialize from JSON string. Returns None on parse error."""
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return None
        # P7-B1-38: valid JSON that isn't an object (123, "str", [..]) must
        # return None like a parse error, not crash on d.get().
        if not isinstance(d, dict):
            return None
        return cls(
            type=d.get("type", ""),
            content=d.get("content", ""),
            role=d.get("role", "user"),
            graceful=d.get("graceful", True),
            source=d.get("source", "parent"),
            force_kill=d.get("force_kill", False),
            task=d.get("task", ""),
        )


# ── Control Queue ──────────────────────────────────────────────────────────────


class ControlQueue:
    """Typed wrapper around queue.Queue for control commands.

    Provides a clean send interface for producers and a drain interface
    for the consumer (agent loop).
    """

    def __init__(self, maxsize: int = 100):
        self._queue: queue.Queue[str] = queue.Queue(maxsize=maxsize)

    # ── Producer API ────────────────────────────────────────────────────────

    def send(self, cmd: ControlCommand) -> bool:
        """Send a command. Returns False if queue is full."""
        try:
            self._queue.put_nowait(cmd.to_json())
            return True
        except queue.Full:
            return False

    def send_inject(self, content: str, role: str = "user") -> bool:
        return self.send(ControlCommand(type="inject", content=content, role=role))

    def send_terminate(
        self, graceful: bool = True, source: str = "parent", force_kill: bool = False
    ) -> bool:
        return self.send(
            ControlCommand(
                type="terminate", graceful=graceful, source=source, force_kill=force_kill
            )
        )

    def send_redirect(self, task: str) -> bool:
        return self.send(ControlCommand(type="redirect", task=task))

    def send_status(self) -> bool:
        return self.send(ControlCommand(type="status"))

    # ── Consumer API ────────────────────────────────────────────────────────

    def drain(self) -> list[ControlCommand]:
        """Remove and return all pending commands (non-blocking)."""
        cmds: list[ControlCommand] = []
        while not self._queue.empty():
            try:
                raw = self._queue.get_nowait()
            except queue.Empty:
                break
            cmd = ControlCommand.from_json(raw)
            if cmd is not None:
                cmds.append(cmd)
        return cmds

    @property
    def raw_queue(self) -> "queue.Queue[str]":
        """Access the underlying queue (for backward compatibility)."""
        return self._queue

    @property
    def empty(self) -> bool:
        return self._queue.empty()

    def __len__(self) -> int:
        return self._queue.qsize()


# ── Command Processor ──────────────────────────────────────────────────────────


def process_control_queue(agent: Any) -> None:
    """Process all pending control commands on the agent.

    Called at turn boundaries (from agent_loop.py). Drains the agent's
    control queue and dispatches each command to the appropriate handler.

    Args:
        agent: The TauErgon agent instance (must have .control_queue,
               .context, .loop_detector, ._cache_tracker, etc.)
    """
    cq: ControlQueue = agent.control_queue
    commands = cq.drain()

    if not commands:
        return

    for cmd in commands:
        _dispatch(agent, cmd)

    from agent_audit_bridge import console_warning
    console_warning(f"Processed {len(commands)} control commands")


def _dispatch(agent: Any, cmd: ControlCommand) -> None:
    """Dispatch a single control command to its handler."""
    handlers = {
        "inject": _handle_inject,
        "terminate": _handle_terminate,
        "redirect": _handle_redirect,
        "status": _handle_status,
    }
    handler = handlers.get(cmd.type)
    if handler is None:
        from agent_console import warning
        warning(f"Control queue: unknown command type '{cmd.type}'")
        return
    handler(agent, cmd)


def _handle_inject(agent: Any, cmd: ControlCommand) -> None:
    """Inject a synthetic user message into the context."""
    if not cmd.content:
        return
    agent.context.append_synthetic_user_with_bridge("parent_inject", cmd.content)
    from agent_console import status
    status(f"External controller injected {cmd.role} message ({len(cmd.content)} chars)")


def _handle_terminate(agent: Any, cmd: ControlCommand) -> None:
    """Handle termination: force_kill, graceful, or forceful."""
    if cmd.force_kill:
        from agent_audit_bridge import console_warning
        _pid = os.getpid()
        console_warning(f"FORCE_KILL: received from {cmd.source}, pid={_pid}")
        import signal as _signal
        import sys as _sys
        _sys.stdout.flush()
        _sys.stderr.flush()
        os.kill(_pid, _signal.SIGKILL)
        return  # unreachable

    if cmd.graceful:
        agent.force_end_turn = (
            "user_stop" if cmd.source == "user" else "external_terminate_graceful"
        )
        if cmd.source == "parent":
            agent.context.append_synthetic_user_with_bridge(
                "parent_inject",
                "External controller has terminated this task. "
                "Please provide a final summary of your work.",
            )
            from agent_console import status
            status("External controller requested graceful termination")
        else:
            from agent_console import status
            status("User requested stop — ending turn")
    else:
        from agent_lifecycle import AgentLifecycle
        AgentLifecycle.set_exit_requested(True)
        from agent_console import status
        status("External controller requested forceful termination")


def _handle_redirect(agent: Any, cmd: ControlCommand) -> None:
    """Redirect to a new task: clear context and stale state."""
    if not cmd.task:
        return
    agent.context.clear()
    agent.context.append_user(cmd.task, user_type="redirect", context_pct=agent.context.context_pct(agent.max_context_tokens))
    if agent.loop_detector:
        agent.loop_detector.reset()
    agent.clear_stale_state()
    from agent_console import status
    status("Redirected to new task by external controller")


def _handle_status(agent: Any, cmd: ControlCommand) -> None:
    """Log current status to audit."""
    from agent_audit_bridge import console_warning
    stats = agent.loop_detector.get_stats() if agent.loop_detector else {}
    console_warning(
        f"STATUS: context={len(agent.context)}, "
        f"loop_warnings={stats.get('total_warnings', 0)}, "
        f"nesting={agent.nesting_count}"
    )
