"""Turn lifecycle operations for TauContext.

Extracted from agent_context.py to separate the turn boundary normalization
concern (cleanup, merge, close) from the core message container.

Uses the mixin pattern: TurnLifecycleMixin provides methods that operate on
a TauContext instance via `self`. No call-site changes required.
"""

from __future__ import annotations

from agent_audit_bridge import (
    log_context_merge,
    log_context_remove,
    log_context_snapshot,
)
from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS
from agent_message_utils import _merge_content, is_synthetic_message
from agent_console import warning


def _merge_content_field(last: dict, msg: dict) -> None:
    """Merge the 'content' field from *msg* into *last*.

    Handles string, list, and None values. If both are present,
    concatenates via ``_merge_content``. If only *msg* has content,
    copies it. If both are None, leaves *last* unchanged.
    """
    last_content = last.get("content")
    msg_content = msg.get("content")
    if last_content is not None and msg_content is not None:
        last["content"] = _merge_content(last_content, msg_content)
    elif msg_content is not None:
        last["content"] = msg_content


def _merge_string_field(last: dict, msg: dict, field: str) -> None:
    """Merge a string field (e.g. 'reasoning', 'refusal') from *msg* into *last*.

    If both have the field, concatenates via ``_merge_content``.
    If only *msg* has it, copies it.
    """
    if msg.get(field) is not None:
        last_val = last.get(field)
        if last_val is not None:
            last[field] = _merge_content(last_val, msg[field])
        else:
            last[field] = msg[field]



