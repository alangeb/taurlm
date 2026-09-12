"""RLM Kernel Wiring — REPL kernel initialization and namespace injection.

This module provides functions to initialize and wire the REPL kernel
namespace with the required functions (spawn, host_request, agent_message,
skills). It is used by agent_core.py to set up the kernel.

Key Functions
-------------
init_repl_kernel(agent) — Initialize REPL kernel and inject all namespace functions
verify_kernel_namespace(kernel) — Verify all critical namespace functions are callable
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_core import TauErgon

__all__ = [
    "init_repl_kernel",
    "verify_kernel_namespace",
]


def init_repl_kernel(agent: "TauErgon") -> None:
    """Initialize the RLM REPL kernel and supporting subsystems.

    Creates and configures:
    - AnswerManager: Tracks answer["content"] and answer["ready"] state
    - PythonKernel: Persistent REPL with state across turns

    The kernel namespace is pre-populated with:
    - answer dict (linked to AnswerManager)
    - spawn() function (for spawning child agents)
    - host_request() function (for host bridge requests)
    - agent_message (for inter-agent communication)
    - available_skills(), load_skill(), and find_skills() (for skill management)
    - Full Python standard library access
    - All installed packages

    Args:
        agent: TauErgon instance to initialize kernel for.
    """
    from rlm.answer import AnswerManager
    from rlm.kernel import PythonKernel

    agent._repl_kernel = None  # type: ignore[attr-defined]
    agent._repl_answer = None  # type: ignore[attr-defined]
    agent._repl_turn_count = 0  # type: ignore[attr-defined]

    # Initialize answer manager
    agent._repl_answer = AnswerManager()  # type: ignore[attr-defined]

    # Use config if available, otherwise use defaults
    rlm_config = getattr(agent.config, "rlm", None) if agent.config else None
    repl_config = getattr(rlm_config, "repl", None) if rlm_config else None


    if repl_config:
        agent._repl_kernel = PythonKernel(  # type: ignore[attr-defined]
            max_output_chars=getattr(repl_config, "max_output_chars", 8192),
            max_state_size_mb=getattr(repl_config, "max_state_size_mb", 100.0),
            bash_timeout_seconds=getattr(repl_config, "bash_timeout_seconds", 180.0),
            python_timeout_seconds=getattr(repl_config, "python_timeout_seconds", 180.0),
            rlm_config=rlm_config,
            agent=agent,
        )
    else:
        agent._repl_kernel = PythonKernel(  # type: ignore[attr-defined]
            max_output_chars=8192,
            max_state_size_mb=100.0,
            bash_timeout_seconds=180.0,
            python_timeout_seconds=180.0,
            rlm_config=rlm_config,
            agent=agent,
        )

    # Inject answer manager into kernel namespace
    if agent._repl_kernel and agent._repl_answer:
        agent._repl_kernel.namespace["answer"] = agent._repl_answer.get_dict()

    # Inject additional namespace functions
    if agent._repl_kernel:
        _inject_host_bridge(agent)
        _inject_skills(agent)
        _inject_wiki(agent)
        _inject_vision(agent)
        verify_kernel_namespace(agent._repl_kernel)


def _inject_host_bridge(agent: "TauErgon") -> None:
    """Inject host_request into kernel namespace."""
    kernel = getattr(agent, "_repl_kernel", None)
    if kernel is None:
        return
    try:
        from rlm.host_bridge import HostBridge
        host_bridge = HostBridge(getattr(agent.config, "rlm", None), agent=agent)
        kernel.namespace["host_request"] = host_bridge.request
    except (ImportError, TypeError) as e:
        logging.warning("host_request injection failed: %s — kernel stub remains", e)


def _inject_skills(agent: "TauErgon") -> None:
    """Inject skills functions into kernel namespace."""
    kernel = getattr(agent, "_repl_kernel", None)
    if kernel is None:
        return
    try:
        from rlm.skills import SkillLoader
        skills_dir = None
        for candidate in [
            Path("skills"),
            Path(__file__).resolve().parent / "skills",
            Path(__file__).resolve().parent.parent / "skills",
        ]:
            if candidate.exists():
                skills_dir = str(candidate)
                break
        _repl_cfg = getattr(getattr(agent.config, "rlm", None), "repl", None)
        _fs = getattr(_repl_cfg, "fence_style", "std") if _repl_cfg else "std"
        loader = SkillLoader(skills_dir, fence_style=_fs)
        kernel.namespace.update(loader.get_initial_namespace())
        # Expose loader on the agent so the per-turn nudge can reuse it
        # (shares the discovery cache + _loaded_names dedup set).
        agent._skill_loader = loader
    except (ImportError, TypeError) as e:
        logging.warning("skills injection failed: %s — kernel stubs remain", e)



def _inject_wiki(agent: "TauErgon") -> None:
    """Inject wiki module into kernel namespace."""
    kernel = getattr(agent, "_repl_kernel", None)
    if kernel is None:
        return
    try:
        import wiki
        kernel.namespace["wiki"] = wiki
    except ImportError as e:
        logging.warning("wiki injection failed: %s", e)


def _inject_vision(agent: "TauErgon") -> None:
    """Inject view_image into kernel namespace."""
    kernel = getattr(agent, "_repl_kernel", None)
    if kernel is None:
        return
    try:
        from rlm.vision import make_view_image
        kernel.namespace["view_image"] = make_view_image(agent)
    except (ImportError, TypeError) as e:
        logging.warning("vision injection failed: %s", e)


def verify_kernel_namespace(kernel: Any) -> None:
    """Verify that all critical namespace functions are callable.

    Raises RuntimeError if any required function is missing or not callable.

    Args:
        kernel: PythonKernel instance to verify.
    """
    if kernel is None:
        raise RuntimeError("REPL kernel is not initialized")

    callables = ["spawn", "host_request", "available_skills", "load_skill", "find_skills", "list_spawns", "get_spawn"]
    objects_with_methods = {}

    missing = []
    not_callable = []
    missing_methods = []

    for name in callables:
        value = kernel.namespace.get(name)
        if value is None:
            missing.append(name)
        elif not callable(value):
            not_callable.append(name)

    for name, required_methods in objects_with_methods.items():
        value = kernel.namespace.get(name)
        if value is None:
            missing.append(name)
        else:
            for method in required_methods:
                if not hasattr(value, method) or not callable(getattr(value, method)):
                    missing_methods.append(f"{name}.{method}")

    errors = []
    if missing:
        errors.append(f"missing: {', '.join(missing)}")
    if not_callable:
        errors.append(f"not callable: {', '.join(not_callable)}")
    if missing_methods:
        errors.append(f"missing methods: {', '.join(missing_methods)}")

    if errors:
        raise RuntimeError(
            f"Kernel namespace verification failed ({'; '.join(errors)}). "
            "Check that rlm_config and agent are passed to PythonKernel."
        )
