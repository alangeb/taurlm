"""Context repair engine for /ctx fix.

Manual fallback repair for corrupted conversation contexts (null LLM
responses, sequence errors from compression/crash, oversized messages,
high-repetition messages).

DESIGN INVARIANT: Pure functions operating on plain message lists — no
agent/context dependencies. This keeps the engine independently testable
and honors decision 18.16 (no auto-fix on mutation): repair only happens
when a human explicitly invokes /ctx fix.

Safety properties:
- Atomic: if the fixed context has MORE validation errors than the
  original, the fix is aborted and the original is returned.
- Idempotent: a second run on an already-fixed context reports 0 fixes.
- Real-user-invariant: real user message content is never modified —
  only dummy messages are inserted around them.
- LLM-honest: every modification is marked with a [CTX-FIX: ...] tag so
  the model knows the content was repaired.

Pipeline: S1 empty-assistant placeholders → S2 sequence repair (fixed
point) → S3 oversize truncation → S4 repetition/entropy correction →
S5 validation gate.
"""
from __future__ import annotations

import copy
import math
from collections import Counter
from dataclasses import dataclass, field

from agent_message_utils import (
    _extract_text_content,
    _make_user_prefix,
    is_real_user_request,
)

__all__ = ["FixReport", "fix_context"]

# ── Tuning knobs ──────────────────────────────────────────────────────────────

MAX_BYTES = 10240              # S3: truncate messages over this (UTF-8 bytes)
ENTROPY_MIN_BYTES = 1000       # S4: skip messages smaller than this
ENTROPY_MIN_LINES = 8          # S4: skip messages with fewer non-empty lines
H_LINE_THRESHOLD = 1.5         # S4: line-entropy below this (bits/line) = degenerate
DUP_RATIO_THRESHOLD = 0.85     # S4: dup ratio above this = repeated blocks
DUP_MIN_LINES = 20             # S4: dup-ratio metric requires at least this many lines
ENTROPY_REPLACE_BYTES = 100    # S4: keep only this many bytes of the original
MAX_SEQUENCE_PASSES = 5        # S2: fixed-point iteration cap

EMPTY_ASSISTANT_PLACEHOLDER = (
    "[CTX-FIX: assistant response was empty/null — no content was produced "
    "this turn; continuing.]"
)
MERGE_ASSISTANT_MARKER = "[CTX-FIX: merged consecutive assistant messages]"
MERGE_USER_MARKER = "[CTX-FIX: merged consecutive user messages]"
DUMMY_ASSISTANT_CONTENT = (
    "[CTX-FIX: placeholder assistant turn inserted to repair user→user sequence]"
)
DUMMY_USER_MARKER = "[CTX-FIX: placeholder user turn inserted to repair sequence]"


# ── Report ────────────────────────────────────────────────────────────────────

