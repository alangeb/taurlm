"""Loop detection for TauErgon RLM agent.

Detects repetitive and cyclical patterns in RLM execution indicating potential
infinite loops:

1. **Consecutive repeat detection**: Warns after repeat_threshold identical
   Python code blocks (default: 3).
2. **Shannon entropy analysis**: Warns when entropy drops below 1.5 over
   rolling window (default: 30).
3. **Answer stagnation**: Detects when answer["content"] doesn't change
   across multiple turns.

Key class:
- LoopDetector: Main detection class with configurable window_size and
  repeat_threshold.

Escalation levels (warnings accumulate monotonically, no reset):
- Level 0: No escalation (normal operation, <3 warnings)
- Level 1: Alert warnings prepended to REPL output (warnings 3-5)
- Level 2: Simulated self-reflection via synthetic user messages (warnings 6-8)
- Level 3: Guided introspection with structured questions (warnings 9-11)
- Level 4: Forced analysis with deep introspection (warnings 12-14)
- Level 5: Termination — force_end_turn (warnings 15+)

In RLM mode, loop detection tracks Python code execution patterns instead
of tool call patterns.
"""

from __future__ import annotations

import math
from collections import Counter, deque

# Escalating warning message templates
WARNING_LEVEL_1 = (
    "⚠️ LOOP WARNING #{warning_count}: Python code pattern repeated "
    "{consecutive} times consecutively. "
    "This is warning #{warning_count} this turn. Consider changing your approach."
)

WARNING_LEVEL_2 = (
    "🔴 LOOP WARNING #{warning_count}: Python code pattern repeated "
    "{consecutive} times consecutively. "
    "This is warning #{warning_count} this turn. You appear stuck in a loop. "
    "Re-analyze the situation before continuing."
)

WARNING_LEVEL_3 = (
    "🚨 CRITICAL LOOP #{warning_count}: Code pattern repeated {consecutive} times. "
    "Warning #{warning_count} this turn. You MUST re-analyze now. "
    "Do not execute more code until you have planned."
)

# Entropy warning templates (parallel to WARNING_LEVEL_*)
ENTROPY_WARNING_LEVEL_1 = (
    "⚠️ LOW ENTROPY #{warning_count}: Code pattern "
    "highly predictable (entropy: {entropy:.2f}). "
    "Consider re-analyzing."
)

ENTROPY_WARNING_LEVEL_2 = (
    "🔴 ENTROPY WARNING #{warning_count}: Code pattern "
    "highly predictable (entropy: {entropy:.2f}). "
    "Re-analyze the situation."
)

ENTROPY_WARNING_LEVEL_3 = (
    "🚨 CRITICAL ENTROPY #{warning_count}: Code pattern "
    "highly predictable (entropy: {entropy:.2f}). "
    "You MUST re-analyze now."
)

# Grouped templates: index = escalation_level-1 (clamped to 0..2)
_WARNING_TEMPLATES = [WARNING_LEVEL_1, WARNING_LEVEL_2, WARNING_LEVEL_3]
_ENTROPY_TEMPLATES = [ENTROPY_WARNING_LEVEL_1, ENTROPY_WARNING_LEVEL_2, ENTROPY_WARNING_LEVEL_3]

# Sustained entropy warning templates (for cycles above 1.5 threshold)
SUSTAINED_ENTROPY_WARNING_LEVEL_1 = (
    "⚠️ SUSTAINED LOW ENTROPY #{warning_count}: Code pattern has been highly predictable "
    "for {sustained_window} consecutive windows (entropy: {entropy:.2f}). "
    "This indicates a likely infinite loop. Re-analyze immediately."
)

SUSTAINED_ENTROPY_WARNING_LEVEL_2 = (
    "🔴 SUSTAINED ENTROPY WARNING #{warning_count}: Code pattern has been highly predictable "
    "for {sustained_window} consecutive windows (entropy: {entropy:.2f}). "
    "You appear stuck in a loop. Re-analyze."
)

SUSTAINED_ENTROPY_WARNING_LEVEL_3 = (
    "🚨 CRITICAL SUSTAINED ENTROPY #{warning_count}: Code pattern has been highly predictable "
    "for {sustained_window} consecutive windows (entropy: {entropy:.2f}). "
    "You MUST re-analyze now."
)

_SUSTAINED_ENTROPY_TEMPLATES = [
    SUSTAINED_ENTROPY_WARNING_LEVEL_1,
    SUSTAINED_ENTROPY_WARNING_LEVEL_2,
    SUSTAINED_ENTROPY_WARNING_LEVEL_3,
]

# Answer stagnation warning
ANSWER_STAGNATION_WARNING = (
    "⚠️ ANSWER STAGNATION #{warning_count}: answer['content'] has not changed "
    "for {stagnant_turns} consecutive turns. You may be stuck. Consider "
    "taking a different approach or setting answer['ready'] = True if done."
)




