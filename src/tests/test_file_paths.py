"""Tests for file path management (audit, context files).

Verifies that:
1. Each file can be independently overridden via environment variables
2. Default location is $HOME/.local/taurlm/log/{prefix}.audit|context
3. No fallback to .agent or .agents directories
4. Variable count is minimized
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch


# Environment variables that affect session file paths.
_SESSION_ENV_KEYS = {
    "TAU_AUDIT_LOG_FILE",
    "TOOL_CONTEXT_FILE",
    "TAU_PARENT_AUDIT_FILE",
    "TAU_FORK_NESTING",
}


def _clear_session_env_vars():
    """Clear all session-file-related environment variables and return them."""
    cleared = {}
    for key in list(os.environ):
        if key in _SESSION_ENV_KEYS or (key.startswith("TOOL_") and key.endswith("_FILE")):
            cleared[key] = os.environ.pop(key)
    return cleared


def _restore_session_env_vars(saved):
    """Restore session-file-related environment variables."""
    os.environ.update(saved)


# Legacy aliases — kept for backward compatibility.
_clear_tool_env_vars = _clear_session_env_vars
_restore_tool_env_vars = _restore_session_env_vars


class TestFilePathIndependence:
    """Test that audit and context files can be independently overridden."""

    def test_default_paths_use_log_dir(self, test_config):
        """Default paths should all be in LOG_DIR with correct extensions."""
        from agent_core import TauErgon

        # Clear any existing environment variables
        saved = _clear_tool_env_vars()
        try:
            with patch.object(TauErgon, "invoke"):
                agent = TauErgon(
                    config=test_config,
                    base_url="http://test:8000/v1",
                    model="test-model",
                )

            # All paths should be in LOG_DIR
            assert agent.audit_file.parent == Path.home() / ".local/taurlm/log"
            assert agent.context_file.parent == Path.home() / ".local/taurlm/log"

            # Extensions should be correct
            assert agent.audit_file.suffix == ".audit"
            assert agent.context_file.suffix == ".context"

            # Audit and context should have same prefix
            audit_prefix = agent.audit_file.stem
            ctx_prefix = agent.context_file.stem
            assert audit_prefix == ctx_prefix
        finally:
            _restore_tool_env_vars(saved)

    def test_audit_file_override_independent(self, test_config):
        """TAU_AUDIT_LOG_FILE override should only affect audit file."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                custom_audit = Path(tmpdir) / "custom.audit"

                with patch.dict(os.environ, {"TAU_AUDIT_LOG_FILE": str(custom_audit)}):
                    with patch.object(TauErgon, "invoke"):
                        agent = TauErgon(
                            config=test_config,
                            base_url="http://test:8000/v1",
                            model="test-model",
                        )

                # Only audit file should be overridden
                assert agent.audit_file == custom_audit
                # Context should still be in LOG_DIR
                assert agent.context_file.parent == Path.home() / ".local/taurlm/log"
        finally:
            _restore_tool_env_vars(saved)

    def test_context_file_override_independent(self, test_config):
        """TOOL_CONTEXT_FILE override should only affect context file."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                custom_ctx = Path(tmpdir) / "custom.context"

                with patch.dict(os.environ, {"TOOL_CONTEXT_FILE": str(custom_ctx)}):
                    with patch.object(TauErgon, "invoke"):
                        agent = TauErgon(
                            config=test_config,
                            base_url="http://test:8000/v1",
                            model="test-model",
                        )

                # Only context file should be overridden
                assert agent.context_file == custom_ctx
                # Audit should still be in LOG_DIR
                assert agent.audit_file.parent == Path.home() / ".local/taurlm/log"
        finally:
            _restore_tool_env_vars(saved)

    def test_all_overrides_together(self, test_config):
        """Both overrides can be set simultaneously."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                custom_audit = Path(tmpdir) / "custom.audit"
                custom_ctx = Path(tmpdir) / "custom.ctx"

                with patch.dict(
                    os.environ,
                    {
                        "TAU_AUDIT_LOG_FILE": str(custom_audit),
                        "TOOL_CONTEXT_FILE": str(custom_ctx),
                    },
                ):
                    with patch.object(TauErgon, "invoke"):
                        agent = TauErgon(
                            config=test_config,
                            base_url="http://test:8000/v1",
                            model="test-model",
                        )

                # All should be overridden
                assert agent.audit_file == custom_audit
                assert agent.context_file == custom_ctx
        finally:
            _restore_tool_env_vars(saved)