@dataclass
class FixReport:
    """Outcome of a fix_context() run."""

    empty_assistant: int = 0
    merged: int = 0
    dummies_inserted: int = 0
    truncated: int = 0
    entropy_fixed: int = 0
    details: list[str] = field(default_factory=list)
    pre_errors: list[str] = field(default_factory=list)
    post_errors: list[str] = field(default_factory=list)
    bytes_before: int = 0
    bytes_after: int = 0
    aborted: bool = False

    @property
    def total_fixes(self) -> int:
        return (
            self.empty_assistant
            + self.merged
            + self.dummies_inserted
            + self.truncated
            + self.entropy_fixed
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_bytes(msg: dict) -> int:
    """UTF-8 byte size of a message's text content."""
    return len(_extract_text_content(msg).encode("utf-8", errors="ignore"))


def _total_bytes(messages: list[dict]) -> int:
    return sum(_content_bytes(m) for m in messages if isinstance(m, dict))


def _is_empty_content(content) -> bool:
    """True if content is null, blank string, or an all-empty text list."""
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        if not content:
            return True
        # Multimodal with non-text parts (images) is NOT considered empty.
        if any(not isinstance(p, dict) or p.get("type") != "text" for p in content):
            return False
        return all(not (isinstance(p, dict) and str(p.get("text", "")).strip()) for p in content)
    return False


def _truncate_str(text: str, max_bytes: int) -> str:
    """Truncate to max_bytes on a UTF-8 character boundary."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _line_entropy(lines: list[str]) -> float:
    """Shannon entropy (bits/line) over a list of lines."""
    if not lines:
        return 0.0
    counter = Counter(lines)
    total = len(lines)
    h = 0.0
    for count in counter.values():
        p = count / total
        h -= p * math.log2(p)
    return h


def _dummy_assistant() -> dict:
    return {"role": "assistant", "content": DUMMY_ASSISTANT_CONTENT}


def _dummy_user(msg_count: int) -> dict:
    prefix = _make_user_prefix("meta", "", msg_count, 0.0, 0.0)
    return {"role": "user", "content": prefix + DUMMY_USER_MARKER}


# ── S1: empty assistant placeholders ─────────────────────────────────────────

def _fix_empty_assistant(messages: list[dict], report: FixReport) -> None:
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue  # null content is legal with tool_calls
        content = msg.get("content")
        if not _is_empty_content(content):
            continue
        note = ""
        reasoning = msg.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            note = f" (reasoning-only response, {len(reasoning)} chars of thinking)"
        msg["content"] = EMPTY_ASSISTANT_PLACEHOLDER + note
        report.empty_assistant += 1
        report.details.append(f"S1: msg {i} empty assistant → placeholder")


# ── S2: sequence repair (to a fixed point) ───────────────────────────────────

def _merge_assistant(messages: list[dict], idx: int, report: FixReport) -> None:
    """Merge messages[idx] into messages[idx-1] (both assistant)."""
    first, second = messages[idx - 1], messages[idx]
    first_text = _extract_text_content(first)
    second_text = _extract_text_content(second)
    first["content"] = (
        f"{first_text}\n\n{MERGE_ASSISTANT_MARKER}\n\n{second_text}"
    )
    # Keep first's reasoning/summary; drop second's.
    messages.pop(idx)
    report.merged += 1
    report.details.append(f"S2: merged assistant msgs at {idx - 1}/{idx}")


def _merge_user(messages: list[dict], idx: int, report: FixReport) -> None:
    """Merge messages[idx] into messages[idx-1] (both non-real user)."""
    first, second = messages[idx - 1], messages[idx]
    first_text = _extract_text_content(first)
    second_text = _extract_text_content(second)
    first["content"] = f"{first_text}\n\n{MERGE_USER_MARKER}\n\n{second_text}"
    messages.pop(idx)
    report.merged += 1
    report.details.append(f"S2: merged synthetic user msgs at {idx - 1}/{idx}")


def _fix_sequences(messages: list[dict], report: FixReport) -> None:
    for _ in range(MAX_SEQUENCE_PASSES):
        changed = False
        i = 1
        while i < len(messages):
            prev, cur = messages[i - 1], messages[i]
            if not isinstance(prev, dict) or not isinstance(cur, dict):
                i += 1
                continue
            # Tool messages: report-only, never auto-fixed.
            if prev.get("role") == "tool" or cur.get("role") == "tool":
                i += 1
                continue
            if prev.get("role") != cur.get("role"):
                i += 1
                continue
            if prev.get("role") == "assistant":
                _merge_assistant(messages, i, report)
                changed = True
                continue  # re-check the same position
            # user → user
            if is_real_user_request(prev) or is_real_user_request(cur):
                messages.insert(i, _dummy_assistant())
                report.dummies_inserted += 1
                report.details.append(f"S2: inserted dummy assistant before msg {i}")
                i += 2
                changed = True
            else:
                _merge_user(messages, i, report)
                changed = True
                continue  # re-check the same position
        # Assistant directly after system (index 1) → insert dummy user.
        if (
            len(messages) > 1
            and messages[0].get("role") == "system"
            and messages[1].get("role") == "assistant"
        ):
            messages.insert(1, _dummy_user(len(messages)))
            report.dummies_inserted += 1
            report.details.append("S2: inserted dummy user after system prompt")
            changed = True
        if not changed:
            break


# ── S3: oversize truncation ──────────────────────────────────────────────────

def _truncate_oversized(messages: list[dict], report: FixReport) -> None:
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        # Real user content is never modified (hard invariant).
        if msg.get("role") == "user" and is_real_user_request(msg):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if "[CTX-FIX: truncated" in content:
                continue  # already truncated — idempotency
            n = len(content.encode("utf-8", errors="ignore"))
            if n > MAX_BYTES:
                msg["content"] = (
                    _truncate_str(content, MAX_BYTES)
                    + f"\n[CTX-FIX: truncated from {n} bytes to {MAX_BYTES}]"
                )
                report.truncated += 1
                report.details.append(f"S3: msg {i} truncated {n} → {MAX_BYTES} bytes")
        elif isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                    and "[CTX-FIX: truncated" not in part["text"]
                    and len(part["text"].encode("utf-8", errors="ignore")) > MAX_BYTES
                ):
                    n = len(part["text"].encode("utf-8", errors="ignore"))
                    part["text"] = (
                        _truncate_str(part["text"], MAX_BYTES)
                        + f"\n[CTX-FIX: truncated from {n} bytes to {MAX_BYTES}]"
                    )
                    report.truncated += 1
                    report.details.append(
                        f"S3: msg {i} text part truncated {n} → {MAX_BYTES} bytes"
                    )


# ── S4: repetition / entropy correction ──────────────────────────────────────

def _fix_entropy(messages: list[dict], report: FixReport) -> None:
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") == "system":
            continue
        # Real user content is never modified (hard invariant).
        if msg.get("role") == "user" and is_real_user_request(msg):
            continue
        text = _extract_text_content(msg)
        if len(text.encode("utf-8", errors="ignore")) < ENTROPY_MIN_BYTES:
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < ENTROPY_MIN_LINES:
            continue
        h = _line_entropy(lines)
        dup = (
            1.0 - len(set(lines)) / len(lines)
            if len(lines) >= DUP_MIN_LINES
            else 0.0
        )
        if h < H_LINE_THRESHOLD or dup > DUP_RATIO_THRESHOLD:
            head = _truncate_str(text, ENTROPY_REPLACE_BYTES)
            msg["content"] = (
                f"{head}\n[CTX-FIX: entropy correction — high repetition "
                f"(H={h:.2f} bits/line, dup={dup:.0%}), content replaced]"
            )
            report.entropy_fixed += 1
            report.details.append(
                f"S4: msg {i} entropy-corrected (H={h:.2f}, dup={dup:.0%})"
            )


# ── Public API ────────────────────────────────────────────────────────────────

def fix_context(
    messages: list[dict], dry_run: bool = False
) -> tuple[list[dict], FixReport]:
    """Repair a corrupted message list.

    Args:
        messages: Current context message list (never mutated).
        dry_run: If True, compute the report but return the original list.

    Returns:
        (fixed_messages, report). If the fix would make validation WORSE,
        report.aborted is True and the original list is returned.
    """
    from agent_context_validation import validate_context

    report = FixReport()
    report.pre_errors = validate_context(messages)
    report.bytes_before = _total_bytes(messages)

    # Preflight: nothing sensible to do without a system prompt at index 0.
    if not messages or not isinstance(messages[0], dict) or messages[0].get("role") != "system":
        report.aborted = True
        report.details.append("Preflight failed: no system message at index 0 — use /ctx clear")
        return messages, report

    work = copy.deepcopy(messages)
    _fix_empty_assistant(work, report)
    _fix_sequences(work, report)
    _truncate_oversized(work, report)
    _fix_entropy(work, report)

    report.post_errors = validate_context(work)
    report.bytes_after = _total_bytes(work)

    # Atomicity gate: never make validation worse.
    if len(report.post_errors) > len(report.pre_errors):
        report.aborted = True
        report.details.append(
            f"Aborted: fixed context has {len(report.post_errors)} validation errors "
            f"(original had {len(report.pre_errors)}) — original preserved"
        )
        return messages, report

    if dry_run:
        return messages, report
    return work, report
