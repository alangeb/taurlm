"""Context validation for TauErgon.

Extracted from agent_context.py to separate validation concerns from context
management.

DESIGN INVARIANT: Validation functions operate on plain message lists, not on
TauContext instances. This allows independent testing and reuse.

Two categories of functions:
- Pure helpers: `_validate_assistant_tool_calls`, `_validate_content`,
  `_validate_tool_message` — no side effects, accept (msg, idx), return errors.
- Full validation: `validate_context`, `validate_on_mutation` — operate on message
  lists; `validate_on_mutation` emits console/audit warnings as side effects.
- Tool resolution: `get_pending_tool_ids`, `validate_tool_resolution` — check
  that all tool calls have matching results.

NOTE: Context recovery was removed. Validation errors are detected and reported
but NOT auto-fixed. Root causes must be fixed at the source (compression, tool
execution, EOT flow) to maintain proper alternation. See docs/designs/DECISIONS.md
decision 18.16 for rationale.
"""
from __future__ import annotations

import json

from agent_audit_bridge import console_warning
from agent_console import context_append_warning
from agent_message_utils import is_synthetic_message


__all__ = [
    "validate_context",
    "validate_on_mutation",
]


# --- Validation helpers (pure functions) ---

def _validate_assistant_tool_calls(msg: dict, idx: int) -> list[str]:
    """Validate tool_calls structure in an assistant message."""
    errors: list[str] = []
    tool_calls = msg.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        errors.append(f"Message {idx} tool_calls is not a list")
        return errors

    seen_ids: set[str] = set()
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            errors.append(f"Message {idx} tool_call is not a dictionary")
            continue

        tool_call_id = tool_call.get("id")
        if not tool_call_id:
            errors.append(f"Message {idx} tool_call missing 'id' field")
        elif tool_call_id in seen_ids:
            errors.append(
                f"Message {idx} has duplicate tool_call_id: {tool_call_id}"
            )
        else:
            seen_ids.add(tool_call_id)

        # OpenAI tool_call structure — function validation only
        # (id uniqueness checked above)
        if "function" not in tool_call:
            errors.append(f"Message {idx} tool_call missing 'function' field")
        else:
            func = tool_call["function"]
            if "name" not in func:
                errors.append(f"Message {idx} function missing 'name' field")
            if "arguments" not in func:
                errors.append(f"Message {idx} function missing 'arguments' field")
            else:
                if not isinstance(func["arguments"], str):
                    errors.append(f"Message {idx} function arguments must be string")
                else:
                    try:
                        json.loads(func["arguments"])
                    except json.JSONDecodeError:
                        errors.append(f"Message {idx} function arguments must be valid JSON")

    return errors


def _validate_content(msg: dict, idx: int) -> list[str]:
    """Validate content field for non-tool messages."""
    errors: list[str] = []
    role = msg.get("role")
    if role == "tool":
        return errors

    content = msg.get("content")
    has_tool_calls = (
        role == "assistant" and len(msg.get("tool_calls") or []) > 0
    )

    if content is None:
        if not has_tool_calls:
            errors.append(f"Message {idx} content cannot be None")
    elif isinstance(content, str) and not content.strip():
        if not has_tool_calls:
            errors.append(f"Message {idx} content cannot be empty string")
    elif isinstance(content, list) and not content:
        errors.append(f"Message {idx} content list cannot be empty")

    return errors


def _validate_tool_message(msg: dict, idx: int) -> list[str]:
    """Validate tool message fields."""
    errors: list[str] = []
    tool_call_id = msg.get("tool_call_id")
    if tool_call_id is None:
        errors.append(f"Message {idx} tool message missing tool_call_id")
    elif not isinstance(tool_call_id, str) or not tool_call_id.strip():
        errors.append(f"Message {idx} tool_call_id must be non-empty string")

    if "name" not in msg:
        errors.append(
            f"Message {idx} tool message missing 'name' field (required by OpenAI spec)"
        )
    elif not isinstance(msg["name"], str) or not msg["name"].strip():
        errors.append(f"Message {idx} tool message 'name' must be non-empty string")

    content = msg.get("content")
    if content is not None and not isinstance(content, str):
        errors.append(f"Message {idx} tool message 'content' must be a string")

    return errors


# --- Full context validation ---

