"""RLM (Reinforcement Learning Model) agent loop.

Pure Python REPL-based agent loop. The model has NO tools — it communicates
entirely through Python code executed in a persistent kernel.

Loop Flow:
1. Call LLM (pure text — no tools)
2. Extract Python code blocks from response (```python ... ```)
3. Execute code in the persistent REPL kernel
4. Collect ALL feedback (output, warnings, escalation) into ONE synthetic user message
5. Check answer["ready"] — if True, end turn and return answer["content"]
6. Loop back to step 1 if answer not ready
7. Force end if max_turns, max_consecutive_errors, or loop escalation reached

Message Contract:
Every assistant message is followed by exactly ONE synthetic user message
containing all feedback (REPL output, loop warnings, answer stagnation,
escalation text). No continuation bridges — the loop guarantees alternation.

Loop Detection:
- Tracks repetitive Python code patterns (consecutive repeats, entropy)
- Escalation returns text included in the synthetic user message
- Level 3+ (9+ warnings) forces end turn
- Detects answer stagnation (answer["content"] not changing)
"""
from __future__ import annotations

import logging
import traceback

__all__ = [
    'run_rlm_loop',
]
from agent_console import (
    error,
    warning,
)
from agent_lifecycle import AgentLifecycle
from agent_pipeline import (
    build_synthetic_feedback,
    call_llm,
    check_work_budget,
    extract_code,
    validate_and_compress,
    validate_eot,
)

from agent_repl_parse import fence_tokens
from agent_llm_invoke import StreamAbortedError

# Consecutive no-code turns before the turn is force-ended. A plain-text
# reply is a WARNING, not an execution error, so it does not feed
# consecutive_errors - but it still needs its own escalation.
_NO_CODE_FORCE = 5


def _answer_to_str(answer) -> str:
    """Convert answer content to string for display/return."""
    if answer is None:
        return ""
    content = answer.content
    if isinstance(content, str):
        return content
    try:
        import json
        return json.dumps(content, indent=2, default=str)
    except (TypeError, ValueError):
        return str(content)


# Console-only copy for the tool-call-tag fixes, addressed to the HUMAN operator
# as a terse glanceable banner. These are display-only: the model still receives
# its own explanatory copy via STEP_MESSAGES (see RepairReport.message /
# _repair_notes.message), which we deliberately leave unchanged so the model is
# told we altered its output. The operator-facing text below is never sent to the
# model. Only the tool-call-tag steps get this treatment; other repairs keep the
# original per-step explanation.
_CONSOLE_MESSAGES = {
    "protocol-envelope-stripped":
        "Stripped tool-call tags wrapped around a valid code fence - code unchanged.",
    "glue-opener-split":
        "Split a tool-call tag fused onto a fence opener - code unchanged.",
}
_FIX_SEP = "\u2500" * 40  # same box-drawing char as the [REPL CODE]/[REPL ERROR] rules


def _repair_notice(rep, fence_style: str) -> None:
    """Yellow multi-line console warning per repaired block (also written to audit).

    Tool-call-tag fixes render as a user-facing `[REPL CODE FENCE FIX]` banner
    (see _CONSOLE_MESSAGES); all other repairs keep the plain-language per-step
    explanation. Either way line 1 is human-facing text, followed by the repaired
    block rendered in the active style, previewed first-2 / `... +N lines ...` /
    last-2 lines.
    """
    from agent_console.primitives import display_warn
    from agent_fence_repair import _preview

    ft = fence_tokens(fence_style)
    for e in rep.entries:
        if e.step in _CONSOLE_MESSAGES:
            is_bash = e.lang == "bash"
            block = [ft["sh_open"] if is_bash else ft["py_open"]]
            block += _preview(e.block)
            block += [ft["sh_close"] if is_bash else ft["py_close"]]
            lines = [f"[REPL CODE FENCE FIX] {_FIX_SEP}", _CONSOLE_MESSAGES[e.step]] + block
        else:
            # Tier-2 entries (llm-repair-applied/failed) carry a dynamic .note
            # and an empty block; render note on its own line, no fake block.
            head = e.message
            if getattr(e, "note", ""):
                head = (head + "  [" + e.note + "]").strip()
            lines = [head]
            if e.block:
                is_bash = e.lang == "bash"
                lines = lines + [ft["sh_open"] if is_bash else ft["py_open"]]
                lines += _preview(e.block)
                lines = lines + [ft["sh_close"] if is_bash else ft["py_close"]]
        display_warn("\n".join(lines))


