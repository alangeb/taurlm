"""Autonomous Command — /autonomous

Bounded autonomous mode — continue within configured turn, token,
and time budgets with optional quality gates.

Usage:
    /autonomous          — View current autonomous status
    /autonomous start    — Start autonomous mode
    /autonomous stop     — Stop autonomous mode
    /autonomous config   — Configure budgets
"""

import inspect
import json
import time
from pathlib import Path

__all__ = [
    'AUTONOMOUS_FILE',
    'AutonomousConfig',
    'run'
]

AUTONOMOUS_FILE = Path(__file__).parent.parent.parent / ".autonomous.json"


class AutonomousConfig:
    """Autonomous mode configuration."""

    def __init__(
        self,
        enabled: bool = False,
        max_turns: int = 20,
        max_tokens: int = 50000,
        max_time_seconds: int = 3600,  # 1 hour
        current_turns: int = 0,
        current_tokens: int = 0,
        start_time: float = 0.0,
        quality_gates: list = None,
    ):
        self.enabled = enabled
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.max_time_seconds = max_time_seconds
        self.current_turns = current_turns
        self.current_tokens = current_tokens
        self.start_time = start_time
        self.quality_gates = quality_gates or []

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
            "max_time_seconds": self.max_time_seconds,
            "current_turns": self.current_turns,
            "current_tokens": self.current_tokens,
            "start_time": self.start_time,
            "quality_gates": self.quality_gates,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutonomousConfig":
        return cls(**{k: v for k, v in data.items() if k in inspect.signature(cls).parameters})

    def is_within_budget(self) -> bool:
        """Check if still within budget."""
        if self.current_turns >= self.max_turns:
            return False
        if self.current_tokens >= self.max_tokens:
            return False
        if self.start_time and (time.time() - self.start_time) >= self.max_time_seconds:
            return False
        return True


def _load_config() -> AutonomousConfig:
    """Load autonomous config from file."""
    if AUTONOMOUS_FILE.exists():
        try:
            data = json.loads(AUTONOMOUS_FILE.read_text())
            return AutonomousConfig.from_dict(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return AutonomousConfig()


def _save_config(config: AutonomousConfig) -> None:
    """Save autonomous config to file."""
    AUTONOMOUS_FILE.write_text(json.dumps(config.to_dict(), indent=2))


def run(args: str = "") -> str:
    """Execute /autonomous command.

    Args:
        args: Command arguments (start, stop, config)

    Returns:
        Command output string.
    """
    args = args.strip().lower()

    if not args:
        # View current status
        config = _load_config()
        if not config.enabled:
            return "Autonomous mode is disabled. Use /autonomous start to enable."
        within = config.is_within_budget()
        elapsed = (
            int(time.time() - config.start_time) if config.start_time else 0
        )
        return (
            f"🤖 Autonomous: {'active' if within else 'exceeded budget'}\n"
            f"   Turns: {config.current_turns}/{config.max_turns}\n"
            f"   Tokens: {config.current_tokens}/{config.max_tokens}\n"
            f"   Time: {elapsed}s/{config.max_time_seconds}s"
        )

    elif args == "start":
        config = _load_config()
        config.enabled = True
        config.current_turns = 0
        config.current_tokens = 0
        config.start_time = time.time()
        _save_config(config)
        return (
            f"🤖 Autonomous mode started\n"
            f"   Max turns: {config.max_turns}\n"
            f"   Max tokens: {config.max_tokens}\n"
            f"   Max time: {config.max_time_seconds}s"
        )

    elif args == "stop":
        config = _load_config()
        config.enabled = False
        _save_config(config)
        return "🤖 Autonomous mode stopped."

    elif args == "config":
        config = _load_config()
        return (
            "Autonomous Configuration:\n"
            f"   Max turns: {config.max_turns}\n"
            f"   Max tokens: {config.max_tokens}\n"
            f"   Max time: {config.max_time_seconds}s\n"
            f"   Quality gates: {len(config.quality_gates)}"
        )

    else:
        return (
            "Usage: /autonomous [start|stop|config]\n"
            "  /autonomous          — View current autonomous status\n"
            "  /autonomous start    — Start autonomous mode\n"
            "  /autonomous stop     — Stop autonomous mode\n"
            "  /autonomous config   — Configure budgets"
        )