def validate_context(messages: list[dict]) -> list[str]:
    """Validate the entire context against OpenAI API compliance rules.

    Checks:
    - Exactly one system message at index 0
    - No consecutive messages with same role (tool exceptions allowed)
    - Valid message structure and required fields
    - Proper tool call/tool result pairing
    - Valid tool_call_id references

    Args:
        messages: List of message dicts to validate.

    Returns:
        List of error strings (empty if valid).
    """
    errors: list[str] = []
    valid_roles = {"system", "user", "assistant", "tool"}

    if not messages:
        return errors

    # Exactly one system message required
    system_count = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "system")
    if system_count != 1:
        errors.append(
            f"Context must have exactly one system message (found {system_count})"
        )

    # Collect tool_call_ids from assistant messages
    tool_call_ids = set()
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            for tool_call in msg.get("tool_calls", []):
                if isinstance(tool_call, dict):
                    tool_id = tool_call.get("id")
                    if tool_id:
                        tool_call_ids.add(tool_id)

    # Validate each message individually
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(f"Message {i} is not a dictionary")
            continue

        role = msg.get("role")
        if role not in valid_roles:
            errors.append(
                f"Message {i} has invalid role '{role}' "
                "(must be system, user, assistant, or tool)"
            )
            continue

        # Content required for all roles
        if "content" not in msg:
            errors.append(f"Message {i} missing required 'content' field")
        else:
            content = msg["content"]
            if content is not None and not isinstance(content, (str, list)):
                errors.append(
                    f"Message {i} has invalid content type: {type(content)} "
                    "(must be str or list)"
                )

        # Delegate role-specific validation to helpers
        if role == "assistant":
            errors.extend(_validate_assistant_tool_calls(msg, i))
        errors.extend(_validate_content(msg, i))
        if role == "tool":
            errors.extend(_validate_tool_message(msg, i))

    # Validate cross-message sequencing rules
    last_role = None
    pending_tool_ids: set[str] = set()
    tool_call_info: dict[str, str] = {}

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")

        if role == "assistant":
            for tool_call in msg.get("tool_calls", []):
                if isinstance(tool_call, dict) and tool_call.get("id"):
                    tool_id = tool_call["id"]
                    func = tool_call.get("function", {})
                    func_name = func.get("name", "unknown")
                    func_args = func.get("arguments", "")
                    tool_call_info[tool_id] = f"{func_name}({func_args})"
                    pending_tool_ids.add(tool_id)

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                if tool_call_id not in pending_tool_ids:
                    errors.append(
                        f"Message {i}: Tool result references unknown "
                        f"tool_call_id '{tool_call_id}'"
                    )
                else:
                    pending_tool_ids.remove(tool_call_id)

        if last_role == "tool" and role == "user":
            errors.append(
                f"Message {i}: Tool message must be followed by assistant, not user"
            )

        if i > 0 and role == "system":
            errors.append(
                f"Message {i} has 'system' role after first message "
                "(should be user/assistant)"
            )

        if (
        # M-P4: [system, tool] is valid - only [system, assistant] is rejected at position 1
            i == 1
            and role == "assistant"
            and messages[0].get("role") == "system"
        ):
            errors.append(
                f"Message {i}: Assistant message must be preceded by user "
                "message after system prompt"
            )

        # Consecutive-role check: exclude synthetic messages (system-injected bridges)
        # BUT update last_role for synthetic messages so they break consecutive sequences
        if is_synthetic_message(msg):
            last_role = role  # Bridge breaks the chain
        elif last_role is not None and role == last_role and role != "tool":
            errors.append(
                f"Message {i} has consecutive messages with same role '{role}'"
            )
            last_role = role
        else:
            last_role = role

    if pending_tool_ids:
        unresolved_details = [
            tool_call_info[tid] for tid in pending_tool_ids if tid in tool_call_info
        ]
        details_str = (
            ", ".join(unresolved_details)
            if unresolved_details
            else str(pending_tool_ids)
        )
        errors.append(
            f"Context has {len(pending_tool_ids)} unresolved tool call(s): "
            f"{pending_tool_ids}. Tool calls: {details_str}. "
            "All tool results should be received before next assistant message."
        )

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id and tool_call_id not in tool_call_ids:
                errors.append(
                    f"Message {i} tool result references non-existent "
                    f"tool_call_id: {tool_call_id}"
                )

    return errors


# --- Post-mutation validation ---

def validate_on_mutation(messages: list[dict]) -> None:
    """Validate context after mutation, printing warnings for errors.

    Suppresses transient warnings when context is mid-batch
    (last message is assistant with tool_calls or a tool result).
    During mid-batch, suppress: unresolved tool calls, consecutive roles,
    synthetic message issues — these are expected transient states.

    Args:
        messages: Current message list.
    """
    errors = validate_context(messages)
    if errors:
        last = messages[-1] if messages else None
        in_progress = last is not None and (
            last.get("role") == "tool"
            or (last.get("role") == "assistant" and last.get("tool_calls"))
        )
        if in_progress:
            errors = [
                e for e in errors
                if "unresolved tool call" not in e
                and "consecutive tool" not in e.lower() and "consecutive user" not in e.lower()
                and "synthetic bridge" not in e.lower() and "synthetic message" not in e.lower()
            ]
        if errors:
            # Audit: log context state alongside the warning
            last_role = last.get("role", "none") if last else "empty"
            console_warning(
                f"Context validation ({len(messages)} msgs, last={last_role}): "
                + "; ".join(errors)
            )
            context_append_warning(errors)


# --- Tool resolution validation ---

# End of file