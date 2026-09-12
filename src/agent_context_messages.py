"""Message construction methods for TauContext.

Provides `MessageBuilderMixin` with the `append_*` methods that build,
validate, log, and append messages to the context.

Requires the host class to provide:
    - self._messages: list[dict]
    - self.nesting_stack: str
    - self.spawn_B: float
    - self._append(msg: dict) -> None
"""

from __future__ import annotations

from typing import Any

from agent_message_utils import (
    _make_user_prefix,
    _sanitize_content,
    _sanitize_text,
    _SYNTHETIC_CATEGORY_TO_TYPE,
)
from agent_console import context_validation_warning


def _emit_context_validation_warning(*message_lines: str) -> None:
    """Emit a validation warning to the console."""
    context_validation_warning(list(message_lines))


class MessageBuilderMixin:
    """Message construction methods for TauContext.

    Mixin that provides the append_* API. The host class must define
    ``_messages``, ``nesting_stack``, ``spawn_B``, and ``_append()``.
    """

    # --- Synthetic user messages ---

    def append_synthetic_user(self, category: str, content: str | list, msg_count: int = 0, context_pct: float = 0.0, display: str | None = None) -> None:
        """Append a synthetic user message to the context.

        Synthetic messages are system-injected bridges that maintain valid
        OpenAI message alternation. They are prefixed with [U:TYPE | N:stack]
        so they can be detected and excluded from undo boundaries and
        consecutive-role validation.

        Logs to console (white) and audit for visibility.

        WARNING: If context ends with tool results, use
        `append_synthetic_user_with_bridge()` instead to maintain alternation.

        Args:
            category: The synthetic message category (e.g., 'heartbeat').
            content: The message content (without prefix).
            display: Optional compact display string for console (defaults to content).
        """
        # Map category to type
        user_type = _SYNTHETIC_CATEGORY_TO_TYPE.get(category, "system")
        # Create prefixed content
        prefix = _make_user_prefix(user_type, self.nesting_stack, msg_count, context_pct, self.spawn_B)
        if isinstance(content, str):
            prefixed_content = prefix + _sanitize_text(content)
        else:
            # Multimodal: prefix the text blocks, pass through image blocks
            prefixed_content = [
                {"type": "text", "text": prefix + _sanitize_text(item.get("text", ""))}
                if item.get("type") == "text" else item
                for item in content
            ]
        # Log synthetic message to console and audit
        # Skip for repl_output/repl_error — already displayed by append_repl_output()/append_repl_error()
        if category not in ("repl_output", "repl_error"):
            from agent_console import synthetic_user
            if display is not None:
                synthetic_user(category, display)
            else:
                synthetic_user(category, content if isinstance(content, str) else "")
        self._append({"role": "user", "content": prefixed_content})

    def append_synthetic_user_with_bridge(self, category: str, content: str,
                                           bridge_text: str = "[Processing new input...]", msg_count: int = 0, context_pct: float = 0.0, display: str | None = None) -> None:
        """Append a synthetic user message with an assistant bridge if needed.

        Ensures OpenAI alternation compliance: if the context ends with tool
        results, an assistant message, or a user message, a synthetic assistant
        message is added first. If the context already ends with an assistant
        message (no pending tool calls), the bridge is skipped.

        This is the PREFERRED method for injecting synthetic user messages
        during tool execution. See docs/designs/DECISIONS.md §27.3 (Full bridge requirement).

        Args:
            category: The synthetic message category (e.g., 'parent_inject').
            content: The message content (without prefix).
            bridge_text: Text for the assistant bridge message (default: minimal).
        """
        # Check if context ends with user message (bridge needed)
        needs_bridge = False
        if self._messages:
            last = self._messages[-1]
            if last.get("role") == "user":
                # Context ends with user message — need assistant bridge
                # before appending another (synthetic) user message
                needs_bridge = True

        if needs_bridge:
            # Add synthetic assistant bridge first
            self.append_assistant(bridge_text, synthetic=True)

        # Add synthetic user message
        self.append_synthetic_user(category, content, msg_count, context_pct, display=display)

    # --- Real user messages ---

    def append_user(self, content: str | list, user_type: str = "real", context_pct: float = 0.0) -> None:
        """Append a user message to the context.

        Emits warnings for invalid sequences (consecutive users, tool->user, etc).

        Args:
            content: The message content (without prefix).
            user_type: The user message type (real, fork, subagent, redirect).
                Defaults to "real" for backward compatibility.
        """
        if not self._messages:
            _emit_context_validation_warning(
                "Attempting to append user message to empty context.",
                "Context should be initialized with set_system() first.",
            )
            return

        last_msg = self._messages[-1]
        last_role = last_msg.get("role")

        if last_role == "user":
            _emit_context_validation_warning(
                "Attempting to append consecutive user messages.",
                "Assistant response required between user messages.",
            )
        elif last_role not in ("system", "assistant"):
            _emit_context_validation_warning(
                f"Invalid context state: cannot append user after role '{last_role}'.",
            )

        # Create prefixed content
        prefix = _make_user_prefix(user_type, self.nesting_stack, len(self._messages), context_pct, self.spawn_B)
        if isinstance(content, str):
            prefixed_content = prefix + _sanitize_content(content)
        else:
            # For list content (multimodal), prefix the text parts
            prefixed_content = [
                {"type": "text", "text": prefix + _sanitize_text(item.get("text", ""))}
                if item.get("type") == "text" else item
                for item in content
            ]
        self._append({"role": "user", "content": prefixed_content})

    # --- Assistant messages ---

    def append_assistant(
        self,
        content: str | None,
        reasoning: str | None = None,
        synthetic: bool = False,
    ) -> None:
        """Append an assistant message to the context.

        RLM mode: No tool calls. Only content and reasoning.

        Args:
            content: Message content.
            reasoning: Reasoning content (if any).
            synthetic: If True, log as synthetic/injected message (white console, audit).
        """
        # Log synthetic assistant messages to console and audit
        if synthetic and content is not None:
            from agent_console import synthetic_assistant
            synthetic_assistant(content)
        if not self._messages:
            _emit_context_validation_warning(
                "Attempting to append assistant message to empty context.",
                "Context should be initialized with set_system() first.",
            )
            return

        last_msg = self._messages[-1]
        last_role = last_msg.get("role")

        if last_role == "system":
            self.append_synthetic_user("turn_started", "Turn started.")
        # RLM mode: no continuation bridge — the loop guarantees a synthetic
        # user message follows every assistant message (REPL feedback).

        msg: dict[str, Any] = {"role": "assistant", "content": _sanitize_content(content) if content is not None else None}
        if reasoning is not None:
            msg["reasoning"] = _sanitize_text(reasoning)
        self._append(msg)

    # --- RLM REPL output methods ---

    def append_repl_output(self, output: str, max_chars: int = 8192, msg_count: int = 0, context_pct: float = 0.0) -> None:
        """Append REPL output as a synthetic user message.

        Used by the RLM loop to feed REPL execution results back into context.
        The output is truncated to max_chars to prevent context explosion.

        The message is prefixed with [REPL output] and wrapped in a synthetic
        user message with category="repl_output". This maintains the OpenAI
        message alternation pattern (assistant → synthetic user → assistant).

        Args:
            output: The REPL output text to append (stdout/stderr from kernel).
            max_chars: Maximum characters to include before truncation.
                       Default is 8192 to prevent context explosion.

        Notes:
            - If output exceeds max_chars, a truncation notice is appended
            - The synthetic message is not sent to the LLM (filtered by cleanup)
            - This preserves context alternation without polluting the conversation
            - Output is displayed in cyan with header/footer for distinction
        """
        truncated = output
        if len(output) > max_chars:
            truncated = output[:max_chars] + f"\n\n[Output truncated: {len(output)} chars total, showing first {max_chars}]"

        # Display REPL output with formatting (cyan)
        try:
            from agent_console import repl_output
            repl_output(truncated)
        except ImportError:
            pass  # Fallback to default display

        self.append_synthetic_user(
            category="repl_output",
            content=f"[REPL output]\n{truncated}",
            msg_count=msg_count,
            context_pct=context_pct
        )

    def append_repl_error(self, error: str, code: str = "", msg_count: int = 0, context_pct: float = 0.0) -> None:
        """Append REPL error as a synthetic user message.

        Used by the RLM loop to feed REPL execution errors back into context.
        The model can see the error and potentially fix it in the next iteration.

        The message is prefixed with [REPL error] and includes the failing code
        (truncated to 2000 chars) for context. This enables the model to debug
        and fix its own code.

        Args:
            error: The error message to append (exception type and message).
            code: The Python code that caused the error (for debugging context).
                  Truncated to 2000 chars to prevent context explosion.

        Notes:
            - Code is wrapped in python fences to reinforce the
              correct output pattern for the LLM.
              (Policy: fences now encouraged, not suppressed.)
            - The synthetic message maintains context alternation
            - Multiple consecutive errors trigger force-end in the RLM loop
            - Errors are displayed in red with header/footer for distinction
        """
        content = f"[REPL error]\n{error}"
        if code:
            # Truncate code to avoid context explosion
            if len(code) > 2000:
                code = code[:2000] + "\n... [code truncated]"
            # Wrap in python fences to reinforce the correct output pattern.
            content += f"\n\nCode that failed:\n{code}"

        self.append_synthetic_user(
            category="repl_error",
            content=content,
            msg_count=msg_count,
            context_pct=context_pct
        )
