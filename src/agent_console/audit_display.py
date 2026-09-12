"""Audit log viewer — parses .audit files and renders colored interaction flow.

Modes:
- short: single-line records, max 130 chars, abbreviated timestamps
- long: identical to short but no truncation
- full: multi-line content preserved faithfully
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from agent_models import Colors

__all__ = ["AuditRecord", "parse_audit_file", "show_audit"]

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class AuditRecord:
    """Parsed audit log record."""

    timestamp: str
    record_type: str
    nesting: int
    fields: dict = field(default_factory=dict)
    content_blocks: dict = field(default_factory=dict)  # label -> list of lines


# ── Parser ────────────────────────────────────────────────────────────────────

# Header: [ISO_TIMESTAMP] RECORD_TYPE key=value key=value nesting=N
# Also handles old format: [ISO_TIMESTAMP] RECORD_TYPE (no fields)
_RE_HEADER = re.compile(r"^\[([^\]]+)\]\s+(\S+)(?:\s+(.*))?")

# Content block label: "  | label:" or "  | label: inline content"
_RE_LABEL = re.compile(r"^  \| (\S+):(\s*(.*))?$")

# Content block indented line: "  |   content"
_RE_INDENTED = re.compile(r"^  \|   (.*)")

# Continuation line: "  | content" (no label prefix)
_RE_CONTINUATION = re.compile(r"^  \| (.*)")


def _parse_fields(raw: str) -> dict:
    """Parse ``key=value`` fields, handling single-quoted values with spaces."""
    if not raw:
        return {}
    result: dict = dict()
    i = 0
    raw += " "  # sentinel
    while i < len(raw):
        # Skip whitespace
        while i < len(raw) and raw[i] == " ":
            i += 1
        if i >= len(raw):
            break
        # Find key=
        eq = raw.find("=", i)
        if eq == -1 or (i > 0 and raw[i - 1:i] == " " and raw[i: eq] == ""):
            break
        key = raw[i: eq]
        if not key or raw[i: eq].strip() != key:
            break
        val_start = eq + 1
        if val_start < len(raw) and raw[val_start] == "'":
            # Quoted value
            end = raw.find("'", val_start + 1)
            if end == -1:
                value = raw[val_start:]
                i = len(raw)
            else:
                value = raw[val_start + 1: end]
                i = end + 1
        else:
            # Unquoted — runs until next space
            end = raw.index(" ", val_start) if " " in raw[val_start:] else len(raw)
            value = raw[val_start: end]
            i = end
        result[key] = value
    return result


def parse_audit_file(path: Path) -> Iterator[AuditRecord]:
    """Yield parsed records from *path*.  Stream-safe — never loads full file."""
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    try:
        current: AuditRecord | None = None
        current_label: str | None = None
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            # Header line
            m = _RE_HEADER.match(line)
            if m:
                if current is not None:
                    yield current
                ts, rtype, fields_raw = m.group(1, 2, 3)
                fields = _parse_fields(fields_raw)
                # Support both old format (nesting=N) and new format (stack=SS)
                stack = fields.pop("stack", None)
                nesting_str = fields.pop("nesting", None)
                if stack is not None:
                    # New format: stack=. (root), stack=S, stack=SS, stack=SF
                    nesting = len(stack) if stack != "." else 0
                elif nesting_str is not None:
                    # Old format: nesting=N
                    try:
                        nesting = int(nesting_str)
                    except ValueError:
                        nesting = 0
                else:
                    nesting = 0
                current = AuditRecord(
                    timestamp=ts, record_type=rtype, nesting=nesting, fields=fields
                )
                current_label = None
                continue
            # Content block label
            if current is not None:
                m = _RE_LABEL.match(line)
                if m:
                    current_label = m.group(1)
                    current.content_blocks[current_label] = []
                    # Handle inline content (e.g., "  | final_args: {...}")
                    inline = m.group(3)
                    if inline:
                        current.content_blocks[current_label].append(inline)
                    continue
                m = _RE_INDENTED.match(line)
                if m and current_label is not None:
                    current.content_blocks[current_label].append(m.group(1))
                    continue
                m = _RE_CONTINUATION.match(line)
                if m:
                    # Format A continuation — use record_type as pseudo-label
                    pseudo = current.record_type.lower()
                    if pseudo not in current.content_blocks:
                        current.content_blocks[pseudo] = []
                    current.content_blocks[pseudo].append(m.group(1))
                    current_label = None
                    continue
        if current is not None:
            yield current
    finally:
        fh.close()


# ── Rendering helpers ────────────────────────────────────────────────────────

# Module-level constants — avoid recreating dicts on every invocation.
_MARKER_MAP: dict[str, str] = {
    "SESSION_START": "[SESS]",
    "USER": "[USER]",
    "ASSISTANT": "[ACON]",
    "CONSOLE_ERROR": "[CERR]",
    "CONSOLE_WARNING": "[CWRN]",
    "CONSOLE_INFO": "[CINF]",
    "CONSOLE_SUCCESS": "[CSUC]",
    "FORK_START": "[FORK>",
    "FORK_END": "[FORK<]",
    "SUBAGENT_START": "[SUBA>",
    "SUBAGENT_END": "[SUBA<]",
}

_COLOR_MAP: dict[str, str] = {
    "SESSION_START": Colors.MAGENTA,
    "USER": Colors.WHITE,
    "ASSISTANT": Colors.GREEN,
    "CONSOLE_ERROR": Colors.RED,
    "CONSOLE_WARNING": Colors.YELLOW,
    "CONSOLE_INFO": Colors.CYAN,
    "CONSOLE_SUCCESS": Colors.GREEN,
    "FORK_START": Colors.BLUE,
    "FORK_END": Colors.BLUE,
    "SUBAGENT_START": Colors.BLUE,
    "SUBAGENT_END": Colors.BLUE,
}

def _abbreviated_timestamp(ts: str) -> str:
    """Extract HH:MM:SS from ISO timestamp."""
    # ts = "2026-09-12T14:57:03+00:00"
    parts = ts.split("T")
    if len(parts) == 2:
        time_part = parts[1].split(".")[0]
        return time_part
    return ts[:8]


def _nesting_prefix(nesting: int, nesting_context: list[str] | None = None) -> str:
    """Nesting prefix: '>' for level 0, '>>' for level 1, '>>>' for level 2, etc.
    
    Optionally includes nesting intention (task description) when context is provided.
    """
    arrows = ">" * max(1, nesting + 1)
    if nesting_context and nesting < len(nesting_context):
        ctx = nesting_context[nesting]
        if ctx:
            return f"{arrows} [{ctx}] "
    return f"{arrows} "


def _bookend_line(nesting: int, kind: str, task: str | None, duration: str | None, nesting_context: list[str] | None = None) -> str:
    """Render a graphical bookend line."""
    prefix = _nesting_prefix(nesting, nesting_context)
    gutter = "────"
    if kind == "FORK_START":
        label = f"## FORK START: {task} ##"
    elif kind == "FORK_END":
        label = f"## FORK END: {duration}s ##"
    elif kind == "SUBAGENT_START":
        label = f"## SUBAGENT START: {task} ##"
    elif kind == "SUBAGENT_END":
        label = f"## SUBAGENT END: {duration}s ##"
    else:
        label = f"## {kind} ##"
    return f"{gutter} {prefix}{label}"


def _marker_for(rtype: str) -> str:
    """Map record type to 4-letter marker."""
    return _MARKER_MAP.get(rtype, f"[{rtype[:4].upper()}]")


def _color_for(rtype: str) -> str:
    """Map record type to ANSI color code."""
    return _COLOR_MAP.get(rtype, Colors.WHITE)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (CSI, OSC, device control, etc.)."""
    # CSI sequences: \x1b[...m (colors, cursor, etc.)
    # OSC sequences: \x1b]...ST (window titles, etc.)
    # Device control: \x1bP...ST, \x1b^...ST, \x1b_...ST
    # Other: \x1b(... (character set), \x1b=... (keypad mode)
    return re.sub(r"\x1b(?:\[[0-9;?]*[a-zA-Z]|][^\x07]*\x07|[\x1b\][\x1b^_][^\x1b]*|\([0-9A-HPa-hp]|\=[0-9]*[a-zA-Z])", "", text)


