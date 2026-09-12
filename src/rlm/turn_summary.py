"""Turn Summary System — automatic post-turn summarization.

Attaches short summaries as metadata to assistant messages. Summaries
survive context save/load, merge, and compression. Accelerates
compression by 2-5x when summaries are available.

Key function:
- generate_turn_summary(agent: TauErgon) -> bool
"""

from __future__ import annotations

import re
import logging

from agent_message_utils import is_real_user_request

logger = logging.getLogger(__name__)

# Maximum chars per section in summary prompt
_MAX_SECTION_CHARS = 2000
# Minimum chars in assistant content to be considered substantive
_MIN_SUBSTANTIVE_CHARS = 50
# Minimum chars after code block stripping to keep summary.
# 10 was too permissive: a real summary of just "TAU-MANUAL-OK" (13 chars)
# slipped through. Real one-line summaries are >=40 chars; below that a
# response is almost always a stub/echo. Tuned empirically.
_MIN_CLEAN_CHARS = 40


def generate_turn_summary(agent: "TauErgon") -> bool:
    """Main entry point. Returns True if summary was generated, False if skipped/failed.

    Guards (in order):
    1. Agent has config with RLM settings
    2. Agent has LLM client
    3. Auto-summary is enabled in config
    4. Root agent only (nesting_stack is empty)
    5. Turn is substantive (not placeholder response)
    """
    audit_target = None
    try:
        # Guard 1: config  (trivially-false guards emit nothing — not-spam)
        if not hasattr(agent, 'config') or not getattr(agent.config, 'rlm', None):
            return False

        # Guard 2: client
        if not agent.client:
            return False

        # Guard 3: enabled
        auto_config = getattr(agent.config.rlm, 'auto_summary', None)
        if not auto_config or not getattr(auto_config, 'enabled', False):
            return False

        # Guard 4: root agent only
        if getattr(agent, 'nesting_stack', ''):
            return False

        # Guard 5: substantive turn
        if not _is_substantive_turn(agent.context):
            return False

        # --- From here the turn WAS substantive: any failure now is an
        # observable skip/fail (FIX 2), so we can count produced-vs-skipped.
        # Audit target = the AuditWriter (typed records), NOT the Path.
        # _session.audit_file is a Path (no turn_summary); _session.audit_writer
        # is the writer that has turn_summary()/turn_summary_status(). If we can
        # resolve a writer lacking turn_summary, warn instead of silently losing
        # observability. None (no session/writer) stays a quiet no-op.
        _sess = getattr(agent, '_session', None)
        audit_target = getattr(_sess, 'audit_writer', None)
        if audit_target is not None and not hasattr(audit_target, 'turn_summary'):
            logger.warning(
                "turn-summary: no AuditWriter exposing turn_summary; "
                "observability lost for this turn")
            audit_target = None
        request_text = _last_request_text(agent.context)

        # Extract last turn
        last_turn = _extract_last_turn(agent.context)
        if not last_turn:
            _write_summary_skip(audit_target, "extract")
            return False

        # Build summary prompt
        summary_messages = _build_summary_prompt(last_turn)

        # Invoke LLM with short timeout (summary shouldn't block prompt)
        from agent_context_compress.steps.llm import _invoke_llm_with_retry_compression

        model = getattr(auto_config, 'model', None) or getattr(agent, 'model_name', 'default')
        max_tokens = getattr(auto_config, 'max_output_tokens', 512)

        # Override timeout for summary calls (30s vs default 300s)
        summary_kwargs = agent.resolve_group_params()
        summary_kwargs["timeout"] = 30

        resp = _invoke_llm_with_retry_compression(
            client=agent.client,
            model_name=model,
            messages=summary_messages,
            stream=False,
            max_retries=2,
            min_response_bytes=10,
            extra_kwargs=summary_kwargs,
            log_file=getattr(agent._session, 'audit_file', None),
            max_context_tokens=getattr(agent, 'max_context_tokens', 200000),
            max_output_tokens=max_tokens,
        )

        # LLMResponse has .text attribute
        if not resp or not resp.text or not resp.text.strip():
            _write_summary_skip(audit_target, "llm_empty")
            return False

        # Strip code blocks
        clean = _strip_code_blocks(resp.text)
        if not _summary_quality_ok(clean, request_text):
            # Reason reflects which quality gate tripped (short vs echo).
            reason = "too_short" if len(clean) < _MIN_CLEAN_CHARS else "echo"
            _write_summary_skip(audit_target, reason)
            return False

        # Attach metadata
        _attach_summary_metadata(agent.context, clean)

        # Write audit (typed TURN_SUMMARY record — one per produced summary).
        _write_summary_audit(audit_target, clean)

        return True

    except TimeoutError as e:
        logger.warning("Turn summary LLM timeout: %s", e)
        _write_summary_skip(audit_target, "llm_timeout")
        return False
    except Exception as e:
        logger.warning("Turn summary failed: %s", e)
        _write_summary_skip(audit_target, "exception")
        return False


