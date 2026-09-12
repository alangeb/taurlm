"""SPAWN — Unified Persistent Agent Delegation for TauRLM.

Single delegation API. All spawns are persistent. All return SpawnHandle.
Budget is a work budget (B:) that only decreases.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import weakref
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
import copy
import signal
from contextlib import contextmanager

if TYPE_CHECKING:
    from agent_core import TauErgon

__all__ = [
    "SpawnHandle",
    "SpawnRegistry",
    "spawn",
    "SpawnError",
    "SpawnLimitError",
    "NestingLimitError",
    "SpawnClosedError",
    "AUTO_COMPRESS_THRESHOLD",
    "AUTO_COMPRESS_TARGET",
]

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_SPAWNS = 5
MAX_NESTING_DEPTH = 3
BUDGET_WARN_THRESHOLD = 0.10  # warn when B < 10%
AUTO_COMPRESS_THRESHOLD = 0.70  # compress when context usage >= 70%
AUTO_COMPRESS_TARGET = 28  # compress to 28% of max context

# ── Exceptions ─────────────────────────────────────────────────────────────────
class SpawnError(Exception):
    """Base exception for spawn errors."""


class SpawnLimitError(SpawnError):
    """Raised when max concurrent spawns is exceeded."""


class NestingLimitError(SpawnError):
    """Raised when max nesting depth is exceeded."""


class SpawnClosedError(SpawnError):
    """Raised when operating on a closed spawn handle."""


# ── Live Output Helper ─────────────────────────────────────────────────────────
@contextmanager
def _paused_parent_timer():
    """Pause the parent REPL block timer across a blocking child call.

    The parent Python block arms a PROCESS-GLOBAL ITIMER_REAL (block_executor.py).
    A child agent runs on the SAME main thread, so the parent alarm would fire
    during a long child task, or the child's own setitimer(...,0) in its finally
    would silently CLOBBER the parent deadline. We SAVE the remaining time, cancel
    it for the blocking call, then RE-ARM on exit (pause, not lose). No-op when no
    parent timer is armed. Main-thread only; guarded.

    DESIGN DECISION — subagents intentionally have NO wall-clock timeout (P7-B4-02,
    reviewed and accepted as by-design, not a defect):
      1. There is no task-independent "right" timeout. A subagent's legitimate
         runtime is task-dependent (a quick count vs. a long training run or a
         deep build), so any fixed cap would either kill valid long work or be
         set so loose it never fires. A wrong number is worse than none.
      2. We trust the harness and the subagent, and the child is already bounded
         by protections around it: per-turn turn-count limits cap the number of
         LLM turns, and every OTHER REPL execution path (each code/bash block,
         including inside the child) is timeout-protected. So a runaway child is
         bounded by those layers; the only residual gap is a single non-LLM
         blocking op with no internal timeout (e.g. a subprocess without one),
         which is governed by the same "block author sets a timeout" contract the
         top-level REPL already relies on.
    Consequence: the parent's outer block timer is OFF for the duration of a
    child turn (see above). This is deliberate. Do not "fix" it by re-arming the
    parent ITIMER_REAL across the child call -- that re-introduces the clobbering
    this pause exists to prevent (single global timer slot, same main thread).
    See wiki topic "subagent-no-timeout" and review_pass8_analysis.md for the
    full rationale.
    """
    try:
        remaining = signal.setitimer(signal.ITIMER_REAL, 0.0)
    except (ValueError, OSError):
        remaining = (0.0, 0.0)
    rem = remaining[0] if remaining else 0.0
    try:
        yield
    finally:
        if rem and rem > 0:
            try:
                signal.setitimer(signal.ITIMER_REAL, rem)
            except (ValueError, OSError):
                pass


def _with_real_stdout(func, *args, **kwargs):
    """Execute func with real stdout/stderr (bypasses REPL capture).
    
    This ensures child agent output appears live on the user console
    rather than being buffered and returned as a string.
    """
    opened = []
    saved_out = saved_err = None
    try:
        real_stdout = os.fdopen(os.dup(1), "w", buffering=1)
        opened.append(real_stdout)
        real_stderr = os.fdopen(os.dup(2), "w", buffering=1)
        opened.append(real_stderr)
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = real_stdout, real_stderr
        return func(*args, **kwargs)
    finally:
        if saved_out is not None:
            sys.stdout, sys.stderr = saved_out, saved_err
        for f in opened:
            try:
                f.close()
            except OSError:
                pass


# ── Spawn Registry (singleton) ─────────────────────────────────────────────────
class SpawnRegistry:
    """Global registry of all active spawn handles. Singleton."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._handles = {}
                cls._instance._lock = threading.Lock()
            return cls._instance

    def _live_locked(self) -> list["SpawnHandle"]:
        """S1: return live handles, pruning weakrefs whose handle was GC'd.

        The registry holds WEAK references so that dropping the last external
        reference to an unclosed handle lets it be collected (its __del__ then
        unregisters it). A strong-ref registry would keep the handle alive
        forever and permanently consume a MAX_SPAWNS slot. Caller holds _lock.
        """
        dead = [k for k, ref in self._handles.items() if ref() is None]
        for k in dead:
            del self._handles[k]
        return [ref() for ref in self._handles.values()]

    def register(self, handle: "SpawnHandle") -> None:
        """Register a new spawn handle. Raises SpawnLimitError if at max."""
        with self._lock:
            active = [h for h in self._live_locked() if h.status != "closed"]
            if len(active) >= MAX_SPAWNS:
                raise SpawnLimitError(
                    f"Max {MAX_SPAWNS} active spawns reached. "
                    f"Close a handle before spawning."
                )
            self._handles[handle.spawn_id] = weakref.ref(handle)

    def unregister(self, spawn_id: str) -> None:
        """Remove a handle from the registry."""
        with self._lock:
            self._handles.pop(spawn_id, None)

    def list_children(self, parent_id: str = "") -> list["SpawnHandle"]:
        """List active spawns. If parent_id given, only that agent's children."""
        with self._lock:
            handles = [h for h in self._live_locked() if h.status != "closed"]
            handles = [h for h in handles if h.parent_id == parent_id]
            return handles

    def list_active(self) -> list["SpawnHandle"]:
        """Return all non-closed handles."""
        with self._lock:
            return [h for h in self._live_locked() if h.status != "closed"]

    def get_by_name_or_id(self, name_or_id: str) -> "SpawnHandle | None":
        """Find a handle by name or spawn_id."""
        with self._lock:
            # Try exact spawn_id first
            ref = self._handles.get(name_or_id)
            if ref is not None and ref() is not None:
                return ref()
            # Try by name
            for h in self._live_locked():
                if h.name == name_or_id:
                    return h
            return None

    def count_active(self) -> int:
        """Count non-closed handles."""
        with self._lock:
            return sum(1 for h in self._live_locked() if h.status != "closed")