class TurnLifecycleMixin:
    """Mixin providing turn boundary lifecycle operations for TauContext.

    Methods:
        cleanup_synthetic() — remove synthetic bridge messages
        merge_consecutive_assistants() — consolidate same-role messages
        close_turn() — finalize a turn to valid terminal state
    """
    def cleanup_synthetic(self) -> None:
        """Remove synthetic bridge messages from the context at turn end.

        Removes messages with synthetic types (meta, confirm, inject, system)
        — e.g., continuation bridges, escalation notices, REPL errors.

        REPL errors ARE visible to the LLM during the loop (they drive self-correction).
        They are cleaned up at turn end so the next user turn starts fresh.

        REPL output (successful execution results) is type="repl" — NOT synthetic —
        and survives cleanup. Successful turns stay on the context stack so the LLM
        can see what it accomplished.

        **Paired assistant removal:** When a repl_error is found, the immediately
        preceding assistant message (the code that caused the error) is also removed.
        This prevents dead-end code from polluting context. Successful turns
        (assistant + repl_output) are preserved intact.

        **Explicit merge design:** This method removes bridges ONLY. It does NOT
        merge consecutive same-role messages. The caller must explicitly invoke
        `merge_consecutive_assistants()` to consolidate any consecutive assistant
        messages left behind.
        This separation prevents hidden mutations: cleanup is pure data removal,
        merge is an explicit policy decision controlled by the caller.

        See docs/designs/DECISIONS.md §18.7 for the rationale.
        """
        # Two-pass: first identify which assistant indices to remove (those
        # immediately preceding a repl_error), then filter everything out.
        remove = set()
        for i, m in enumerate(self._messages):
            if is_synthetic_message(m):
                remove.add(i)
                # If this is a repl_error, also mark the preceding assistant.
                content = m.get("content", "")
                if isinstance(content, str) and "[REPL error]" in content:
                    if i > 0 and self._messages[i - 1].get("role") == "assistant":
                        remove.add(i - 1)

        removed = len(remove)
        self._messages = [m for i, m in enumerate(self._messages) if i not in remove]
        if removed:
            log_context_remove(removed, len(self._messages), self.bytes_size())
        # Intentionally NO _validate_on_mutation() here.
        # Removing bridges creates transient consecutive same-role messages.
        # Validation runs after merge_consecutive_assistants() in close_turn().

    def merge_consecutive_assistants(self) -> None:
        """Merge consecutive same-role messages into single messages.

        RLM mode: Only merges assistant and user messages. No tool messages.

        Merge strategy:
        - `content`: concatenated with newline separator
        - `reasoning`: concatenated with newline separator (assistant only)
        - `refusal`: concatenated with newline separator (assistant only)
        """
        if len(self._messages) <= 1:
            return

        merged: list[dict] = [dict(self._messages[0])]

        for msg in self._messages[1:]:
            last = merged[-1]
            last_role = last.get("role")
            msg_role = msg.get("role")

            if last_role == msg_role and last_role == "assistant":
                _merge_content_field(last, msg)
                _merge_string_field(last, msg, "reasoning")
                _merge_string_field(last, msg, "refusal")
            elif last_role == msg_role and last_role == "user":
                # Consecutive user messages — merge gracefully.
                _merge_content_field(last, msg)
                warning(
                    f"merge_consecutive_assistants(): merged consecutive "
                    f"'user' messages — context state was non-alternating. "
                    f"Merged {len(merged)} messages so far."
                )
            else:
                merged.append(dict(msg))

        # Log the merge: count = original - merged
        if len(merged) < len(self._messages):
            log_context_merge("assistant", "assistant", len(self._messages) - len(merged))
        self._messages = merged

    def close_turn(self, reason: str, skip_cleanup: bool = False) -> None:
        """Close an incomplete turn to ensure the context ends in a valid terminal state.

        Explicit merge design: cleans up all synthetic messages via cleanup_synthetic(),
        then explicitly merges consecutive assistant messages via merge_consecutive_assistants().
        This two-step approach ensures no hidden mutations: cleanup removes internal-only
        synthetic bridges, merge consolidates the resulting consecutive assistant messages.

        Resolves pending tool calls with the provided reason, then appends an
        assistant message if the last role is user or tool.
        If the last role is "system" or "assistant", inserts a synthetic user
        message first to maintain valid message alternation.

        If *skip_cleanup* is True, defer cleanup_synthetic() to after the next
        LLM call (via agent._cleanup_pending). This lets compression see the full
        context including REPL errors for a richer summary and better cache hits.

        Idempotent: if context already ends with "assistant" and has no pending
        tool calls, this is a no-op (already in valid terminal state).
        """
        if not self._messages:
            return

        # Clean up synthetic messages before closing the turn.
        # Deferred cleanup (skip_cleanup=True) lets the next LLM call's compression
        # see the full context including REPL errors for a richer summary.
        if not skip_cleanup:
            self.cleanup_synthetic()
        self.merge_consecutive_assistants()
        # Repair: if cleanup removed a synthetic user bridge that was the only separator
        # between system and the first assistant, insert a minimal user message to
        # maintain valid alternation (system → user → assistant).
        if (
            len(self._messages) >= 2
            and self._messages[0].get("role") == "system"
            and self._messages[1].get("role") == "assistant"
        ):
            self._messages.insert(
                1, {"role": "user", "content": "[U:system | N:0] [context boundary]"}  # M-L4: N:0 is correct - synthetic boundary marker, not a real message
            )
        # Validate after merge — cleanup_synthetic() intentionally skips validation
        # because bridge removal creates transient consecutive same-role messages.
        self._validate_on_mutation()

        last_role = self._messages[-1].get("role")

        # Idempotent: if already in valid terminal state (assistant), do nothing
        if last_role == "assistant":
            return

        if last_role == "system":
            # Insert synthetic user message to maintain valid alternation
            self.append_synthetic_user("turn_closed", f"Turn closed: {reason}")
        if self._messages[-1].get("role") in ("user", "tool"):
            self.append_assistant(reason)
        # Log context snapshot at turn boundary
        log_context_snapshot(len(self._messages), self.bytes_size(), DEFAULT_MAX_CONTEXT_TOKENS)
