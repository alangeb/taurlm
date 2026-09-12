"""TauRLM Core Module

Core functionality for TauRLM system: Python REPL-based agent loop, context management.

Key Components
- TauErgon: Main agent class, orchestrates agent lifecycle
- run_rlm_loop(): Core RLM loop (REPL → LLM → answer)
- run(): Starts agent

Architecture
Message-driven RLM loop:
1. User Input: Messages appended via invoke()
2. LLM Interaction: run_rlm_loop() calls LLM WITHOUT tools, extracts Python code
3. REPL Execution: Python code executed in persistent kernel
4. Answer Check: answer["ready"] = True signals end of turn
5. Context Management: TauContext maintains history, auto-compresses near token limits
6. Safety: Loop detection, interrupt/exit flags

Entry Points
- invoke(user_input): Send message, run RLM loop, return response
- invoke_loop(): Core loop (calls run_rlm_loop)
- run(inputs, interactive): Start agent with input handling

Commands
Slash commands (/goal, /agents, /heartbeat, /autonomous, /refine) dispatched via _handle_command().

Thread Safety
System-wide flags (_interrupted, _exit_requested) for cooperative shutdown.

Example
    from agent_core import TauErgon
    from agent_config import Config
    config = Config.load()
    agent = TauErgon(config=config, agent_name="my-agent")
    response = agent.invoke("What can you help me with?")
    print(response)

See Also
- agent_context: Context management
- agent_loop: RLM loop implementation
- rlm.spawn: Unified agent delegation (spawn)
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from agent_context_manager import ContextManager, RestartManager
from agent_config import Config
from agent_context import TauContext
from agent_init import resolve_agent_init
from agent_input import InputHandler
from agent_llm_client import SimpleOpenAIClient
from agent_models import AgentStatus, InputMessage

from agent_command_handlers import CommandHandlersMixin
from agent_control import ControlQueue, process_control_queue

if TYPE_CHECKING:
    from agent_audit_writer import AuditWriter


# ── Safe template substitution ─────────────────────────────────────────────

_SAFE_PLACEHOLDER_RE = re.compile(r'\{(\w+)\}')


def _safe_format_template(template: str, **kwargs: str) -> str:
    """Replace {placeholder} with values, leaving everything else untouched.

    Only replaces placeholders whose names are in *kwargs*.  Unknown
    placeholders are left as-is (no crash, no code execution).  Literal
    ``{{`` and ``}}`` are passed through unchanged because the regex
    only matches single-brace ``{word}`` patterns.

    This replaces the previous ``str.format()`` call which could execute
    arbitrary Python expressions (e.g. ``{__import__('os').system('cmd')}``).
    """
    values = {k: str(v) for k, v in kwargs.items()}

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        return values.get(name, m.group(0))  # Leave unknown as-is

    return _SAFE_PLACEHOLDER_RE.sub(_replace, template)


__all__ = [
    "TauErgon",
]


class TauErgon(CommandHandlersMixin):
    MAX_OUTER_RECOVERY = 5  # Max recovery attempts before forced termination
    """RLM agent with Python REPL, context management, loop detection, and subagent support.

    The TauErgon is the main orchestrator for AI agent interactions, providing:
    - Python REPL execution (persistent kernel with state across turns)
    - Context management with automatic compression
    - Loop detection to prevent infinite cycles
    - Subagent and fork support for hierarchical task delegation
    - Command handling for interactive control
