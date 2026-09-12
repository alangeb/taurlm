"""Goal Command — /goal

Persistent goals for the RLM agent. Goals persist across turns until
completed, paused, or cleared.

Usage:
    /goal set <goal>      — Set a new goal
    /goal                 — View current goal
    /goal update <goal>   — Update current goal
    /goal complete        — Mark goal as complete
    /goal pause           — Pause current goal
    /goal clear           — Clear current goal
"""

import inspect
import json
import time
from pathlib import Path

__all__ = [
    'GOAL_FILE',
    'Goal',
    'run'
]

GOAL_FILE = Path(__file__).parent.parent.parent / ".goal.json"


class Goal:
    """Persistent goal state."""

    def __init__(
        self,
        content: str = "",
        status: str = "active",  # active, paused, completed
        created_at: float = 0.0,
        updated_at: float = 0.0,
        progress: str = "",
    ):
        self.content = content
        self.status = status
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.progress = progress

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Goal":
        return cls(**{k: v for k, v in data.items() if k in inspect.signature(cls).parameters})

    def __str__(self) -> str:
        if not self.content:
            return "No active goal."
        status_icon = {"active": "🎯", "paused": "⏸️", "completed": "✅"}.get(
            self.status, "📋"
        )
        result = f"{status_icon} Goal: {self.content}"
        if self.progress:
            result += f"\n   Progress: {self.progress}"
        return result


def _load_goal() -> Goal:
    """Load goal from file."""
    if GOAL_FILE.exists():
        try:
            data = json.loads(GOAL_FILE.read_text())
            return Goal.from_dict(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return Goal()


def _save_goal(goal: Goal) -> None:
    """Save goal to file."""
    GOAL_FILE.write_text(json.dumps(goal.to_dict(), indent=2))


def run(args: str = "") -> str:
    """Execute /goal command.

    Args:
        args: Command arguments (set, update, complete, pause, clear)

    Returns:
        Command output string.
    """
    args = args.strip()

    if not args:
        # View current goal
        goal = _load_goal()
        return str(goal)

    parts = args.split(None, 1)
    action = parts[0].lower()
    action_args = parts[1] if len(parts) > 1 else ""

    if action == "set":
        if not action_args.strip():
            return "Usage: /goal set <goal>"
        goal = Goal(content=action_args.strip())
        _save_goal(goal)
        return f"🎯 Goal set: {goal.content}"

    elif action == "update":
        goal = _load_goal()
        if not goal.content:
            return "No active goal to update. Use /goal set first."
        if action_args.strip():
            goal.content = action_args.strip()
        goal.updated_at = time.time()
        _save_goal(goal)
        return f"🎯 Goal updated: {goal.content}"

    elif action == "progress":
        goal = _load_goal()
        if not goal.content:
            return "No active goal."
        if action_args.strip():
            goal.progress = action_args.strip()
            goal.updated_at = time.time()
            _save_goal(goal)
        return str(goal)

    elif action == "complete":
        goal = _load_goal()
        if not goal.content:
            return "No active goal to complete."
        goal.status = "completed"
        goal.updated_at = time.time()
        _save_goal(goal)
        return f"✅ Goal completed: {goal.content}"

    elif action == "pause":
        goal = _load_goal()
        if not goal.content:
            return "No active goal to pause."
        if goal.status == "paused":
            goal.status = "active"
            goal.updated_at = time.time()
            _save_goal(goal)  # P7-B7-20: persist the resume (was in-memory only)
            return f"▶️  Goal resumed: {goal.content}"
        goal.status = "paused"
        goal.updated_at = time.time()
        _save_goal(goal)
        return f"⏸️  Goal paused: {goal.content}"

    elif action == "clear":
        if GOAL_FILE.exists():
            GOAL_FILE.unlink()
        return "🗑️  Goal cleared."

    else:
        return (
            "Usage: /goal [set|update|progress|complete|pause|clear] [args]\n"
            "  /goal set <goal>      — Set a new goal\n"
            "  /goal                 — View current goal\n"
            "  /goal update <goal>   — Update current goal\n"
            "  /goal progress <text> — Update progress\n"
            "  /goal complete        — Mark goal as complete\n"
            "  /goal pause           — Pause/resume goal\n"
            "  /goal clear           — Clear current goal"
        )