def _format_content_short(record: AuditRecord) -> str:
    """Format content for short/long mode (single line).

    Returns empty string for SESSION_START/SESSION_END records.
    """
    # For these record types, fields are more important than content blocks
    if record.record_type in ("SESSION_START", "SESSION_END"):
        return ""

    blocks = record.content_blocks
    parts: list[str] = []

    # Handle FORMAT A (pseudo-label) — no label prefix
    pseudo = record.record_type.lower()
    if pseudo in blocks:
        text = " ".join(_strip_ansi(line) for line in blocks[pseudo])
        parts.append(text)

    # Handle FORMAT B labeled blocks
    for label, lines in blocks.items():
        if label == pseudo:
            continue
        # For ASSISTANT records, skip 'reasoning' block (handled by [AREA])
        if record.record_type == "ASSISTANT" and label == "reasoning":
            continue
        text = " ".join(_strip_ansi(line) for line in lines)
        if text:
            # Strip label prefix for cleaner output
            parts.append(text)
    return " ".join(parts)





def _format_fields_summary(record: AuditRecord) -> str:
    """Format fields dict into a compact summary string."""
    parts: list[str] = []
    f = record.fields
    if "model" in f:
        parts.append(f"model={f['model']}")
    if "tools" in f:
        parts.append(f"tools={f['tools']}")
    if "cwd" in f:
        parts.append(f"cwd={f['cwd']}")
    if "task" in f:
        parts.append(f"task={f['task']}")
    if "duration_s" in f:
        parts.append(f"duration_s={f['duration_s']}")
    if "tokens_in" in f:
        parts.append(f"tokens: {f['tokens_in']} in, {f.get('tokens_out', '?')} out, {f.get('cached', '?')} cached")
    if "error_type" in f:
        parts.append(f"{f.get('tool', '?')} error_type={f['error_type']}")
    if "tool" in f and "error_type" not in f:
        parts.append(f"tool={f['tool']}")
    if "before_tokens" in f:
        parts.append(f"before={f['before_tokens']} after={f.get('after_tokens', '?')} ratio={f.get('ratio', '?')}")
    # Fallback: show all fields
    if not parts:
        for k, v in f.items():
            parts.append(f"{k}={v}")
    return " ".join(parts)