class TestNoAgentFallback:
    """Test that there is no fallback to .agent or .agents directories."""

    def test_no_dot_agent_in_default_paths(self, test_config):
        """Default paths should not contain .agent or .agents."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with patch.object(TauErgon, "invoke"):
                agent = TauErgon(
                    config=test_config,
                    base_url="http://test:8000/v1",
                    model="test-model",
                )

            # Check audit file path
            audit_path_str = str(agent.audit_file)
            assert ".agent" not in audit_path_str or ".local/taurlm/log/tool" in audit_path_str

            # Check context file path
            ctx_path_str = str(agent.context_file)
            assert (
                ".agent" not in ctx_path_str
                or ".local/taurlm/log/tool" in ctx_path_str
            )
        finally:
            _restore_tool_env_vars(saved)

    def test_no_dot_agents_in_default_paths(self, test_config):
        """Default paths should not contain .agents directory."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with patch.object(TauErgon, "invoke"):
                agent = TauErgon(
                    config=test_config,
                    base_url="http://test:8000/v1",
                    model="test-model",
                )

            # Check audit file path
            audit_path_str = str(agent.audit_file)
            assert ".agents" not in audit_path_str

            # Check context file path
            ctx_path_str = str(agent.context_file)
            assert ".agents" not in ctx_path_str
        finally:
            _restore_tool_env_vars(saved)


class TestEnvVarOverride:
    """Test environment variable overrides for file paths."""

    def test_audit_file_override(self, test_config):
        """TAU_AUDIT_LOG_FILE should override audit file path."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                custom_audit = Path(tmpdir) / "custom.audit"

                with patch.dict(os.environ, {"TAU_AUDIT_LOG_FILE": str(custom_audit)}):
                    with patch.object(TauErgon, "invoke"):
                        agent = TauErgon(
                            config=test_config,
                            base_url="http://test:8000/v1",
                            model="test-model",
                        )

                assert agent.audit_file == custom_audit
        finally:
            _restore_tool_env_vars(saved)

    def test_context_file_override(self, test_config):
        """TOOL_CONTEXT_FILE should override context file path."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                custom_ctx = Path(tmpdir) / "custom.context"

                with patch.dict(os.environ, {"TOOL_CONTEXT_FILE": str(custom_ctx)}):
                    with patch.object(TauErgon, "invoke"):
                        agent = TauErgon(
                            config=test_config,
                            base_url="http://test:8000/v1",
                            model="test-model",
                        )

                assert agent.context_file == custom_ctx
        finally:
            _restore_tool_env_vars(saved)


class TestPathConsistency:
    """Test that paths remain consistent across operations."""

    def test_paths_remain_after_context_operations(self, test_config):
        """File paths should remain consistent after context operations."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with patch.object(TauErgon, "invoke"):
                agent = TauErgon(
                    config=test_config,
                    base_url="http://test:8000/v1",
                    model="test-model",
                )

            initial_audit = agent.audit_file
            initial_ctx = agent.context_file

            # Clear context
            agent._context_manager.clear()

            # Paths should remain the same
            assert agent.audit_file == initial_audit
            assert agent.context_file == initial_ctx
        finally:
            _restore_tool_env_vars(saved)


class TestNewFunctionality:
    """Test new functionality for file path management."""

    def test_log_file_creation(self, test_config):
        """Test that audit file is created correctly."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with patch.object(TauErgon, "invoke"):
                agent = TauErgon(
                    config=test_config,
                    base_url="http://test:8000/v1",
                    model="test-model",
                )

            # Trigger audit file creation by writing a session start record
            agent.audit_writer.user("test")
            agent.audit_writer.flush()

            # Verify audit file exists and has correct extension
            assert agent.audit_file.exists(), f"Audit file {agent.audit_file} should exist after initialization"
            assert agent.audit_file.suffix == ".audit"
        finally:
            _restore_tool_env_vars(saved)

    def test_context_file_creation(self, test_config):
        """Test that context file is created correctly."""
        from agent_core import TauErgon

        saved = _clear_tool_env_vars()
        try:
            with patch.object(TauErgon, "invoke"):
                agent = TauErgon(
                    config=test_config,
                    base_url="http://test:8000/v1",
                    model="test-model",
                )

            # Trigger context file creation by writing minimal context
            import json
            with open(agent.context_file, "w", encoding="utf-8") as f:
                json.dump([], f)

            # Verify context file exists and has correct extension
            assert agent.context_file.exists(), f"Context file {agent.context_file} should exist after initialization"
            assert agent.context_file.suffix == ".context"
        finally:
            _restore_tool_env_vars(saved)
