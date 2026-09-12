"""Data models for TauErgon — messages, colors, subagent results."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

__all__ = ["InputMessage", "Colors", "SubAgentResult", "AgentStatus"]


@dataclass
class InputMessage:
    """Unified input message from any source (a2a, interactive, CLI)."""

    source: str
    content: str
    request_id: str | None = None
    timestamp: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = time.time()

    @classmethod
    def from_a2a(
        cls, content: str, request_id: str | None = None, timestamp: float | None = None
    ) -> InputMessage:
        """Create an InputMessage from A2A protocol input."""
        return cls(
            source="a2a",
            content=content,
            request_id=request_id or str(uuid.uuid4()),
            timestamp=timestamp,
        )

    @classmethod
    def from_interactive(cls, content: str, timestamp: float | None = None) -> InputMessage:
        """Create an InputMessage from interactive user input."""
        return cls(source="interactive", content=content, timestamp=timestamp)

    @classmethod
    def from_command_line(cls, content: str, timestamp: float | None = None) -> InputMessage:
        """Create an InputMessage from command-line input."""
        return cls(source="command_line", content=content, timestamp=timestamp)


class Colors:
    """ANSI color codes for terminal output."""

    YELLOW = "\033[93m"  # warnings, advisory, pending
    GREEN = "\033[92m"  # success, AI output, dynamic results
    CYAN = "\033[96m"  # tool output, execution indicators
    BLUE = "\033[94m"  # nested agent context
    RED = "\033[91m"  # errors, validation failures
    WHITE = "\033[97m"  # section text, plain labels
    MAGENTA = "\033[35m"  # headers
    RESET = "\033[0m"  # reset formatting
    INVERT_CYAN = "\033[30;46m"  # context status bar
    INVERT_BLUE = "\033[97;48;5;61m"  # nested agent status bar
    REASONING = "\033[38;5;109m"  # model reasoning/thinking


@dataclass
class SubAgentResult:
    """Result from synchronous subagent execution."""

    output: str
    input_tokens: int
    output_tokens: int


@dataclass
class AgentStatus:
    """Encapsulated view model for agent status information.

    Replaces direct access to TauErgon internals from the display layer,
    improving encapsulation and decoupling.
    """

    # --- Context stats ---
    token_count: int = 0
    percentage: float = 0.0
    byte_count: int = 0
    is_exact: bool = False
    context_len: int = 0
    max_context_tokens: int = 0

    # --- Model info ---
    model_name: str = ""
    base_url: str = ""
    model_source: str = ""  # "cli" or "group:<name>"
    base_url_source: str = ""  # "cli" or "group:<name>"

    # --- Group info ---
    current_group_name: str = ""
    llm_groups: list[str] | None = None
    gen_params: dict[str, Any] | None = None

    # --- File paths ---
    context_file: Path | None = None
    audit_file: Path | None = None

    # --- Process info ---
    pid: int = 0
    ppid: int = 0

    # --- Token tracking ---
    last_turn_in: int = 0
    last_turn_out: int = 0
    last_turn_cached: int = 0
    session_in: int = 0
    session_out: int = 0
    session_cached: int = 0
    # --- Byte tracking (estimated: ~4 bytes/token for UTF-8) ---
    last_turn_in_bytes: int = 0
    last_turn_out_bytes: int = 0
    last_turn_cached_bytes: int = 0
    session_in_bytes: int = 0
    session_out_bytes: int = 0
    session_cached_bytes: int = 0
    last_tg_tps: float = 0.0
    last_ttft: float = 0.0

    # --- Cache ---
    has_cache_data: bool = False
    cumulative_hit_rate: float | None = None
    sliding_hit_rate: float | None = None
    last_hit_rate: float | None = None
    call_count: int = 0

    # --- Agent info ---
    agent_name: str = ""
    nesting_count: int = 0
    nesting_stack: str = ""  # e.g. "SF" = fork in subagent, "T" = think
    turn_active: bool = False  # True while agent is processing a turn

    # --- Spawn/budget ---
    spawn_B: float = 0.0  # remaining work budget (0 = no budget / root agent)
    spawns_count: int = 0  # active spawns (root only)

    # --- Loop detection ---
    loop_stats: dict[str, Any] | None = None

    # --- Commands ---
    available_commands: list[str] | None = None
