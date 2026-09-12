"""Audit writer and error tracking for TauErgon.

Extracted from agent_session.py to separate audit logging concerns from
session lifecycle management.

Responsibilities:
- Buffered audit log writing (AuditWriter)

This module has NO dependency on agent_session.py, breaking the circular
dependency chain. It depends only on:
  - agent_console (for warning() display)
  - agent_version (lazy import for session_start)

# ============================================================================
# AUDIT LOGGING — NON-NEGOTIABLE DESIGN REQUIREMENTS
# ============================================================================
#
# The audit log is the ABSOLUTE SOURCE OF TRUTH for every agent session.
# It is the single, complete, immutable record of what happened.
#
# These requirements are MANDATORY and must NEVER be relaxed:
#
#   1. NEVER TRUNCATE — Tool outputs, user messages, assistant responses,
        #      stack traces, system prompts: everything is logged in
#      full. No character limits, no byte limits, no line limits.
#
#   2. NEVER ROTATE — The audit file grows for the lifetime of the session.
#      No log rotation, no archival, no compression-on-write. A 100 MB file
#      is acceptable. A 1 GB file is acceptable. Disk space is cheap; data
#      loss is not.
#
#   3. NEVER REVERT — The audit log is append-only. Once written, a record
#      is immutable. Never overwrite, never delete, never "correct" past
#      entries. If something was wrong, log a new record describing the
#      correction — but never change what was already recorded.
#
#   4. AUDIT IS THE SOURCE OF TRUTH — All debugging, post-mortem analysis,
#      and LLM learning signals derive from the audit log. If it is incomplete
#      or inaccurate, everything downstream is compromised.
#
# We are AWARE and ACCEPT that audit files may grow very large. This is a
# deliberate trade-off: completeness over storage efficiency.
#
# ============================================================================
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime as dt
from pathlib import Path

from agent_audit_bridge import emit_console_warning

__all__ = [
    "AuditWriter",
]


# ── AuditWriter ───────────────────────────────────────────────────────────


class AuditWriter:
    """Buffered writer for structured audit log records.

    Writes structured text records to an audit file with synchronous
    flush-at-turn-boundaries. No truncation — audit is never truncated.

    Format: [TIMESTAMP] RECORD_TYPE stack=SS field1=value1 field2=value2
            | continuation_line
    'stack' encodes agent identity: . = root, S = subagent, SS = nested, SF = fork-in-subagent
    """

    def __init__(self, audit_file: Path, pid: int | None = None, initial_nesting: int = 0):
        self._file = audit_file
        self._pid = pid or os.getppid()
        self._buffer: list[str] = []

        self._lock = threading.Lock()
        self._closed = False
        self._nesting_stack: str = ""  # e.g. "S", "SS", "SF" — human-readable agent identity
        self._closed = False

        try:
            audit_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.warning("audit_writer: failed to create audit dir %s: %s", audit_file.parent, e)

    # --- Timestamp & buffering -------------------------------------------------------

    def _ts(self) -> str:
        return dt.now().isoformat(timespec="milliseconds")

    def _enqueue(self, line: str) -> None:
        with self._lock:
            self._buffer.append(line)
    def _enqueue_indented(self, label: str, text: str) -> None:
        """Enqueue a labeled block of indented lines."""
        self._enqueue(f"  | {label}:\n")
        for line in text.split("\n"):
            self._enqueue(f"  |   {line}\n")

    def _flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            data = "".join(self._buffer)
            self._buffer.clear()
        try:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(data)
        except Exception as e:
            # Graceful degradation: log to stderr, retain buffer for retry.
            sys.stderr.write(
                f"CRITICAL: Audit write failed: {e}\n"
                f"File: {self._file}\n"
                f"Buffer: {len(data)} bytes (retained for retry)\n"
            )
            sys.stderr.flush()
            # Re-enqueue data for retry
            with self._lock:
                self._buffer.insert(0, data)
    def _emit(self, record_type: str, fields: str, continuations: list[str] | None = None) -> None:
        ts = self._ts()
        stack_display = self._nesting_stack or "."
        # Format: [TS] RECORD_TYPE stack=SS fields
        # 'stack' encodes both depth and agent type: . = root, S = subagent, SS = nested, SF = fork-in-subagent
        if fields:
            header = f"[{ts}] {record_type} stack={stack_display} {fields}\n"
        else:
            header = f"[{ts}] {record_type} stack={stack_display}\n"
        self._enqueue(header)
        if continuations:
            for cont in continuations:
                # A continuation may itself contain newlines (e.g. multi-line
                # console display). Keep the "  | " prefix invariant by
                # emitting one prefixed physical line per embedded newline.
                for line in cont.split("\n"):
                    self._enqueue(f"  | {line}\n")

    # --- Session lifecycle ---------------------------------------------------------

    def session_start(
        self,
        model: str,
        cwd: str,
        system_prompt: str,
    ) -> None:
        # Import here to avoid circular dependencies
        from agent_version import get_version_info
        version_info = get_version_info()
        fields = f"version={version_info['version']} branch={version_info['branch']!r} hash={version_info['hash']!r} pid={self._pid} model={model!r} cwd={cwd!r}"
        self._emit("SESSION_START", fields)
        self._enqueue_indented("system_prompt", system_prompt)


    # --- Message logging -----------------------------------------------------------

    def user(self, content: str | list) -> None:
        """Log a user message (backward compatible — no truncation)."""
        if isinstance(content, list):
            image_count = sum(1 for p in content if p.get("type") == "image_url")
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            audit_text = f"[{image_count} image(s), {len(text_parts)} text block(s)]"
            if text_parts:
                # NO TRUNCATION — log full content (P7-B7-01: ALL text
                # blocks, not just the first; _emit splits embedded
                # newlines into prefixed continuation lines).
                audit_text += ": " + "\n".join(text_parts)
            self._emit("USER", "", [audit_text])
        else:
            self._emit("USER", "", [content])

    # --- Subagent / fork logging ---------------------------------------------------

    # --- Context logging ------------------------------------------------------------

    def context_add(self, count: int, total: int, bytes_total: int) -> None:
        """Log context messages added."""
        fields = f"count={count} total={total} bytes_total={bytes_total}"
        self._emit("CONTEXT_ADD", fields)

    def context_remove(self, count: int, total: int, bytes_total: int) -> None:
        """Log context messages removed."""
        fields = f"count={count} total={total} bytes_total={bytes_total}"
        self._emit("CONTEXT_REMOVE", fields)

    def context_merge(self, source: str, target: str, count: int) -> None:
        """Log context messages merged."""
        fields = f"source={source!r} target={target!r} count={count}"
        self._emit("CONTEXT_MERGE", fields)

    def context_snapshot(self, total: int, bytes_total: int, max_tokens: int) -> None:
        """Log a context snapshot."""
        fields = f"total={total} bytes_total={bytes_total} max_tokens={max_tokens}"
        self._emit("CONTEXT_SNAPSHOT", fields)

    # --- Compression audit ----------------------------------------------------------

    def compress_pipeline_start(
        self, original_size: int, target_size: int, compression_factor: float, last_known_tokens: int | None
    ) -> None:
        """Log compression pipeline start."""
        fields = f"original={original_size} target={target_size} factor={compression_factor:.2f} tokens={last_known_tokens or 'unknown'}"
        self._emit("COMPRESS_START", fields)

    def compress_step(
        self, step_name: str, before: int, after: int, msgs_before: int, msgs_after: int, status: str, actions: str = ""
    ) -> None:
        """Log a single compression step (consolidated start+end)."""
        fields = f"step={step_name} before={before} after={after} msgs={msgs_before}→{msgs_after} status={status}"
        if actions:
            fields += f" actions={actions[:200]}"
        self._emit("COMPRESS_STEP", fields)

    def compress_action(self, step_name: str, action_type: str, description: str) -> None:
        """Log a single compression action taken by a step."""
        fields = f"step={step_name} action={action_type} desc={description[:200]}"
        self._emit("COMPRESS_ACTION", fields)

    def compress_pipeline_end(self, final_size: int, algorithms_used: list, total_saved: int, duration: float = 0.0) -> None:
        """Log compression pipeline end."""
        fields = f"final={final_size} algorithms={','.join(algorithms_used) or 'none'} saved={total_saved} duration={duration:.1f}s"
        self._emit("COMPRESS_END", fields)

    # --- Console-to-audit bridging (INTERNAL — use agent_audit_bridge only) --------
    # These methods are NOT part of the public API. They are called exclusively
    # through agent_audit_bridge explicit functions (console_error, console_warning,
    # console_info, console_success), which provide exception handling and
    # guard against writer being None. Direct calls bypass this safety mechanism.

    def _console_error(self, message: str) -> None:
        """Log a console error message to audit. INTERNAL — use agent_audit_bridge."""
        self._emit("CONSOLE_ERROR", "", [message])

    def _console_warning(self, message: str) -> None:
        """Log a console warning message to audit. INTERNAL — use agent_audit_bridge."""
        self._emit("CONSOLE_WARNING", "", [message])

    def _console_info(self, message: str) -> None:
        """Log a console info message to audit. INTERNAL — use agent_audit_bridge."""
        self._emit("CONSOLE_INFO", "", [message])

    def _console_success(self, message: str) -> None:
        """Log a console success message to audit. INTERNAL — use agent_audit_bridge."""
        self._emit("CONSOLE_SUCCESS", "", [message])

    # --- Turn summary ---------------------------------------------------------

    def turn_summary(self, summary: str) -> None:
        """Emit a typed TURN_SUMMARY record with the summary as a continuation."""
        self._emit("TURN_SUMMARY", "", [summary])

    def turn_summary_status(self, status: str, reason: str = "") -> None:
        """Emit a TURN_SUMMARY observability record (status/reason only).

        Used to make skip/fail greppable: ``TURN_SUMMARY stack=. status=skipped
        reason=...``. Counts produced-vs-skipped from a single grep.
        """
        fields = f"status={status}"
        if reason:
            fields += f" reason={reason}"
        self._emit("TURN_SUMMARY", fields)

    # --- Flush / close ------------------------------------------------------------

    def flush(self) -> None:
        self._flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._flush()