# ── Spawn Handle ───────────────────────────────────────────────────────────────
@dataclass
class SpawnHandle:
    """Handle to a persistent child agent. Provides full control interface."""

    spawn_id: str
    parent_id: str = ""
    name: str | None = None
    status: str = "running"  # running|completed|yielded|budget_exhausted|error|closed
    last_result: str = ""
    turns: int = 0
    budget: float = 0.70
    _agent: "TauErgon | None" = field(repr=False, default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)  # H14

    # ── Properties ─────────────────────────────────────────────────────────
    @property
    def context_pct(self) -> float:
        """Current context usage percentage of the child."""
        if self._agent is None:
            return 0.0
        try:
            _, pct, _, _ = self._agent.context.get_usage_stats(
                self._agent.max_context_tokens
            )
            return pct
        except Exception:
            return 0.0

    @property
    def min_B(self) -> float:
        """Minimum B value reached (lowest budget remaining)."""
        if self._agent is None:
            return 0.0
        return getattr(self._agent, "spawn_min_B", 1.0)

    # ── Methods ────────────────────────────────────────────────────────────
    def _check_open(self) -> None:
        if self.status == "closed" or self._agent is None:
            raise SpawnClosedError(
                f"Spawn '{self.name or self.spawn_id}' is closed."
            )

    def send(self, prompt: str) -> str:
        """Send a new instruction to the child. Blocks until response.
        
        Auto-compresses context if usage exceeds threshold.
        Returns the child's answer content.
        """
        with self._lock:  # H14: atomic check-and-get
            if self._agent is None or self.status == "closed":
                raise SpawnClosedError(f"SpawnHandle '{self.name or self.spawn_id}' is closed")
            agent = self._agent
        self._maybe_compress()
        # DESIGN DECISION (intentional, NOT a defect): subagents have NO
        # wall-clock invoke timeout. A child's legitimate runtime is task-
        # dependent (a quick count vs. a long build/training run), so any fixed
        # cap would either kill valid long work or be set so loose it never
        # fires. The child is instead bounded by (a) per-turn turn-count limits
        # and (b) per-block code/bash timeouts enforced inside the child's own
        # REPL — see _paused_parent_timer docstring and wiki "subagent-no-timeout"
        # for the full rationale. Do NOT add a wall-clock cap here.
        with _paused_parent_timer():
            result = _with_real_stdout(agent.invoke, prompt)
        self.last_result = result if isinstance(result, str) else str(result)
        self.turns += 1
        self._update_status()
        return self.last_result

    def resume(self, hint: str | None = None) -> str:
        """Resume current work with optional direction hint.

        (Named resume() because continue is a Python keyword.)
        Auto-compresses context if usage exceeds threshold.
        Returns the child's answer content.
        """
        with self._lock:  # H14: atomic check-and-get (P7-B4-01)
            if self._agent is None or self.status == "closed":
                raise SpawnClosedError(f"SpawnHandle '{self.name or self.spawn_id}' is closed")
            agent = self._agent
        self._maybe_compress()
        if hint:
            prompt = f"Continue your current work. Direction: {hint}"
        else:
            prompt = "Continue your current work."
        with _paused_parent_timer():
            result = _with_real_stdout(agent.invoke, prompt)
        self.last_result = result if isinstance(result, str) else str(result)
        self.turns += 1
        self._update_status()
        return self.last_result

    def summarize(self) -> str:
        """Get a 1-turn summary of what the child has done so far.
        
        Uses _force_max_turns=1 to limit to a single LLM call.
        The summary is appended to the child's context (cache-friendly).
        Returns the summary text.
        """
        with self._lock:  # H14: atomic check-and-get (P7-B4-01)
            if self._agent is None or self.status == "closed":
                raise SpawnClosedError(f"SpawnHandle '{self.name or self.spawn_id}' is closed")
            agent = self._agent
        self._maybe_compress()
        agent._force_max_turns = 1
        try:
            prompt = (
                "Summarize what you have done so far. "
                "Be concise: what was the task, what did you accomplish, "
                "what remains. Set answer['content'] to the summary. "
                "Set answer['ready']=True."
            )
            with _paused_parent_timer():
                result = _with_real_stdout(agent.invoke, prompt)
            self.last_result = result if isinstance(result, str) else str(result)
        finally:
            agent._force_max_turns = None
        self.turns += 1
        self._update_status()
        return self.last_result

    def inspect(self, mode: str = "last_n", **kwargs) -> str:
        """Inspect the child's context. No LLM call. Free.
        
        Modes:
            'overview': Total messages, roles breakdown, first/last timestamps
            'last_n': Last N messages (default n=5, chars=80 per message)
            'first_n': First N messages
            'last_user': Last user message (truncated)
            'last_assistant': Last assistant message (truncated)
        
        Args:
            mode: Inspection mode.
            n: Number of messages (for last_n/first_n). Default 5.
            chars: Max chars per message. Default 80.
            include_reason: Include reasoning in assistant messages. Default False.
        """
        self._check_open()
        if self._agent is None or not hasattr(self._agent, "context"):
            return "[No context available]"
        
        messages = self._agent.context._messages
        n = kwargs.get("n", 5)
        chars = kwargs.get("chars", 80)
        include_reason = kwargs.get("include_reason", False)

        if mode == "overview":
            roles = {}
            for m in messages:
                role = m.get("role", "unknown")
                roles[role] = roles.get(role, 0) + 1
            role_str = ", ".join(f"{k}:{v}" for k, v in roles.items())
            first_preview = (messages[0].get("content", "")[:40] + "...") if messages else "?"
            last_preview = (messages[-1].get("content", "")[:40] + "...") if messages else "?"
            return (
                f"Messages: {len(messages)} ({role_str})\n"
                f"First: {first_preview}\nLast: {last_preview}"
            )

        elif mode == "last_n":
            selected = messages[-n:]
            return self._format_messages(selected, chars, include_reason)

        elif mode == "first_n":
            selected = messages[:n]
            return self._format_messages(selected, chars, include_reason)

        elif mode == "last_user":
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content", "")
                    return f"[user] {content[:chars]}"
            return "[No user messages]"

        elif mode == "last_assistant":
            for m in reversed(messages):
                if m.get("role") == "assistant":
                    content = m.get("content", "")
                    reason = m.get("reasoning", "") if include_reason else ""
                    base = f"[assistant] {content[:chars]}"
                    if reason:
                        base += f"\n  [reason] {reason[:chars]}"
                    return base
            return "[No assistant messages]"

        else:
            return f"[Unknown inspect mode: {mode}]"

    def _format_messages(self, messages: list, chars: int, include_reason: bool) -> str:
        """Format a list of messages for display."""
        lines = []
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content", "")
            line = f"[{role}] {content[:chars]}"
            if include_reason and role == "assistant":
                reason = m.get("reasoning", "")
                if reason:
                    line += f"\n  [reason] {reason[:chars]}"
            lines.append(line)
        return "\n".join(lines) if lines else "[Empty]"

    def extend_budget(self, amount: float) -> None:
        """Grant additional work budget to the child.
        
        This is the ONLY way B increases.
        Resets the budget warning if B goes above threshold.
        """
        with self._lock:  # H14: atomic check-and-get (P7-B4-01)
            if self._agent is None or self.status == "closed":
                raise SpawnClosedError(f"SpawnHandle '{self.name or self.spawn_id}' is closed")
            agent = self._agent
        agent.spawn_B += amount
        agent.spawn_B = min(agent.spawn_B, 1.0)
        if agent.spawn_B > BUDGET_WARN_THRESHOLD:
            agent._budget_warned = False

    def close(self) -> None:
        """Close the spawn. Frees resources. Handle is no longer usable."""
        with self._lock:  # H14: atomic close
            if self.status == "closed":
                return
            _final_status = self.status  # capture terminal status before overwrite
            self.status = "closed"
            agent = self._agent
            self._agent = None
        # Verification-cost metric: record the spawn's real terminal
        # outcome (completed/suspect/budget_exhausted/error/yielded) once,
        # here at close — the single guaranteed end-of-life point. Fail-safe.
        try:
            from rlm.skill_telemetry import SkillTelemetry
            SkillTelemetry().record_spawn(_final_status, self.spawn_id)
        except Exception:
            pass
        if agent is not None:
            # P7-CT-10: free the child kernel. The teardown object is
            # _repl_kernel (PythonKernel.close() clears the namespace,
            # kernel.py:611) — REPLManager (_repl) has NO close() method.
            # close() is idempotent: namespace.clear() on a cleared dict is a
            # no-op, so a prior close (e.g. spawn error path) is harmless.
            try:
                kernel = getattr(agent, "_repl_kernel", None)
                if kernel is not None:
                    kernel.close()
            except Exception:
                pass
        SpawnRegistry().unregister(self.spawn_id)

    def __del__(self):
        """Warn if handle is GC'd without close(), and free its registry slot.

        S1: an unclosed handle that is GC'd would otherwise stay registered and
        permanently consume a MAX_SPAWNS slot. Unregister it (fail-safe).
        """
        if self.status != "closed" and self._agent is not None:
            # Unregister FIRST (frees the MAX_SPAWNS slot) — this is the point
            # of the fix; must survive even if logging is unavailable.
            try:
                SpawnRegistry().unregister(self.spawn_id)
            except Exception:
                pass
            self._agent = None
            # Warn only outside interpreter shutdown (importing/logging can fail
            # during finalization -> "sys.meta_path is None").
            try:
                if not getattr(__import__("sys"), "is_finalizing", lambda: False)():
                    import logging
                    logging.warning(
                        "SpawnHandle %s (%s) was garbage collected without close(). "
                        "Agent resources may leak.", self.spawn_id, self.name
                    )
            except Exception:
                pass

    def status_dict(self) -> dict:
        """Return a dict with spawn status info."""
        return {
            "spawn_id": self.spawn_id,
            "name": self.name,
            "status": self.status,
            "turns": self.turns,
            "budget": self.budget,
            "context_pct": round(self.context_pct, 2),
            "min_B": round(self.min_B, 4),
            "last_result_preview": self.last_result[:80] if self.last_result else "",
        }

    def _maybe_compress(self) -> None:
        """Auto-compress child context if usage exceeds threshold.
        
        Logs a warning and swallows exceptions — compression failure
        should not block the agent from continuing.
        """
        if self._agent is None:
            return
        try:
            from agent_context_store import compute_live_tokens
            # P1: live exact-anchored count (exact prompt + post-anchor estimate,
            # reply included) replaces the stale-exact read AND the pure-estimate
            # fallback — prices the anchored prefix exactly and the newest tool
            # result by estimate.
            def _live_pct():
                sess = getattr(self._agent, "_session", None)
                ctx = self._agent.context
                msgs = ctx.get_messages() if hasattr(ctx, "get_messages") else ctx._messages
                live = compute_live_tokens(
                    msgs,
                    getattr(sess, "last_exact_context_tokens", None) if sess else None,
                    getattr(sess, "last_exact_msg_count", None) if sess else None,
                    getattr(sess, "last_turn_output_tokens", 0) if sess else 0,
                )
                return live, live / self._agent.max_context_tokens if self._agent.max_context_tokens > 0 else 0.0
            live_tokens, pct = _live_pct()
            if pct >= AUTO_COMPRESS_THRESHOLD:
                from agent_context_compress import compress_to_target
                # Pass the live count so compress_to_target does not fall back
                # to stale last_exact_context_tokens and silently early-return.
                compress_to_target(
                    self._agent.context, self._agent, AUTO_COMPRESS_TARGET,
                    current_tokens=live_tokens,
                )
                # M-S2: Recompute B after compression freed context
                _, new_pct = _live_pct()
                current_C = new_pct / 100.0
                self._agent.spawn_B = max(
                    self._agent.spawn_B,
                    self.budget - current_C,
                )
        except Exception as e:
            logging.warning(
                f"spawn: auto-compress failed for '{self.name}': {e}"
            )

    def _update_status(self) -> None:
        """Update status based on agent state after an invoke."""
        if self._agent is None:
            return
        answer = self._agent.get_answer()
        # Check for yield
        if answer and getattr(answer, "yielded", False):
            self.status = "yielded"
            return
        # If agent set ready=True, it completed its work. But 'ready' with
        # EMPTY content is the exact signature of a worker that ended its
        # turn without producing an answer (the 'completed with empty
        # last_result' failure). Don't over-claim success: mark it SUSPECT
        # so the manager knows to re-verify rather than trust 'completed'.
        if answer and getattr(answer, "ready", False):
            _content = getattr(answer, "content", None)
            _empty = _content is None or (isinstance(_content, str) and not _content.strip())
            self.status = "suspect" if _empty else "completed"
            return
        # No ready: was it cut off by budget?
        if getattr(self._agent, "spawn_B", 1.0) <= 0:
            self.status = "budget_exhausted"
            return
        # S2: NOT ready and NOT budget-exhausted => the agent exited early
        # (max_turns / force_end) and returned content WITHOUT setting
        # ready=True. Do NOT over-claim success as 'completed' (reserved for
        # actual ready); report a distinct 'incomplete' status.
        self.status = "incomplete"

    # ── Dunder ─────────────────────────────────────────────────────────────
    def __str__(self) -> str:
        """Returns last_result for backward compatibility with print(h)."""
        return self.last_result

    def __repr__(self) -> str:
        name = self.name or self.spawn_id[:8]
        return (
            f"SpawnHandle({name!r}, status={self.status!r}, "
            f"turns={self.turns}, B={self.min_B:.3f})"
        )


