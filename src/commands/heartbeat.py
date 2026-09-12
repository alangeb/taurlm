"""Heartbeat Command — /heartbeat

Re-entry scheduling — configure periodic heartbeat or view status.

Usage:
    /heartbeat              — View current heartbeat status
    /heartbeat help         — Show usage help
    /heartbeat prompt       — Display the heartbeat prompt
    /heartbeat prompt <text> — Set a new heartbeat prompt
    /heartbeat interval     — Display the current interval
    /heartbeat interval <s> — Set the interval in seconds
    /heartbeat on           — Enable heartbeat
    /heartbeat off          — Disable heartbeat
"""

from pathlib import Path

__all__ = ['run']

# Path to heartbeat.md command file
_COMMANDS_DIR = Path(__file__).parent
_HEARTBEAT_MD = _COMMANDS_DIR / "heartbeat.md"


def _get_agent_heartbeat(agent):
    """Get the HeartbeatManager from the agent."""
    return getattr(agent, '_heartbeat', None)


def _display_status(agent) -> str:
    """Display current heartbeat status (always shows config regardless of ON/OFF)."""
    hb = _get_agent_heartbeat(agent)
    if hb is None:
        return "Heartbeat: not initialized"

    state = "ON" if hb.enabled else "OFF"
    interval = hb.interval_seconds if hb.interval_seconds is not None else 300

    # Load prompt preview (first 100 chars, single line)
    prompt_preview = ""
    if _HEARTBEAT_MD.exists():
        try:
            pc = _HEARTBEAT_MD.read_text()
            if pc.startswith("---"):
                parts = pc.split("---", 2)
                if len(parts) >= 3:
                    pc = parts[2]
            prompt_preview = " ".join(pc.split())[:100]
        except OSError:
            pass

    # User-supplied prompt preview (first 100 chars, single line)
    user_prompt_preview = "<none>"
    if _USER_PROMPT_FILE.exists():
        try:
            upc = _USER_PROMPT_FILE.read_text().strip()
            if upc:
                user_prompt_preview = " ".join(upc.split())[:100]
        except OSError:
            pass

    lines = (
        f"Heartbeat: {state}\n"
        f"Interval: {interval}s\n"
        f"Prompt: {prompt_preview if prompt_preview else '<none>'}\n"
        f"User prompt: {user_prompt_preview}"
    )
    return lines


def _display_help() -> str:
    """Display usage help."""
    return (
        "Usage:\n"
        "  /heartbeat              — View current status\n"
        "  /heartbeat help         — Show this help\n"
        "  /heartbeat prompt       — Display the heartbeat prompt\n"
        "  /heartbeat prompt <text> — Set a new heartbeat prompt\n"
        "  /heartbeat prompt clear — Clear prompt (use default)\n"
        "  /heartbeat interval     — Display the current interval\n"
        "  /heartbeat interval <s> — Set the interval in seconds\n"
        "  /heartbeat on           — Enable heartbeat\n"
        "  /heartbeat off          — Disable heartbeat\n"
        "  /heartbeat test         — Trigger one heartbeat immediately"
    )


_USER_PROMPT_FILE = _COMMANDS_DIR / "heartbeat_user.txt"
_DEFAULT_USER_PROMPT = (
    "Review the current context and determine if there is a task "
    "you should continue or initiate."
)


def _display_prompt() -> str:
    """Display the current user heartbeat prompt."""
    if _USER_PROMPT_FILE.exists():
        text = _USER_PROMPT_FILE.read_text().strip()
        if text:
            return f"Heartbeat prompt (custom):\n{text}"
    return f"Heartbeat prompt (default):\n{_DEFAULT_USER_PROMPT}"


def _set_prompt(text: str) -> str:
    """Set a new user heartbeat prompt (stored in heartbeat_user.txt)."""
    _USER_PROMPT_FILE.write_text(text.strip() + "\n")
    return "Heartbeat prompt updated."


def _clear_prompt() -> str:
    """Clear the user heartbeat prompt (revert to default)."""
    if _USER_PROMPT_FILE.exists():
        _USER_PROMPT_FILE.unlink()
    return "Heartbeat prompt cleared (using default)."


def _run_test(agent) -> str:
    """Trigger exactly one heartbeat immediately, then restore state.

    Works regardless of current enabled/interval state.
    Uses the normal time-expired path: sets last_activity_time to the past
    so run_heartbeat() passes its idle check naturally.
    """
    import time
    hb = _get_agent_heartbeat(agent)
    if hb is None:
        return "Heartbeat: not initialized"

    # Save current state
    was_enabled = hb.enabled
    orig_interval = hb.interval_seconds

    # Force conditions for the idle check to pass
    hb.enabled = True
    if hb.interval_seconds is None:
        hb.interval_seconds = 300
    # Simulate expiry: set last activity to just past the interval
    hb.last_activity_time = time.time() - hb.interval_seconds - 1

    # Run the heartbeat (goes through the normal path)
    try:
        result = hb.run_heartbeat()
    except Exception as e:
        hb.enabled = was_enabled
        return f"Heartbeat test: ERROR - {type(e).__name__}: {e}"

    # Restore enabled state (interval stays as-is)
    hb.enabled = was_enabled

    if result is None:
        return "Heartbeat test: no response (LLM unavailable or prompt missing)"
    if result.action == "no_action":
        return f"Heartbeat test: OK (<NO_ACTION>) — no task injected"
    return f"Heartbeat test: OK (<PROMPT>) — task: {result.task}"


def run(agent, args: list[str] = None) -> str:
    """Execute /heartbeat command.

    Args:
        agent: The TauErgon agent instance.
        args: Command arguments (list with one string, or None).

    Returns:
        Command output string.
    """
    # Parse args: dispatch passes a list of words
    if args is None:
        args = []
    parts = args

    if not parts:
        return _display_status(agent)

    sub = parts[0].lower()

    if sub == "help":
        return _display_help()

    if sub == "prompt":
        if len(parts) > 1:
            if parts[1].lower() == "clear":
                return _clear_prompt()
            new_prompt = " ".join(parts[1:])
            return _set_prompt(new_prompt)
        else:
            return _display_prompt()

    if sub == "interval":
        hb = _get_agent_heartbeat(agent)
        if hb is None:
            return "Heartbeat: not initialized"

        if len(parts) > 1:
            # Set new interval
            try:
                interval = int(parts[1])
                if interval <= 0:
                    return "Interval must be positive"
                hb.interval_seconds = interval
                return f"Heartbeat interval set to {interval}s"
            except ValueError:
                return f"Invalid interval: {parts[1]}"
        else:
            # Display current interval
            if hb.interval_seconds is None:
                return "Heartbeat interval: not set"
            return f"Heartbeat interval: {hb.interval_seconds}s"

    if sub == "on":
        hb = _get_agent_heartbeat(agent)
        if hb is None:
            return "Heartbeat: not initialized"
        hb.enabled = True
        if hb.interval_seconds is None:
            hb.interval_seconds = 300  # Default 5 minutes
        return f"Heartbeat enabled ({hb.interval_seconds}s)"

    if sub == "off":
        hb = _get_agent_heartbeat(agent)
        if hb is None:
            return "Heartbeat: not initialized"
        hb.enabled = False
        return "Heartbeat disabled"

    if sub == "test":
        return _run_test(agent)

    return _display_help()