def _extract_last_turn(context: "TauContext") -> list[dict]:
    """Find most recent real user message and all messages after it."""
    msgs = context._messages
    if not msgs:
        return []

    # Scan backward for last real user message
    last_real_user_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        msg = msgs[i]
        if is_real_user_request(msg):
            last_real_user_idx = i
            break

    if last_real_user_idx is None:
        return []

    # Return messages from last real user onward
    return msgs[last_real_user_idx:]


def _build_summary_prompt(last_turn: list[dict]) -> list[dict]:
    """Build minimal messages for LLM call."""
    system = (
        "You are a turn summarizer. Summarize everything that happened "
        "since the last user message provided below.\n\n"
        "Answer these questions concisely in plain text:\n"
        "• What was the goal of this turn?\n"
        "• What was accomplished?\n"
        "• What failed?\n"
        "• What is left to do?\n"
        "• Which files were modified and how?\n"
        "• What was learned?\n\n"
        "RULES:\n"
        "1. Plain text only. NO code blocks, NO markdown fences.\n"
        "2. Use ONLY information from the exchange. Do not speculate.\n"
        "3. Be specific: mention file names, function names, outcomes.\n"
        "4. One-shot response. Stop after answering."
    )

    # Build user content: anchor + everything that happened after
    parts = []
    for msg in last_turn:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content") or ""
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            content = " ".join(text_parts)
        content = str(content)[:_MAX_SECTION_CHARS]
        parts.append(f"[{role}] {content}")

    user_content = "\n\n".join(parts)
    if len(user_content) > 4000:
        user_content = user_content[:3997] + "..."

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


# Fence styles: each entry is (paired_pattern, dangling_opener_pattern).
# Paired patterns remove complete ``open ... close`` blocks.
# Dangling patterns remove an UNCLOSED opener and everything after it — but the
# opener MUST start a line (`(?m)^[ \t]*`) and we strip to true end-of-string
# (`\Z`). Unanchored dangling patterns were the MAJOR bug: inline prose that
# merely *mentions* a fence token (e.g. "added @PY and @SH stripping") got
# truncated to its first token. Anchoring to line-start keeps prose mentions
# intact while still stripping a real own-line (or indented) unclosed opener.
# Ordered: backtick, html <py>/<sh>, at-style @PY/@SH.
_FENCE_STYLES: list[tuple[str, str]] = [
    (r'`{3,6}[\s\S]*?`{3,6}', r'(?m)^[ \t]*`{3,6}[\s\S]*\Z'),
    (r'<py>[\s\S]*?</py>', r'(?m)^[ \t]*<py>[\s\S]*\Z'),
    (r'<sh>[\s\S]*?</sh>', r'(?m)^[ \t]*<sh>[\s\S]*\Z'),
    (r'@PY[\s\S]*?@/PY', r'(?m)^[ \t]*@PY[\s\S]*\Z'),
    (r'@SH[\s\S]*?@/SH', r'(?m)^[ \t]*@SH[\s\S]*\Z'),
]


def _strip_code_blocks(text: str) -> str:
    """Strip code blocks from text for summarization (all fence styles).

    Two passes, ordered deliberately:
      1. Paired blocks (``open ... close``) removed first — non-greedy so a
         real close boundary is honored.
      2. Dangling openers (unclosed block, opener to end-of-string) removed
         after, so a well-formed block that closes is preserved up to it while
         a leaked unclosed block is stripped to the end.

    Styles stay symmetric: every paired pattern has a matching dangling
    pattern in _FENCE_STYLES.
    """
    cleaned = text
    for paired, _dangling in _FENCE_STYLES:
        cleaned = re.sub(paired, '', cleaned)
    for _paired, dangling in _FENCE_STYLES:
        cleaned = re.sub(dangling, '', cleaned)
    return cleaned.strip()


def _attach_summary_metadata(context: "TauContext", summary: str) -> None:
    """Add 'summary' key to last assistant message dict in place."""
    msgs = context._messages
    for msg in reversed(msgs):
        if msg.get("role") == "assistant":
            if "summary" not in msg:
                msg["summary"] = summary
            return


