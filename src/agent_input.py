"""Input processing and run loop for TauErgon.

Manages user input from stdin, context file operations, and the main agent run loop.

Input Prefix Protocol:
======================
Regular input (outside multiline block):
  - '#'     Start multiline block (content until 2+ blank lines)
  - '#!'    Start multiline block (alternative syntax, equivalent to #)
  - '!'     Execute shell command via subprocess
  - '+'     Steering control (inject when turn is active)
  - '/'     Dispatch slash command to command handler

Inside multiline block:
  - '#!'    Continue block (prefix stripped from content)
  - '#+'    Break multiline and route steering command
  - '#/'    Break multiline and execute as slash command
  - 2+ blank lines  Submit the accumulated block

Context files: JSON arrays stored in LOG_DIR as {ppid}_{timestamp}_{counter}.context.

See tests/test_input_protocol.py for comprehensive test coverage.
"""

from __future__ import annotations

import queue
import select
import signal
import sys
import threading
import time
import traceback
from io import StringIO
from pathlib import Path
from typing import Any

from agent_lifecycle import AgentLifecycle
from agent_console import (
    blank_line,
    echo_no_newline,
    error,
    force_exit_message,
    interrupted_message,
    print_agent_exit_summary,
    print_context_status,
    prompt,
    shell_command_usage,
    status,
    user_echo,
    warning,
)
from agent_models import InputMessage

# Conditional imports for non-RLM mode
try:
    from agent_heartbeat import HeartbeatResponse
    _HEARTBEAT_AVAILABLE = True
except ImportError:
    HeartbeatResponse = None  # type: ignore[assignment,misc]
    _HEARTBEAT_AVAILABLE = False

try:
    from tools import TOOLS
    _TOOLS_AVAILABLE = True
except ImportError:
    TOOLS = {}  # type: ignore[assignment]
    _TOOLS_AVAILABLE = False

__all__ = [
    "OutputCapture",
    "InputHandler",
]
# Re-exported for backward compat - prefer agent_context_utils
from agent_context_utils import (
    get_context_file_by_parent_ppid,
    list_context_files,
    preview_context,
)


# ── Output capture ─────────────────────────────────────────────────────────


class OutputCapture:
    """Captures stdout by swapping the process-global sys.stdout.

    DESIGN DECISION — the agent runs SINGLE-THREADED (P7-B1-32, reviewed and
    accepted as by-design, not a defect):
      All agent execution (agent loop, REPL block exec, LLM invoke, spawn child
      turns) runs on the MAIN thread. The ONLY off-main threads are:
        - the stdin input reader (agent_input.py, daemon thread)
        - the A2A accept loop + per-connection handler threads (a2a_server.py,
          daemon threads)
        - signal-based timeouts (ITIMER_REAL / SIGALRM), which are main-thread
          by nature.
      Because agent execution is confined to the main thread, swapping the
      PROCESS-GLOBAL sys.stdout/sys.stderr (here, and the block-executor's
      OutputCapture in rlm/kernel_types.py) does NOT race agent execution — only
      one thread ever captures agent output at a time. self._lock guards this
      capture object's own state, not the global swap.
      Contract for the daemon threads: they must NOT rely on print()/sys.stdout
      for agent-visible output — they use os.write(1, ...) or pin their own
      stdout via os.dup(1) at thread entry (see kernel_types.py OutputCapture).
      A2A inbound queries are enqueued to the main loop, so the agent work they
      trigger still runs single-threaded.
      Residual (accepted): a daemon thread that printed during a capture window
      would land in the wrong stream; the contract above prevents this. Adopting
      per-thread capture (threading.local or fd dup) would touch every writer
      path incl. streaming/a2a — deliberately not done. See wiki topic
      "single-threaded-execution-model".

    Used for A2A response handling to capture tool output without polluting the
    main stdout stream.
    """

    def __init__(self):
        self.captured: list[str] = []
        self._original_stdout: object | None = None
        self._buffer: StringIO | None = None
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self._original_stdout = sys.stdout
            self._buffer = StringIO()
            sys.stdout = self._buffer
            self.captured.append("")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with self._lock:
            if self._buffer is not None:
                self.captured.append(self._buffer.getvalue())
                sys.stdout = self._original_stdout
                self._buffer = None
            return False

    def get_last(self) -> str:
        with self._lock:
            return self.captured[-1] if self.captured else ""



