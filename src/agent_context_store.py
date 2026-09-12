
from __future__ import annotations

import json
import os
from pathlib import Path

# ── ContextStoreMixin ─────────────────────────────────────────────────────────

def _estimate_text_tokens(text: str) -> int:
    """Estimate tokens with CJK awareness. H8."""
    cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) <= 0x4DBF or 0x20000 <= ord(c) <= 0x2A6DF or 0xF900 <= ord(c) <= 0xFAFF)
    non_cjk = len(text) - cjk
    return cjk + non_cjk // 3  # ~3 chars/token: conservative (over-counts) so the compression gate fires early


class ContextStoreMixin:
    """Standard data-structure for token estimation and file persistence.

    This Mixin extracts the Standard data-structure concern from `TauContext`
    so that the God Node only handles message storage.
    """

    def estimate_tokens(self, pending_tokens: int = 0) -> int:
        """Estimate the total token count for the context.

        Uses character-based heuristic: ~3 chars per token, 15 tokens structural
        overhead per message.  Multimodal image_url blocks are estimated at
        1120 tokens each (upper-bound for Gemma 4).
        """
        return estimate_messages_tokens(self._messages, pending_tokens)

    def get_usage_stats(
        self, max_tokens: int, exact_tokens: int | None = None
    ) -> tuple[int, float, int, bool]:
        """Get comprehensive token usage statistics.

        Returns (token_count, percentage, byte_count, is_exact).
        """
        if exact_tokens is not None and exact_tokens > 0:
            token_count, is_exact = exact_tokens, True
        else:
            token_count, is_exact = self.estimate_tokens(), False
        byte_count = self.bytes_size()
        percentage = token_count / max_tokens if max_tokens > 0 else 0.0
        return token_count, percentage, byte_count, is_exact

    def context_pct(self, max_tokens: int) -> float:
        """Get context usage as a fraction (0.0-1.0) for message prefix display."""
        return self.get_usage_stats(max_tokens)[1]

    def bytes_size(self) -> int:
        """Calculate the size of the serialized context in bytes."""
        return len(json.dumps(self._messages).encode("utf-8"))

    def load_from_file(self, context_file: Path) -> bool:
        """Load context messages from a JSON file. Returns True on success.

        Supports both legacy bare-array format and new metadata-wrapped format.
        """
        if not context_file.exists():
            return False
        try:
            with open(context_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "messages" in data:
                # New format with metadata
                if not isinstance(data["messages"], list):
                    return False
                self._metadata = data.get("metadata", {})
                self.set_messages(data["messages"])
            elif isinstance(data, list):
                # Legacy bare-array format
                self.set_messages(data)
            else:
                return False
            return True
        except (json.JSONDecodeError, IOError, TypeError, ValueError):
            # H7: do NOT clear context on load failure — caller decides
            return False

    def save_to_file(self, context_file: Path, force: bool = False) -> bool:
        """Save the current context to a JSON file.

        Writes a metadata block alongside messages for context provenance.
        Returns True if file was written, False otherwise.
        """
        if not force and len(self._messages) < 3:
            return False
        data = {
            "metadata": self._metadata,
            "messages": self._messages,
        }
        tmp_file = context_file.with_suffix(".tmp")  # H6: atomic write
        context_file.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, context_file)  # H6: atomic
        return True


def estimate_messages_tokens(messages, pending_tokens: int = 0) -> int:
    """Estimate tokens for an ARBITRARY message list (sliceable).

    Extracted from ContextStoreMixin.estimate_tokens so the exact-anchor live
    estimator can price only the messages appended after the anchor.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_text_tokens(content)  # H8: CJK-aware
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total += _estimate_text_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":
                    total += 1120
        reasoning = msg.get("reasoning", "")
        if isinstance(reasoning, str) and reasoning:
            total += _estimate_text_tokens(reasoning)
        # Tool-call payloads are the largest per-message growth source; the
        # estimator must count them or the compression gate is blind to the
        # very delta that overflows the window.
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            args = fn.get("arguments", "")
            if isinstance(args, str) and args:
                total += _estimate_text_tokens(args)
            name = fn.get("name", "")
            if isinstance(name, str) and name:
                total += _estimate_text_tokens(name)
        total += 15  # structural overhead (role, IDs, JSON framing)
    total += pending_tokens
    return total


def compute_live_tokens(messages, exact_tokens, anchor_count, output_tokens) -> int:
    """LIVE context token count: exact anchor + post-anchor delta.

    exact_tokens = prompt_tokens of the last API call, which covers the messages
    that were SENT (messages[:anchor_count]). output_tokens = that call's
    completion_tokens (the assistant response, priced exactly). Everything
    appended after the anchor (tool result / synthetic feedback / ...) is
    estimated. This is the delta the stale exact count predates — exactly what
    can silently push a turn over the window.

    Re-baseline (fall back to a full estimate) when:
      - exact_tokens is None / non-int (no API measurement yet), OR
      - anchor_count is None / non-int, OR
      - anchor_count > len(messages) — the context list was REPLACED/shrunk
        (compression, undo, remove_messages) so the anchor no longer aligns.

    The assistant reply (completion_tokens == output_tokens) is ALREADY the
    element at messages[anchor_count], so it is priced by the estimate slice.
    Adding output_tokens here would double-count it and inflate the count by the
    entire last reply — a huge reply then trips the guard spuriously (anchor +
    reply + reply again). output_tokens is accepted for signature stability but
    deliberately NOT added; the delta is estimated uniformly from the slice.
    """
    if not isinstance(exact_tokens, int) or not isinstance(anchor_count, int):
        return estimate_messages_tokens(messages)
    try:
        n = len(messages)
    except TypeError:
        return estimate_messages_tokens(messages)
    if anchor_count > n:
        return estimate_messages_tokens(messages)
    return exact_tokens + estimate_messages_tokens(messages[anchor_count:])