# ── Main spawn function ────────────────────────────────────────────────────────
def spawn(
    task: str,
    *,
    inherit_context: bool = False,
    name: str | None = None,
    budget: float = 0.70,
    parent_agent: "TauErgon",
) -> SpawnHandle:
    """Spawn a child agent. Always returns a SpawnHandle.
    
    Blocks until the child completes its initial task.
    
    Args:
        task: Task description for the child agent.
        inherit_context: If True, child inherits parent's conversation history.
        name: Optional name for tracking (shown in console).
        budget: Work budget as fraction (0.0-0.95). Default 0.70.
        parent_agent: The parent TauErgon instance.
    
    Returns:
        SpawnHandle for the child agent.
    """
    # 1. Check nesting depth
    parent_depth = getattr(parent_agent, "nesting_count", 0)
    if parent_depth >= MAX_NESTING_DEPTH:
        raise NestingLimitError(
            f"Max nesting depth ({MAX_NESTING_DEPTH}) reached. "
            f"Cannot spawn from depth {parent_depth}."
        )

    # 2. Check registry limit
    registry = SpawnRegistry()
    if registry.count_active() >= MAX_SPAWNS:
        raise SpawnLimitError(
            f"Max {MAX_SPAWNS} active spawns reached. Close a handle first."
        )

    # 3. Create child agent
    from agent_core import TauErgon

    child_config = parent_agent.config
    child_name = name or f"spawn-{uuid.uuid4().hex[:6]}"
    # Child inherits the parent's LIVE group (current_group_name reflects
    # runtime /llm switches), not the config default. getattr fallback keeps
    # stub parents (no attr) on today's config-default behavior.
    child = TauErgon(config=child_config, agent_name=child_name,
                     llm_group_name=getattr(parent_agent, "current_group_name", None))
    child.nesting_stack = parent_agent.nesting_stack + ("F" if inherit_context else "S")
    child.context.nesting_stack = child.nesting_stack

    # 4. Set up context
    if inherit_context:
        # Copy parent's messages
        child.context._messages = [
            copy.deepcopy(m) for m in parent_agent.context._messages
        ]
    # Else: fresh context (default)

    # 5. Calculate B_start
    # NOTE: B=0 is intentional — it gives the child exactly 1 turn of work.
    # The child gets one LLM call, produces output, then budget hits 0 and
    # it terminates. Useful for "ask one question" spawns.
    _, C_start, _, _ = child.context.get_usage_stats(child.max_context_tokens)
    child.spawn_B = max(0.0, budget - C_start)
    child.spawn_C_last = C_start
    child.spawn_min_B = child.spawn_B
    child.context.spawn_B = child.spawn_B
    if child.spawn_B <= 0:
        import logging
        logging.info(
            f"spawn: B_start=0 (1-turn spawn). Child gets exactly one LLM call. "
            f"Context {C_start*100:.1f}%, budget {budget*100:.1f}%."
        )

    # 6. Create handle
    spawn_id = uuid.uuid4().hex[:12]
    parent_spawn_id = getattr(parent_agent, "spawn_id", "")
    child.spawn_id = spawn_id
    handle = SpawnHandle(
        spawn_id=spawn_id,
        parent_id=parent_spawn_id,
        name=name,
        budget=budget,
        _agent=child,
    )

    # 7. Execute initial task with live output
    try:
        handle._maybe_compress()
        with _paused_parent_timer():
            result = _with_real_stdout(child.invoke, task)
        handle.last_result = result if isinstance(result, str) else str(result)
        handle.turns = 1
    except Exception as e:
        handle.last_result = f"[Spawn error: {e}]"
        handle.status = "error"
        handle.turns = 1
        # Clean up child agent resources on failure (P7-CT-10: _repl has no
        # close(); the kernel teardown is _repl_kernel.close()).
        try:
            kernel = getattr(child, '_repl_kernel', None)
            if kernel is not None:
                kernel.close()
        except Exception:
            pass
        # Don't register failed spawns
        return handle

    # 8. Determine status
    handle._update_status()

    # 9. Register
    registry.register(handle)

    return handle
