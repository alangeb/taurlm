"""Refine Command — /refine

Harness improvement — review current trajectory and apply small,
evidence-backed updates to supplemental harness state.

Usage:
    /refine              — Review current trajectory
    /refine apply <text> — Apply a refinement
    /refine history      — View refinement history
    /refine rollback     — Rollback last refinement
"""

import json
import time
from pathlib import Path
from typing import List

__all__ = [
    'REFINEMENT_FILE',
    'Refinement',
    'run'
]

REFINEMENT_FILE = Path(__file__).parent.parent.parent / ".refinements.json"


class Refinement:
    """A single refinement entry."""

    def __init__(
        self,
        content: str,
        reason: str = "",
        timestamp: float = 0.0,
        applied: bool = False,
    ):
        self.content = content
        self.reason = reason
        self.timestamp = timestamp or time.time()
        self.applied = applied

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Refinement":
        return cls(**data)


def _load_refinements() -> List[Refinement]:
    """Load refinements from file."""
    if REFINEMENT_FILE.exists():
        try:
            data = json.loads(REFINEMENT_FILE.read_text())
            return [Refinement.from_dict(r) for r in data]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _save_refinements(refinements: List[Refinement]) -> None:
    """Save refinements to file."""
    REFINEMENT_FILE.write_text(
        json.dumps([r.to_dict() for r in refinements], indent=2)
    )


def run(args: str = "") -> str:
    """Execute /refine command.

    Args:
        args: Command arguments (apply, history, rollback)

    Returns:
        Command output string.
    """
    args = args.strip().lower()

    if not args or args == "review":
        # Review current trajectory
        refinements = _load_refinements()
        if not refinements:
            return (
                "No refinements yet. Use /refine apply <text> to add one.\n"
                "Refinements are small, evidence-backed updates to harness state."
            )
        lines = [f"Refinements ({len(refinements)}):"]
        for i, r in enumerate(refinements[-10:], 1):  # Last 10
            status = "✅" if r.applied else "⏳"
            lines.append(f"  {status} {i}. {r.content[:100]}")
            if r.reason:
                lines.append(f"     Reason: {r.reason[:100]}")
        return "\n".join(lines)

    elif args.split(maxsplit=1)[0] == "apply":
        parts = args.split(maxsplit=1)
        content = parts[1].strip() if len(parts) > 1 else ""
        if not content:
            return "Usage: /refine apply <text>"
        refinements = _load_refinements()
        refinement = Refinement(content=content, applied=True)
        refinements.append(refinement)
        _save_refinements(refinements)
        return f"✅ Refinement applied: {content[:100]}"

    elif args == "history":
        refinements = _load_refinements()
        if not refinements:
            return "No refinement history."
        lines = ["Refinement History:"]
        for i, r in enumerate(refinements, 1):
            status = "✅" if r.applied else "⏳"
            lines.append(f"  {status} {i}. {r.content[:100]}")
            if r.reason:
                lines.append(f"     Reason: {r.reason[:100]}")
        return "\n".join(lines)

    elif args == "rollback":
        refinements = _load_refinements()
        if not refinements:
            return "No refinements to rollback."
        last = refinements.pop()
        _save_refinements(refinements)
        return f"↩️  Rolled back: {last.content[:100]}"

    else:
        return (
            "Usage: /refine [apply|history|rollback]\n"
            "  /refine              — Review current trajectory\n"
            "  /refine apply <text> — Apply a refinement\n"
            "  /refine history      — View refinement history\n"
            "  /refine rollback     — Rollback last refinement"
        )
