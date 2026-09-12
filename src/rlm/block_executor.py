"""Block execution engine for the RLM Python kernel.

Extracted from PythonKernel.execute_blocks() to isolate block execution
logic (bash subprocess, python exec, magic commands, error recovery) from
kernel lifecycle management.

The BlockExecutor operates on a shared namespace dict and uses callbacks
for kernel-specific concerns (magic commands, error formatting, answer state).
"""

from __future__ import annotations

import importlib
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path as _Path
from typing import Callable, ClassVar

from rlm.kernel_types import REPLResult, _OutputCapture, _REPLTimeout, _alarm_handler

__all__ = ["BlockExecutor"]


# Per-turn aggregate cap on the assembled multi-block context output. Must be
# > the single-block cap (max_output_chars, default 8192) so a normal multi-
# block turn is never clipped, but bounded so N blocks can't sum unbounded.
AGGREGATE_OUTPUT_CAP = 32000


# ----------------------------------------------------------------------
# Auto-reload of stale project modules
# ----------------------------------------------------------------------
# After a block edits a .py file on disk, a later block that imports that
# module must see the fresh source WITHOUT the caller calling importlib.reload.
# Staleness is purely sys.modules caching, so we reload changed modules before
# each exec(). The mtimes dict is owned by the kernel (it survives the
# per-call BlockExecutor instances) and shared in via the executor.

# Hard-coded safety blocklist: NEVER reload these – they own live registries /
# singletons (the active spawn registry, the kernel, the namespace builder) and
# reloading them would orphan in-flight state.
NEVER_RELOAD: frozenset = frozenset({
    "rlm.spawn",
    "rlm.kernel",
    "rlm.kernel_wiring",
    "rlm.namespace",
    "rlm.block_executor",
    "rlm.kernel_types",
})

# Root of the project source tree (src/). Only modules whose resolved
# __file__ lives under this root are ever considered for reload.
_SRC_ROOT: str = str(_Path(__file__).resolve().parent.parent)  # -> .../src


def _iter_src_modules():
    """Yield (name, module, resolved_path) for sys.modules entries under src/."""
    for mod_name, mod in list(sys.modules.items()):
        if mod_name in NEVER_RELOAD:
            continue
        mod_file = getattr(mod, "__file__", None)
        if mod_file is None:
            continue
        try:
            resolved = str(_Path(mod_file).resolve())
        except (OSError, ValueError):
            continue
        if not resolved.startswith(_SRC_ROOT):
            continue
        yield mod_name, mod, resolved


def reload_stale_modules(mtimes: dict[str, float]) -> None:
    """Reload src/ modules whose source mtime advanced past the recorded one.

    On first encounter of a module the mtime is recorded but NOT reloaded
    (the in-memory copy is assumed current). Reload failures are swallowed so
    a broken edit never breaks the executing block.
    """
    # Invalidate import caches once so freshly-written files are seen.
    importlib.invalidate_caches()
    for mod_name, mod, resolved in _iter_src_modules():
        try:
            current = os.path.getmtime(resolved)
        except OSError:
            continue
        last = mtimes.get(mod_name)
        if last is None:
            mtimes[mod_name] = current  # baseline, don't reload
            continue
        if current <= last:
            continue  # unchanged
        # Drop the cached bytecode first: the .pyc header records the source
        # mtime at SECOND granularity, so a sub-second edit with identical
        # byte-size would otherwise make importlib.reload reuse stale bytecode.
        cached = getattr(mod, "__cached__", None)
        if cached:
            try:
                os.remove(cached)
            except OSError:
                pass
        try:
            importlib.reload(mod)
        except Exception:
            pass  # never break the user's block
        mtimes[mod_name] = current


def record_new_module_mtimes(mtimes: dict[str, float]) -> None:
    """Record mtimes for src/ modules imported *during* the just-run exec().

    A module imported inside a block is absent from sys.modules when
    reload_stale_modules() runs (it runs before exec), so its baseline must be
    captured after exec – otherwise the next block treats it as a first
    encounter and skips the reload it actually needs.
    """
    for mod_name, _mod, resolved in _iter_src_modules():
        if mod_name in mtimes:
            continue
        try:
            mtimes[mod_name] = os.path.getmtime(resolved)
        except OSError:
            pass