def _no_code_message(fence_style: str, ambiguous_fences: bool, streak: int,
                  force_at: int) -> str:
    """No-CODE feedback; fence-aware (see _repair_notice) but NEVER drops
    the streak escalation. Options list only when fences are not to blame."""
    ft = fence_tokens(fence_style)
    if not ambiguous_fences:
        msg = ("[SYSTEM: NO CODE] Your response contains no code blocks. "
               "In RLM mode, you must execute Python code to make progress. "
               f"Options: (1) Write code in {ft['py_label']} or {ft['sh_label']} blocks, "
               "(2) Write a single code block setting answer['content'] and answer['ready'] = True, "
               "(3) Use available functions: host_request(), "
               "available_skills(), find_skills(), load_skill(), spawn().")
    else:
        msg = ("[SYSTEM: NO CODE] Your code was not executed: the fencing was "
               "ambiguous and could not be repaired safely. Follow the "
               "fence-fix note below and re-emit the block - each fence token "
               "ALONE on its own line at column 0.")
    if streak >= 2:
        msg += f" (attempt {streak} of {force_at})"
    return msg


def run_rlm_loop(agent: 'TauErgon') -> str:
    """RLM main loop: call LLM without tools, execute Python code, check answer.

    The model has NO tools — it communicates entirely through Python code
    executed in a persistent kernel. See module docstring for full details.

    Each turn produces exactly ONE synthetic user message containing all
    feedback (REPL output, loop warnings, escalation text). No continuation
    bridges — the loop guarantees user-assistant alternation.

    Args:
        agent: The TauErgon agent instance (must have REPL enabled).

    Returns:
        The final answer content or an error message.

    Raises:
        ValueError: If agent.client is None.
    """
    from agent_loop_detect import LoopDetector
    from agent_loop_escalation import LoopEscalationManager

    # Get RLM config
    rlm_config = getattr(agent.config, "rlm", None) if agent.config else None
    max_turns = getattr(agent, "_force_max_turns", None) or (getattr(rlm_config, "max_turns", 500) if rlm_config else 500)
    repl_cfg = getattr(rlm_config, "repl", None) if rlm_config else None
    max_output_chars = getattr(repl_cfg, "max_output_chars", 8192) if repl_cfg else 8192

    agent.force_end_turn = None
    agent.last_substantive_response = None
    turn_count = 0
    # M1: reset the no-code streak for every NEW user turn, like the other
    # per-turn counters. It is otherwise only cleared when a code block runs, so
    # prose replies from a previous turn carried over and could force-end the
    # FIRST prose reply of the next turn (streak 4 -> 5 on the opening message).
    agent._no_code_streak = 0
    consecutive_errors = 0
    max_consecutive_errors = 5
    agent.reset_answer()

    if getattr(agent, 'loop_detector', None) is None:
        agent.loop_detector = LoopDetector()
    loop_detector = agent.loop_detector
    # Reset loop state for each new user turn (prevents escalation carryover)
    loop_detector.reset()
    loop_escalation = LoopEscalationManager(loop_detector, agent.context, agent)

    while True:
        def _check_lifecycle(label: str):
            """Check lifecycle and return early if exit/interrupt requested."""
            if AgentLifecycle.is_exit_requested():
                agent._cleanup_pending = True
                agent.context.close_turn(f"[{label}]", skip_cleanup=True)
                answer = agent.get_answer()
                return _answer_to_str(answer) if answer else f"[{label}]"
            if AgentLifecycle.is_interrupted():
                agent._cleanup_pending = True
                agent.context.close_turn("[Interrupted]", skip_cleanup=True)
                answer = agent.get_answer()
                return _answer_to_str(answer) if answer else "[Interrupted]"

        result = _check_lifecycle("Session ended")
        if result is not None:
            return result

        agent._process_control_queue()

        result = _check_lifecycle("Session ended")
        if result is not None:
            return result

        # Check max turns
        turn_count += 1
        _errors_counted_this_turn = False
        if turn_count > max_turns:
            warning(f"RLM loop: max turns ({max_turns}) reached — force ending")
            agent._cleanup_pending = True
            answer = agent.get_answer()
            if answer and answer.content:
                answer_str = _answer_to_str(answer)
                agent.context.close_turn(answer_str, skip_cleanup=True)
                return answer_str
            error_msg = (
                f"RLM loop: max turns ({max_turns}) reached without answer. "
                f"The task may be too complex. Consider: (1) Breaking it into smaller subtasks, "
                f"(2) Using simpler code, (3) Delegating to subagents with rlm()."
            )
            agent.context.close_turn(error_msg, skip_cleanup=True)
            return error_msg

        # --- B: WORK BUDGET CHECK ---
        budget_exit = check_work_budget(agent)
        if budget_exit is not None:
            return budget_exit

        # Validate context + proactive compression
        validate_and_compress(agent, turn_count)

        code = ""
        try:
            # Call LLM
            response_text, reasoning_content = call_llm(agent)

            # In-place fence/protocol repair: rewrite wrong-style fences and
            # tool-call mimicry to the ACTIVE style BEFORE storing, so the model
            # never re-sees its own bad pattern (mirrors tau llm_postparse).
            _fence_style = getattr(repl_cfg, "fence_style", "std") if repl_cfg else "std"
            from agent_fence_repair import repair_response
            response_text, _repair_notes = repair_response(response_text, _fence_style)
            # Tier-2: the deterministic ladder can only say AMBIGUOUS here -> ask
            # the context-free segmenter BEFORE storing, so the model never re-sees
            # broken fencing (REQ-LFS-004). Never raises: transport errors return
            # status-quo text + a lesson entry. Stream-abort path never reaches here.
            if getattr(_repair_notes, "verdict", "ok") == "ambiguous":
                try:
                    from agent_pipeline import build_segmenter_invoke_fn
                    from agent_fence_segmenter import segment_and_repair
                    _inv = build_segmenter_invoke_fn(
                        agent.client, agent.model_name, agent.resolve_group_params())
                    _t2_text, _t2_entries, _t2_verdict, _t2_tl = segment_and_repair(
                        response_text, _fence_style,
                        invoke_fn=_inv, model=agent.model_name)
                    if _t2_verdict == "ok":
                        # D3: the fixer succeeded, so the ladder's earlier
                        # "could not repair" lessons are now false — code WILL run.
                        # Keep only the accurate applied lesson (telemetry still
                        # holds the full _t2_tl forensics, written independently).
                        response_text = _t2_text
                        # Keep ladder lessons that STAY TRUE after a tier-2
                        # success (style conversion / glue / envelope strip teach
                        # the active token) + the applied lesson; drop only the
                        # now-false refusal notes.
                        _KEEP = {"other-style-enclosed", "half-open-other-style",
                                 "protocol-envelope-stripped", "protocol-tags",
                                 "glue-opener-split"}
                        _repair_notes[:] = (_t2_entries + [e for e in _repair_notes
                                                          if e.step in _KEEP])
                        _repair_notes.verdict = "ok"
                    elif _t2_entries:
                        _repair_notes.extend(_t2_entries)
                except Exception as _t2_exc:
                    warning(f"RLM: tier-2 segmenter skipped: {type(_t2_exc).__name__}: {_t2_exc}")
            if _repair_notes:
                _repair_notice(_repair_notes, _fence_style)

            # Append assistant response (NO bridge — loop guarantees alternation)
            agent.context.append_assistant(response_text, reasoning=reasoning_content)

            # Extract Python code blocks
            code, code_blocks = extract_code(response_text, fence_style=_fence_style)

            # Collect ALL feedback into parts list
            parts: list[str] = []
            repl_result = None

            if code_blocks:
                agent._no_code_streak = 0
                # Display code (magenta). Suppressed when the block was repaired —
                # _repair_notice already showed it, so echoing twice is noise.
                if not _repair_notes:
                    try:
                        from agent_console import repl_code
                        repl_code(code)
                    except ImportError:
                        pass

                # Loop detection — warning goes into feedback
                loop_warning = loop_detector.record_code_execution(code)
                if loop_warning:
                    warning(f"RLM: {loop_warning}")
                    parts.append(loop_warning)

                # Execute code
                try:
                    repl_result = agent._repl.execute_blocks(code_blocks, error_count=consecutive_errors + 1)

                    if repl_result.success and repl_result.output:
                        try:
                            from agent_console import repl_output
                            repl_output(repl_result.output)
                        except ImportError:
                            pass
                        _seq = getattr(repl_result, "code_seq", 0)
                        if _seq:
                            parts.append(f"[this block: source in _code{_seq}, output in _output{_seq}]")
                        parts.append(repl_result.output)
                        consecutive_errors = 0
                    elif not repl_result.success:
                        error_msg = repl_result.error or "Unknown error"
                        try:
                            from agent_console import repl_error
                            repl_error(error_msg)
                        except ImportError:
                            pass
                        # B13: include partial stdout so the LLM sees output
                        # produced before the failure, not just the error.
                        if repl_result.output:
                            parts.append(repl_result.output)
                        parts.append(error_msg)
                        consecutive_errors += 1
                        _errors_counted_this_turn = True
                        if consecutive_errors >= max_consecutive_errors:
                            error_detail = (
                                f"Too many consecutive errors ({max_consecutive_errors}): {error_msg}. "
                                f"Consider: (1) Checking your code for syntax errors, "
                                f"(2) Using simpler approaches, "
                                f"(3) Breaking the task into smaller steps."
                            )
                            error(f"RLM: {max_consecutive_errors} consecutive errors — force ending")
                            agent._cleanup_pending = True
                            answer = agent.get_answer()
                            agent.context.close_turn(error_detail, skip_cleanup=True)
                            return _answer_to_str(answer) if answer else error_detail

                except Exception as exec_err:
                    error_msg = f"{type(exec_err).__name__}: {exec_err}"
                    try:
                        from agent_console import repl_error
                        repl_error(error_msg)
                    except ImportError:
                        pass
                    parts.append(error_msg)
                    consecutive_errors += 1
                    _errors_counted_this_turn = True
                    if consecutive_errors >= max_consecutive_errors:
                        error(f"RLM: {max_consecutive_errors} consecutive errors — force ending")
                        agent._cleanup_pending = True
                        answer = agent.get_answer()
                        agent.context.close_turn(f"Too many consecutive errors: {error_msg}", skip_cleanup=True)
                        return _answer_to_str(answer) if answer else error_msg
            else:
                # No code found
                ft = fence_tokens(_fence_style)
                # WARNING only — a plain-text reply is not an execution error and
                # must not feed the consecutive_errors force-kill (see design record).
                # But it still needs SOME escalation, or a prose-only model spins
                # to max_turns with zero pressure. Separate streak, separate rule.
                _no_code_streak = getattr(agent, "_no_code_streak", 0) + 1
                agent._no_code_streak = _no_code_streak
                if _no_code_streak >= _NO_CODE_FORCE:
                    detail = (
                        f"No code block produced {_no_code_streak} turns in a row. "
                        f"Wrap code in {ft['py_label']} / {ft['py_close']} (or "
                        f"{ft['sh_label']} / {ft['sh_close']} for shell), each token "
                        "alone at the start of its own line."
                    )
                    error(f"RLM: {_no_code_streak} consecutive no-code turns — force ending")
                    agent._cleanup_pending = True
                    answer = agent.get_answer()
                    agent.context.close_turn(detail, skip_cleanup=True)
                    return _answer_to_str(answer) if answer else detail
                # If the fences caused the miss, the refusal note below carries
                # the exact fix — the generic option list would bury it.
                no_code_msg = _no_code_message(
                    _fence_style,
                    getattr(_repair_notes, "verdict", "ok") == "ambiguous",
                    _no_code_streak, _NO_CODE_FORCE)
                warning(no_code_msg)
                parts.append(no_code_msg)

            # Repair note goes AFTER the REPL output/error — more visible there
            # than as a header (design record).
            if _repair_notes and _repair_notes.message:
                parts.append(_repair_notes.message)

            # Answer stagnation check
            answer = agent.get_answer()
            if answer and answer.content:
                stagnation_warning = loop_detector.check_answer_stagnation(answer.content)
                if stagnation_warning:
                    warning(f"RLM: {stagnation_warning}")
                    parts.append(stagnation_warning)

            # EOT validation (multi-block, empty, oversized)
            parts.extend(validate_eot(agent, response_text, turn_count))
            # Re-fetch: validate_eot may clear ready (rejection). The snapshot
            # taken above is stale — using it at the exit gate would defeat
            # EOT rejection (P7-B1-26).
            answer = agent.get_answer()

            # Loop escalation — RIGHT HERE, after all feedback collected
            escalation_text, force_end = loop_escalation.handle_loop_escalation()
            if escalation_text:
                parts.append(escalation_text)

            # Add budget warning if triggered this turn
            if getattr(agent, '_budget_warning_text', None):
                parts.append(agent._budget_warning_text)
                agent._budget_warning_text = None

            # Build and append synthetic feedback message
            build_synthetic_feedback(agent, parts)

            # Force end if escalation demands it
            if force_end:
                answer = agent.get_answer()
                agent._cleanup_pending = True
                term_msg = f"Loop terminated ({loop_detector.get_escalation_info()['total_warnings']} warnings)."
                if answer and answer.content:
                    term_msg += f" Last answer: {answer.content[:200]}"
                agent.context.close_turn(term_msg, skip_cleanup=True)
                return _answer_to_str(answer) if answer else term_msg

            # Answer check — THE EXIT GATE
            if answer and answer.ready:
                answer_str = _answer_to_str(answer)
                agent.last_substantive_response = answer_str
                agent._cleanup_pending = True
                agent.context.close_turn(answer_str, skip_cleanup=True)

                # Check for pending .md command segments
                if agent._pending_md_segments:
                    try:
                        from agent_console.templates import answer_display
                        answer_display(answer_str)
                    except Exception:
                        pass
                    top_segments = agent._pending_md_segments[-1]
                    if top_segments:
                        next_segment = top_segments.pop(0)
                        if not top_segments:
                            agent._pending_md_segments.pop()
                        input_handler = getattr(agent, "_input_handler", None)
                        if input_handler is not None:
                            agent._process_md_segment(
                                next_segment, input_handler, "multiprompt"
                            )
                            continue

                # Default: display and return (covers top-level and cmd_dispatch)
                try:
                    from agent_console.templates import answer_display
                    answer_display(answer_str)
                except Exception:
                    pass
                return answer_str

        except StreamAbortedError as e:
            # Stream aborted due to token limit — preserve partial work in context
            _repl_cfg = getattr(getattr(agent.config, "rlm", None), "repl", None)
            _fs = getattr(_repl_cfg, "fence_style", "std") if _repl_cfg else "std"
            ft = fence_tokens(_fs)

            if e.partial_content.strip():
                # 2b: partial content exists — preserve it for the LLM to continue
                truncated = e.partial_content.rstrip() + "\n\n[TRUNCATED: max_tokens exceeded]"
                from agent_fence_repair import repair_response as _rr
                truncated, _ = _rr(truncated, _fs)
                agent.context.append_assistant(truncated, reasoning=e.partial_reasoning or None)
                feedback = "[STREAM ABORTED: response truncated at max_tokens. Partial work preserved. Continue from where you left off.]"
            else:
                # 2a: no content, model was still reasoning
                synthetic_code = (
                    f"{ft['py_open']}\n"
                    "answer['content'] = \"Thinking budget exhausted for this round. \"\n"
                    "    'I was still reasoning and need to continue next round where I left off.'\n"
                    f"{ft['py_close']}"
                )
                agent.context.append_assistant(synthetic_code, reasoning=e.partial_reasoning or None, synthetic=True)
                feedback = "[STREAM ABORTED: reasoning exceeded max_tokens. Continue from where you left off.]"

            warning(f"RLM: {feedback}")
            build_synthetic_feedback(agent, [feedback])

            consecutive_errors += 1
            _errors_counted_this_turn = True
            if consecutive_errors >= max_consecutive_errors:
                agent._cleanup_pending = True
                answer = agent.get_answer()
                agent.context.close_turn(
                    f"RLM: too many consecutive stream aborts ({consecutive_errors})",
                    skip_cleanup=True,
                )
                return _answer_to_str(answer) if answer else f"Stream aborted {consecutive_errors} times"

        except Exception as e:
            traceback.print_exc()
            error_detail = f"{type(e).__name__}: {e}"
            error(f"RLM loop error: {error_detail}")
            try:
                from agent_console import repl_error
                repl_error(error_detail)
            except ImportError:
                pass
            try:
                msgs = agent.context._messages
                if msgs and msgs[-1].get('role') == 'user':
                    logging.warning("agent_loop: skipping error append — last msg is user (alternation)")
                else:
                    msg_count = len(msgs)
                    _sess = getattr(agent, '_session', None)
                    from agent_context_store import compute_live_tokens
                    _live = compute_live_tokens(
                        msgs,
                        getattr(_sess, 'last_exact_context_tokens', None) if _sess else None,
                        getattr(_sess, 'last_exact_msg_count', None) if _sess else None,
                        getattr(_sess, 'last_turn_output_tokens', 0) if _sess else 0,
                    )
                    _max = getattr(agent, 'max_context_tokens', 200000) or 200000
                    pct = _live / _max
                    agent.context.append_repl_error(error_detail, code, msg_count, pct)
            except Exception as ctx_err:
                logging.warning("agent_loop: failed to append error to context: %s", ctx_err)

            if not _errors_counted_this_turn:
                consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                agent._cleanup_pending = True
                answer = agent.get_answer()
                agent.context.close_turn(f"RLM loop error: {error_detail}", skip_cleanup=True)
                return _answer_to_str(answer) if answer else error_detail

    answer = agent.get_answer()
    return _answer_to_str(answer) if answer else "[RLM loop exited unexpectedly]"
