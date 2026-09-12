"""RLM Answer Mechanism

This module implements the answer dict management for RLM. The model
writes its final answer to `answer["content"]` and signals completion
by setting `answer["ready"] = True`.

Architecture
------------
The answer dict is initialized in the kernel namespace and managed
by this module. It supports:
- Progressive content updates across multiple turns
- Ready state detection
- State persistence across kernel executions
- Serialization for context management

Key Classes
-----------
AnswerManager : Manages the answer dict lifecycle
AnswerState   : Immutable snapshot of answer state

Example
-------
    manager = AnswerManager()
    manager.update_content("Partial answer...")
    manager.update_content("Complete answer")
    manager.set_ready(True)
    assert manager.is_ready()
    assert manager.get_content() == "Complete answer"

Notes
-----
The answer dict is the primary mechanism for the RLM model to communicate
its final answer. Unlike traditional tool-calling agents that use an
end_turn tool, RLM models write to the answer dict and set the ready
flag to signal completion.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AnswerManager", "AnswerState"]


@dataclass(frozen=True)
class AnswerState:
    """Immutable snapshot of answer state.

    Attributes:
        content: Current answer content
        ready: Whether the answer is complete
    """
    content: str
    ready: bool
    yielded: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {"content": self.content, "ready": self.ready, "yielded": self.yielded}

    @classmethod
    def from_dict(cls, data: dict) -> AnswerState:
        """Deserialize from dict."""
        return cls(content=data.get("content", ""), ready=data.get("ready", False), yielded=data.get("yielded", False))

    def clone(self) -> AnswerState:
        """Create a clone of this state."""
        return AnswerState(content=self.content, ready=self.ready, yielded=self.yielded)


class AnswerManager:
    """Manages the answer dict lifecycle.

    The answer dict is the primary mechanism for the model to communicate
    its final answer. It supports progressive updates across multiple turns
    and signals completion via the ready flag.

    Attributes:
        content: Current answer content
        ready: Whether the answer is complete
    """

    def __init__(self):
        """Initialize answer manager with empty state."""
        self._content: str = ""
        self._ready: bool = False

        self._yield: bool = False
    @property
    def content(self) -> str:
        """Get current answer content."""
        return self._content

    @property
    def ready(self) -> bool:
        """Get current ready state."""
        return self._ready

    def get_dict(self) -> dict:
        """Get answer as dict (for kernel namespace).

        Returns:
            Dict with content and ready keys
        """
        return {"content": self._content, "ready": self._ready}

    def update_content(self, content: str) -> None:
        """Update answer content.

        Args:
            content: New answer content
        """
        self._content = content

    def set_ready(self, ready: bool) -> None:
        """Set ready flag.

        Args:
            ready: Whether the answer is complete
        """
        self._ready = ready

    def set_yielded(self, value: bool) -> None:
        """Set yielded flag."""
        self._yield = value

    def is_ready(self) -> bool:
        """Check if answer is ready.

        Returns:
            True if answer["ready"] is True
        """
        return self._ready

    def get_content(self) -> str:
        """Get answer content.

        Returns:
            Current answer content string
        """
        return self._content

    def get_state(self) -> AnswerState:
        """Get current answer state.

        Returns:
            Immutable AnswerState snapshot
        """
        return AnswerState(content=self._content, ready=self._ready, yielded=self._yield)

    def reset(self) -> None:
        """Reset answer to initial state."""
        self._content = ""
        self._ready = False
        self._yield = False

    def to_dict(self) -> dict:
        """Serialize answer manager state.

        Returns:
            Dict with content, ready, and history
        """
        return {
            "content": self._content,
            "ready": self._ready,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AnswerManager:
        """Deserialize answer manager state.

        Args:
            data: Dict with content, ready, and history

        Returns:
            AnswerManager instance
        """
        manager = cls()
        manager._content = data.get("content", "")
        manager._ready = data.get("ready", False)
        return manager
