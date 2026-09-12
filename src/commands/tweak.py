"""Tweak Command - /tweak. Runtime generation parameter overrides.

Usage:
    /tweak temperature=1.0              -- Set override
    /tweak temperature=1.0 top_p=0.9    -- Set multiple
    /tweak temperature=                 -- Delete override (revert to config)
    /tweak chat_template_kwargs.enable_thinking=false  -- Set nested param
    /tweak                              -- Show active overrides
    /tweak help                         -- Show usage
"""

from __future__ import annotations

from typing import Any

from agent_console.primitives import display_info, display_warning


def _parse_value(s: str) -> Any:
    """Parse a string into int, float, bool, None, or string.

    Empty string or "null"/"none" (case-insensitive) -> None (delete).
    "true"/"false" (case-insensitive) -> bool.
    Numeric strings -> int or float.
    Otherwise -> string.
    """
    if s == "" or s.lower() in ("null", "none"):
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    # Try int
    try:
        return int(s)
    except ValueError:
        pass
    # Try float
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _apply(agent, key: str, value: Any) -> str:
    """Set or delete one override.

    If value is None, delete the key (revert to config).
    If key starts with "chat_template_kwargs.", route into nested dict.
    Returns a status message string.
    """
    if key.startswith("chat_template_kwargs."):
        sub_key = key[len("chat_template_kwargs."):]
        if value is None:
            # Delete nested key
            kwargs = agent._gen_overrides.get("chat_template_kwargs", {})
            if sub_key in kwargs:
                del kwargs[sub_key]
                if not kwargs:
                    agent._gen_overrides.pop("chat_template_kwargs", None)
                return f"Deleted chat_template_kwargs.{sub_key}"
            return f"chat_template_kwargs.{sub_key} was not set"
        # Set nested key
        if "chat_template_kwargs" not in agent._gen_overrides:
            agent._gen_overrides["chat_template_kwargs"] = {}
        agent._gen_overrides["chat_template_kwargs"][sub_key] = value
        return f"Set chat_template_kwargs.{sub_key}={value!r}"

    if value is None:
        if key in agent._gen_overrides:
            del agent._gen_overrides[key]
            return f"Deleted {key}"
        return f"{key} was not set"

    agent._gen_overrides[key] = value
    return f"Set {key}={value!r}"


def run(agent, args: list[str]) -> None:
    """Main entry point for /tweak."""
    if not args:
        # Show active overrides
        if not agent._gen_overrides:
            display_info("No active overrides.")
            return
        display_info("Active overrides:")
        for k, v in agent._gen_overrides.items():
            if isinstance(v, dict):
                for sk, sv in v.items():
                    display_info(f"  {k}.{sk} = {sv!r}")
            else:
                display_info(f"  {k} = {v!r}")
        return

    if args[0] == "help":
        display_info(__doc__ or "Usage: /tweak [key=value ...]")
        return

    # Parse key=value pairs
    for arg in args:
        if "=" not in arg:
            display_warning(f"Invalid argument (expected key=value): {arg!r}")
            continue
        key, _, raw_val = arg.partition("=")
        key = key.strip()
        if not key:
            display_warning(f"Empty key in: {arg!r}")
            continue
        value = _parse_value(raw_val)
        msg = _apply(agent, key, value)
        display_info(msg)