def _truncate(line: str, max_len: int) -> str:
    """Truncate *line* to *max_len* chars, appending '...' if truncated."""
    if len(line) <= max_len:
        return line
    return line[: max_len - 3] + "..."


# ── Renderers ─────────────────────────────────────────────────────────────────

def _render_bookend(record: AuditRecord, mode: str, use_color: bool = True, nesting_context: list[str] | None = None) -> None:
    """Render a fork/subagent bookend record."""
    ts = _abbreviated_timestamp(record.timestamp) if mode != "full" else record.timestamp
    rtype = record.record_type
    nesting = record.nesting
    task = record.fields.get("task", "")
    duration = record.fields.get("duration_s", "")
    line = f"{ts} {_bookend_line(nesting, rtype, task, duration, nesting_context)}"
    if mode == "short":
        line = _truncate(line, 130)
    if use_color:
        print(f"{Colors.BLUE}{line}{Colors.RESET}")
    else:
        print(line)


def _render_reasoning(record: AuditRecord, ts: str, prefix: str, use_color: bool, preserve_newlines: bool) -> None:
    """Render [AREA] lines for assistant reasoning."""
    reasoning = record.content_blocks.get("reasoning", [])
    if not reasoning:
        return

    if preserve_newlines:
        for line in reasoning:
            area_line = f"{ts} [AREA] {prefix}{_strip_ansi(line)}"
            if use_color:
                print(f"{Colors.REASONING}{area_line}{Colors.RESET}")
            else:
                print(area_line)
    else:
        reasoning_text = " ".join(_strip_ansi(line) for line in reasoning)
        area_line = f"{ts} [AREA] {prefix}{reasoning_text}"
        if use_color:
            print(f"{Colors.REASONING}{area_line}{Colors.RESET}")
        else:
            print(area_line)


def _render_record(record: AuditRecord, mode: str, use_color: bool = True, nesting_context: list[str] | None = None) -> None:
    """Unified renderer for all modes.

    Args:
        record: The audit record to render
        mode: 'short', 'long', or 'full'
        use_color: Whether to apply ANSI colors
        nesting_context: Stack of task descriptions per nesting level
    """
    rtype = record.record_type

    # Skip LLM_CALL records — they're internal observability, not conversation flow
    if rtype == "LLM_CALL":
        return

    nesting = record.nesting
    prefix = _nesting_prefix(nesting, nesting_context)
    marker = _marker_for(rtype)
    color = _color_for(rtype)

    # Timestamp: abbreviated for short/long, full for full mode
    ts = _abbreviated_timestamp(record.timestamp) if mode != "full" else record.timestamp

    # Bookend records
    if rtype in ("FORK_START", "FORK_END", "SUBAGENT_START", "SUBAGENT_END"):
        _render_bookend(record, mode, use_color, nesting_context)
        return

    # Build display text based on record type
    else:
        content = _format_content_short(record)
        fields_summary = _format_fields_summary(record)
        if content:
            display_text = content
        elif fields_summary:
            display_text = fields_summary
        else:
            display_text = ""

    display = f"{ts} {marker} {prefix}{display_text}"

    # Truncate only in short mode
    if mode == "short":
        display = _truncate(display, 130)

    if use_color:
        print(f"{color}{display}{Colors.RESET}")
    else:
        print(display)

    # [AREA] on own line for assistant reasoning
    if rtype == "ASSISTANT" and "reasoning" in record.content_blocks:
        _render_reasoning(record, ts, prefix, use_color, mode == "full")


# ── Entry point ──────────────────────────────────────────────────────────────

def show_audit(path: Path, mode: str = "short") -> None:
    """Parse *path* and render records in *mode* (short/long/full)."""
    if not path.exists():
        print(f"No audit data available ({path})", file=sys.stderr)
        return

    use_color = sys.stdout.isatty()

    # Track nesting context: stack of task descriptions per level
    nesting_context: list[str] = []

    for record in parse_audit_file(path):
        rtype = record.record_type

        # Update nesting context on fork/subagent boundaries
        if rtype in ("FORK_START", "SUBAGENT_START"):
            task = record.fields.get("task", "")
            # Truncate long task descriptions for display
            if len(task) > 60:
                task = task[:57] + "..."
            # Ensure context list is long enough, then set/append
            while len(nesting_context) <= record.nesting:
                nesting_context.append("")
            nesting_context[record.nesting] = task
        elif rtype in ("FORK_END", "SUBAGENT_END"):
            # Clear this nesting level
            idx = record.nesting
            if idx < len(nesting_context):
                nesting_context[idx] = ""

        _render_record(record, mode, use_color, nesting_context)