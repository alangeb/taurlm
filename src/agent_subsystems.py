"""Subsystem initialization for TauErgon.

Encapsulates the creation and wiring of all agent subsystems:
session manager, loop detector, loop escalation manager,
EOT protection, heartbeat manager, and input handler.

This module reduces the import burden on agent_core.py and makes
subsystem initialization testable in isolation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_console.templates import register_console_messages
from agent_session import AgentSessionManager

if TYPE_CHECKING:
    from agent_core import TauErgon


@dataclass
class SubsystemBundle:
    """Bundle of initialized subsystems returned by init_subsystems().

    Contains ONLY the subsystem instances that TauErgon needs after
    initialization. State variables and None placeholders are NOT included
    — they are initialized directly in _init_subsystems() as assignments.
    """
    # Session management
    session: AgentSessionManager

    # Loop detection and escalation (None in RLM mode)
    loop_detector: Any = None
    loop_escalation: Any = None

    # EOT protection (None in RLM mode)

    # Heartbeat (idle detection) — None in RLM mode
    heartbeat: Any = None

    # Commands / tools


def init_subsystems(
    agent: TauErgon,
    init: "AgentInitConfig",
) -> SubsystemBundle:
    """Initialize all agent subsystems and return a bundle.

    This function encapsulates the creation and wiring of all subsystems
    that TauErgon needs. It is called once from TauErgon.__init__() after
    config resolution.

    RLM mode is the ONLY mode — the agent uses a persistent Python REPL
    as its primary interface.

    Args:
        agent: The TauErgon instance being initialized.
        init: The resolved AgentInitConfig with all settings.

    Returns:
        SubsystemBundle containing only the subsystem instances.
        State variables and None placeholders are NOT included — they are
        initialized directly in _init_subsystems() as assignments.
    """
    # M-C3: Collect subsystem init errors
    _init_errors: list[str] = []

    # Session management (token tracking, context/audit file paths)
    try:
        session = AgentSessionManager()
        session.init_audit_writer()
    except Exception as e:
        _init_errors.append(f"session: {e}")
        session = None

    # Register console message callbacks (audit bridge wiring)
    try:
        register_console_messages()
    except Exception as e:
        _init_errors.append(f"console: {e}")

    # ── RLM mode: initialize subsystems ──

    # Loop detection (sliding-window pattern matching)
    try:
        from agent_loop_detect import LoopDetector
        loop_detector = LoopDetector(
            window_size=init.loop_detection_window_size,
            repeat_threshold=init.loop_detection_repeat_threshold,
        )
    except ImportError:
        loop_detector = None
    except Exception as e:
        _init_errors.append(f"loop_detector: {e}")
        loop_detector = None

    # Heartbeat (idle detection)
    try:
        from agent_heartbeat import HeartbeatManager
        heartbeat = HeartbeatManager(
            enabled=init.heartbeat_enabled,
            interval_seconds=init.heartbeat_interval,
            agent=agent,
        )
    except Exception as e:
        _init_errors.append(f"heartbeat: {e}")
        heartbeat = None

    if _init_errors:
        raise RuntimeError(f"Subsystem initialization failed: {'; '.join(_init_errors)}")

    return SubsystemBundle(
        session=session,
        loop_detector=loop_detector,
        loop_escalation=None,
        heartbeat=heartbeat,
        
    )


def read_system_prompt(fence_style: str = "std") -> str:
    """Read and format the system prompt from AGENT_RLM.md.

    Applies dynamic fence-style replacements to instruction lines
    based on the configured fence_style.

    Returns:
        The formatted system prompt string.
    """
    from agent_core import _safe_format_template
    from agent_session import LOG_DIR
    from agent_session import SESSION_PREFIX as _SP  # function-local import: reads CURRENT value, not import-time binding
    from agent_session import _peek_log_filename_prefix
    from agent_repl_parse import fence_tokens

    # NEVER claim a prefix here — this function is also called outside a real
    # session (tests, /ctx syspromptupdate) and must not leak stray 0-byte
    # {prefix}.context files. Prefer the live session prefix if one exists;
    # otherwise peek a display-only prefix (creates nothing).
    prefix = _SP
    if prefix is None:
        prefix = _peek_log_filename_prefix()
    audit_file = Path(LOG_DIR) / f"{prefix}.audit"
    context_file = Path(LOG_DIR) / f"{prefix}.context"

    agent_path = Path(__file__).resolve().parent / "AGENT_RLM.md"
    system_prompt = "You are helpful AI assistant. Do what User asks."
    if agent_path.exists():
        try:
            raw = agent_path.read_text().strip()
            system_prompt = _safe_format_template(
                raw,
                log_file=str(audit_file),
                audit_file=str(audit_file),
                context_file=str(context_file),
            )
            # Dynamic fence-style normalization (all sections except reference table)
            from agent_repl_parse import normalize_fences
            system_prompt = normalize_fences(
                system_prompt, fence_style,
                skip_sections=["Code Fence Rules"],
            )
        except OSError as exc:
            from agent_console import warning as _w; _w(f"WARNING: Could not read {agent_path.name}: {exc}", )

    # Append the standing skill menu so the model always sees available skills
    try:
        from rlm.skills import SkillLoader
        _skills_dir = None
        for _cand in [
            Path("skills"),
            Path(__file__).resolve().parent / "skills",
            Path(__file__).resolve().parent.parent / "skills",
        ]:
            if _cand.exists():
                _skills_dir = str(_cand)
                break
        if _skills_dir:
            _loader = SkillLoader(_skills_dir, fence_style=fence_style)
            _menu = _loader.render_skill_menu()
            if _menu:
                system_prompt = system_prompt + _menu
    except Exception:
        pass  # Skills are optional; never break the prompt

    return system_prompt


__all__ = [
    "SubsystemBundle",
    "init_subsystems",
    "read_system_prompt",
]