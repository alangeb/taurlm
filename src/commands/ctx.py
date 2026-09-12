"""Context Command — /ctx

Display conversation context in various formats.

Usage:
    /ctx              — Show summary (default, like parent tau)
    /ctx full         — Show full context (all messages, no truncation)
    /ctx summary      — Compact summary with token stats
    /ctx short        — Compact view: one line per message (80 chars max)
    /ctx sum          — Show accumulated turn summaries
    /ctx last         — Show last message
    /ctx last sum     — Show last turn summary
    /ctx last user    — Show last real user prompt (skips REPL/synthetic)
    /ctx user         — Show ALL real user prompts (untruncated, context-indexed)
    /ctx last assistant — Show last assistant message
    /ctx assistant    — Show ALL EOT assistant answers (untruncated, context-indexed)
    /ctx clear        — Clear context (keep REPL state)
    /ctx push         — Push context to stack
    /ctx pop          — Pop context from stack
    /ctx undo [n]     — Undo last n user turn(s) (default 1)
    /ctx compress [n] — Compress to n% of max context (default 30)
    /ctx fix          — Repair context corruption (empty responses, sequence
                        errors, oversize messages, high-repetition messages).
                        Backs up to stack first (undo with /ctx pop).
    /ctx fix check    — Dry run: report what /ctx fix would change
    /ctx help         — Show this help
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_core import TauErgon


def run(agent: "TauErgon", args: list[str] | None = None) -> str:
    """Run the /ctx command.

    Args:
        agent: The current TauErgon agent instance.
        args: Command arguments list.

    Returns:
    args = args or []
        Status message or empty string (output displayed directly).
    """
    args = args or []
    from agent_console.primitives import display_info
    from agent_context_dump import dump_context, dump_last, format_single_message

    # Parse arguments
    arg_str = " ".join(args).strip().lower()
    parts = arg_str.split() if arg_str else []

    # Help
    if parts and parts[0] == "help":
        return (
            "CONTEXT COMMANDS\n"
            "  /ctx              — Show summary (default)\n"
            "  /ctx full         — Full context (all messages, no truncation)\n"
            "  /ctx summary      — Compact summary with token stats\n"
            "  /ctx short        — Compact: one line per message (80 chars)\n"
            "  /ctx sum          — Show accumulated turn summaries\n"
            "  /ctx last         — Show last message\n"
            "  /ctx last sum     — Show last turn summary\n"
            "  /ctx last user    — Show last real user prompt\n"
            "  /ctx user         — Show ALL real user prompts (untruncated, context-indexed)\n"
            "  /ctx last assistant — Show last assistant message\n"
            "  /ctx assistant    — Show ALL EOT assistant answers (untruncated, context-indexed)\n"
            "  /ctx clear        — Clear context (keep REPL state)\n"
            "  /ctx push         — Push context to stack\n"
            "  /ctx pop          — Pop context from stack\n"
            "  /ctx undo [n]     — Undo last n user turn(s) (default 1)\n"
            "  /ctx compress [n] — Compress to n% of max (default 30)\n"
            "  /ctx fix          — Repair context corruption (backup → /ctx pop)\n"
            "  /ctx fix check    — Dry run: report fixes without applying\n"
            "  /ctx syspromptupdate — Re-read AGENT_RLM.md and update system prompt\n"
        )

    # /ctx or /ctx summary (default)
    if not parts or parts[0] == "summary":
        output = dump_context(agent.context, mode="summary", max_tokens=agent.max_context_tokens)
        display_info(output)
        return ""

    # /ctx full
    if parts[0] == "full":
        output = dump_context(agent.context, mode="full", max_tokens=agent.max_context_tokens)
        display_info(output)
        return ""

    # /ctx short
    if parts[0] == "short":
        output = dump_context(agent.context, mode="short")
        display_info(output)
        return ""

    # /ctx sum — show accumulated turn summaries
    if parts[0] == "sum":
        return _ctx_sum(agent)

    # /ctx last [role]
    if parts[0] == "last":
        role = parts[1] if len(parts) > 1 else None
        if role == "sum":
            return _ctx_last_sum(agent)
        if role == "user":
            # Show last REAL user prompt (skip REPL/synthetic messages)
            output = _dump_last_real_user(agent)
        else:
            output = dump_last(agent.context, role)
        display_info(output)
        return ""

    # /ctx user — show ALL real user prompts
    if parts[0] == "user":
        output = _dump_all_real_users(agent)
        display_info(output)
        return ""

    # /ctx assistant — show ALL EOT assistant answers
    if parts[0] == "assistant":
        output = _dump_all_eot_assistants(agent)
        display_info(output)
        return ""

    # /ctx clear — clear context, keep REPL
    if parts[0] == "clear":
        return _ctx_clear(agent)

    # /ctx push — push context to stack
    if parts[0] == "push":
        return _ctx_push(agent)

    # /ctx pop — pop context from stack
    if parts[0] == "pop":
        return _ctx_pop(agent)

    # /ctx undo [n] — undo last n user turns
    if parts[0] == "undo":
        n = 1
        if len(parts) > 1:
            try:
                n = int(parts[1])
                if n < 1:
                    return "Undo count must be >= 1"
            except ValueError:
                return f"Invalid undo count: {parts[1]}"
        return _ctx_undo(agent, n)

    # /ctx compress [n] — compress to n% of max context
    if parts[0] == "compress":
        n = 30
        if len(parts) > 1:
            try:
                n = int(parts[1])
                if n < 1 or n > 100:
                    return "Target must be 1-100"
            except ValueError:
                return f"Invalid target: {parts[1]}"
        return _ctx_compress(agent, n)

    # /ctx fix [check] — repair context corruption
    if parts[0] == "fix":
        dry = len(parts) > 1 and parts[1] == "check"
        return _ctx_fix(agent, dry)

    # /ctx syspromptupdate — re-read AGENT_RLM.md and update system prompt
    if parts[0] == "syspromptupdate":
        return _ctx_syspromptupdate(agent)

    # Unknown subcommand
    return (
        f"Unknown /ctx subcommand: '{parts[0]}'\n"
        "Use /ctx help for available options."
    )


def _ctx_clear(agent: "TauErgon") -> str:
    """Clear context messages but preserve REPL kernel state."""
    from agent_models import Colors

    agent.context.clear()
    return f"{Colors.GREEN}Context cleared. REPL state preserved.{Colors.RESET}"


def _ctx_push(agent: "TauErgon") -> str:
    """Push current context to in-memory stack."""
    from agent_models import Colors
    import copy

    if not hasattr(agent, '_ctx_stack'):
        agent._ctx_stack = []

    agent._ctx_stack.append(copy.deepcopy(agent.context._messages))
    depth = len(agent._ctx_stack)
    return f"{Colors.GREEN}Context pushed (#{depth}). Stack depth: {depth}.{Colors.RESET}"


def _ctx_pop(agent: "TauErgon") -> str:
    """Pop context from in-memory stack."""
    from agent_models import Colors

    if not hasattr(agent, '_ctx_stack') or not agent._ctx_stack:
        return f"{Colors.YELLOW}Stack is empty. Nothing to pop.{Colors.RESET}"

    messages = agent._ctx_stack.pop()
    agent.context._messages = messages
    depth = len(agent._ctx_stack)
    return f"{Colors.GREEN}Context restored. Stack depth: {depth}.{Colors.RESET}"


def _ctx_fix(agent: "TauErgon", dry: bool = False) -> str:
    """Repair context corruption via the context_fix engine.

    dry=True: report-only (no mutation, no backup).
    dry=False: backup to _ctx_stack (undo via /ctx pop), apply, verify.
    """
    import copy

    from agent_models import Colors
    from context_fix import fix_context

    messages = agent.context._messages
    if not messages:
        return f"{Colors.YELLOW}Context is empty — nothing to fix.{Colors.RESET}"

    fixed, report = fix_context(messages, dry_run=dry)

    if report.aborted:
        reason = report.details[-1] if report.details else "unknown"
        return f"{Colors.YELLOW}Fix aborted (original preserved): {reason}{Colors.RESET}"

    if dry:
        return _format_fix_report(report, dry=True)

    if report.total_fixes == 0:
        extra = (
            f"\n{Colors.YELLOW}Remaining validation issues (not auto-fixable): "
            + "; ".join(report.post_errors)
            + Colors.RESET
            if report.post_errors
            else ""
        )
        return (
            f"{Colors.GREEN}Context is healthy — 0 fixes needed."
            f"{Colors.RESET}{extra}"
        )

    # Backup BEFORE mutating (undo via /ctx pop).
    if not hasattr(agent, "_ctx_stack"):
        agent._ctx_stack = []
    agent._ctx_stack.append(copy.deepcopy(messages))

    agent.context._messages = fixed
    return _format_fix_report(report, stack_depth=len(agent._ctx_stack))


def _format_fix_report(report, dry: bool = False, stack_depth: int = 0) -> str:
    """Format a FixReport as a console string."""
    from agent_models import Colors

    mode = "DRY RUN — nothing changed" if dry else "applied"
    lines = [
        f"{Colors.CYAN}CTX FIX REPORT ({mode}) — {report.total_fixes} fix(es){Colors.RESET}",
        f"  empty assistant → placeholder : {report.empty_assistant}",
        f"  merged consecutive messages   : {report.merged}",
        f"  dummy turns inserted          : {report.dummies_inserted}",
        f"  oversized truncated           : {report.truncated}",
        f"  entropy-corrected             : {report.entropy_fixed}",
        f"  bytes: {report.bytes_before} → {report.bytes_after}",
    ]
    if report.details:
        lines.append(f"{Colors.WHITE}  details:{Colors.RESET}")
        lines.extend(f"    {d}" for d in report.details[:20])
        if len(report.details) > 20:
            lines.append(f"    ... and {len(report.details) - 20} more")
    if report.pre_errors:
        lines.append(f"{Colors.YELLOW}  pre-fix validation errors: {len(report.pre_errors)}{Colors.RESET}")
    if report.post_errors:
        lines.append(
            f"{Colors.YELLOW}  remaining validation errors: {len(report.post_errors)} — "
            + "; ".join(report.post_errors[:5])
            + Colors.RESET
        )
    else:
        lines.append(f"{Colors.GREEN}  post-fix validation: clean{Colors.RESET}")
    if not dry and stack_depth:
        lines.append(f"{Colors.WHITE}  backup saved (stack #{stack_depth}) — /ctx pop to undo{Colors.RESET}")
    return "\n".join(lines)


def _dump_last_real_user(agent: "TauErgon") -> str:
    """Show the last REAL user prompt (skips REPL and synthetic messages)."""
    from agent_message_utils import is_real_user_request
    from agent_models import Colors
    # P7-V1: format_single_message is imported inside run(), which does NOT
    # leak into this function scope -> NameError at the call below. Import here.
    from agent_context_dump import format_single_message

    msgs = agent.context._messages
    for msg in reversed(msgs):
        if is_real_user_request(msg):
            return format_single_message(agent.context, msg, show_index=True)
    return f"{Colors.YELLOW}No real user messages in context.{Colors.RESET}"



def _dump_all_real_users(agent: "TauErgon") -> str:
    """Show ALL real user prompts, UNTRUNCATED, numbered by context index.

    Numbering is the absolute 1-based position in the full context list
    (matching /ctx short and format_single_message), so a printed [N] can be
    fed straight back to /ctx undo / /ctx last — not a per-filter running count.
    """
    from agent_message_utils import is_real_user_request
    from agent_models import Colors

    msgs = agent.context._messages
    real_users = [
        (i, m) for i, m in enumerate(msgs) if is_real_user_request(m)
    ]

    if not real_users:
        return f"{Colors.YELLOW}No real user messages in context.{Colors.RESET}"

    lines = [f"{Colors.CYAN}ALL REAL USER PROMPTS ({len(real_users)}):{Colors.RESET}"]
    for idx, msg in real_users:
        text = msg.get("content", "")
        if isinstance(text, list):
            text = " ".join(
                p.get("text", "") for p in text
                if isinstance(p, dict) and p.get("type") == "text"
            )
        # Strip [U:...] prefix (display nicety; not a truncation)
        if text.startswith("[U:"):
            try:
                end = text.index("]")
                text = text[end + 1:].lstrip()
            except ValueError:
                pass
        lines.append(f"{Colors.WHITE}[{idx + 1}] {text}{Colors.RESET}")

    return "\n".join(lines)


def _dump_all_eot_assistants(agent: "TauErgon") -> str:
    """Show ALL EOT assistant answers, UNTRUNCATED, numbered by context index.

    An EOT assistant message is the last message in context, or one
    immediately followed by a [U:real] user message. Numbering is the absolute
    1-based position in the full context list (matching /ctx short and
    format_single_message), so a printed [N] maps to the real message index.
    """
    from agent_message_utils import is_real_user_request
    from agent_models import Colors

    msgs = agent.context._messages
    eot_indices = []

    for i, msg in enumerate(msgs):
        if msg.get("role") != "assistant":
            continue
        # EOT if last message OR followed by a real user message
        if i == len(msgs) - 1:
            eot_indices.append(i)
        elif i + 1 < len(msgs) and is_real_user_request(msgs[i + 1]):
            eot_indices.append(i)

    if not eot_indices:
        return f"{Colors.YELLOW}No EOT assistant messages in context.{Colors.RESET}"

    lines = [f"{Colors.CYAN}ALL EOT ASSISTANT ANSWERS ({len(eot_indices)}):{Colors.RESET}"]
    for idx in eot_indices:
        text = msgs[idx].get("content", "")
        if isinstance(text, list):
            text = " ".join(
                p.get("text", "") for p in text
                if isinstance(p, dict) and p.get("type") == "text"
            )
        lines.append(f"{Colors.GREEN}[{idx + 1}] {text}{Colors.RESET}")

    return "\n".join(lines)


def _ctx_undo(agent: "TauErgon", n: int = 1) -> str:
    """Undo last n user turns by truncating context.

    Finds the n-th-to-last real user message and removes everything after it.
    REPL, meta, inject, and system messages are not counted as turns.

    Args:
        agent: The current TauErgon agent instance.
        n: Number of turns to undo (default 1).

    Returns:
        Status message.
    """
    from agent_message_utils import is_real_user_request
    from agent_models import Colors

    msgs = agent.context._messages

    # Find real user messages (exclude REPL, meta, inject, system)
    user_indices = [
        i for i, m in enumerate(msgs)
        if is_real_user_request(m)
    ]

    if not user_indices:
        return f"{Colors.YELLOW}No user turns found. Nothing to undo.{Colors.RESET}"

    if n >= len(user_indices):
        # Undo all turns — keep only system message (if present)
        target = 1 if msgs and msgs[0].get("role") == "system" else 0
    else:
        # Truncate at the n-th-to-last user message (the one we're removing)
        target = user_indices[-n]

    removed = len(msgs) - target
    agent.context._messages = msgs[:target]

    return (
        f"{Colors.GREEN}Undid {n} turn(s). "
        f"Removed {removed} message(s). "
        f"Context now has {len(agent.context._messages)} message(s).{Colors.RESET}"
    )


def _ctx_compress(agent: "TauErgon", target_pct: int = 30) -> str:
    """Compress context to target percentage of max.

    Args:
        agent: The current TauErgon agent instance.
        target_pct: Target percentage of max context (default 30).

    Returns:
        Status message.
    """
    from agent_models import Colors

    max_tokens = agent.max_context_tokens
    exact_tokens = getattr(agent._session, 'last_exact_context_tokens', None)
    _, current_pct, _, _ = agent.context.get_usage_stats(max_tokens, exact_tokens)
    
    # current_pct is a fraction (0.0-1.0), target_pct is 0-100
    if current_pct <= target_pct / 100.0:
        return f"{Colors.YELLOW}Already at {current_pct*100:.1f}% (target {target_pct}%). No compression needed.{Colors.RESET}"

    from agent_context_compress import compress_to_target
    result = compress_to_target(agent.context, agent, target_pct)
    if result:
        # Re-fetch the exact token count after compression — the pre-compression
        # value is stale. If it hasn't changed (compression didn't produce a new
        # API reading), fall back to estimation from the current messages.
        new_exact_tokens = getattr(agent._session, 'last_exact_context_tokens', None)
        if new_exact_tokens == exact_tokens:
            new_exact_tokens = None
        _, new_pct, _, _ = agent.context.get_usage_stats(max_tokens, new_exact_tokens)
        return (
            f"{Colors.GREEN}Compressed from {current_pct*100:.1f}% to {new_pct*100:.1f}% "
            f"of max context.{Colors.RESET}"
        )
    return f"{Colors.RED}Compression failed.{Colors.RESET}"


def _ctx_sum(agent: "TauErgon") -> str:
    """Display accumulated turn summaries from message metadata."""
    from agent_models import Colors

    msgs = agent.context._messages
    summaries = []
    for i, msg in enumerate(msgs):
        if msg.get("role") == "assistant" and "summary" in msg:
            summary = msg["summary"]
            if isinstance(summary, str) and summary.strip():
                # Find preceding user message (anchor)
                user_prompt = None
                for j in range(i - 1, -1, -1):
                    if msgs[j].get("role") == "user":
                        user_prompt = msgs[j].get("content", "")
                        if isinstance(user_prompt, list):
                            text_parts = [p.get("text", "") for p in user_prompt if p.get("type") == "text"]
                            user_prompt = " ".join(text_parts)
                        break
                summaries.append((i + 1, str(user_prompt or ""), summary.strip()))

    if not summaries:
        return (
            f"{Colors.YELLOW}No accumulated turn summaries found.{Colors.RESET}\n"
            f"  Turn summaries are generated automatically when enabled.\n"
            f"  See task 009 (turn_summary) for configuration."
        )

    lines = [f"\n{Colors.CYAN}ACCUMULATED TURN SUMMARIES ({len(summaries)} turns){Colors.RESET}"]
    for idx, user_prompt, summary in summaries:
        lines.append(f"\n{Colors.CYAN}--- Turn {idx} ---{Colors.RESET}")
        # User prompt in white
        lines.append(f"{Colors.WHITE}{user_prompt}{Colors.RESET}")
        # Summary in green (assistant)
        lines.append(f"{Colors.GREEN}{summary}{Colors.RESET}")

    return "\n".join(lines)


def _ctx_last_sum(agent: "TauErgon") -> str:
    """Display just the last turn summary from message metadata."""
    from agent_models import Colors

    msgs = agent.context._messages
    for msg in reversed(msgs):
        if msg.get("role") == "assistant" and "summary" in msg:
            summary = msg["summary"]
            if isinstance(summary, str) and summary.strip():
                try:
                    idx = msgs.index(msg) + 1
                    total = len(msgs)
                    header = f"{Colors.CYAN}--- Last Turn Summary (msg {idx}/{total}){Colors.RESET}"
                except ValueError:
                    header = f"{Colors.CYAN}--- Last Turn Summary{Colors.RESET}"
                return f"{header}\n{Colors.GREEN}{summary.strip()}{Colors.RESET}"

    return (
        f"{Colors.YELLOW}No turn summaries found.{Colors.RESET}\n"
        f"  Turn summaries are generated automatically when enabled."
    )



def _ctx_syspromptupdate(agent: "TauErgon") -> str:
    """Re-read AGENT_RLM.md and update the system prompt in context."""
    from agent_subsystems import read_system_prompt
    from agent_console.primitives import display_info

    _repl_cfg = getattr(getattr(agent.config, "rlm", None), "repl", None)
    _fs = getattr(_repl_cfg, "fence_style", "std") if _repl_cfg else "std"
    fresh_prompt = read_system_prompt(fence_style=_fs)
    agent.context.update_system(fresh_prompt)
    display_info("System prompt updated from AGENT_RLM.md")
    return ""
