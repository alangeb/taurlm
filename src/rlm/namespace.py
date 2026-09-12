"""RLM Kernel Namespace Construction

Builds the function surface (capabilities) available to the LLM in the
persistent Python kernel. Separates the "what can the agent do" concern
from the "how does code execute" concern in PythonKernel.

The namespace includes:
- Standard library pre-imports
- answer dict (for setting final answers)
- spawn() / list_spawns() / get_spawn() (delegation)
- host_request() (host bridge)
- available_skills / find_skills / load_skill (skills system)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

__all__ = ["build_namespace"]


def build_namespace(agent: Any, rlm_config: Any) -> dict[str, Any]:
    """Build the initial kernel namespace with all capabilities.

    Args:
        agent: The parent TauErgon agent instance (or None for standalone).
        rlm_config: RLM configuration object (may be None).

    Returns:
        Initial namespace dict ready for exec().
    """
    # Trust model: allow all builtins — RLM operates on trust, no sandbox
    namespace: dict[str, Any] = {"__builtins__": __builtins__}

    # Pre-load common imports
    namespace["Path"] = Path
    namespace["__pathlib__"] = Path

    # Pre-import common modules
    for mod_name in (
        "json", "os", "sys", "re", "math", "time", "collections",
        "itertools", "functools", "dataclasses", "typing", "io",
        "tempfile", "shutil", "glob", "subprocess",
    ):
        namespace[mod_name] = __import__(mod_name)

    # Initialize answer dict
    namespace["answer"] = {"content": "", "ready": False}

    # Delegation: spawn / list_spawns / get_spawn
    namespace["spawn"] = _make_spawn_callable(agent)
    namespace["list_spawns"] = _make_list_spawns(agent)
    namespace["get_spawn"] = _make_get_spawn()

    # Host bridge
    namespace["host_request"] = _make_host_request(rlm_config, agent)

    # Skills
    namespace.update(_make_skills_namespace(agent))

    # Set __name__ for module detection
    namespace["__name__"] = "__main__"

    return namespace


# ── Capability builders ──────────────────────────────────────────────────


def _make_spawn_callable(agent: Any) -> Any:
    """Create spawn() callable for kernel namespace.

    Returns:
        spawn(task, *, inherit_context=False, name=None, budget=0.70) function
        that ALWAYS returns a SpawnHandle. Blocks until child completes
        initial task. Use handle methods for multi-turn steering.
    """
    try:
        from rlm.spawn import spawn as _spawn_func

        def spawn(
            task: str,
            *,
            inherit_context: bool = False,
            name: str = None,
            budget: float = 0.70,
        ) -> "SpawnHandle":
            """Spawn a child agent. Always returns a SpawnHandle.

            Args:
                task: Task description for the child agent.
                inherit_context: If True, child inherits parent conversation.
                name: Optional name for tracking (shown in console).
                budget: Work budget as fraction (0.0-0.95). Default 0.70.

            Returns:
                SpawnHandle. Blocks until child completes initial task.
                Check handle.last_result for the answer.
            """
            if agent is None:
                raise RuntimeError(
                    "spawn() requires a parent agent. "
                    "Ensure the kernel was created with agent= parameter."
                )
            return _spawn_func(
                task=task,
                inherit_context=inherit_context,
                name=name,
                budget=budget,
                parent_agent=agent,
            )

        return spawn
    except ImportError as e:
        logging.warning("spawn() import failed: %s — stub will be used", e)

    def spawn_stub(
        task: str,
        *,
        inherit_context: bool = False,
        name: str = None,
        budget: float = 0.70,
    ):
        raise NotImplementedError(
            "spawn() is not configured. Ensure rlm.spawn is available."
        )

    return spawn_stub


def _make_list_spawns(agent: Any) -> Any:
    """Create list_spawns() callable. Returns direct children by default."""
    from rlm.spawn import SpawnRegistry

    def list_spawns(all: bool = False):
        reg = SpawnRegistry()
        if all:
            return reg.list_active()
        # Default: only this agent's direct children
        my_id = getattr(agent, "spawn_id", "") if agent else ""
        return reg.list_children(my_id)

    return list_spawns


def _make_get_spawn() -> Any:
    """Create get_spawn() callable. Get handle by name or spawn_id."""
    from rlm.spawn import SpawnRegistry

    def get_spawn(name_or_id: str):
        return SpawnRegistry().get_by_name_or_id(name_or_id)

    return get_spawn


def _make_host_request(rlm_config: Any, agent: Any) -> Any:
    """Create host_request() callable for kernel namespace.

    Returns:
        HostBridge.request() method if available, otherwise a stub
        that raises NotImplementedError with a helpful message.
    """
    try:
        from rlm.host_bridge import HostBridge
        host_bridge = HostBridge(rlm_config, agent=agent)
        return host_bridge.request
    except ImportError as e:
        logging.warning("host_request import failed: %s — stub will be used", e)

    # Fallback: stub with helpful error message
    def host_request_stub(req_type: str, action: str, **params):
        raise NotImplementedError(
            "host_request() is not available. Ensure rlm.host_bridge is installed."
        )
    return host_request_stub


def _make_skills_namespace(agent: Any) -> dict[str, Any]:
    """Create skills-related functions for kernel namespace.

    Returns:
        Dict with available_skills and load_skill functions.
    """
    try:
        from rlm.skills import SkillLoader
        # Try to find skills directory relative to project root
        skills_dir = None
        # Skills discovery is static and should not require an agent instance.
        for candidate in [
            Path("skills"),
            Path(__file__).resolve().parent.parent / "skills",
            Path.home() / "taurlm" / "skills",
        ]:
            if candidate.exists():
                skills_dir = str(candidate)
                break
        if skills_dir is None:
            logging.warning("skills directory not found; available_skills() will be empty")
        _repl_cfg = getattr(getattr(agent, "config", None), "rlm", None) if agent else None
        _repl_cfg = getattr(_repl_cfg, "repl", None) if _repl_cfg else None
        _fs = getattr(_repl_cfg, "fence_style", "std") if _repl_cfg else "std"
        loader = SkillLoader(skills_dir, fence_style=_fs)
        return loader.get_initial_namespace()
    except (ImportError, TypeError) as e:
        logging.warning("skills import failed: %s — stubs will be used", e)

    # Fallback: stub functions
    def available_skills_stub() -> list:
        return []

    def load_skill_stub(name: str):
        return None

    def find_skills_stub(query: str, top_n: int = 20) -> list:
        return []

    return {
        "available_skills": available_skills_stub,
        "load_skill": load_skill_stub,
        "find_skills": find_skills_stub,
    }