def _summary_quality_ok(summary: str, request_text: str) -> bool:
    """Minimal quality gate for a produced summary (FIX 4).

    Rejects:
      * summaries shorter than _MIN_CLEAN_CHARS (stubbed stubs like
        'TAU-MANUAL-OK' that are not otherwise placeholder-shaped), and
      * near-verbatim echoes of the user's own request (a model that just
        parrots the request back is useless as a compression signal).

    Echo test is intentionally simple/robust: normalized-token overlap
    >= 0.8 between summary and request is treated as an echo. This catches
    copy-paste while tolerating light rephrasing.
    """
    s = (summary or "").strip()
    if len(s) < _MIN_CLEAN_CHARS:
        return False
    req = (request_text or "").strip()
    if not req:
        return True
    s_tokens = re.findall(r'[\w]+', s.lower())
    r_tokens = re.findall(r'[\w]+', req.lower())
    if not s_tokens:
        return False
    # overlap: fraction of summary tokens that also appear in the request.
    # Threshold 0.9 (raised from 0.8): a 0.8 cut false-rejected legitimate
    # >=40-char summaries that are merely dominated by shared stop-words
    # (the, and, the-fence-word, ...). 0.9 keeps catching near-verbatim echoes
    # while sparing rephrased summaries. Kept simple + testable deliberately.
    req_set = set(r_tokens)
    overlap = sum(1 for tok in s_tokens if tok in req_set) / len(s_tokens)
    if overlap >= 0.9:
        return False
    return True


def _last_request_text(context: "TauContext") -> str:
    """Text of the last real user message (for echo detection)."""
    for msg in reversed(context._messages):
        if is_real_user_request(msg):
            content = msg.get("content") or ""
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                content = " ".join(parts)
            return str(content)
    return ""


def _write_summary_audit(audit_writer, summary: str) -> None:
    """Write a typed TURN_SUMMARY success record to the audit writer.

    Uses the public AuditWriter.turn_summary() API (which emits a greppable
    ``TURN_SUMMARY`` record with the summary as a continuation line). Guards
    against a None/legacy writer that predates turn_summary().
    """
    try:
        if audit_writer and hasattr(audit_writer, 'turn_summary'):
            audit_writer.turn_summary(summary)
            if hasattr(audit_writer, 'flush'):
                audit_writer.flush()
    except Exception as e:
        logger.warning("Audit write failed: %s", e)


def _write_summary_skip(audit_writer, reason: str) -> None:
    """Emit a TURN_SUMMARY status=skipped observability record (FIX 2).

    Only called when the turn WAS substantive but summarization then failed,
    so the audit distinguishes 'ran and produced', 'ran but skipped (why)'
    from 'never attempted'. Cheap: single record, never spam on trivially-false
    guards. Uses the same TURN_SUMMARY type so produced-vs-skipped is one grep.
    """
    try:
        if audit_writer and hasattr(audit_writer, 'turn_summary_status'):
            audit_writer.turn_summary_status("skipped", reason)
        elif audit_writer and hasattr(audit_writer, 'turn_summary'):
            # Fallback for writers without the status variant.
            audit_writer.turn_summary(f"status=skipped reason={reason}")
        else:
            return
        if hasattr(audit_writer, 'flush'):
            audit_writer.flush()
    except Exception as e:
        logger.warning("Audit skip-write failed: %s", e)


def _is_substantive_turn(context: "TauContext") -> bool:
    """Return False if turn is not substantive."""
    msgs = context._messages
    if not msgs:
        return False

    # Find last real user message
    last_real_user_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        msg = msgs[i]
        if is_real_user_request(msg):
            last_real_user_idx = i
            break

    if last_real_user_idx is None:
        return False

    # Check for assistant messages after last real user
    assistant_msgs = []
    for msg in msgs[last_real_user_idx + 1:]:
        if msg.get("role") == "assistant":
            assistant_msgs.append(msg)

    if not assistant_msgs:
        return False

    # Check last assistant content
    last_assistant = assistant_msgs[-1]
    content = last_assistant.get("content") or ""
    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        content = " ".join(text_parts)

    content = str(content).strip()

    # Check length and placeholder patterns
    if len(content) < _MIN_SUBSTANTIVE_CHARS:
        # Check for placeholder patterns
        placeholder_patterns = [
            r'^\s*\(none\)\s*$',
            r'^\s*\(\s*placeholder\s*\)\s*$',
            r'^\s*<\s*thinking\s*>',
        ]
        for pattern in placeholder_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return False

    return True
