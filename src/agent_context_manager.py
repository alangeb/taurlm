"""Context management and restart logic for TauErgon.

Encapsulates context operations and restart logic that were previously
part of the TauErgon class. This module provides focused classes for
managing context lifecycle and agent restart.

Key Components
- ContextManager: Manages context loading, saving, and restoration
- RestartManager: Handles agent restart with preserved state
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

__all__ = [
    'ContextManager',
    'RestartManager',
]

from agent_console import (
    context_list_display,
    context_preview_display,
    context_restored,
    context_restore_failure,
    echo,
    no_context_file_found,
    print_agent_exit_summary,
    restart_fallback_failure,
    restart_failure,
    restart_flow,
    undo_message,
    warning,
)
from agent_context_utils import (
    get_all_context_files as _get_all_context_files,
    list_context_files,
)
from agent_project import (
    find_project_root,
    get_all_entries,
    get_entry_info,
    get_loadable_contexts,
)
from agent_session import LOG_DIR, SESSION_PREFIX

if TYPE_CHECKING:
    from agent_core import TauErgon


class ContextManager:
    """Manage context operations: loading, saving, undo, and restoration.

    Handles context file operations, plan/audit file copying, and
    context restoration from previous sessions.
    """

    def __init__(self, agent: TauErgon) -> None:
        """Initialize context manager with reference to the TauErgon instance.

        Args:
            agent: The TauErgon instance this manager belongs to.
        """
        self._agent = agent

    def clear(self) -> str:
        """Clear all messages except the system prompt and reset token counters.

        Returns:
            str: Confirmation message "Context cleared."
        """
        self._agent.context.clear()
        self._agent._session.clear_tokens()
        return "Context cleared."

    def undo(self) -> None:
        """Undo the last conversation turn.

        Removes messages from the last user message onward, effectively
        reverting the last turn. This allows correcting mistakes or trying
        a different approach.

        Displays the number of messages removed via the console.
        """
        old_len = len(self._agent.context)
        self._agent.context.undo()
        undo_message(old_len - len(self._agent.context))

    def load_context_by_id(self, idx: int) -> dict | None:
        """Load a context file by its ID from the context list.

        Retrieves a context file entry from the list of available contexts
        using a 1-based index.

        Args:
            idx: 1-based index of the context to load.

        Returns:
            dict | None: The context dictionary containing 'name' and 'file' keys
                if the ID is valid, otherwise None.

        Displays:
            - Error message if ID is out of range
        """
        contexts = list_context_files()
        if not contexts or idx < 1 or idx > len(contexts):
            echo(f"ID {idx} out of range (1-{len(contexts)})")
            return None
        return contexts[idx - 1]

    def copy_plan_file(self, old_context_file: Path) -> None:
        """Copy the old session's .plan file to the new session's plan path.

        Called after /continue loads a context from a previous session so that
        plan entries survive session restoration.
        """
        old_plan = old_context_file.with_suffix(".plan")
        if not old_plan.exists():
            return

        if not SESSION_PREFIX:
            return

        new_plan = LOG_DIR / f"{SESSION_PREFIX}.plan"
        if old_plan != new_plan:
            try:
                shutil.copy2(old_plan, new_plan)
            except OSError as e:
                warning(f"Failed to copy plan file {old_plan} -> {new_plan}: {e}")

    def copy_audit_file(self, old_context_file: Path) -> None:
        """Copy the old session's .audit file into the current session's audit file.

        Called after /continue loads a context from a previous session so that
        /audit shows the full history (old + new session records).

        Appends old audit content to the current audit file so the audit writer
        can continue writing to the same file without losing history.
        """
        old_audit = old_context_file.with_suffix(".audit")
        if not old_audit.exists():
            return

        new_audit = self._agent._session.audit_file
        if old_audit != new_audit:
            try:
                with open(old_audit, "r", encoding="utf-8") as src:
                    content = src.read()
                with open(new_audit, "a", encoding="utf-8") as dst:
                    dst.write(content)
            except OSError as e:
                warning(f"Failed to copy audit file {old_audit} -> {new_audit}: {e}")

class RestartManager:
    """Handle agent restart with preserved state.

    Manages the restart process: filtering CLI args, saving context,
    clearing bytecode cache, and exec'ing the agent process.
    """

    def __init__(self, agent: TauErgon) -> None:
        """Initialize restart manager with reference to the TauErgon instance.

        Args:
            agent: The TauErgon instance to restart.
        """
        self._agent = agent