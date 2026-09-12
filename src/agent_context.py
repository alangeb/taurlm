"""Conversation context management for TauRLM.

Core `TauContext` class maintains OpenAI-compatible conversation context with
strict validation for API compliance.

DESIGN INVARIANT: Message alternation is maintained via synthetic bridges.
RLM mode has NO tool calls - only system, user, assistant messages.

"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from agent_core import TauErgon

from agent_console import (
    _role_color,
)
from agent_audit_bridge import log_context_add, log_context_remove, log_context_snapshot
from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from agent_message_utils import (
    _sanitize_content,
    _sanitize_text,
    is_real_user_request,
)
from agent_context_validation import (
    validate_context,
    validate_on_mutation,
)
from agent_context_turn import TurnLifecycleMixin
from agent_context_messages import MessageBuilderMixin
from agent_context_store import ContextStoreMixin


__all__ = ["TauContext", "ContextMessage", "TauContextInstance"]

ContextMessage: TypeAlias = dict[str, Any]
TauContextInstance: TypeAlias = "TauContext"



# ── TauContext ────────────────────────────────────────────────────────────────


class TauContext(TurnLifecycleMixin, MessageBuilderMixin, ContextStoreMixin):
    """In-place conversation context with validation after every mutation.

    Each TauErgon owns exactly one instance. Fork metadata is stored separately
    from the message list to avoid polluting conversation history.

    """
    def __init__(self, messages: list[dict] | None = None, nesting_stack: str = "0"):
        self._messages: list[dict] = list(messages) if messages else []
        self._metadata: dict[str, Any] = {}
        self._fork_metadata: dict[str, Any] = dict(self._DEFAULT_FORK_METADATA)
        self.nesting_stack: str = nesting_stack
        self.spawn_B: float = 0.0
        self._validate_on_mutation()

    # --- List protocol ---
    @property
    def messages(self) -> list[dict[str, Any]]:
        """Read-only access to the message list."""
        return self._messages

    def __len__(self) -> int:
        """Return the number of messages."""
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def __getitem__(self, idx: int) -> dict:
        return self._messages[idx]

    def __contains__(self, item: Any) -> bool:
        return item in self._messages

    def __repr__(self) -> str:
        return f"TauContext({len(self)} msgs)"

    # --- Metadata ---
    def set_metadata(self, **kwargs) -> None:
        """Set metadata fields for context file save.

            Args:
                **kwargs: Metadata key-value pairs (e.g., pid, working_dir, start_time, model, agent_name).
        """
        self._metadata.update(kwargs)

    # --- Validation (delegated to agent_context_validation) ---
    def _validate_on_mutation(self) -> None:
        """Validate context after mutation, printing warnings for errors."""
        validate_on_mutation(self._messages)

    # --- Mutations ---
    def _append(self, msg: dict) -> None:
        """Internal method to append a message to the context."""
        self._messages.append(msg)
        log_context_add(1, len(self._messages), self.bytes_size())

    def clear(self) -> None:
        """Clear all messages except the system prompt (preserved at index 0)."""
        system = (
            self._messages[0]
            if self._messages and self._messages[0].get("role") == "system"
            else None
        )
        removed = len(self._messages) - (1 if system is not None else 0)
        self._messages.clear()
        if system is not None:
            self._messages.append(system)
        log_context_remove(removed, len(self._messages), self.bytes_size())
        self._validate_on_mutation()

    def extend(self, msgs: list[dict]) -> None:
        """Extend the context by appending multiple messages at once."""
        self._messages.extend(msgs)
        log_context_add(len(msgs), len(self._messages), self.bytes_size())
        self._validate_on_mutation()

    def undo(self) -> None:
        """Undo the last conversation turn by removing messages from the last user message onward.

            Preserves the system message (if present at index 0) and all messages
            up to but not including the last user message.

            Non-real user messages (synthetic and REPL) are skipped -
            they are system-injected and should not affect undo boundaries.
        """
        if len(self._messages) < 2:
            return
        last_user_idx = None
        for i in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[i]
            if is_real_user_request(msg):
                last_user_idx = i
                break
        if last_user_idx is None:
            return
        if last_user_idx == 0:
            return
        removed = len(self._messages) - last_user_idx
        self._messages = self._messages[:last_user_idx]
        log_context_remove(removed, len(self._messages), self.bytes_size())
        self._validate_on_mutation()

    # --- Validation (delegated to agent_context_validation) ---

    def remove_last_n(self, n: int) -> None:
        """Remove the last n messages with validation.

        Args:
            n: Number of messages to remove from the end.
        """
        if n <= 0 or n > len(self._messages):
            return
        self._messages = self._messages[:-n]
        self._validate_on_mutation()

    def validate(self) -> list[str]:
        """Validate the entire context against OpenAI API compliance rules."""
        return validate_context(self._messages)

    # --- Token estimation ---







    def set_system(self, content: str) -> None:
        if any(m.get("role") == "system" for m in self._messages):
            raise ValueError(
                "Cannot set system message - system message already exists"
            )
        self._append({"role": "system", "content": _sanitize_text(content)})

    def update_system(self, content: str) -> None:
        """Update the system message content.

        Args:
            content: New system message content.

        Raises:
            ValueError: If no system message exists.
        """
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0]["content"] = _sanitize_text(content)
        else:
            raise ValueError("Cannot update system message - no system message exists")

    # --- Getters / setters ---
    def get_messages(self) -> list[dict]:
        """Return a copy of the internal message list."""
        return self._messages.copy()

    def set_messages(self, msgs: list[dict]) -> None:
        """Replace all messages in the context."""
        self._messages = [self._sanitize_message(m) for m in msgs]
        self._validate_on_mutation()
    def _sanitize_message(self, msg: dict) -> dict:
        """Sanitize a single message — strips lone UTF-16 surrogates."""
        sanitized = dict(msg)
        for field, fn in (("content", _sanitize_content), ("reasoning", _sanitize_text)):
            if msg.get(field) is not None:
                sanitized[field] = fn(msg[field])
        return sanitized

    def copy(self) -> TauContext:
        """Create a shallow copy of the context (messages only, not fork metadata)."""
        return TauContext([dict(m) for m in self._messages])

    def set_fork_metadata(
        self, fork_call_id: str | None = None, fork_task: str | None = None
    ) -> None:
        """Update fork metadata."""
        self._fork_metadata["fork_call_id"] = fork_call_id
        self._fork_metadata["fork_task"] = fork_task

    def clear_fork_metadata(self) -> None:
        """Reset fork metadata to default empty values."""
        self._fork_metadata = dict(self._DEFAULT_FORK_METADATA)

    # --- Compression ---
    _DEFAULT_FORK_METADATA = {"fork_call_id": None, "fork_task": None}

    def compress(
        self,
        target_percentage: float,
        agent: TauErgon,
        last_known_tokens: int | None = None,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> bool:
        """Compress the context to the target percentage. Delegate to compress_api."""
        from agent_context_compress.compress_api import compress as _compress
        return _compress(
            self, agent, target_percentage,
            last_known_tokens=last_known_tokens,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
        )

    def compress_to_target(
        self,
        target_pct: int,
        agent: TauErgon,
    ) -> bool:
        """Compress to target percentage. Delegate to compress_api."""
        from agent_context_compress.compress_api import compress_to_target as _c2t
        return _c2t(self, agent, target_pct)

    def to_list(self) -> list[dict]:
        """Convert the context to a plain list for JSON serialization. Alias of get_messages()."""
        return self.get_messages()

    def get_system(self) -> str | None:
        """Return the system message content (at index 0), or None."""
        return self._messages[0].get("content") if self._messages and self._messages[0].get("role") == "system" else None


