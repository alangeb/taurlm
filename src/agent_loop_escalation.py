"""Loop escalation management for TauErgon RLM agent.

Handles loop detection escalation, reflection injection, and recovery from
invalid end-of-turn states in RLM mode.

Key class:
- LoopEscalationManager: Orchestrates loop escalation, recovery, and reflection

Escalation ladder (warnings accumulate monotonically, no reset):
- Level 0-1 (warnings 0-5): Alert warnings prepended to REPL output
- Level 2 (warnings 6-8): Simulated self-reflection via synthetic user messages
- Level 3 (warnings 9-11): Guided introspection with structured questions
- Level 4+ (warnings 12+): Termination — force_end_turn with retry suggestion

In RLM mode, escalation returns (text, force_end) tuples for the caller
to include in a single synthetic user message, since the main agent has no tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_console import loop_warning
from agent_loop_detect import LoopDetector

if TYPE_CHECKING:
    from agent_context import TauContext
    from agent_core import TauErgon


__all__ = [
    'LoopDetector',
    'LoopEscalationManager',
    'loop_warning'
]

class LoopEscalationManager:
    """Manage loop detection escalation, reflection injection, and recovery.

    Encapsulates the loop escalation logic for RLM mode:
    - Escalation handling (levels 0-4+)
    - Recovery from invalid end-of-turn states
    - Periodic reflection injection

    In RLM mode, escalation returns (text, force_end) tuples for the caller
    to include in a single synthetic user message, since the main agent has no tools.

    Attributes:
        loop_detector: LoopDetector instance for pattern detection.
        context: TauContext instance for context manipulation.
        agent: Parent TauErgon reference for agent state access.
    """

    def __init__(
        self,
        loop_detector: LoopDetector,
        context: TauContext,
        agent: TauErgon,
    ):
        """Initialize the loop escalation manager.

        Args:
            loop_detector: LoopDetector instance for pattern detection.
            context: TauContext instance for context manipulation.
            agent: Parent TauErgon reference for agent state access.
        """
        self._loop_detector = loop_detector
        self._context = context
        self._agent = agent

    def handle_loop_escalation(self) -> tuple[str | None, bool]:
        """Handle loop escalation based on total_warnings count.

        4-level escalation system:
        - Level 0-1 (warnings 0-5): No escalation text; warnings in feedback
        - Level 2 (warnings 6-8): Self-reflection prompt
        - Level 3 (warnings 9-11): Guided introspection
        - Level 4+ (warnings 12+): Termination — force end turn

        Returns:
            (escalation_text, should_force_end): escalation_text is None for
            levels 0-1, or a prompt string for levels 2-4. should_force_end
            is True only at level 4+ (termination).
        """
        info = self._loop_detector.get_escalation_info()
        total = info["total_warnings"]
        level = info["escalation_level"]

        if level >= 4:
            # Termination: force end of turn with retry suggestion
            loop_warning(4, f"Turn terminated: {total} loop warnings")
            return self._format_termination_message(info), True

        if level == 3:
            # Guided introspection
            user_text = self._get_level3_text(total, info)
            loop_warning(3, f"Guided introspection: {total} warnings")
            return user_text, False

        if level == 2:
            # Self-reflection
            user_text = self._get_level2_text(total, info)
            loop_warning(2, f"Self-reflection: {total} warnings")
            return user_text, False

        # Level 0-1: alert only — loop_prefix in REPL output handles prepend
        if level == 1:
            loop_warning(1, f"Possible loop: {total} warnings")
        return None, False

    # ── Text generation helpers ──────────────────────────────────────────────

    def _text_index(self, total_warnings: int, level_start: int) -> int:
        """Get text index (0, 1, 2) for a given warning count within a level."""
        return int((total_warnings - level_start) % 3)

    def _get_level2_text(self, total_warnings: int, info: dict) -> str:
        """Get self-reflection text (level 2)."""
        idx = self._text_index(total_warnings, 6)
        texts = [
            (
                "[SYSTEM: Self-reflection] You appear to be in a loop. "
                "Pause and analyze:\n"
                "1. What code have you been executing repeatedly?\n"
                "2. Why isn't it producing the expected result?\n"
                "3. What alternative approach could you try?\n"
                "4. Should you set answer['ready'] = True if you're done?"
            ),
            (
                "[SYSTEM: Self-reflection] Loop detected. Before executing more "
                "code, reflect:\n"
                "1. Is your current approach working?\n"
                "2. What information are you missing?\n"
                "3. Could you use a sub-LLM (rlm()) to investigate?\n"
                "4. Are you close to completing the task?"
            ),
            (
                "[SYSTEM: Self-reflection] You've been repeating code patterns. "
                "Consider:\n"
                "1. Changing your strategy entirely\n"
                "2. Using a different Python module or function\n"
                "3. Delegating to a sub-LLM for research\n"
                "4. Setting answer['content'] and answer['ready'] = True"
            ),
        ]
        return texts[idx]

    def _get_level3_text(self, total_warnings: int, info: dict) -> str:
        """Get guided introspection text (level 3)."""
        idx = self._text_index(total_warnings, 9)
        texts = [
            (
                "[SYSTEM: Guided introspection] You are stuck in a persistent loop. "
                "Answer these questions:\n"
                "1. What is the exact task you're trying to complete?\n"
                "2. What progress have you made?\n"
                "3. What is blocking you?\n"
                "4. What is the minimum viable answer you can provide?\n"
                "5. Set answer['content'] with your best answer and "
                "answer['ready'] = True."
            ),
            (
                "[SYSTEM: Guided introspection] Loop continues. Critical analysis:\n"
                "1. Have you been executing the same code with minor variations?\n"
                "2. Is the problem unsolvable with your current approach?\n"
                "3. Could a sub-LLM help with research or analysis?\n"
                "4. What partial answer can you provide?\n"
                "5. Set answer['content'] and answer['ready'] = True."
            ),
            (
                "[SYSTEM: Guided introspection] Persistent loop. Final analysis:\n"
                "1. What have you learned from the repeated attempts?\n"
                "2. What is the simplest code that could work?\n"
                "3. Should you abandon this approach entirely?\n"
                "4. What answer can you give based on what you know?\n"
                "5. Set answer['content'] and answer['ready'] = True."
            ),
        ]
        return texts[idx]

    def _format_termination_message(self, info: dict) -> str:
        """Format termination message."""
        total = info["total_warnings"]
        return (
            f"Loop detected ({total} warnings) — terminating turn. "
            "Your last answer content was preserved. "
            "Try a different approach in the next turn."
        )
