"""Message content utilities for TauErgon.

Pure utility functions for message sanitization, synthetic message handling,
and text extraction. Zero dependencies on other agent modules.

Extracted from agent_context.py to break circular dependencies and improve
module organization.
"""

from __future__ import annotations

# ── User message prefix protocol ────────────────────────────────────────────────
# All user messages are prefixed with [U:TYPE | N:stack] to indicate their type
# and nesting level. Real user messages have [U:real | N:...], synthetic messages
# have other types ([U:meta | N:...], [U:confirm | N:...], etc.).
# Format: [U:TYPE | N:stack] Content
# Types: real, meta, confirm, inject, system, fork, subagent, redirect
_USER_PREFIX_PATTERN = "[U:"
_USER_PREFIX_FORMAT = "[U:{type} | N:{stack} | M:{msgs} | C:{pct}{budget}] "
_REAL_USER_PREFIX = "[U:real | N:"

# Category-to-type mapping for synthetic user messages
# Types NOT in _SYNTHETIC_TYPES (meta, confirm, inject, system) survive
# cleanup_synthetic() — this is how repl_output persists across turns.
_SYNTHETIC_CATEGORY_TO_TYPE = {
    "continuation": "meta",
    "turn_started": "meta",
    "turn_closed": "meta",
    "parent_inject": "inject",
    "escalation": "system",
    "recovery": "system",
    "repl_output": "repl",  # survives cleanup — successful REPL turns stay on stack
    "repl_feedback": "repl",  # survives cleanup — REPL feedback (errors, tracebacks)
}

# Legacy synthetic prefix (backward compatibility)
_SYNTHETIC_PREFIX = "[SYSTEM-SYNTHETIC: "


def is_synthetic_message(msg: dict) -> bool:
    """Check if any message (user or assistant) is synthetic.

    A message is synthetic if it has a user message prefix with a synthetic type
    (meta, confirm, inject, system). Real user messages (real, fork, subagent,
    redirect) are NOT synthetic, even though they have prefixes.

    Also checks for legacy [SYSTEM-SYNTHETIC: prefix for backward compatibility.

    Args:
        msg: A message dictionary to check.

    Returns:
        True if the message was system-injected, False otherwise.
    """
    # Synthetic types: meta, confirm, inject, system
    # Non-synthetic types: real, fork, subagent, redirect
    _SYNTHETIC_TYPES = {"meta", "confirm", "inject", "system"}

    content = msg.get("content", "")
    if isinstance(content, str):
        # Check for new prefix format
        if content.startswith(_USER_PREFIX_PATTERN):
            user_type = _get_user_type_from_prefix(content)
            return user_type in _SYNTHETIC_TYPES
        # Legacy check (backward compatibility)
        return content.startswith("[SYSTEM-SYNTHETIC: ")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                # Check for new prefix format
                if text.startswith(_USER_PREFIX_PATTERN):
                    user_type = _get_user_type_from_prefix(text)
                    return user_type in _SYNTHETIC_TYPES
                # Legacy check (backward compatibility)
                if text.startswith("[SYSTEM-SYNTHETIC: "):
                    return True
    return False


def is_repl_message(msg: dict) -> bool:
    """True if message is REPL output/error (type='repl' in prefix).

    In RLM, REPL output arrives as user messages with type 'repl'.
    These are NOT synthetic (is_synthetic_message returns False) but
    are also NOT real user requests.
    """
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    if isinstance(content, str):
        return _get_user_type_from_prefix(content) == "repl"
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return _get_user_type_from_prefix(part.get("text", "")) == "repl"
    return False


def is_real_user_request(msg: dict) -> bool:
    """True if message is a real user request (type='real' in prefix).

    Falls back to True for unprefixed user messages (defensive: legacy
    messages without [U: prefix are treated as real).
    Returns False for REPL, meta, inject, system, and all other non-real types.
    """
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    if isinstance(content, str):
        user_type = _get_user_type_from_prefix(content)
        return user_type is None or user_type == "real"
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                user_type = _get_user_type_from_prefix(part.get("text", ""))
                return user_type is None or user_type == "real"
    return True  # no extractable content → treat as real (defensive)


def _get_user_type_from_prefix(content: str) -> str | None:
    """Extract user type from prefixed content."""
    if not isinstance(content, str) or not content.startswith(_USER_PREFIX_PATTERN):
        return None
    try:
        end = content.index("]", 3)
        prefix = content[1:end]  # "U:TYPE | N:stack"
        type_part = prefix.split(" | ")[0]  # "U:TYPE"
        return type_part[2:]  # "TYPE"
    except (ValueError, IndexError):
        return None


def _make_user_prefix(user_type: str, nesting_stack: str, msg_count: int = 0, context_pct: float = 0.0, spawn_B: float = 0.0) -> str:
    """Create a user message prefix."""
    budget_str = f" | B:{spawn_B*100:.1f}%" if spawn_B > 0 else ""
    return _USER_PREFIX_FORMAT.format(type=user_type, stack=nesting_stack, msgs=msg_count, pct=f"{int(context_pct * 100)}%", budget=budget_str)


def _extract_text_content(msg: dict) -> str:
    """Extract text content from a message, handling multimodal content."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return " ".join(parts)
    return ""


# ── Sanitization ──────────────────────────────────────────────────────────────

_SURROGATE_DELETE = str.maketrans("", "", "".join(chr(c) for c in range(0xD800, 0xE000)))


def _sanitize_text(text: str) -> str:
    """Strip lone UTF-16 surrogates from text.

    Surrogates (U+D800–U+DFFF) are never valid in UTF-8/UTF-32.
    They only appear as pairs in UTF-16 encoding.
    Lone surrogates cause tokenizer rejection (TextEncodeInput error).
    """
    return text.translate(_SURROGATE_DELETE)


def _sanitize_content(content: str | list) -> str | list:
    """Sanitize content — handles plain string, multimodal list, and nested dicts."""
    if isinstance(content, str):
        return _sanitize_text(content)
    if isinstance(content, list):
        return [
            _sanitize_text(item) if isinstance(item, str)
            else {**item, "text": _sanitize_text(item["text"])}
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
            else item
            for item in content
        ]
    return content


# ── Content Merging ───────────────────────────────────────────────────────────


def _merge_content(a: object, b: object) -> str | list:
    """Merge two content values.

    For multimodal list content, concatenate the content-block lists so
    image_url blocks are preserved. Falls back to string concatenation
    when both sides are plain strings.

    Used by ``TauContext.merge_consecutive_assistants()`` to merge
    adjacent same-role messages after synthetic bridges are removed.
    """
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    if isinstance(a, list):
        return a + [{"type": "text", "text": str(b)}]
    if isinstance(b, list):
        return [{"type": "text", "text": str(a)}] + b
    return str(a) + "\n" + str(b)


__all__ = [
    # Prefix protocol constants (internal — used by agent_context.py)
    "_SYNTHETIC_PREFIX",
    "_SYNTHETIC_CATEGORY_TO_TYPE",
    # Sanitization
    "_sanitize_content",
    "_sanitize_text",
    # Content merging
    "_merge_content",
    # Synthetic message detection
    "is_synthetic_message",
    "is_repl_message",
    "is_real_user_request",
    # Prefix helpers (internal — used by agent_context.py)
    "_make_user_prefix",
]
