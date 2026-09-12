"""Command dispatch mixin for TauErgon.

Extracted from agent_core.py to resolve the phantom CommandHandlersMixin import.
Contains the 5 command dispatch methods and their 2 instance attributes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent_models import InputMessage


class CommandHandlersMixin:
    """Mixin providing slash-command and .md-command dispatch to TauErgon."""

    # ── Initialisation ──────────────────────────────────────────────────

    def _init_command_handlers(self) -> None:
        """Initialise command-dispatch state. Call from the host __init__."""
        self._cmd_dispatch_depth: int = 0
        self._pending_md_segments: list[list[str]] = []

    # ── Command discovery ───────────────────────────────────────────────

    def _get_available_commands(self) -> dict[str, Any]:
        """Discover and return available markdown commands dynamically.

        Indirection exists so tests can monkeypatch this method
        without touching rlm.command_dispatch.
        """
        from rlm.command_dispatch import get_available_commands
        return get_available_commands()

    # ── Slash-command dispatch ──────────────────────────────────────────

    def _handle_command(
        self, cmd_name: str, cmd_full: str, msg: Optional[InputMessage] = None
    ) -> None:
        """Dispatch a slash command to the appropriate handler.

        Dispatch order: help aliases → .py commands (COMMANDS) → .md commands (MD_COMMANDS).
        .md commands are expanded into segments that flow through the input pipeline
        as simulated user input, making them indistinguishable from real user prompts.
        """
        from rlm.command_dispatch import (
            handle_command,
            strip_frontmatter,
            parse_multi_prompt,
        )
        from agent_console.primitives import display_error

        # Help aliases: /, /help, /?
        if cmd_full in ("", "/") or cmd_name in ("help", "?"):
            from rlm.command_dispatch import _show_help
            _show_help()
            return

        # .py commands (COMMANDS dict)
        try:
            from commands import COMMANDS
            if cmd_name in COMMANDS:
                handle_command(self, cmd_name, cmd_full, msg)
                return
        except ImportError:
            pass

        # .md commands (MD_COMMANDS dict)
        try:
            from commands import MD_COMMANDS
            if cmd_name in MD_COMMANDS:
                self._cmd_dispatch_depth += 1
                try:
                    self._dispatch_md(cmd_name, cmd_full, strip_frontmatter, parse_multi_prompt)
                finally:
                    self._cmd_dispatch_depth -= 1
                return
        except ImportError:
            pass

        # Unknown command
        display_error(f"Unknown command: /{cmd_name}")

    # ── .md command expansion ───────────────────────────────────────────

    def _dispatch_md(
        self,
        cmd_name: str,
        cmd_full: str,
        strip_frontmatter,
        parse_multi_prompt,
    ) -> None:
        """Expand a .md command into segments and inject into the input pipeline.

        Segments are stored in _pending_md_segments for sequential processing.
        Only the first segment is injected immediately; subsequent segments
        are injected after each LLM turn completes.

        Each segment is either:
        - A "/" prefix: recursively dispatch to another command
        - Regular text: injected as simulated user input via _process_input()

        Recursion is guarded by _cmd_dispatch_depth (capped at MAX_MD_RECURSION).
        """
        from agent_audit_bridge import console_info
        from commands import MD_COMMANDS
        from agent_console.primitives import display_error
        from rlm.command_dispatch import MAX_MD_RECURSION

        # Log .md command invocation
        console_info(f"MD_COMMAND_INVOKE: /{cmd_name} {cmd_full}")

        md_file = MD_COMMANDS[cmd_name]
        try:
            content = strip_frontmatter(md_file.read_text())
        except FileNotFoundError:
            display_error(f"Command file not found: {cmd_name}.md")
            console_info(f"MD_COMMAND_ERROR: /{cmd_name} file not found")
            return
        except OSError as e:
            display_error(f"Failed to read command file {cmd_name}.md: {e}")
            console_info(f"MD_COMMAND_ERROR: /{cmd_name} read failed: {e}")
            return
        args = cmd_full.split()[1:]

        # Get the input handler from the agent (set during run())
        input_handler = getattr(self, "_input_handler", None)
        if input_handler is None:
            display_error("Cannot dispatch .md commands: no active input handler")
            console_info(f"MD_COMMAND_ERROR: /{cmd_name} no input handler")
            return

        # Parse all segments
        segments = []
        for segment in parse_multi_prompt(content, args):
            segment = segment.strip()
            if segment:
                segments.append(segment)

        if not segments:
            return

        # Store pending segments on stack (all except the first)
        if len(segments) > 1:
            self._pending_md_segments.append(segments[1:])

        # Process first segment
        self._process_md_segment(segments[0], input_handler, cmd_name)

    # ── Segment processing ──────────────────────────────────────────────

    def _process_md_segment(
        self,
        segment: str,
        input_handler,
        parent_cmd_name: str,
    ) -> None:
        """Process a single .md command segment.

        Args:
            segment: The segment text (may start with "/" for recursion).
            input_handler: The InputHandler instance for injection.
            parent_cmd_name: The parent .md command name for logging.
        """
        from agent_audit_bridge import console_info
        from agent_models import InputMessage
        from agent_console.primitives import display_error
        from rlm.command_dispatch import MAX_MD_RECURSION

        if segment.startswith("/"):
            # Recursion: dispatch to another command
            if self._cmd_dispatch_depth >= MAX_MD_RECURSION:
                display_error(
                    f"Command recursion limit ({MAX_MD_RECURSION}) reached — "
                    f"stopping dispatch for: {segment}"
                )
                console_info(f"MD_COMMAND_RECURSION_LIMIT: /{parent_cmd_name} -> {segment}")
                return
            sub_name = segment.split()[0][1:]
            console_info(f"MD_COMMAND_RECURSION: /{parent_cmd_name} -> /{sub_name}")
            self._handle_command(
                sub_name,
                segment,
                InputMessage(content=segment, source="command_file"),
            )
        else:
            # Inject as simulated user input — flows through full LLM turn
            console_info(f"MD_COMMAND_SEGMENT: /{parent_cmd_name} -> [input pipeline]")
            cmd_msg = InputMessage(content=segment, source="command_file")
            input_handler._process_input(cmd_msg)
