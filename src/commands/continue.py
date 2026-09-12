"""Continue command — /continue

Loads a previous context file and restores the conversation.

Usage:
    /continue              — Load most recent context
    /continue help         — Show this help
    /continue list         — Show last 20 contexts
    /continue list 100     — Show last 100 contexts
    /continue <n>          — Load the n-th context (1 = newest)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List


if TYPE_CHECKING:
    from agent_core import TauErgon


def run(agent: "TauErgon", args: List[str] | None = None) -> str:
    """Run the /continue command.

    Args:
        agent: The current TauErgon agent instance.
        args: Command arguments list.

    Returns:
        Status message or empty string (output displayed directly).
    """
    args = args or []
    parts = [a.strip() for a in args if a.strip()]

    # /continue (no args) — load most recent
    if not parts:
        return _continue_latest(agent)

    # /continue help
    if parts[0].lower() == "help":
        return (
            "CONTINUE COMMANDS\n"
            "  /continue              — Load most recent context\n"
            "  /continue help         — Show this help\n"
            "  /continue list         — Show last 20 contexts\n"
            "  /continue list <n>     — Show last n contexts\n"
            "  /continue <n>          — Load the n-th context (1 = newest)\n"
        )

    # /continue list [n]
    if parts[0].lower() == "list":
        limit = 20
        if len(parts) > 1:
            try:
                limit = int(parts[1])
                if limit < 1:
                    return "Limit must be >= 1"
            except ValueError:
                return f"Invalid limit: {parts[1]}"
        return _continue_list(agent, limit)

    # /continue <n>
    try:
        n = int(parts[0])
    except ValueError:
        return f"Unknown subcommand: \'{parts[0]}\'. Use /continue help."

    if n < 1:
        return "Number must be >= 1"

    return _continue_by_number(agent, n)


def _get_context_list(agent: "TauErgon", *, refresh: bool = False) -> List[dict]:
    """Get available context files with metadata, newest-first.

    Consumes the SAME cached snapshot as ``_get_valid_context_files`` so the
    numbers shown by ``/continue list`` always match what ``/continue <n>``
    loads. ``refresh=True`` rebuilds the snapshot first.
    """
    from agent_context_utils import read_context_metadata

    ctx_files = _get_valid_context_files(agent, refresh=refresh)

    results: List[dict] = []
    for ctx_file in ctx_files:
        try:
            size = ctx_file.stat().st_size
        except OSError:
            size = 0

        # Get message count
        msg_count = 0
        try:
            msg_count, _ = read_context_metadata(ctx_file)
        except Exception:
            pass

        # Get timestamp from filename or mtime
        timestamp = _parse_timestamp(ctx_file)

        # Get last real user message
        last_msg = _last_user_message(ctx_file)

        results.append({
            "number": len(results) + 1,
            "file": ctx_file,
            "time": timestamp,
            "msgs": msg_count,
            "size": size,
            "last_msg": last_msg,
        })

    return results




def _get_valid_context_files(agent: "TauErgon", *, refresh: bool = False) -> List[Path]:
    """Get valid, loadable context file paths, newest first.

    The result is cached on the agent so repeated ``/continue <n>`` calls
    resolve to the SAME file. Without caching, ``_continue_by_number`` mutates
    ``agent._session.context_file`` after each load, and the "exclude current
    session" filter below would then drop the just-loaded file from the list —
    shifting every index >= it by one, so a second ``/continue 8`` silently
    loaded a *different* context (the reported flakiness).

    The excluded "current" file is pinned to the session's startup context on
    first build (``_continue_exclude_ctx``) for the same reason: loading a file
    must not change what counts as "current" for indexing.

    Pass ``refresh=True`` (used by ``/continue list``) to rebuild the snapshot
    after new context files may have appeared.

    Files that hold no messages (e.g. 2-byte ``[]`` placeholders, or malformed
    JSON) are excluded — they load 0 messages and would otherwise make
    ``/continue <n>`` land on an empty context.
    """
    # Pin the excluded "current" file to the startup context the first time.
    exclude = getattr(agent, "_continue_exclude_ctx", None)
    if exclude is None:
        exclude = getattr(agent._session, "context_file", None)
        agent._continue_exclude_ctx = exclude

    cached = getattr(agent, "_continue_files_cache", None)
    if cached is not None and not refresh and not _continue_cache_is_stale(agent, cached, exclude):
        return cached

    from agent_context_utils import get_all_context_files, read_context_metadata

    # Compare on resolved paths: get_all_context_files() dedups on resolve(),
    # so a symlinked or relative startup context would not match a plain str()
    # comparison and would stay in the list - shifting every index >= it, which
    # is exactly the "/continue <n> loads the wrong file" flakiness.
    def _is_current(ctx_file: Path) -> bool:
        if not exclude:
            return False
        try:
            return Path(ctx_file).resolve() == Path(exclude).resolve()
        except OSError:
            return str(ctx_file) == str(exclude)

    results: List[Path] = []
    for ctx_file in get_all_context_files():
        if _is_current(ctx_file):
            continue
        if not ctx_file.exists():
            continue
        try:
            size = ctx_file.stat().st_size
        except OSError:
            continue
        # Cheap skip for empty placeholders ([] / {}), then a real count check.
        if size <= 2:
            continue
        try:
            msg_count, _ = read_context_metadata(ctx_file)
        except Exception:
            msg_count = 0
        if msg_count == 0:
            continue
        results.append(ctx_file)

    agent._continue_files_cache = results
    agent._continue_files_cache_ts = time.time()
    return results


def _continue_cache_is_stale(agent: "TauErgon", cached: List[Path], exclude) -> bool:
    """True when a context file appeared/changed after the snapshot was built.

    Without this the snapshot is permanent: a session that writes new contexts
    (or a /continue list that refreshes) is the only way to see them, so
    ``/continue <n>`` keeps indexing a stale list. The CURRENT session's own
    context file is ignored - it is rewritten every turn, so honouring its
    mtime would invalidate the snapshot constantly and re-introduce the index
    drift the cache exists to prevent.
    """
    stamp = float(getattr(agent, "_continue_files_cache_ts", 0.0) or 0.0)
    if not stamp:
        return True

    def _skip(path) -> bool:
        if exclude is None:
            return False
        try:
            return Path(path).resolve() == Path(exclude).resolve()
        except OSError:
            return str(path) == str(exclude)

    probe: List[Path] = [f for f in cached if not _skip(f)]
    try:
        from agent_session import LOG_DIR
        probe += [f for f in LOG_DIR.glob("*.context") if not _skip(f)]
    except OSError:
        pass
    for f in probe:
        try:
            if f.stat().st_mtime > stamp:
                return True
        except OSError:
            continue
    return False
def _parse_timestamp(ctx_file: Path) -> str:
    """Parse timestamp from context filename or use mtime.

    Filename format: PID_YYYYMMDDHHMMSS_N.context
    """
    stem = ctx_file.stem  # e.g., "70269_20260817220339_1"
    parts = stem.split("_")
    if len(parts) >= 2:
        ts_str = parts[1]
        if len(ts_str) == 14 and ts_str.isdigit():
            try:
                dt = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                return dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
    # Fallback to mtime
    mtime = ctx_file.stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _last_user_message(ctx_file: Path) -> str:
    """Extract last real user message from context file.
    Reads file JSON to find last real user message. Skips files > 1MB.
    """
    try:
        file_size = ctx_file.stat().st_size
        if file_size > 1_000_000:  # Skip files > 1MB for performance
            return "-"

        with open(ctx_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Handle both formats
        if isinstance(data, dict) and "messages" in data:
            messages = data["messages"]
        elif isinstance(data, list):
            messages = data
        else:
            return "-"

        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(text_parts)
            if not isinstance(content, str) or not content.strip():
                continue
            # Check prefix for real user message: [U:real | ...]
            if not content.startswith("[U:real"):
                continue
            # Strip the [U:real | N:... | M:... | C:...] prefix
            close_idx = content.find("] ")
            if close_idx != -1:
                text = content[close_idx + 2:].strip()
            else:
                text = content.strip()
            text = text.replace("\n", " ")
            if len(text) > 40:
                text = text[:37] + "..."
            return text
        return "-"
    except Exception:
        return "-"


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes // 1024}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def _continue_latest(agent: "TauErgon") -> str:
    """Load the most recent context file (current behavior)."""
    from agent_console import context_restored

    ctx_list = _get_valid_context_files(agent)
    if not ctx_list:
        return "No previous context file found (excluding current session)."

    ctx_file = ctx_list[0]
    agent._session.context_file = ctx_file
    if agent.context.load_from_file(ctx_file):
        loaded = len(agent.context)
        context_restored(loaded, ctx_file)
        return f"Context restored: {loaded} messages from {ctx_file.name}"
    return "Failed to load context file: " + str(ctx_file.name)


def _continue_list(agent: "TauErgon", limit: int) -> str:
    """Display a table of available contexts."""
    from agent_console.primitives import display_info

    ctx_list = _get_context_list(agent, refresh=True)
    if not ctx_list:
        display_info("No available context files.")
        return ""

    shown = ctx_list[:limit]
    total = len(ctx_list)

    lines = []
    lines.append(f"AVAILABLE CONTEXTS (showing {len(shown)} of {total})")
    lines.append("    #   Time              Msgs   Size     Last user message")
    lines.append("    ---   ----------------- ----- -------  ----------------------------------------")

    for item in shown:
        last_msg = item["last_msg"]
        lines.append(
            f"  {item['number']:>3}  {item['time']:<17} "
            f"{item['msgs']:>5} {_format_size(item['size']):>7}  \"{last_msg}\""
        )

    lines.append("")
    lines.append(f"  Use /continue <n> to load. /continue list 100 for more.")

    display_info("\n".join(lines))
    return ""


def _continue_by_number(agent: "TauErgon", n: int) -> str:
    """Load the n-th context file (stable across repeated calls)."""
    from agent_console import context_restored

    ctx_list = _get_valid_context_files(agent)
    if not ctx_list:
        return "No previous context file found (excluding current session)."

    if n > len(ctx_list):
        return f"Only {len(ctx_list)} context(s) available. Use /continue list to see them."

    ctx_file = ctx_list[n - 1]

    agent._session.context_file = ctx_file
    if agent.context.load_from_file(ctx_file):
        loaded = len(agent.context)
        context_restored(loaded, ctx_file)
        return f"Context restored: {loaded} messages from {ctx_file.name} (#{n})"
    return f"Failed to load context file: {ctx_file.name}"