class StreamAbortChecker:
    """Checks streaming token output for abort conditions.

    Feeds tokens incrementally and signals when the stream should be
    aborted (e.g. token budget exceeded).
    """

    def __init__(self, max_tokens: int | None = None) -> None:
        self._max_tokens = max_tokens
        self._token_count = 0
        self._aborted = False
        self._reason: str | None = None

    def feed(self, token: str) -> None:
        """Feed a token (or token chunk) to the checker."""
        if self._aborted:
            return
        # Rough word-based token estimate (1 token ≈ 1 word + 1 for punctuation/whitespace)
        self._token_count += len(token.split()) + 1
        if (
            self._max_tokens is not None
            and self._max_tokens > 0
            and self._token_count > self._max_tokens
        ):
            self._aborted = True
            self._reason = (
                f"Stream aborted: estimated token count {self._token_count} "
                f"exceeded max_tokens={self._max_tokens}"
            )

    @property
    def should_abort(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def token_count(self) -> int:
        return self._token_count


__all__ = ["LoopDetector", "StreamAbortChecker"]


class LoopDetector:
    """Detect repetitive or cyclical patterns in RLM execution.

    1. **Repeat**: Warns after *repeat_threshold* identical consecutive
       code blocks.
    2. **Entropy**: Warns when Shannon entropy over the last *window_size*
       code blocks drops below 1.5 (highly predictable pattern).
    3. **Escalation**: Tracks cumulative warnings and escalates intervention.
    4. **Answer stagnation**: Warns when answer["content"] doesn't change
       across multiple turns.
    """

    def __init__(
        self,
        window_size: int = 30,
        repeat_threshold: int = 3,
        warn_threshold: int = 3,
        inject_threshold: int = 7,
        force_think_threshold: int = 11,
        end_turn_threshold: int = 15,
        sustained_window: int = 3,
        sustained_threshold: float = 2.5,
        answer_stagnation_threshold: int = 3,
    ):
        """Initialize loop detector.

        Args:
            window_size: Rolling window size for entropy calculation.
            repeat_threshold: Consecutive identical code blocks before warning.
            warn_threshold: Warnings before level 1 escalation.
            inject_threshold: Warnings before level 2 escalation.
            force_think_threshold: Warnings before level 3 escalation.
            end_turn_threshold: Warnings before forced termination.
            sustained_window: Consecutive low-entropy windows for sustained warning.
            sustained_threshold: Entropy threshold for sustained warnings.
            answer_stagnation_threshold: Turns without answer change before warning.
        """
        self.window_size = window_size
        self.repeat_threshold = repeat_threshold
        self.warn_threshold = warn_threshold
        self.inject_threshold = inject_threshold
        self.force_think_threshold = force_think_threshold
        self.end_turn_threshold = end_turn_threshold

        # Code execution history
        self.code_history: deque[str] = deque(maxlen=window_size)
        self.consecutive_repeats = 0
        self.last_code_hash: str | None = None

        # Warning tracking
        self.total_warnings = 0
        self.entropy_warnings = 0
        self.code_warnings: dict[str, int] = {}
        self.escalation_level = 0

        # Sustained entropy tracking
        self.sustained_window = sustained_window
        self.sustained_threshold = sustained_threshold
        self._entropy_history: deque[float] = deque(maxlen=sustained_window)

        # Answer stagnation tracking
        self.answer_stagnation_threshold = answer_stagnation_threshold
        self._last_answer_content: str | None = None
        self._answer_stagnant_turns = 0

    def _code_hash(self, code: str) -> str:
        """Create a hash of code for comparison.

        Normalizes whitespace and removes comments for better comparison.
        """
        if not code:
            return ""
        # Normalize whitespace
        normalized = " ".join(code.split()).strip()  # H13: no comment stripping
        return normalized

    @staticmethod
    def _select_template(level: int, templates: list[str]) -> str:
        """Select a template by escalation level, clamped to valid range."""
        idx = min(max(level - 1, 0), len(templates) - 1)
        return templates[idx]

    def _get_warning_message(self, code_hash: str) -> str:
        """Build repeat warning message."""
        template = self._select_template(self.escalation_level, _WARNING_TEMPLATES)
        return template.format(
            warning_count=self.total_warnings,
            consecutive=self.consecutive_repeats,
        )

    def _get_entropy_warning(self, entropy: float) -> str:
        """Build entropy warning message."""
        template = self._select_template(self.escalation_level, _ENTROPY_TEMPLATES)
        return template.format(
            warning_count=self._display_warning_count(),
            entropy=entropy,
        )

    def _get_sustained_entropy_warning(self, entropy: float) -> str:
        """Build sustained entropy warning message."""
        template = self._select_template(
            self.escalation_level, _SUSTAINED_ENTROPY_TEMPLATES
        )
        return template.format(
            warning_count=self._display_warning_count(),
            entropy=entropy,
            sustained_window=self.sustained_window,
        )

    def _get_answer_stagnation_warning(self) -> str:
        """Build answer stagnation warning message."""
        return ANSWER_STAGNATION_WARNING.format(
            warning_count=self.total_warnings,
            stagnant_turns=self._answer_stagnant_turns,
        )

    def _display_warning_count(self) -> int:
        """Total human-readable warning count.

        total_warnings already includes entropy warnings (incremented in
        check_code_repetition), so adding entropy_warnings again here
        double-counted them.
        """
        return self.total_warnings

    def _update_escalation_level(self) -> None:
        """Update escalation level based on total_warnings.

        Level structure (configurable via thresholds):
        - Level 0: < warn_threshold (no escalation)
        - Level 1: warn_threshold+ (alert)
        - Level 2: inject_threshold+ (self-reflection)
        - Level 3: force_think_threshold+ (guided introspection)
        - Level 4: end_turn_threshold+ (forced analysis)
        - Level 5: end_turn_threshold+3 (termination)
        """
        if self.total_warnings >= self.end_turn_threshold + 3:
            new_level = 5
        elif self.total_warnings >= self.end_turn_threshold:
            new_level = 4
        elif self.total_warnings >= self.force_think_threshold:
            new_level = 3
        elif self.total_warnings >= self.inject_threshold:
            new_level = 2
        elif self.total_warnings >= self.warn_threshold:
            new_level = 1
        else:
            new_level = 0
        if new_level > self.escalation_level:
            self.escalation_level = new_level

    def _calculate_entropy(self) -> float:
        """Calculate Shannon entropy of code execution history."""
        if len(self.code_history) < 2:
            return float("inf")

        counter = Counter(self.code_history)
        total = len(self.code_history)
        entropy = 0.0

        for count in counter.values():
            if count == 0:
                continue
            probability = count / total
            entropy -= probability * math.log2(probability)

        return entropy

    def record_code_execution(self, code: str) -> str | None:
        """Record a code execution and check for loops.

        Args:
            code: The Python code that was executed.

        Returns:
            Warning message if a loop is detected, None otherwise.
        """
        code_hash = self._code_hash(code)
        if not code_hash:
            return None  # L-S2: skip empty code

        # Check consecutive repeats
        if code_hash == self.last_code_hash:
            self.consecutive_repeats += 1
        else:
            self.consecutive_repeats = 1
            self.last_code_hash = code_hash

        # Add to history for entropy calculation
        self.code_history.append(code_hash)

        # Check repeat threshold
        if (
            self.consecutive_repeats >= self.repeat_threshold
            and code_hash
        ):
            self.total_warnings += 1
            self.code_warnings[code_hash] = self.code_warnings.get(code_hash, 0) + 1
            self._update_escalation_level()
            return self._get_warning_message(code_hash)

        # Check entropy
        entropy = self._calculate_entropy()
        if entropy < 1.5 and len(self.code_history) >= self.window_size // 2:
            self.entropy_warnings += 1
            self.total_warnings += 1  # C3: was 0.5, caused float crash in escalation  # Entropy warnings count as 0.5
            self._update_escalation_level()

            # Check sustained low entropy
            self._entropy_history.append(entropy)
            if (
                len(self._entropy_history) >= self.sustained_window
                and all(e < self.sustained_threshold for e in self._entropy_history)
            ):
                return self._get_sustained_entropy_warning(entropy)

            return self._get_entropy_warning(entropy)

        return None

    def check_answer_stagnation(self, answer_content: str | None) -> str | None:
        """Check if answer content has stagnated.

        Args:
            answer_content: Current answer content.

        Returns:
            Warning message if answer is stagnant, None otherwise.
        """
        if answer_content is None:
            return None

        if answer_content == self._last_answer_content:
            self._answer_stagnant_turns += 1
        else:
            self._answer_stagnant_turns = 0
            self._last_answer_content = answer_content

        if self._answer_stagnant_turns >= self.answer_stagnation_threshold:
            self.total_warnings += 1
            self._update_escalation_level()
            return self._get_answer_stagnation_warning()

        return None

    def get_escalation_info(self) -> dict:
        """Get current escalation information.

        Returns:
            Dict with escalation_level, total_warnings, and other info.
        """
        return {
            "escalation_level": self.escalation_level,
            "total_warnings": self.total_warnings,
            "repeat_warnings": self.total_warnings - self.entropy_warnings,
            "entropy_warnings": self.entropy_warnings,
            "consecutive_repeats": self.consecutive_repeats,
            "entropy": self._calculate_entropy(),
            "answer_stagnant_turns": self._answer_stagnant_turns,
        }

    def get_stats(self) -> dict:
        """Get loop detection stats (alias for get_escalation_info).

        Returns:
            Dict with loop detection statistics.
        """
        return self.get_escalation_info()

    def reset(self) -> None:
        """Reset all detection state for a new turn."""
        # L-S3: code_history preserved across resets
        self.consecutive_repeats = 0
        self.last_code_hash = None
        self.total_warnings = 0
        self.entropy_warnings = 0
        self.code_warnings.clear()
        self.escalation_level = 0
        self._entropy_history.clear()
        self._last_answer_content = None
        self._answer_stagnant_turns = 0
