"""RLM Persistent Python Kernel

This module implements the persistent Python REPL kernel that serves as
the primary interface for the RLM model. Python state (variables, imports,
functions) persists across turns, allowing the model to maintain working
context without bloating the conversation history.

Trust Model
-----------
RLM operates on TRUST — no security sandbox. The model has full Python
access by design. See docs/trust-model.md for rationale.

Architecture
------------
The kernel maintains a Python namespace where:
- Standard library is always available
- All installed packages are accessible
- `spawn()` function is available for spawning child agents (unified API)
- `answer` dict is available for setting the final answer
- Output is captured and limited to prevent context explosion

Key Classes
-----------
PythonKernel : Main kernel class managing execution state
REPLResult   : Result from a single REPL execution

Execution Flow
--------------
1. Model generates response with Python code blocks
2. Code extracted from response
3. Executed in persistent kernel namespace
4. Output captured (stdout/stderr) in full; context copy clamped to max_output_chars
5. Result returned to agent loop
6. If answer["ready"] is True, turn ends

Resource Management
-------------------
- Output size limit
- State size limit to prevent memory bloat
- Bash command timeout

Example
-------
    kernel = PythonKernel()
    result = kernel.execute("x = 1 + 2\\nprint(x)")
    print(result.output)  # "3"
    print(result.success)  # True
    print(kernel.namespace["x"])  # 3

See Also
--------
- docs/trust-model.md: Trust model rationale
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import signal
import time
from pathlib import Path
from typing import Any

__all__ = ["PythonKernel", "REPLResult"]


# ── Subprocess safety: prevent terminal stdin inheritance deadlock ──
# Child processes inherit the terminal as stdin by default. Commands
# like 'wc -l' (no file arg) block forever waiting for terminal input.
# This patch sets stdin=DEVNULL as a soft default; explicit stdin= overrides.
import subprocess as _sp

_orig_subprocess_run = _sp.run
_orig_subprocess_popen = _sp.Popen

def _safe_subprocess_run(*a, **kw):
    if kw.get('stdin') is None and 'input' not in kw:
        kw['stdin'] = _sp.DEVNULL
    return _orig_subprocess_run(*a, **kw)

def _safe_subprocess_popen(*a, **kw):
    if kw.get('stdin') is None:
        kw['stdin'] = _sp.DEVNULL
    return _orig_subprocess_popen(*a, **kw)

def _apply_subprocess_patch():
    """Apply stdin=DEVNULL patch to subprocess (scoped to kernel execution)."""
    if not getattr(_sp, '_rlm_patched', False):
        _sp.run = _safe_subprocess_run
        _sp.Popen = _safe_subprocess_popen
        _sp._rlm_patched = True

def _restore_subprocess_patch():
    """Restore original subprocess functions."""
    if getattr(_sp, '_rlm_patched', False):
        _sp.run = _orig_subprocess_run
        _sp.Popen = _orig_subprocess_popen
        _sp._rlm_patched = False

# ── REPL execution timeout: SIGALRM-based, single-threaded ───────────────
# Python blocks run via exec() in the main thread with no inherent
# timeout. A blocking call (infinite loop, socket.recv, input()) would
# freeze the REPL permanently. SIGALRM interrupts any blocking C call
# and raises _REPLTimeout, returning control to the calling agent.
from rlm.kernel_types import REPLResult, _OutputCapture, _REPLTimeout, _alarm_handler
from rlm.block_executor import (
    BlockExecutor,
    reload_stale_modules,
    record_new_module_mtimes,
)
from rlm.namespace import build_namespace


class PythonKernel:
    _NESTED_MARKERS = frozenset({"<string>", "<unknown>", "<exec>", "<stdin>", ""})
    """Persistent Python REPL kernel.

    Maintains execution state across turns. Variables, imports, and
    functions defined in one turn are available in subsequent turns.

    Execution is synchronous and single-threaded. Python blocks have a
    SIGALRM-based timeout (default 180s). Bash blocks use subprocess timeout.
    Code runs to completion or raises an exception.

    Attributes:
        namespace: The Python execution namespace (dict)
        max_output_chars: Maximum output characters per execution
        bash_timeout_seconds: Timeout for bash commands
    """

    # Magic command patterns — %cd for directory changes
    _CD_MAGIC_RE = re.compile(r"^%cd\s+(.+)$", re.MULTILINE)
    # P7-B1-01: allow leading blank/whitespace-only lines before %timeout —
    # previously the magic line was only recognized at absolute position 0,
    # so a blank first line made it exec as Python (SyntaxError) and the
    # timeout silently did not apply. It must still be the first non-blank
    # line. (Matched via .match(), so pattern starts at pos 0.)
    _TIMEOUT_MAGIC_RE = re.compile(r"(?:[ \t]*(?:\r?\n))*[ \t]*%timeout\s+(\d+)[ \t]*(?:\r?\n)?")

    # Helpful suggestions for common error types
    _ERROR_HINTS: dict[str, str] = {
        "NameError": (
            ". To fix: Check variable names, ensure imports are present, "
            "use 'dir()' to list available names in the namespace."
        ),
        "SyntaxError": (
            ". To fix: Check for missing colons, parentheses, "
            "or indentation errors. Python is indentation-sensitive."
        ),
        "AttributeError": (
            ". To fix: Check object types with type(var), "
            "ensure the attribute/method exists on that object."
        ),
        "TypeError": (
            ". To fix: Check argument types, ensure functions are called "
            "with the correct number and type of arguments."
        ),
        "ImportError": (
            ". To fix: Check module name spelling, "
            "ensure the package is installed (pip install <package>)."
        ),
        "ModuleNotFoundError": (
            ". To fix: Check module name spelling, "
            "ensure the package is installed (pip install <package>)."
        ),
        "ValueError": (
            ". To fix: Check argument values. Print the offending variable "
            "to inspect its actual value."
        ),
        "IndexError": (
            ". To fix: Index out of range. Check len() of the collection "
            "before accessing by index."
        ),
        "KeyError": (
            ". To fix: Dict key missing. Use .get(key) or check keys() "
            "first."
        ),
        "FileNotFoundError": (
            ". To fix: Path does not exist. Use Path.exists() or glob "
            "to verify the path."
        ),
        "PermissionError": (
            ". To fix: No permission for this operation. Check file mode "
            "or use a different path."
        ),
        "RecursionError": (
            ". To fix: Infinite recursion. Check your base case condition."
        ),
        "MemoryError": (
            ". To fix: Object too large. Process in chunks or del large "
            "variables to free memory."
        ),
        "UnicodeDecodeError": (
            ". To fix: File is not valid UTF-8. Try specifying encoding "
            "parameter (e.g. encoding='latin-1') or errors='replace'."
        ),
    }

    def __init__(
        self,
        max_output_chars: int = 8192,
        max_state_size_mb: float = 100.0,  # Deprecated: state size checking removed
        bash_timeout_seconds: float = 180.0,
        python_timeout_seconds: float = 180.0,
        rlm_config: Any = None,
        agent: Any = None,
    ):
        """Initialize kernel with configuration.

        Args:
            max_output_chars: Maximum output characters per execution
            bash_timeout_seconds: Timeout for bash commands
            python_timeout_seconds: Timeout for Python block execution (SIGALRM)
            rlm_config: RLM configuration for spawn() callable (optional)
            agent: Parent agent instance for spawn() spawning (optional)
        """
        self.max_output_chars = max_output_chars

        self.max_state_size_mb = max_state_size_mb
        self.bash_timeout_seconds = bash_timeout_seconds
        self.python_timeout_seconds = python_timeout_seconds
        self._rlm_config = rlm_config
        self._agent = agent

        # Initialize namespace with safe builtins and pre-loaded items
        self.namespace: dict[str, Any] = self._create_namespace()
        self.total_executions: int = 0
        # Monotonic per-block sequence: one increment per executed block
        # (python AND bash), shared with BlockExecutor via next_seq().
        self.block_seq: int = 0
        self._working_dir: Path = Path.cwd()
        # Auto-reload bookkeeping: {module_name: last_mtime}. Lives on the
        # kernel (not BlockExecutor) so it survives the per-call executor.
        self._module_mtimes: dict[str, float] = {}

    def _create_namespace(self) -> dict[str, Any]:
        """Create initial namespace with capabilities.

        Delegates to rlm.namespace.build_namespace() which constructs
        the full capability surface (spawn, host_request, skills, etc.).
        """
        return build_namespace(self._agent, self._rlm_config)

    def _next_block_seq(self) -> int:
        """Advance and return the monotonic block sequence number.

        Single source of truth for N so both the legacy execute() path and
        BlockExecutor share the same sequence.
        """
        self.block_seq += 1
        return self.block_seq

    def _store_block_code(self, seq: int, code: str) -> None:
        """Store the block source as a list of lines under _code{seq}."""
        self.namespace[f"_code{seq}"] = code.splitlines()

    # RAM guard for _output{N}: the namespace is never sent to the LLM, so the
    # FULL block output survives there (context copy is clamped separately in
    # BlockExecutor). Cap only guards against multi-GB strings eating RAM.
    _OUTPUT_NS_RAM_GUARD = 1_000_000

    def _clamp_for_context(self, text: str, seq: int) -> str:
        """Clamp the context-facing copy of a block's output to max_output_chars.

        The FULL output lives in _output{seq} (namespace, never sent to the
        LLM); this clamped copy carries a hint so the agent can slice the
        remainder. Mirrors BlockExecutor._clamp_for_context.
        """
        if len(text) <= self.max_output_chars:
            return text
        return (
            text[:self.max_output_chars]
            + f"\n... [output truncated; full output in _output{seq}]"
        )

    def _store_block_output(self, seq: int, output: str) -> None:
        """Store captured output under _output{seq} (full, RAM-guarded).

        Stored on failure too. The context-facing copy is clamped to
        max_output_chars by the caller; this namespace copy stays complete so
        the agent can slice _output{N} for the truncated remainder.
        """
        if len(output) > self._OUTPUT_NS_RAM_GUARD:
            output = output[:self._OUTPUT_NS_RAM_GUARD] + "\n... [RAM guard: output truncated at 1MB]"
        self.namespace[f"_output{seq}"] = output

    @staticmethod
    def _tb_lineno(e: BaseException, total: int) -> int:
        """Line number of the deepest traceback frame belonging to the block itself."""
        tb = getattr(e, "__traceback__", None)
        if tb is None:
            return 0
        import traceback as _tb
        best = 0
        try:
            frames = _tb.extract_tb(tb)
        except Exception:
            return 0
        for fr in frames:
            if fr.filename in PythonKernel._NESTED_MARKERS and 1 <= fr.lineno <= total:
                best = fr.lineno
        return best

    _SEVERITY = {
        "TimeoutError": "RETRY",
        "MemoryError": "FATAL",
        "RecursionError": "FATAL",
    }

    def _format_execution_error(self, e: Exception, code: str, prefix: str = "", error_count: int = 1, seq: int = 0) -> str:
        """Format a REPL execution error into a directive message.

        Detects whether the error is in the executed code or in nested code
        (ast.parse, compile, eval, etc.) and produces a focused, actionable
        error message with severity, trimmed context, and fix strategy.

        Args:
            e: The exception that was raised.
            code: The source code that was being executed.
            prefix: Optional prefix (e.g. "Block 1 of 3, ") for multi-block context.
            error_count: Consecutive error number (1-based).
            seq: Block sequence number N (0 = unknown); names _code{N} in the hint.

        Returns:
            Formatted error message string.
        """
        error_type = type(e).__name__
        severity = self._SEVERITY.get(error_type, "RECOVERABLE")
        lines = code.splitlines()
        total = len(lines)
        el = getattr(e, "lineno", 0) or 0
        if not (1 <= el <= total):
            # Runtime exceptions carry no .lineno — recover it from the
            # traceback frame that belongs to the executed block.
            el = self._tb_lineno(e, total) or el
        e_text = getattr(e, "text", None)
        e_filename = getattr(e, "filename", None) or ""

        # Default-safe: assume NOT nested unless positive evidence
        is_nested = (
            (e_filename and e_filename not in self._NESTED_MARKERS)
            or (el > total)
            or (e_text is not None and el <= total and el >= 1
                and lines[el - 1].strip() != e_text.strip())
        )

        # Header with error count and severity
        header = f"{prefix}{error_type} | {severity} | error #{error_count}"

        if is_nested:
            src_label = e_filename if e_filename not in self._NESTED_MARKERS else "code processed by your script"
            error_msg = f"{header}\n  In {src_label} (line {el}):"
            if e_text:
                error_msg += f"\n    {e_text.rstrip()}"
            error_msg += f"\n  {e}"
            error_msg += f"\n  NOTE: This error is NOT in your executed code ({total} lines)."
            error_msg += "\n  Fix the source file or the string you passed to parse/compile."
        else:
            # Build trimmed code context: ±3 lines around error
            if total > 8 and el > 0:
                lo = max(1, el - 3)
                hi = min(total, el + 3)
                fl = []
                if lo > 1:
                    fl.append(f"  ... (lines 1-{lo-1}) ...")
                for i in range(lo - 1, hi):
                    ln = lines[i]
                    fl.append(f"{i+1:2d} | {ln}")
                    if i + 1 == el:
                        off = getattr(e, "offset", 1) or 1
                        fl.append(" " * (len(f"{i+1:2d} | ")) + " " * max(0, off - 1) + "^")
                if hi < total:
                    fl.append(f"  ... (lines {hi+1}-{total}) ...")
            else:
                fl = []
                for i, ln in enumerate(lines):
                    fl.append(f"{i+1:2d} | {ln}")
                    if i + 1 == el:
                        off = getattr(e, "offset", 1) or 1
                        fl.append(" " * (len(f"{i+1:2d} | ")) + " " * max(0, off - 1) + "^")

            error_msg = f"{header}\n" + "\n".join(fl)
            error_msg += f"\n  {e}"

        # Directive: fix, don't re-do
        if severity == "RECOVERABLE":
            if seq >= 1 and not is_nested and 1 <= el <= len(lines):
                error_msg += f"\n  _code{seq} holds your {len(lines)} lines; line {el} is the failing one."
                error_msg += f"\n  Lines 1-{el-1} ALREADY RAN - their side-effects persist; do NOT re-run them."
                for j in (el - 1, el, el + 1):
                    if 1 <= j <= len(lines):
                        tag = ">>" if j == el else "  "
                        error_msg += f"\n  {tag} _code{seq}[{j-1}] = {lines[j-1]!r}"
                error_msg += (f"\n  Fix in place then run ONLY the tail: "
                              f"_code{seq}[{el-1}] = \"<new line>\"; exec(chr(10).join(_code{seq}[{el-1}:]))")
            else:
                error_msg += "\n  Fix only the affected line(s). Do not re-emit the full block."
        elif severity == "RETRY":
            error_msg += "\n  Transient error. Retry the same code."
        else:
            error_msg += "\n  FATAL: This approach will not work. Change strategy."

        return error_msg


    def execute(self, code: str, error_count: int = 1) -> REPLResult:
        """Execute Python code in the persistent kernel namespace.

        Executes the given Python code in the kernel's namespace, capturing
        stdout/stderr output. Execution is synchronous with a SIGALRM-based
        timeout (default 180s). On timeout, partial output is preserved.

        Args:
            code: Python code to execute. May contain %cd magic.

        Returns:
            REPLResult containing:
            - success: True if execution completed without exceptions
            - output: Captured stdout (clamped to max_output_chars for context; full copy in _output{seq})
            - error: Error message if execution failed
            - exception_type: Type of exception if one occurred
            - duration_ms: Execution time in milliseconds
            - answer_ready: True if answer["ready"] was set to True
            - answer_content: Current value of answer["content"]

        Raises:
        """
        self.total_executions += 1
        start_time = time.monotonic()


        # Check for magic commands first
        code, bash_output, bash_error, timeout_override = self._handle_magic_commands(code)

        # One increment per executed block; store source BEFORE exec so it
        # exists on success too. Line numbers match the error's "line N".
        seq = self._next_block_seq()
        self._store_block_code(seq, code)

        if bash_error:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._store_block_output(seq, bash_output)
            return REPLResult(
                success=False,
                output=bash_output,
                error=bash_error,
                duration_ms=duration_ms,
                code_seq=seq,
            )

        # Execute code with SIGALRM timeout (single-threaded)
        _eff_timeout = timeout_override if timeout_override is not None else self.python_timeout_seconds
        _old_sig = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, _eff_timeout)
        _apply_subprocess_patch()
        capture = None  # R3: bind before try so a timeout during reload can't NameError
        try:
            with _OutputCapture(self.max_output_chars) as capture:
                # R3: reload runs INSIDE the capture so a SIGALRM timeout during
                # reload has a bound capture in the except handlers below.
                reload_stale_modules(self._module_mtimes)
                exec(code, self.namespace)
            record_new_module_mtimes(self._module_mtimes)
        except _REPLTimeout:
            duration_ms = (time.monotonic() - start_time) * 1000
            _partial = capture.get_output() if capture else ""
            self._store_block_output(seq, _partial)
            return REPLResult(
                success=False,
                output=self._clamp_for_context(_partial, seq),
                error=f"Timed out after {_eff_timeout}s. For longer work use tmux. See EXECUTION TIMEOUT in AGENT_RLM.md.",
                exception_type="_REPLTimeout",
                duration_ms=duration_ms,
                code_seq=seq,
            )
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._store_block_output(seq, capture.get_output() if capture else "")
            # C1: Re-inject answer dict if model deleted it
            if "answer" not in self.namespace:
                self.namespace["answer"] = {"content": "", "ready": False}
            error_type = type(e).__name__
            error_msg = self._format_execution_error(e, code, error_count=error_count, seq=seq)
            # Log to audit
            try:
                from agent_audit_bridge import console_info
                console_info(f"[REPL EXECUTE] success=False error={error_msg[:200]!r}")
            except Exception:
                pass
            return REPLResult(
                success=False,
                output="",
                error=error_msg,
                exception_type=error_type,
                duration_ms=duration_ms,
                code_seq=seq,
            )
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, _old_sig)
            _restore_subprocess_patch()

        # C1: Re-inject answer dict if model deleted it
        if "answer" not in self.namespace:
            self.namespace["answer"] = {"content": "", "ready": False}
        # Get output
        output = capture.get_output()
        stderr_output = capture.get_error_output()

        # Check answer state
        answer = self._get_answer()
        answer_ready = answer.get("ready", False) is True
        answer_content = answer.get("content", "")

        duration_ms = (time.monotonic() - start_time) * 1000

        # Combine output, bash output, and stderr
        parts = [bash_output, output, stderr_output]
        full_output = "\n".join(p for p in parts if p)
        self._store_block_output(seq, full_output)
        # Context copy is clamped (with an _output{seq} hint); the full copy
        # lives in the namespace so the agent can slice the remainder.
        ctx_output = self._clamp_for_context(full_output, seq)

        # Log to audit
        try:
            from agent_audit_bridge import console_info
            console_info(f"[REPL EXECUTE] success=True output={full_output[:200]!r}")
        except Exception:
            pass

        return REPLResult(
            success=True,
            output=ctx_output,
            duration_ms=duration_ms,
            answer_ready=answer_ready,
            answer_content=answer_content,
            code_seq=seq,
        )

    def execute_blocks(self, blocks: list["CodeBlock"], error_count: int = 1) -> REPLResult:
        """Execute multiple code blocks in sequence.

        Delegates to BlockExecutor which handles bash/python execution,
        magic commands, and error recovery.
        """
        def _fmt_err(e, code, prefix="", **kw):
            kw.setdefault("error_count", error_count)
            return self._format_execution_error(e, code, prefix=prefix, **kw)

        executor = BlockExecutor(
            namespace=self.namespace,
            python_timeout=self.python_timeout_seconds,
            bash_timeout=self.bash_timeout_seconds,
            max_output_chars=self.max_output_chars,
            handle_magic=self._handle_magic_commands,
            format_error=_fmt_err,
            get_answer=self._get_answer,
            # Shared monotonic sequence: BlockExecutor draws N from the kernel
            # so the legacy execute() path and this path form ONE sequence.
            next_seq=self._next_block_seq,
            store_code=self._store_block_code,
            store_output=self._store_block_output,
            module_mtimes=self._module_mtimes,
        )
        return executor.execute(blocks)

    def _handle_magic_commands(self, code: str) -> tuple[str, str, str, int | None]:
        """Process magic commands (%cd, %timeout).

        Handles %cd magic (can appear anywhere in code).
        Handles %timeout N on the first line (sets per-block timeout).
        Returns (processed_code, "", bash_error, timeout_override).
        """
        bash_error = ""
        timeout_override: int | None = None

        # %timeout: first line only, valid range 1-3600
        m = self._TIMEOUT_MAGIC_RE.match(code)
        if m:
            val = int(m.group(1))
            if val < 1 or val > 3600:
                return "", "", f"%timeout must be between 1 and 3600 seconds (got {val})", None
            timeout_override = val
            code = code[m.end():]

        def _cd_repl(m: re.Match) -> str:
            cd_path = m.group(1).strip().strip(chr(39) + chr(34))
            try:
                self._working_dir = Path(cd_path).resolve()
                os.chdir(self._working_dir)
                self.namespace["__cwd__"] = str(self._working_dir)
            except Exception as e:
                nonlocal bash_error
                bash_error += f"cd error: {type(e).__name__}: {e}\n"
            return ""

        code = self._CD_MAGIC_RE.sub(_cd_repl, code)

        return code.strip(), "", bash_error, timeout_override
    def _execute_bash(self, bash_code: str) -> tuple[str, str]:
        """Execute bash command.

        Args:
            bash_code: Bash command to execute

        Returns:
            Tuple of (stdout, stderr)
        """
        try:
            proc = subprocess.Popen(
                bash_code,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(self._working_dir),
            )
            try:
                stdout_b, stderr_b = proc.communicate(timeout=self.bash_timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait()
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()
                return "", (
                    f"Bash timed out after {self.bash_timeout_seconds}s. "
                    f"Process group killed. For longer work use tmux. "
                    f"See EXECUTION TIMEOUT in AGENT_RLM.md."
                )
            output = stdout_b.decode('utf-8', errors='replace') if stdout_b else ''
            error = stderr_b.decode('utf-8', errors='replace') if stderr_b else ''
            # Return FULL output; context clamping (with the _output{N} hint)
            # is the caller's job so the namespace copy stays complete.
            return output, error
        except Exception as e:
            return "", f"Bash error: {type(e).__name__}: {e}"

    def clear_state(self) -> None:
        """Clear kernel state (reset namespace)."""
        self.namespace = self._create_namespace()
        self.total_executions = 0
        self.block_seq = 0
        self._working_dir = Path.cwd()


    def close(self) -> None:
        """Close the kernel and clear all namespace state."""
        self.namespace.clear()
    def _get_answer(self) -> dict:
        """Get the answer dict from namespace, safely."""
        answer = self.namespace.get("answer", {})
        return answer if isinstance(answer, dict) else {}

    def is_answer_ready(self) -> bool:
        """Check if answer["ready"] is True."""
        return self._get_answer().get("ready") is True

    def get_answer_content(self) -> str:
        """Get answer["content"]."""
        content = self._get_answer().get("content", "")
        return str(content) if content is not None else ""

    def get_working_dir(self) -> Path:
        """Get current working directory.

        Returns:
            Current working directory as Path
        """
        return self._working_dir
