"""Prompt templates for the dream orchestrator (tracked single source of truth).

Each prompt is a markdown file loaded via get_prompt(name). dream_steps.py reads
these instead of embedding prompt text, so prompts are version-controlled.
"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def get_prompt(name: str) -> str:
    """Load a prompt template by name (e.g. 'dotask', 'skillmaintenance')."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text()
