"""REPL Manager — isolates REPL execution, answer management, and turn counting.

Extracted from TauErgon (agent_core.py) to reduce god-class coupling.
Thin wrapper: holds references to the kernel and answer manager, delegates
all logic. No new behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rlm.kernel import PythonKernel, REPLResult
    from rlm.answer import AnswerManager, AnswerState


class REPLManager:
    """Encapsulates REPL execution, answer management, and turn counting.

    Thin wrapper around PythonKernel and AnswerManager. Exists to give
    agent_loop.py and other callers a single, narrow interface for REPL
    operations without reaching into TauErgon's 40+ attributes.
    """

    def __init__(self, kernel: PythonKernel | None, answer_mgr: AnswerManager | None):
        self._kernel = kernel
        self._answer_mgr = answer_mgr
        self._turn_count = 0

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def turn_count(self) -> int:
        """Number of REPL turns executed in the current session."""
        return self._turn_count

    @property
    def is_initialized(self) -> bool:
        """True if the underlying kernel has been created."""
        return self._kernel is not None

    # ── Core operations ─────────────────────────────────────────────────

    def execute(self, code: str, error_count: int = 1) -> REPLResult:
        """Execute Python code in the REPL kernel.

        Executes the provided code in the persistent Python kernel, capturing
        stdout/stderr output. After execution, syncs the answer state if the
        model set answer["ready"] = True or updated answer["content"].

        Args:
            code: Python code to execute in the kernel namespace.

        Returns:
            REPLResult containing success, output, error, answer_ready,
            answer_content.

        Raises:
            RuntimeError: If REPL kernel is not initialized.
        """
        if self._kernel is None:
            raise RuntimeError("REPL kernel is not initialized")

        result = self._kernel.execute(code, error_count=error_count)

        # Sync answer state if answer manager exists
        if self._answer_mgr and result.answer_ready:
            self._answer_mgr.update_content(result.answer_content)
            self._answer_mgr.set_ready(True)
        elif self._answer_mgr and result.answer_content:
            self._answer_mgr.update_content(result.answer_content)

        # C4: Sync yield flag from kernel namespace
        if self._answer_mgr and self._kernel:
            ns_answer = self._kernel.namespace.get("answer", {})
            if isinstance(ns_answer, dict) and ns_answer.get("yield", False):
                self._answer_mgr.set_yielded(True)
        self._turn_count += 1
        return result

    def execute_blocks(self, blocks: list["CodeBlock"], error_count: int = 1) -> REPLResult:
        """Execute multiple Python code blocks in order, stopping on first error.

        Delegates to the kernel's execute_blocks and performs the same
        post-processing as execute(): answer sync, yield flag, turn count.

        Args:
            blocks: List of CodeBlock objects to execute in order.

        Returns:
            REPLResult with combined output or error details.

        Raises:
            RuntimeError: If REPL kernel is not initialized.
        """
        if self._kernel is None:
            raise RuntimeError("REPL kernel is not initialized")

        result = self._kernel.execute_blocks(blocks, error_count=error_count)

        # Sync answer state if answer manager exists
        if self._answer_mgr and result.answer_ready:
            self._answer_mgr.update_content(result.answer_content)
            self._answer_mgr.set_ready(True)
        elif self._answer_mgr and result.answer_content:
            self._answer_mgr.update_content(result.answer_content)

        # C4: Sync yield flag from kernel namespace
        if self._answer_mgr and self._kernel:
            ns_answer = self._kernel.namespace.get("answer", {})
            if isinstance(ns_answer, dict) and ns_answer.get("yield", False):
                self._answer_mgr.set_yielded(True)
        self._turn_count += 1
        return result

    def get_answer(self) -> AnswerState | None:
        """Get current answer state from the REPL kernel.

        Returns:
            AnswerState with content and ready flags if REPL is enabled,
            None otherwise.
        """
        if self._answer_mgr is None:
            return None
        return self._answer_mgr.get_state()

    def reset_answer(self) -> None:
        """Reset answer to initial state.

        Clears both the AnswerManager state and the kernel namespace answer
        dict. Called at the start of each new turn.
        """
        if self._answer_mgr is not None:
            self._answer_mgr.reset()
        if self._kernel is not None:
            self._kernel.namespace["answer"] = {"content": "", "ready": False}