- for premature end-of-turn detection

    Attributes:
        config: Configuration object for the agent.
        agent_name: Name identifier for this agent instance.
        context: TauContext instance holding conversation history.
        client: SimpleOpenAIClient for LLM communication.
        loop_detector: LoopDetector for detecting conversation loops.
    """

    def __init__(
        self,
        config: Config | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_context_tokens: int | None = None,
        agent_name: str | None = None,
        llm_group_name: str | None = None,
        heartbeat_seconds: int | None = None,
    ):
        """Initialize the TauErgon with configuration and resources.

        Sets up the agent with the provided configuration, initializing the LLM
        client, context, and various subsystems. Configuration
        priority: explicit argument > config object > defaults/errors.

        Args:
            config: Configuration object containing LLM settings, loop detection,
                heartbeat, and other agent parameters.
            base_url: Optional override for LLM API base URL.
            model: Optional override for LLM model name.
            max_context_tokens: Optional override for maximum context size.
            agent_name: Optional name identifier for this agent instance.
            llm_group_name: Optional LLM group name to use. Defaults to the
                first available group or config.llm_group_name.
            heartbeat_seconds: Optional heartbeat interval in seconds. If set,
                enables heartbeat checking for idle detection.

        Raises:
            ValueError: If no LLM group is found in the configuration.
        """
        # Resolve all config + overrides into a single, fully-resolved config.
        init = resolve_agent_init(
            config=config,
            base_url=base_url,
            model=model,
            max_context_tokens=max_context_tokens,
            agent_name=agent_name,
            llm_group_name=llm_group_name,
            heartbeat_seconds=heartbeat_seconds,
        )

        self.config = config
        self.agent_name = init.agent_name
        self.llm_groups = init.llm_groups

        self.current_group_name = init.current_group_name
        self._llm_model_override = init.model_override
        self._llm_base_url_override = init.base_url_override
        self._llm_context_override = init.max_context_tokens_override

        self.model_name = init.model_name
        self.base_url = init.base_url
        self._current_api_key = init.api_key
        self.max_context_tokens = init.max_context_tokens

        self.max_silent_retries = init.max_silent_retries
        self.max_enhanced_retries = init.max_enhanced_retries
        self.max_explicit_retries = init.max_explicit_retries

        from agent_llm_cache import PrefixCacheTracker

        self._cache_tracker = PrefixCacheTracker()
        self.client = SimpleOpenAIClient(
            base_url=self.base_url,
            api_key=self._current_api_key,
            timeout=init.timeout,
            cache_tracker=self._cache_tracker,
        )
        self.context: TauContext = TauContext()
        self.context.set_metadata(
            pid=os.getpid(),
            working_dir=os.getcwd(),
            start_time=datetime.now().isoformat(),
            model=init.model_name,
            agent_name=init.agent_name,
        )

        self.inference_params = init.inference_params

        self._gen_overrides: dict = {}
        self.max_tokens = init.max_tokens

        self._repl_turn_count = 0
        self._init_subsystems(init)

    # ── Subsystem initialization ──────────────────────────────────────────

    def _init_subsystems(self, init: "AgentInitConfig") -> None:
        """Initialize all agent subsystems.

        Delegates to agent_subsystems.init_subsystems() which creates and
        wires up: session manager, loop detector, loop escalation
        manager, and heartbeat manager.

        State variables and None placeholders are initialized directly
        as assignments after the bundle is returned.

        Called once from ``__init__`` after config resolution.
        """
        from agent_subsystems import init_subsystems, read_system_prompt

        # Initialize subsystems FIRST (RLM mode): Session claims the prefix and
        # sets SESSION_PREFIX. read_system_prompt runs AFTER so it reuses that
        # prefix instead of claiming its own. Historically it claimed a throwaway
        # prefix first, leaking a stray 0-byte _1.context per process, and would
        # have interpolated the wrong _1 paths into any {audit_file}/{context_file}
        # placeholder in AGENT_RLM.md (it has none today, but keep this ordering).
        bundle = init_subsystems(self, init)

        # Load system prompt (RLM mode — always uses AGENT_RLM.md)
        _rlm_cfg = getattr(self.config, "rlm", None) if self.config else None
        _repl_cfg = getattr(_rlm_cfg, "repl", None) if _rlm_cfg else None
        _fence_style = getattr(_repl_cfg, "fence_style", "std") if _repl_cfg else "std"
        system_prompt = read_system_prompt(fence_style=_fence_style)

        # Assign subsystems to self
        self._session = bundle.session
        self.loop_detector = bundle.loop_detector
        self._loop_escalation = bundle.loop_escalation
        self._heartbeat = bundle.heartbeat

        # Assign REPL function names

        # Initialize state variables directly (NOT in the bundle)
        self.nesting_stack: str = ""  # e.g. "SF" = fork in subagent
        self.original_cwd = Path.cwd()
        self._start_time = time.time()
        self.original_task: str | None = None
        self._init_command_handlers()
        self.force_end_turn: str | None = None
        self.last_substantive_response: str | None = None

        # Vision / image queue
        self._queued_images: list[tuple[str, str, str]] = []
        self._vision_supported: bool | None = None

        # A2A state
        self._pending_a2a_responses: dict[str, dict] = {}
        self._pending_a2a_chunks: dict[str, list[dict]] = {}  # request_id -> list of chunk dicts
        self._current_a2a_request_id: str | None = None  # Set during A2A query processing

        # Turn active tracking (for status endpoint / tauweb running/idle detection)
        self._turn_active: bool = False

        # Control queue (inter-process supervision)
        self.control_queue = ControlQueue(maxsize=100)
        self._parent_pid: int | None = None

        # Commands
        self.available_commands: dict[str, Any] = {}
        self._commands_directory = None

        # Context and restart managers
        self._context_manager = ContextManager(self)
        self._restart_manager = RestartManager(self)

        # Input / threading state
        self.input_queue: queue.Queue = queue.Queue()
        self._a2a_listener_thread = None
        self._input_thread = None
        self._input_thread_stop = threading.Event()
        self._a2a_server = None
        self._keep_alive = False

        # Set system prompt in context
        self.context.set_system(system_prompt)

        # Log session start with full system prompt
        self._session.audit_writer.session_start(
            model=self.model_name,
            cwd=os.getcwd(),
            system_prompt=self.context.get_system() or "",
        )

        # Initialize RLM REPL kernel
        self._init_repl_kernel()

        # Spawn/budget attributes (set by spawn() for children, 0 for root)
        self.spawn_B: float = 0.0
        self.spawn_C_last: float = 0.0
        self.spawn_min_B: float = 1.0
        self._budget_warned: bool = False
        self._budget_warning_text: str | None = None
        self._force_max_turns: int | None = None

    # ── RLM REPL Kernel (delegated to rlm/kernel_wiring.py) ─────────────

    def _init_repl_kernel(self) -> None:
        """Initialize the RLM REPL kernel and supporting subsystems.

        Delegates to rlm.kernel_wiring.init_repl_kernel().
        """
        from rlm.kernel_wiring import init_repl_kernel
        init_repl_kernel(self)

        from agent_repl_manager import REPLManager
        self._repl = REPLManager(self._repl_kernel, self._repl_answer)

    def execute_repl(self, code: str) -> "REPLResult":
        """Execute Python code in the REPL kernel.

        DEPRECATED: use self._repl.execute() instead.
        Kept for backward compatibility with agent_loop.py and tests.
        """
        return self._repl.execute(code)

    # DEPRECATED: extract_and_execute() removed — RLM loop handles code extraction directly.
    # The old method extracted Python code from LLM responses and executed them.
    # This functionality is now handled directly in run_rlm_loop() in agent_loop.py.

    def get_answer(self) -> "AnswerState | None":
        """Get current answer state from the REPL kernel.

        DEPRECATED: use self._repl.get_answer() instead.
        """
        return self._repl.get_answer()

    def reset_answer(self) -> None:
        """Reset answer to initial state.

        DEPRECATED: use self._repl.reset_answer() instead.
        """
        self._repl.reset_answer()

    def get_repl_turn_count(self) -> int:
        """Get the number of REPL turns executed in the current session.

        DEPRECATED: use self._repl.turn_count instead.
        """
        return self._repl.turn_count

    # ── Vision / image queue management ──────────────────────────────────────

    def _recover_from_vision_error(self) -> bool:
        """Recover from a vision-incompatible model error.

        Strips image_url blocks from the last user message (which is the
        multimodal REPL feedback). Caches vision capability as False.

        Returns True if recovery was performed, False if context didn't match
        expected pattern (recovery not possible).
        """
        msgs = self.context._messages
        if len(msgs) < 1:
            return False

        last = msgs[-1]

        # Verify last message is a user message with image blocks
        if last.get("role") != "user":
            return False
        content = last.get("content", [])
        if not isinstance(content, list):
            return False
        has_images = any(b.get("type") == "image_url" for b in content)
        if not has_images:
            return False

        # Strip image blocks from the message, keep text
        text_blocks = [b for b in content if b.get("type") != "image_url"]
        if text_blocks:
            last["content"] = text_blocks
        else:
            last["content"] = "[image: model does not support vision]"

        # Cache vision capability
        self._vision_supported = False
        return True

    # ── Control queue ────────────────────────────────────────────────────────

    def _process_control_queue(self) -> None:
        """Process pending control commands (delegates to agent_control module).

        Called at turn boundaries (agent_loop.py). See agent_control.py
        for the full command protocol and handler implementations.
        """
        process_control_queue(self)

    @property
    def _control_queue(self) -> "queue.Queue[str]":
        """Backward-compatible access to the raw queue (for tests)."""
        return self.control_queue.raw_queue

    def clear_stale_state(self) -> None:
        """Clear per-task state (A2A, images, cache). Called on redirect."""
        self._pending_a2a_responses.clear()
        self._pending_a2a_chunks.clear()
        self._current_a2a_request_id = None
        self._queued_images.clear()
        self._cache_tracker.reset()

    def resolve_group_params(self) -> dict[str, Any]:
        """Return generation parameters for the current LLM group.

        Resolves generation parameters following a priority order from lowest to highest:
        1. Global inference_params (deprecated fallback from top-level "inference" block)
        2. Group-specific generation params (max_tokens, temperature, top_p, etc.)
        3. Group-specific chat_template_kwargs

        Returns:
            dict: Merged generation parameters with group-specific values taking
                precedence over global defaults.
        """
        group = self.llm_groups.get(self.current_group_name)
        if group is None:
            raise ValueError(f"Unknown LLM group {self.current_group_name!r}. Available: {list(self.llm_groups.keys())}")
        params: dict[str, Any] = {}

        # 1) Global inference_params as base (deprecated fallback for configs
        #    that still use the top-level "inference" block).
        if self.inference_params:
            params.update(self.inference_params)

        # 2) Group-specific generation params override global values.
        for attr in (
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "frequency_penalty",
            "repetition_penalty",
        ):
            val = getattr(group, attr, None)
            if val is not None:
                params[attr] = val

        # 3) Group-specific chat_template_kwargs override global values.
        if group.chat_template_kwargs:
            params["chat_template_kwargs"] = group.chat_template_kwargs


        # 4) Runtime overrides (highest priority, set via /tweak)
        if self._gen_overrides:
            for k, v in self._gen_overrides.items():
                if k == "chat_template_kwargs" and isinstance(v, dict):
                    # Deep-merge nested dict to preserve config values
                    existing = params.get("chat_template_kwargs") or {}
                    merged = {**existing, **v}
                    params["chat_template_kwargs"] = merged
                else:
                    params[k] = v
        return params

    def _rebuild_client(self, clear_overrides: bool = False) -> None:
        """Rebuild the HTTP client and refresh parameters after LLM group switch.

        Reinitializes the SimpleOpenAIClient with the current LLM group's
        configuration. Optionally clears any model/base URL/context overrides.

        Args:
            clear_overrides: If True, clears all LLM configuration overrides
                (_llm_model_override, _llm_base_url_override, _llm_context_override).
                If False, preserves existing overrides.

        Raises:
            ValueError: If the current LLM group is not found or has invalid config.
        """
        group = self.llm_groups.get(self.current_group_name)
        if not group:
            raise ValueError(
                f"No LLM group '{self.current_group_name}' found. "
                f"Available groups: {list(self.llm_groups.keys()) or '(none)'}"
            )
        if clear_overrides:
            self._llm_model_override = None
            self._llm_base_url_override = None
            self._llm_context_override = None

        base_url = self._llm_base_url_override or group.api_base
        if not base_url:
            raise ValueError(
                f"LLM group '{self.current_group_name}' has no valid api_base "
                f"(override={self._llm_base_url_override!r}, "
                f"group.api_base={group.api_base!r}). Cannot rebuild client."
            )

        self._current_api_key = group.api_key
        self.client = SimpleOpenAIClient(
            base_url=base_url,
            api_key=self._current_api_key,
            timeout=group.timeout,
            cache_tracker=self._cache_tracker,
        )
        self.model_name = self._llm_model_override or group.model
        self.base_url = self._llm_base_url_override or group.api_base
        self.max_tokens = group.max_tokens
        self.max_context_tokens = (
            self._llm_context_override
            if self._llm_context_override is not None
            else group.max_context_tokens
        )

    def _is_restricted_nesting(self) -> bool:
        """Check if current nesting type allows relaxed EOT (no sentinel required).

        Returns True for 'T' (think) and 'K' (skill) nesting types.
        These types accept a basic assistant message as end-of-turn without
        requiring the explicit sentinel confirmation.
        """
        return self.nesting_stack and self.nesting_stack[-1] in ("T", "K")


    def invoke(self, user_input: str) -> str:
        """Send *user_input* to the model, execute Python code, return response text.

        CRITICAL OPENAI COMPLIANCE:
        This method APPENDS a user message to the context before calling the LLM.
        The context MUST end with an assistant message before this call
        to maintain valid message alternation (no consecutive user messages).

        TauRLM has NO tool calls — the agent communicates entirely through
        Python code blocks executed in a persistent REPL kernel.

        If the context already ends with a user message, use invoke_loop()
        directly instead to avoid violating the OpenAI spec.

        Args:
            user_input: The user message content to append and send to the model.

        Returns:
            The final assistant response text (or error message).
        """
        self._turn_active = True
        try:
            if self.original_task is None:
                self.original_task = user_input
            # Tier 1: auto-SUGGEST (never auto-load). Append a one-line skill
            # nudge to the NEWEST user message so the model is reminded of a
            # confident skill match at the moment of intent. The model stays
            # in control (only loads if it agrees); prompt cache is preserved
            # because only the tail message changes, not the system prompt.
            _ctx_content = user_input
            try:
                _loader = getattr(self, "_skill_loader", None)
                if _loader is not None:
                    _nudge = _loader.render_skill_nudge(user_input)
                    if _nudge:
                        _ctx_content = user_input + _nudge
            except Exception:
                pass  # skills failure must NEVER break the turn
            # Audit the AUGMENTED text actually sent to the LLM (Tier 1
            # nudge included), so audits reflect what the model saw.
            self._session.audit_writer.user(_ctx_content)
            self.context.append_user(_ctx_content, user_type="real", context_pct=self.context.context_pct(self.max_context_tokens))
            result = self.invoke_loop()
            self._session.audit_writer.flush()
            return result
        finally:
            self._turn_active = False

    def invoke_loop(self) -> str:
        """Core loop: RLM mode (Python REPL-based).

        CRITICAL OPENAI COMPLIANCE REQUIREMENT:
        The context MUST already end with a user message when this method is called.
        This method does NOT append any messages before the LLM call.

        Returns:
            The final assistant response text (or error message).
        """
        from agent_loop import run_rlm_loop
        return run_rlm_loop(self)

    def run(
        self,
        inputs: list[str] = None,
        a2a_server=None,
        keep_alive=False,
        interactive=True,
    ) -> None:
        """Delegate the main run loop to InputHandler.

        Starts the agent's main execution loop by delegating to the InputHandler
        which manages user input processing, command handling, and interaction flow.

        Args:
            inputs: Optional list of initial input strings to process.
            a2a_server: Optional A2A (Agent-to-Agent) server instance.
            keep_alive: If True, keep the agent running after processing inputs.
            interactive: If True, enable interactive mode for user input.
        """
        input_handler = InputHandler(self)
        self._input_handler = input_handler  # Store for .md command dispatch
        try:
            input_handler.run(
                inputs=inputs,
                a2a_server=a2a_server,
                keep_alive=keep_alive,
                interactive=interactive,
            )
        finally:
            self.close()

    def close(self) -> None:
        """Clean up all agent resources. Safe to call multiple times."""
        # Stop input handler
        handler = getattr(self, "_input_handler", None)
        if handler is not None:
            self._input_handler = None
        # Stop A2A server
        if hasattr(self, '_a2a_server') and self._a2a_server:
            try:
                self._a2a_server.stop()
            except Exception:
                pass
        # Flush and close audit writer
        if self._session and self._session.audit_writer:
            try:
                self._session.audit_writer.flush()
                self._session.audit_writer.close()
            except Exception:
                pass
        # Close REPL kernel
        if hasattr(self, '_repl') and self._repl:
            try:
                self._repl.close()
            except Exception:
                pass
        # Save context to file
        try:
            if self._session and self._session.context_file:
                self.context.save_to_file(self._session.context_file)
        except Exception:
            pass

    def get_status(self) -> AgentStatus:
        """Return an encapsulated view of agent status for display functions.

        Replaces direct access to agent internals from the display layer.
        """
        token_count, percentage, byte_count, is_exact = self.context.get_usage_stats(
            self.max_context_tokens, self._session.last_exact_context_tokens
        )

        return AgentStatus(
            # Context stats
            token_count=token_count,
            percentage=percentage,
            byte_count=byte_count,
            is_exact=is_exact,
            context_len=len(self.context),
            max_context_tokens=self.max_context_tokens,
            # Model info
            model_name=self.model_name,
            base_url=self.base_url,
            model_source="cli" if self._llm_model_override else f"group:{self.current_group_name}",
            base_url_source="cli" if self._llm_base_url_override else f"group:{self.current_group_name}",
            # Group info
            current_group_name=self.current_group_name,
            llm_groups=list(self.llm_groups.keys()),
            gen_params=self.resolve_group_params(),
            # Token tracking
            last_turn_in=self._session.last_turn_input_tokens,
            last_turn_out=self._session.last_turn_output_tokens,
            last_turn_cached=self._session.last_turn_cached_tokens,
            session_in=self._session.input_tokens,
            session_out=self._session.output_tokens,
            session_cached=self._session.cached_tokens,
            session_in_bytes=self._session.input_bytes,
            session_out_bytes=self._session.output_bytes,
            session_cached_bytes=self._session.cached_bytes,
            last_tg_tps=self._session.last_tg_tps,
            last_ttft=self._session.last_ttft,
            # Cache
            has_cache_data=self._session.cache_tracker.has_cache_data,
            cumulative_hit_rate=self._session.cache_tracker.cumulative_hit_rate,
            sliding_hit_rate=self._session.cache_tracker.sliding_hit_rate,
            last_hit_rate=self._session.cache_tracker.last_hit_rate,
            call_count=self._session.cache_tracker.call_count,
            # Agent info
            agent_name=self.agent_name,
            context_file=self._session.context_file.resolve() if self._session.context_file else None,
            nesting_count=self.nesting_count,
            nesting_stack=self.nesting_stack,
            turn_active=self._turn_active,
            # Spawn/budget
            spawn_B=getattr(self, 'spawn_B', 0.0),
            spawns_count=self._get_spawns_count(),
            # Loop detection
            loop_stats=self.loop_detector.get_stats() if self.loop_detector else {},
            # Commands
            available_commands=list(self._get_available_commands().keys()),
            # File paths (absolute)
            audit_file=self._session.audit_file.resolve() if hasattr(self._session, 'audit_file') and self._session.audit_file else None,
            # Process info
            pid=os.getpid(),
            ppid=os.getppid(),
        )

    def _get_spawns_count(self) -> int:
        """Get count of active spawns (root agents only)."""
        if self.nesting_stack:
            return 0
        try:
            from rlm.spawn import SpawnRegistry
            return SpawnRegistry().count_active()
        except (ImportError, Exception):
            return 0

    # ── Backward-compatible property wrappers (tests access these directly) ──

    @property
    def nesting_count(self) -> int:
        """Nesting depth derived from nesting_stack length."""
        return len(self.nesting_stack)

    @property
    def audit_file(self) -> Path:
        """Delegate to session manager."""
        return self._session.audit_file

    @property
    def context_file(self) -> Path:
        """Delegate to session manager."""
        return self._session.context_file

    @context_file.setter
    def context_file(self, value: Path) -> None:
        self._session.context_file = value

    @property
    def audit_writer(self) -> AuditWriter:
        """Delegate to session manager."""
        return self._session.audit_writer