@dataclass(repr=False)
class BlockExecutor:
    """Executes a sequence of code blocks (python/bash) in a shared namespace.

    This class encapsulates the block execution loop that was previously
    embedded in PythonKernel.execute_blocks(). It handles:
    - Bash subprocess execution with timeout and process group cleanup
    - Python exec with SIGALRM-based timeout
    - Magic command processing (delegated via callback)
    - Error recovery (answer dict restoration)
    - Output aggregation and truncation

    Args:
        namespace: The shared execution namespace (mutated in place).
        python_timeout: Default timeout for Python blocks in seconds.
        bash_timeout: Default timeout for Bash blocks in seconds.
        max_output_chars: Maximum output characters to capture.
        handle_magic: Callback(code) -> (processed, bash_output, bash_error, timeout_override).
        format_error: Callback(exception, code, prefix) -> formatted error string.
        get_answer: Callback() -> dict (the answer dict from namespace).
    """

    namespace: dict
    python_timeout: int
    bash_timeout: int
    max_output_chars: int
    handle_magic: Callable[[str], tuple[str, str, str, int | None]]
    format_error: Callable[..., str]
    get_answer: Callable[[], dict]
    # Optional block-sequence wiring (provided by PythonKernel). next_seq()
    # draws the monotonic N; store_code/store_output record _code{N}/_output{N}
    # in the shared namespace so both execution paths share one sequence.
    next_seq: Callable[[], int] | None = None
    store_code: Callable[[int, str], None] | None = None
    store_output: Callable[[int, str], None] | None = None
    # --- auto-reload bookkeeping ---
    # Shared dict owned by the kernel so mtimes persist across the
    # per-call BlockExecutor instances. Defaults to a fresh dict when the
    # executor is constructed standalone (e.g. in unit tests).
    module_mtimes: dict[str, float] = field(default_factory=dict, repr=False)

    def _clamp_for_context(self, text: str, seq: int) -> str:
        """Clamp the context-facing copy of a block's output to max_output_chars.

        The FULL output lives in _output{seq} (namespace, never sent to the LLM);
        the marker points the model there so it can slice the rest on demand.
        """
        if len(text) <= self.max_output_chars:
            return text
        return (
            text[:self.max_output_chars]
            + f"\n... [output truncated; full output in _output{seq}]"
        )

    def _seq(self) -> int:
        """Draw the next monotonic block sequence number (0 if unwired)."""
        return self.next_seq() if self.next_seq else 0

    def _store_code(self, seq: int, code: str) -> None:
        if self.store_code:
            self.store_code(seq, code)

    def _store_output(self, seq: int, output: str) -> None:
        if self.store_output:
            self.store_output(seq, output)

    # ------------------------------------------------------------------
    # Auto-reload stale project modules before each exec()
    # ------------------------------------------------------------------

    def _auto_reload_stale_modules(self) -> None:
        """Reload project modules whose source file mtime changed (see module
        helper reload_stale_modules)."""
        reload_stale_modules(self.module_mtimes)

    def _record_new_module_mtimes(self) -> None:
        """Record mtimes for modules imported during the just-run exec()."""
        record_new_module_mtimes(self.module_mtimes)

    def execute(self, blocks: list, error_count: int = 1) -> REPLResult:
        """Execute multiple code blocks (python/bash) in order.

        Stopping rules:
        - Python exception or bash timeout: stop remaining blocks, return error.
        - Bash non-zero exit: warning, continue to next block.
        - Empty blocks: skipped.

        Args:
            blocks: List of CodeBlock objects (has .language and .code).

        Returns:
            REPLResult with combined output, or error details.
        """
        start_time = time.monotonic()
        total_blocks = len(blocks)
        all_output: list[str] = []
        all_bash_error: list[str] = []
        last_seq = 0

        for idx, block in enumerate(blocks):
            block_num = idx + 1

            if block.language == "bash":
                result, seq = self._exec_bash_block(block, block_num, total_blocks, all_output, all_bash_error)
                if result is not None:
                    result.duration_ms = (time.monotonic() - start_time) * 1000
                    result.code_seq = seq or last_seq
                    return result
                last_seq = seq or last_seq
                continue

            # Python block (default)
            result, seq = self._exec_python_block(block, block_num, total_blocks, all_output, error_count)
            if result is not None:
                result.duration_ms = (time.monotonic() - start_time) * 1000
                result.code_seq = seq or last_seq
                return result
            last_seq = seq or last_seq

        # All blocks succeeded
        if "answer" not in self.namespace:
            self.namespace["answer"] = {"content": "", "ready": False}

        answer = self.get_answer()
        duration_ms = (time.monotonic() - start_time) * 1000
        full_output = chr(10).join(p for p in all_output if p)
        if len(full_output) > AGGREGATE_OUTPUT_CAP:
            full_output = (
                full_output[:AGGREGATE_OUTPUT_CAP]
                + "\n... [aggregate output truncated; see per-block _output<N> namespace vars]"
            )

        try:
            from agent_audit_bridge import console_info
            console_info(f"[REPL EXECUTE] success=True blocks={total_blocks} output={full_output[:200]!r}")
        except Exception:
            pass

        return REPLResult(
            success=True,
            output=full_output,
            error=chr(10).join(all_bash_error) if all_bash_error else "",
            duration_ms=duration_ms,
            answer_ready=(answer.get("ready") is True) if answer else False,
            answer_content=answer.get("content", "") if answer else "",
            code_seq=last_seq,
        )

    def _exec_bash_block(
        self, block, block_num: int, total_blocks: int,
        all_output: list[str], all_bash_error: list[str],
    ) -> tuple[REPLResult | None, int]:
        """Execute a single bash block. Returns (REPLResult|None, seq)."""
        if not block.code.strip():
            return None, 0

        seq = self._seq()

        processed_bash, _, bash_err, bash_to = self.handle_magic(block.code)
        # P7-B1-10: store POST-magic code (what actually runs) so shell error
        # line numbers map correctly onto _code{N}; magic lines (%timeout/%cd)
        # are stripped from the executed script. On a magic-command error the
        # original code is more useful for the reported magic line.
        self._store_code(seq, block.code if bash_err else processed_bash)
        if bash_err:
            self._store_output(seq, bash_err)
            return REPLResult(
                success=False, output="",
                error=f"Block {block_num} of {total_blocks}: {bash_err}",
            ), seq

        bash_code = processed_bash
        eff_timeout = bash_to if bash_to is not None else self.bash_timeout

        try:
            proc = subprocess.Popen(
                bash_code, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                stdout_b, stderr_b = proc.communicate(timeout=eff_timeout)
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
                error_msg = (
                    f"Block {block_num} of {total_blocks}: "
                    f"Bash timed out after {eff_timeout}s. "
                    f"Process group killed. For longer work use tmux. "
                    f"See EXECUTION TIMEOUT in AGENT_RLM.md."
                )
                try:
                    from agent_audit_bridge import console_info
                    console_info(f"[REPL EXECUTE] success=False block={block_num} timeout")
                except Exception:
                    pass
                self._store_output(seq, "")
                return REPLResult(
                    success=False,
                    output=chr(10).join(all_output),
                    error=error_msg,
                    exception_type="TimeoutError",
                ), seq
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            # P0: store the FULL output in _output{seq} (uncapped, RAM-guarded in
            # the kernel); the CONTEXT copy appended to the transcript is clamped
            # to max_output_chars so a huge bash result can't blow the window.
            self._store_output(seq, chr(10).join(x for x in (stdout, stderr) if x))
            if stdout:
                all_output.append(self._clamp_for_context(stdout, seq))
            if proc.returncode != 0:
                err = stderr or f"exit code {proc.returncode}"
                all_bash_error.append(f"[block {block_num}] {self._clamp_for_context(err, seq)}")
        except Exception as e:
            all_bash_error.append(f"[block {block_num}] {type(e).__name__}: {e}")
            self._store_output(seq, f"{type(e).__name__}: {e}")

        return None, seq

    def _exec_python_block(
        self, block, block_num: int, total_blocks: int,
        all_output: list[str], error_count: int = 1,
    ) -> tuple[REPLResult | None, int]:
        """Execute a single python block. Returns (REPLResult|None, seq)."""
        processed, bash_output, bash_error, timeout_override = self.handle_magic(block.code)

        if bash_error:
            seq = self._seq()
            self._store_code(seq, processed)
            self._store_output(seq, chr(10).join(x for x in (bash_output, bash_error) if x))
            return REPLResult(
                success=False,
                output=bash_output,
                error=f"Block {block_num} of {total_blocks}: {bash_error}",
            ), seq

        if bash_output:
            all_output.append(bash_output)

        if not processed.strip():
            return None, 0

        # N is drawn once per executed block; source stored BEFORE exec so it
        # exists on success. Stored post-magic so indices match error line numbers.
        seq = self._seq()
        self._store_code(seq, processed)

        eff_timeout = timeout_override if timeout_override is not None else self.python_timeout
        old_sig = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, eff_timeout)
        capture = None  # R3: bind before try so a timeout during reload can't NameError
        try:
            with _OutputCapture(self.max_output_chars) as capture:
                # R3: reload runs INSIDE the capture so a SIGALRM timeout during
                # reload has a bound capture in the except handlers below.
                self._auto_reload_stale_modules()
                exec(processed, self.namespace)
            self._record_new_module_mtimes()
        except _REPLTimeout:
            block_prefix = f"Block {block_num} of {total_blocks}, " if total_blocks > 1 else ""
            self._store_output(seq, capture.get_output() if capture else "")
            _ans = self.namespace.get("answer")
            _ans = _ans if isinstance(_ans, dict) else {}
            return REPLResult(
                success=False,
                output=chr(10).join(all_output) + chr(10) + (self._clamp_for_context(capture.get_output(), seq) if capture else ""),
                error=f"{block_prefix}Timed out after {eff_timeout}s. For longer work use tmux. See EXECUTION TIMEOUT in AGENT_RLM.md.",
                exception_type="_REPLTimeout",
                # P7-CT-02: carry answer state set by earlier blocks so a
                # later block's failure doesn't discard it (extra LLM call).
                answer_ready=_ans.get("ready", False) is True,
                answer_content=str(_ans.get("content", "") or ""),
            ), seq
        except Exception as e:
            error_type = type(e).__name__
            self._store_output(seq, capture.get_output() if capture else "")
            if "answer" not in self.namespace:
                self.namespace["answer"] = {"content": "", "ready": False}
            block_prefix = f"Block {block_num} of {total_blocks}, " if total_blocks > 1 else ""
            error_msg = self.format_error(e, processed, prefix=block_prefix, seq=seq)
            try:
                from agent_audit_bridge import console_info
                console_info(f"[REPL EXECUTE] success=False block={block_num}/{total_blocks} error={error_msg[:200]!r}")
            except Exception:
                pass

            _ans = self.namespace.get("answer")
            _ans = _ans if isinstance(_ans, dict) else {}
            return REPLResult(
                success=False,
                output=chr(10).join(all_output),
                error=error_msg,
                exception_type=error_type,
                # P7-CT-02: carry answer state set by earlier blocks so a
                # later block's failure doesn't discard it (extra LLM call).
                answer_ready=_ans.get("ready", False) is True,
                answer_content=str(_ans.get("content", "") or ""),
            ), seq
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_sig)

        output = capture.get_output()
        stderr_output = capture.get_error_output()
        # Store FULL output; clamp the context copy with the _output{seq} hint.
        self._store_output(seq, chr(10).join(x for x in (output, stderr_output) if x))
        if output:
            all_output.append(self._clamp_for_context(output, seq))
        if stderr_output:
            all_output.append(self._clamp_for_context(stderr_output, seq))

        return None, seq
