"""Heartbeat management for TauErgon.

Handles idle detection, activity tracking, and heartbeat prompt execution.
When the agent is idle past the configured interval, loads the heartbeat
prompt from commands/heartbeat.md and sends it to the LLM. The LLM responds
with either <PROMPT>task</PROMPT> (inject task) or <NO_ACTION> (do nothing).

Key class:
- HeartbeatManager: Orchestrates heartbeat activity tracking and idle detection
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent_core import TauErgon

# Maximum heartbeat attempts (initial + retries).
_MAX_HEARTBEAT_ATTEMPTS = 3

# Path to heartbeat.md command file
_COMMANDS_DIR = Path(__file__).parent / "commands"
_HEARTBEAT_MD = _COMMANDS_DIR / "heartbeat.md"


@dataclass(frozen=True)
class HeartbeatResponse:
    """Parsed heartbeat response with structured exit state.

    Attributes:
        action: One of "prompt" or "no_action".
        task: Task description if action is "prompt", else None.
    """
    action: str
    task: Optional[str]


def _parse_heartbeat_response(raw: str) -> Optional[HeartbeatResponse]:
    """Parse a raw heartbeat response into a structured ``HeartbeatResponse``.

    Accepts two valid exit states:
    - ``<PROMPT>task</PROMPT>`` → action="prompt", task=content
    - ``<NO_ACTION>`` → action="no_action", task=None

    Uses ``re.search`` (not ``re.match``) to tolerate surrounding text
    (reasoning, commentary) that LLMs frequently emit.

    Returns ``None`` if the response does not match either pattern.
    """
    if raw is None:
        return None
    stripped = raw.strip()

    # 1. XML-style tags — search anywhere in the response (tolerant)
    match = re.search(r"<PROMPT>\s*(.+?)\s*</PROMPT>", stripped, re.DOTALL)
    if match:
        return HeartbeatResponse(action="prompt", task=match.group(1).strip())

    if re.search(r"<NO_ACTION>", stripped):
        return HeartbeatResponse(action="no_action", task=None)

    return None


_DEFAULT_USER_PROMPT = (
    "Review the current context and determine if there is a task "
    "you should continue or initiate."
)

_USER_PROMPT_FILE = _COMMANDS_DIR / "heartbeat_user.txt"


def _get_user_prompt() -> str:
    """Get the user-supplied heartbeat prompt.

    Returns the content of heartbeat_user.txt if it exists and is non-empty,
    otherwise returns the default prompt.
    """
    if _USER_PROMPT_FILE.exists():
        try:
            text = _USER_PROMPT_FILE.read_text().strip()
            if text:
                return text
        except OSError:
            pass
    return _DEFAULT_USER_PROMPT


def _load_heartbeat_prompt() -> Optional[str]:
    """Load the heartbeat prompt from commands/heartbeat.md.

    Strips YAML frontmatter, substitutes ${user_prompt} with the user's
    custom prompt (or default), then substitutes dynamic placeholders
    (${time}, ${date}, ${datetime}).

    Returns:
        The prompt string, or None if the file doesn't exist or is empty.
    """
    if not _HEARTBEAT_MD.exists():
        return None

    try:
        content = _HEARTBEAT_MD.read_text()
    except OSError:
        return None

    # Strip YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]

    # Substitute ${user_prompt} placeholder
    if "${user_prompt}" in content:
        content = content.replace("${user_prompt}", _get_user_prompt())

    # Substitute dynamic placeholders (reuse from command_dispatch)
    from rlm.command_dispatch import _substitute_dynamic_placeholders
    content = _substitute_dynamic_placeholders(content)

    content = content.strip()
    return content if content else None


class HeartbeatManager:
    """Manage heartbeat activity tracking and idle detection.

    Encapsulates the heartbeat logic:
    - Activity timestamp tracking
    - Idle time detection
    - Heartbeat prompt execution with response validation

    The manager holds a reference to the parent agent so that ``run_heartbeat()``
    requires no parameters.

    Attributes:
        enabled: Whether heartbeat checking is enabled.
        interval_seconds: Idle threshold in seconds before triggering heartbeat.
        last_activity_time: Timestamp of last agent activity.
    """

    def __init__(
        self,
        enabled: bool = False,
        interval_seconds: Optional[int] = None,
        agent: Optional["TauErgon"] = None,
    ):
        """Initialize the heartbeat manager.

        Args:
            enabled: Whether heartbeat checking is enabled.
            interval_seconds: Idle threshold in seconds before triggering heartbeat.
            agent: Parent TauErgon reference (used for LLM calls).
        """
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.last_activity_time = time.time()
        self._agent = agent
        self._failure_count = 0  # M-A9: heartbeat failure backoff

    def touch_activity(self) -> None:
        """Update the last activity timestamp."""
        self.last_activity_time = time.time()

    def should_check(self) -> bool:
        """NOTE: Unused. Kept for API compatibility. L-A5"""
        """Check if heartbeat should run (idle past interval)."""
        if not self.enabled or self.interval_seconds is None:
            return False
        idle_seconds = time.time() - self.last_activity_time
        return idle_seconds >= self.interval_seconds

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM with the current context + heartbeat prompt.

        Uses the agent's client to make a simple chat completion call.
        No code extraction or REPL execution — just a text response.

        Args:
            prompt: The heartbeat prompt to send.

        Returns:
            The LLM's response text, or None on failure.
        """
        if self._agent is None:
            return None

        try:
            # Build messages: current context + heartbeat prompt
            messages = list(self._agent.context)
            messages = messages[-20:]  # M-A7: Truncate context to last 20
            messages.append({"role": "user", "content": prompt})

            # Simple LLM call — no tools, no code extraction
            response = self._agent.client.chat.completions.create(
                model=self._agent.model_name,
                messages=messages,
                max_tokens=512,
                temperature=0.3,
            )

            return response.choices[0].message.content

        except Exception as e:
            from agent_console import error
            error(f"Heartbeat LLM call failed: {e}")
            return None

    def run_heartbeat(self) -> Optional[HeartbeatResponse]:
        """Run a heartbeat check if the agent has been idle past the interval.

        Checks if the agent has been idle for longer than the configured heartbeat
        interval. If so, loads the heartbeat prompt and calls the LLM.

        Validates the LLM's response against the structured exit-state format
        and retries if the format is invalid.

        Returns:
            ``HeartbeatResponse`` with parsed action/task if executed, otherwise ``None``.
            Returns ``None`` if heartbeat is disabled, interval not set, or idle time
            is below the threshold.
        NOTE: This blocks the main input loop for the duration of the LLM call (5-30s).
        """
        if not self.enabled or self.interval_seconds is None:
            return None

        idle_seconds = time.time() - self.last_activity_time
        if idle_seconds < self.interval_seconds + (self._failure_count * 60):
            return None

        # Reset idle timer immediately to prevent rapid re-trigger
        # (LLM call takes 5-30s; without this, next loop iteration
        # sees idle still past threshold and fires again)
        self.touch_activity()

        # Load the heartbeat prompt
        prompt = _load_heartbeat_prompt()
        if prompt is None:
            from agent_console import error
            error("Heartbeat prompt file not found or empty")
            self._failure_count += 1  # M-A9: track failure for backoff
            self.touch_activity()  # prevent rapid re-trigger on failure
            return None

        from agent_console import status
        from datetime import datetime
        ts = datetime.now().strftime("%y%m%d %H%M%S")
        status(f"[HEARTBEAT] {ts} Idle {int(idle_seconds)}s, checking...")

        # Audit: log heartbeat trigger
        try:
            self._agent._session.audit_writer._emit(
                "HEARTBEAT_TRIGGER",
                f"idle={int(idle_seconds)}s interval={self.interval_seconds}s"
            )
        except Exception:
            pass  # Audit failures must not break heartbeat flow

        # Call LLM with retries
        for attempt in range(1, _MAX_HEARTBEAT_ATTEMPTS + 1):
            current_prompt = prompt
            if attempt > 1:
                current_prompt = (
                    prompt
                    + "\n\nIMPORTANT: Your previous response did not match the required format. "
                    "You MUST respond with exactly one of:\n"
                    "  <PROMPT>task description</PROMPT>\n"
                    "  <NO_ACTION>"
                )

            raw = self._call_llm(current_prompt)
            if raw is None:
                return None

            parsed = _parse_heartbeat_response(raw)
            if parsed is not None:
                # Audit: log the heartbeat action
                try:
                    self._agent._session.audit_writer._emit(
                        "HEARTBEAT_ACTION",
                        f"action={parsed.action} task={parsed.task or 'none'}"
                    )
                except Exception:
                    pass
                self._failure_count = 0  # M-A9: reset on success
                return parsed

            if attempt < _MAX_HEARTBEAT_ATTEMPTS:
                from agent_console import warning
                warning(f"Heartbeat response format invalid (attempt {attempt}), retrying...")
            else:
                from agent_console import error
                error("Heartbeat response format invalid after all retries")

        return None