# ── InputHandler ──────────────────────────────────────────────────────────


class InputHandler:
    """Manage stdin thread, signal handling, and input dispatch for TauErgon.

    Handles interactive input with multiline support ('#' prefix for blocks),
    signal handling (Ctrl+C for interrupt, double Ctrl+C for force exit),
    input queue management, and heartbeat processing.
    """

    def __init__(self, agent):
        self.agent = agent
        self.input_queue: queue.Queue = agent.input_queue
        self.input_thread: threading.Thread | None = None
        self._input_thread_stop = threading.Event()

    # --- Signal handling ---
    def _signal_handler(self, _signum: int, _frame) -> None:
        """Handle SIGINT (Ctrl+C): first press interrupts, second forces exit."""
        if AgentLifecycle.is_exit_requested():
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise SystemExit(1)
        elif AgentLifecycle.is_interrupted():
            AgentLifecycle.set_exit_requested(True)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            force_exit_message()
            raise SystemExit(0)
        else:
            AgentLifecycle.set_interrupted(True)
            interrupted_message()

    # --- Input thread ---
    def _start_input_thread(self) -> None:
        """Spawn daemon thread for reading stdin with multiline block support.

        Input Prefix Protocol:
        ======================
        Outside multiline block:
          - '#'     Start multiline block (content until 2+ blank lines)
          - '#!'    Start multiline block (alternative syntax, same as #)
          - '!'     Shell command (execute via subprocess)
          - '+'     Steering control (when turn is active)
          - '/'     Slash command (dispatch to command handler)

        Inside multiline block:
          - '#!'    Continue block (prefix stripped)
          - '#'     Continue block (prefix stripped)
          - '#+'    Break multiline and route steering
          - '#/'    Break multiline and execute as command
          - 2+ blank lines  Submit the accumulated block

        Steering commands (after + or #+):
          - 'stop'       Gracefully terminate current turn
          - 'redirect X' Redirect to new task, clearing context
          - 'status'     Display agent status
          - Other text   Inject as user message

        See tests/test_input_protocol.py for comprehensive test coverage.
        """

        def _route_steering(payload: str) -> None:
            """Route a steering message to the control queue.

            If no turn is active, the message goes to the input queue as regular input.
            
            Supports steering commands:
            - "stop" - Gracefully terminate the current turn
            - "redirect <task>" - Redirect to a new task, clearing context
            - "status" - Display current agent status
            - Any other text - Inject as user message into the conversation
            """
            if not self.agent._turn_active:
                self.input_queue.put(InputMessage.from_interactive(f"+{payload}"))
                return

            cq = self.agent.control_queue

            def _send(ok: bool) -> None:
                """Warn if the control queue rejected the steering message."""
                if not ok:
                    warning("[Control queue full — steering message dropped]")

            if payload == "stop":
                _send(cq.send_terminate(graceful=True, source="user"))
                sys.stdout.write("\r\033[K[Injected: stop]\n")
                try:
                    from agent_audit_bridge import console_info
                    console_info("[Injected: stop]")
                except Exception:
                    pass
            elif payload.startswith("redirect "):
                _send(cq.send_redirect(payload[9:]))
                sys.stdout.write(f"\r\033[K[Injected: redirect {payload[9:40]}...]\n")
                try:
                    from agent_audit_bridge import console_info
                    console_info(f"[Injected: redirect {payload[9:40]}...]")
                except Exception:
                    pass
            elif payload == "status":
                _send(cq.send_status())
                sys.stdout.write("\r\033[K[Injected: status]\n")
                try:
                    from agent_audit_bridge import console_info
                    console_info("[Injected: status]")
                except Exception:
                    pass
            else:
                _send(cq.send_inject(payload))
                preview = payload[:50] + ("..." if len(payload) > 50 else "")
                sys.stdout.write(f"\r\033[K[Injected: {preview}]\n")
                try:
                    from agent_audit_bridge import console_info
                    console_info(f"[Injected: {preview}]")
                except Exception:
                    pass
            sys.stdout.flush()

        def input_handler():
            buffer: list[str] = []
            active = False

            # H19: Cache stdin fd to avoid OSError if stdin is replaced
            try:
                _stdin_fd = sys.stdin.fileno()
            except (AttributeError, OSError):
                _stdin_fd = None
            blanks = 0

            while (
                not AgentLifecycle.is_exit_requested() and not self._input_thread_stop.is_set()
            ):
                try:
                    ready, _, _ = select.select([_stdin_fd], [], [], 0.1) if _stdin_fd is not None else ([], [], [])
                    if ready:
                        line = sys.stdin.readline()
                        if not line:
                            break
                        content = line.rstrip("\n")

                        if active:
                            # Inside multiline block
                            if content.startswith("#+"):
                                # Break multiline and route steering
                                _route_steering(content[2:].strip())
                                buffer, blanks, active = [], 0, False
                            elif content.startswith("#/"):
                                # Break multiline and queue as command (P7-B1-34:
                                # was agent._handle_command() ON THIS INPUT THREAD
                                # mid-turn -> races main-loop state; now queued
                                # like plain '/' input, executed by the main loop).
                                self.input_queue.put(
                                    InputMessage.from_interactive(
                                        "/" + content[2:].strip()
                                    )
                                )
                                buffer, blanks, active = [], 0, False
                            elif content.startswith("#!") or content.startswith("#"):
                                # Continue block, strip the prefix
                                blanks = 0
                                prefix_len = 2 if content.startswith("#!") else 1
                                buffer.append(content[prefix_len:])
                            elif content == "":
                                blanks += 1
                                if blanks >= 2:
                                    # Two blank lines end the block
                                    self.input_queue.put(
                                        InputMessage.from_interactive("\n".join(buffer))
                                    )
                                    buffer, blanks, active = [], 0, False
                            else:
                                blanks = 0
                                buffer.append(content)
                        elif content.startswith("#") and not content.startswith("#/"):
                            # Start multiline block (# or #!, but not #/ which needs turn active)
                            # Note: #/ is handled as a command only when turn is active
                            active, buffer = True, [content[1:]]  # Strip the #
                        elif content.startswith("+"):
                            # Steering command (only works when turn is active)
                            _route_steering(content[1:].strip())
                        elif content.startswith("!"):
                            # Shell command - queue for processing
                            self.input_queue.put(InputMessage.from_interactive(content))
                        elif content.startswith("/"):
                            # Slash command - queue for processing
                            self.input_queue.put(InputMessage.from_interactive(content))
                        elif content:
                            # Regular input
                            self.input_queue.put(InputMessage.from_interactive(content))
                except (EOFError, OSError, KeyboardInterrupt):
                    break
                except (RuntimeError, ValueError, TypeError):
                    break

        self.input_thread = threading.Thread(target=input_handler, daemon=True)
        self.input_thread.start()

    # --- Main run loop ---
    def run(
        self,
        inputs: list[str] | None = None,
        a2a_server=None,
        keep_alive: bool = False,
        interactive: bool = True,
    ):
        """Run the main input handling loop."""
        signal.signal(signal.SIGINT, self._signal_handler)

        if interactive:
            self._start_input_thread()
            time.sleep(0.05)

        need_prompt = True

        while not AgentLifecycle.is_exit_requested():
            try:

                try:
                    msg = self.input_queue.get(timeout=0.1)
                    need_prompt = True
                except queue.Empty:
                    stdin_done = self.input_thread and not self.input_thread.is_alive()

                    if not interactive and not keep_alive:
                        self._exit()
                        return
                    if not keep_alive and stdin_done:
                        self._exit()
                        return

                    # Heartbeat check (only while waiting for input)
                    if hasattr(self.agent, '_heartbeat') and self.agent._heartbeat is not None:
                        hb_result = self.agent._heartbeat.run_heartbeat()
                        if hb_result:
                            self._handle_heartbeat_result(hb_result)
                            continue

                    # One-shot prompt: ensure cursor is on a fresh line, then
                    # display rlm>>>. The \n handles prior output that didn't end
                    # with \n (cursor mid-line). At worst one extra blank line.
                    if need_prompt:
                        sys.stdout.write("\n")
                        # Show context size and [RUNNING] indicator
                        ctx_len = len(self.agent.context) if hasattr(self.agent, 'context') else 0
                        if self.agent._turn_active:
                            prompt_text = f"rlm[{ctx_len}]>>> [RUNNING] "
                        else:
                            prompt_text = f"rlm[{ctx_len}]>>> "
                        prompt(prompt_text)
                        sys.stdout.flush()
                        need_prompt = False
                    continue

                if not msg.content.strip():
                    need_prompt = True
                    continue

                AgentLifecycle.set_interrupted(False)
                if hasattr(self.agent, '_heartbeat') and self.agent._heartbeat is not None:
                    self.agent._heartbeat.touch_activity()
                user_echo(msg.content)

                if msg.source == "a2a":
                    with OutputCapture() as capture:
                        result = self._process_input(msg)
                    captured = capture.get_last()
                    echo_no_newline(captured)
                    if captured and not captured.endswith("\n"):
                        sys.stdout.write("\n")

                    a2a_response = (
                        result if result else (captured if captured else "No response")
                    )
                    self.agent._pending_a2a_responses[msg.request_id] = (  # pylint: disable=W0212
                        {
                            "type": "response",
                            "id": msg.request_id,
                            "query": msg.content,
                            "response": a2a_response,
                            "context_length": len(self.agent.context),
                        }
                    )
                else:
                    result = self._process_input(msg)

                if result:
                    # Print answer explicitly with green header/footer
                    from agent_console.templates import answer_display
                    answer_display(result)
                # Turn summarization (after answer display, before prompt)
                if result is not None and msg.source in ("interactive", "command_line", "a2a", "system"):
                    try:
                        from rlm.turn_summary import generate_turn_summary
                        generate_turn_summary(self.agent)
                    except Exception:
                        pass
                need_prompt = True

            except KeyboardInterrupt:
                print_agent_exit_summary(self.agent)
                interrupted_message()
                self._input_thread_stop.set()
                # M-A3: Join input thread before closing stdin
                if self.input_thread and self.input_thread.is_alive():
                    self.input_thread.join(timeout=2.0)
                try:
                    sys.stdin.close()
                except (OSError, IOError):
                    pass
                break
            except EOFError:
                if keep_alive:
                    continue
                print_agent_exit_summary(self.agent)
                interrupted_message()
                break

        # Exit cleanup: delegate to _exit() to avoid duplicating close_turn/save logic
        self._exit()

    # --- Input processing ---
    def _process_input(self, msg: InputMessage) -> str | None:
        """Process an input message: regular, /command, or !shell."""
        if AgentLifecycle.is_interrupted():
            return None

        if not msg.content.strip():
            return None

        content_stripped = msg.content.strip()
        if content_stripped.startswith("/"):
            cmd = content_stripped[1:].strip()
            cmd_name = cmd.split()[0] if cmd else ""
            self.agent._handle_command(
                cmd_name, content_stripped, msg
            )  # pylint: disable=W0212
            return None
        elif content_stripped.startswith("!"):
            command = content_stripped[1:].strip()
            if not command:
                shell_command_usage()
                return None
            # Execute shell command via subprocess (tools deleted in RLM mode)
            try:
                import subprocess
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True, timeout=60
                )
                output = result.stdout or result.stderr or "(no output)"
                output = output[:8192]  # Truncate long output
                status(output)
            except subprocess.TimeoutExpired:
                error("Shell command timed out after 60 seconds")
            except Exception as e:
                error(f"Shell command failed: {e}")
            return None

        try:
            # Set A2A request ID for chunk emission during tool execution
            if msg.source == "a2a":
                self.agent._current_a2a_request_id = msg.request_id  # pylint: disable=W0212
            try:
                answer_content = self.agent.invoke(msg.content)

                if msg.source in ["interactive", "command_line", "system", "a2a"]:
                    print_context_status(self.agent.get_status())

                self.agent.context.save_to_file(self.agent._session.context_file)
                if hasattr(self.agent, '_heartbeat') and self.agent._heartbeat is not None:
                    self.agent._heartbeat.touch_activity()


                return answer_content
            finally:
                # Always clear A2A request ID after processing (success or error)
                if msg.source == "a2a":
                    self.agent._current_a2a_request_id = None  # pylint: disable=W0212
        except Exception as e:
            # Catch ALL exceptions (not just RuntimeError/ValueError/TypeError/OSError)
            # so that ANY crash during tool invocation is logged and handled gracefully.
            if msg.source == "a2a":
                self.agent._current_a2a_request_id = None  # pylint: disable=W0212
                # M-A6: Send error response to A2A client
                if hasattr(self.agent, "_pending_a2a_responses") and msg.request_id:
                    self.agent._pending_a2a_responses[msg.request_id] = (  # pylint: disable=W0212
                        {"type": "error", "id": msg.request_id, "message": f"{type(e).__name__}: {e}"}
                    )
            error(f"invoke failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None

    # --- Heartbeat handling ---
    def _handle_heartbeat_result(self, result: Any) -> None:
        """Handle structured heartbeat result.

        A ``HeartbeatResponse`` with ``action="prompt"`` auto-injects the task
        into the input queue.  ``action="no_action"`` is displayed silently.
        ``None`` means the fork failed or validation was exhausted — display
        a brief warning and move on.
        """
        # CRITICAL: reset idle timer BEFORE any branching.
        if hasattr(self.agent, '_heartbeat') and self.agent._heartbeat is not None:
            self.agent._heartbeat.touch_activity()

        if result is None:
            warning("[HEARTBEAT] Fork returned no valid response — skipping")
            return

        if result.action == "prompt":
            task = result.task or ""
            blank_line()
            status("[HEARTBEAT]")
            warning(f"Executing: {task}")
            blank_line()
            # Auto-execute: inject into input queue.
            # The task will be processed normally, creating its own context entries.
            self.input_queue.put(InputMessage.from_interactive(task))
        else:
            # action == "no_action" — silent, no context pollution.
            blank_line()
            status("[HEARTBEAT] No action needed.")
            blank_line()

    # --- Exit ---
    def _exit(self):
        """Graceful shutdown: close turn, force-save context, stop A2A, exit."""
        self.agent.context.close_turn("[Session ended]")
        if hasattr(self.agent, "a2a_server") and self.agent.a2a_server:
            self.agent.a2a_server.stop()
        try:
            self.agent.context.save_to_file(self.agent._session.context_file, force=True)
        except Exception as exc:  # pragma: no cover
            error(f"Failed to save context on exit: {exc}")
        try:
            print_agent_exit_summary(self.agent)
        except Exception as exc:  # pragma: no cover
            error(f"Unable to display exit summary: {exc}")
        # Flush and close audit writer to ensure all buffered data is written
        if hasattr(self.agent, "_session"):
            self.agent._session.audit_writer.flush()
        sys.exit(0)
