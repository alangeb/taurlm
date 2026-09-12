"""Status display functions for TauErgon console.

Handles agent status, exit summaries, context status, and cache hit rates.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

from agent_console.primitives import (
    _cw,
    blank_line,
    compute_duration,
    display_info,
    display_success,
)

__all__ = [
    "agent_status",
    "exit_summary",
    "print_agent_exit_summary",
    "print_context_status",
    "build_cache_hit_rates_str",
]


# ── Status Display ────────────────────────────────────────────────────────────


def _cwd_for_display() -> str:
    """Return the process cwd as a display string, robust to a dangling cwd.

    A `%cd` block (rlm/kernel.py) does a process-wide os.chdir() that is never
    restored. If that directory is later deleted (cleanup, `rm -rf`, checkout),
    os.getcwd()/Path.cwd() raise FileNotFoundError [Errno 2], which would crash
    every subsequent status line. Display must never take down a turn, so fall
    back to $PWD, then '?'.
    """
    try:
        return str(Path.cwd())
    except OSError:
        return os.environ.get("PWD") or "?"


def agent_status(status: object) -> None:
    """Display comprehensive agent status information."""
    token_display = (
        f"{status.token_count:,}" if status.is_exact else f"~{status.token_count:,}"
    )

    display_info("AGENT STATUS")
    display_success(f"PID: {os.getpid()}")
    display_success(f"Parent PID: {os.getppid()}")
    if status.agent_name:
        display_success(f"Name: {status.agent_name}")
    display_success(f"Context: {status.context_len} msgs | {token_display} tokens ({status.byte_count:,} bytes)")
    display_success(f"Capacity: {status.percentage * 100:.1f}% ({status.max_context_tokens:,} max)")
    display_success(f"Session file: {status.context_file}")
    if status.audit_file:
        display_success(f"Audit file: {status.audit_file}")
    if status.current_group_name:
        groups = f" (available: {', '.join(status.llm_groups)})" if status.llm_groups else ""
        display_success(f"LLM group: {status.current_group_name}{groups}")
    model_src = f" [{status.model_source}]" if status.model_source else ""
    display_success(f"Model: {status.model_name}{model_src}")
    api_src = f" [{status.base_url_source}]" if status.base_url_source else ""
    display_success(f"API: {status.base_url}{api_src}")
    if status.gen_params:
        formatted = ", ".join(f"{k}={v}" for k, v in status.gen_params.items())
        display_success(f"Gen params: {formatted}")
    display_success(f"Working directory: {_cwd_for_display()}")
    if status.last_turn_in > 0:
        display_success(f"Last turn tokens: {status.last_turn_in:,} in + {status.last_turn_out:,} out + {status.last_turn_cached:,} cached")
    total = status.session_in + status.session_out
    total_bytes = status.session_in_bytes + status.session_out_bytes
    display_success(f"Session total: {status.session_in:,} in + {status.session_out:,} out + {status.session_cached:,} cached = {total:,} total")
    display_success(f"Session bytes: {status.session_in_bytes:,} in + {status.session_out_bytes:,} out + {status.session_cached_bytes:,} cached = {total_bytes:,} total")
    if status.has_cache_data:
        display_success(f"Cache hit rates: {build_cache_hit_rates_str(status)}")
    blank_line()


def exit_summary(status: object, duration: float) -> None:
    """Print a tidy exit summary with context metrics and session information."""
    from agent_models import Colors

    loop_stats = status.loop_stats or {}
    entropy = loop_stats.get("entropy", 0.0)
    history_size = loop_stats.get("history_size", 0)

    token_display = (
        f"{status.token_count:,}" if status.is_exact else f"~{status.token_count:,}"
    )

    cache_hit_rates = (
        build_cache_hit_rates_str(status) if status.has_cache_data else None
    )

    blank_line()
    _cw(Colors.INVERT_CYAN, "########## EXIT SUMMARY ##########")
    try:
        from agent_audit_bridge import console_info
        console_info("########## EXIT SUMMARY ##########")
    except Exception:
        pass
    display_info(f"Context: {status.context_len} msgs | {token_display} tokens ({status.byte_count:,} bytes)")
    display_info(f"Session file: {status.context_file}")
    if status.audit_file:
        display_info(f"Audit file: {status.audit_file}")
    total = status.session_in + status.session_out
    display_info(f"Token usage: {status.session_in:,} in + {status.session_out:,} out + {status.session_cached:,} cached = {total:,} total")
    if cache_hit_rates:
        display_info(f"Cache hit rates: {cache_hit_rates}")
    display_info("==================================")
    blank_line()


def print_agent_exit_summary(agent: object) -> None:
    """Print exit summary for an agent-like object."""
    duration = compute_duration(agent)
    exit_summary(getattr(agent, 'get_status', lambda: {})(), duration)


def print_context_status(status: object) -> None:
    """Print a single-line context status display."""
    from agent_models import Colors

    loop_stats = status.loop_stats or {}
    entropy = loop_stats.get("entropy", 0.0)
    history_size = loop_stats.get("history_size", 0)
    cwd = _cwd_for_display()
    try:
        cwd = "~/" + str(Path(cwd).relative_to(Path.home()))
    except (ValueError, RuntimeError):
        pass

    token_display = f"{status.token_count}" if status.is_exact else f"~{status.token_count}"

    cache_str = (
        build_cache_hit_rates_str(status)
        if status.has_cache_data
        else ""
    )

    timestamp = datetime.datetime.now().strftime("%y%m%d %H%M%S")
    current_pid = os.getpid()
    parent_pid = os.getppid()
    base_content = (
        f"ctx: {token_display} tk ({status.percentage:.1%}) {status.context_len} msgs"
        + (f" | cache: {cache_str}%" if cache_str else "")
        + (f" | {status.last_tg_tps:.1f} t/s" if status.last_tg_tps > 0 else "")
        + (f" | ttft: {status.last_ttft*1000:.0f}ms" if status.last_ttft > 0 else "")
        + f" | pid: {current_pid}({parent_pid}) | {timestamp} | entropy: {entropy:.2f} ({history_size}) | cwd: {cwd}"
    )

    if status.nesting_stack:
        base_content += f" | nest({status.spawns_count}): {status.nesting_stack}"
        if status.spawn_B > 0:
            base_content += f" | B:{status.spawn_B*100:.1f}%"
        color = Colors.INVERT_BLUE
    else:
        base_content += f" | nest({status.spawns_count}): ."
        if status.spawns_count > 0:
            base_content += f" | spawns: {status.spawns_count}"
        color = Colors.INVERT_CYAN

    base_content += f" | llmg: {status.current_group_name}"
    base_content += f" | name: {status.agent_name}"

    _cw(color, f"# {base_content}")
    try:
        from agent_audit_bridge import console_info
        console_info(f"# {base_content}")
    except Exception:
        pass


def build_cache_hit_rates_str(status: object) -> str:
    """Build a cache hit rate display string, handling None values safely."""
    parts: list[str] = []
    cum = status.cumulative_hit_rate
    slid = status.sliding_hit_rate
    last = status.last_hit_rate
    if cum is not None:
        parts.append(f"{int(cum * 100)}%")
    if slid is not None:
        parts.append(f"{int(slid * 100)}%")
    if last is not None:
        parts.append(str(int(last * 100)))
    return "/".join(parts) if parts else ""
