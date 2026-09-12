"""Shared types for the RLM kernel.

Contains REPLResult, _OutputCapture, _REPLTimeout, and _alarm_handler
which are used by both kernel.py and block_executor.py.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass

__all__ = ["REPLResult", "_OutputCapture", "_REPLTimeout", "_alarm_handler"]


class _REPLTimeout(BaseException):
    """Raised by SIGALRM handler when a Python block exceeds its timeout."""
    pass


def _alarm_handler(signum, frame):
    raise _REPLTimeout()


@dataclass
class REPLResult:
    """Result from a single REPL execution.

    Attributes:
        success: Whether the execution succeeded without exceptions
        output: Captured stdout output (truncated to max_output_chars)
        error: Error message if execution failed
        exception_type: Type of exception if one occurred
        duration_ms: Execution time in milliseconds
        answer_ready: Whether answer["ready"] was set to True
        answer_content: Current value of answer["content"]
    """
    success: bool
    output: str = ""
    error: str = ""
    exception_type: str | None = None
    duration_ms: float = 0.0
    answer_ready: bool = False
    answer_content: str = ""
    # Block sequence number N: the block's source lives in _code{N}, output in _output{N}
    code_seq: int = 0

    def to_dict(self) -> dict:
        """Serialize to dict."""
        from dataclasses import asdict
        return asdict(self)


class _OutputCapture:
    """Context manager for capturing stdout/stderr.

    WARNING: NOT thread-safe. This swaps sys.stdout/sys.stderr globally,
    so any daemon thread (e.g. A2AServer) that calls print() while
    capture is active will write into the capture buffer instead of the
    real terminal. Daemon threads must use os.write(1, ...) for output
    or pin their own stdout via os.dup(1) at thread entry.

    DESIGN DECISION: the agent is single-threaded (only the input reader, the
    A2A accept/handler threads, and signal timeouts run off-main), so swapping
    the process-global sys.stdout/sys.stderr here is safe — agent execution is
    main-thread-only and never races this swap (P7-B1-32, by-design). See
    agent_input.OutputCapture and wiki topic "single-threaded-execution-model".
    """

    def __init__(self, max_chars: int = 8192):
        self.max_chars = max_chars
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self._original_stdout = None
        self._original_stderr = None

    def __enter__(self):
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self.stdout
        sys.stderr = self.stderr
        return self

    def __exit__(self, *args):
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

    def get_output(self) -> str:
        """Get the FULL captured stdout (uncapped).

        The context-facing clamp (to max_chars, with an _output{N} hint) is
        applied by the caller via BlockExecutor._clamp_for_context, NOT here —
        truncating here would corrupt the full copy stored in _output{N}.
        """
        return self.stdout.getvalue()

    def get_error_output(self) -> str:
        """Get the FULL captured stderr (uncapped); clamped for context by caller."""
        return self.stderr.getvalue()
