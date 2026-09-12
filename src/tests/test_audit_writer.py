"""Unit and integration tests for AuditWriter (simplified, no-thread version).

Tests cover:
- Buffer → flush → disk roundtrip
- Batch flushing
- Empty buffer handling
- Append mode preservation
- Multi-line content formatting
- Graceful disk failure handling (stderr warning, buffer retained)
- Tool call counter
- Session start logging
- Full turn cycle integration
- Regression: no threads spawned
- Context logging methods
- Console logging methods
- No truncation verification
- New format: stack=SS as first field (replaces nesting=N)
"""

import threading

import pytest

from agent_audit_writer import AuditWriter


@pytest.fixture
def audit_file(temp_dir):
    """Provide a temporary audit file path."""
    return temp_dir / "test.audit"


class TestAuditWriterBasic:
    """Basic unit tests for AuditWriter."""

    def test_flush_writes_buffer(self, audit_file):
        """Verify flush writes buffer to disk."""
        writer = AuditWriter(audit_file)
        writer.user("hello world")
        assert not audit_file.exists()  # Not flushed yet
        writer.flush()
        assert audit_file.exists()
        assert "hello world" in audit_file.read_text()

    def test_batch_flush(self, audit_file):
        """Verify multiple records batch into single flush."""
        writer = AuditWriter(audit_file)
        writer.user("msg1")
        writer.user("msg2")
        writer.context_add(1, 3, 500)
        assert not audit_file.exists()  # nothing hits disk until flush
        writer.flush()
        content = audit_file.read_text()
        assert content.count("USER") == 2
        assert content.count("CONTEXT_ADD") == 1

    def test_flush_empty_buffer(self, audit_file):
        """Verify flush on empty buffer doesn't create file."""
        writer = AuditWriter(audit_file)
        writer.flush()
        assert not audit_file.exists()

    def test_append_preserves_data(self, audit_file):
        """Verify append mode preserves previous flushes."""
        writer = AuditWriter(audit_file)
        writer.user("first")
        writer.flush()
        writer.user("second")
        writer.flush()
        content = audit_file.read_text()
        assert "first" in content
        assert "second" in content

    def test_multiline_assistant_response(self, audit_file):
        """Verify multi-line content is properly formatted."""
        writer = AuditWriter(audit_file)
        writer.session_start(
            model="test-model", cwd="/tmp",
            system_prompt="line1\nline2\nline3",
        )
        writer.flush()
        content = audit_file.read_text()
        assert "  |   line1" in content
        assert "  |   line2" in content
        assert "  |   line3" in content

    def test_graceful_disk_failure(self, audit_file, capsys):
        """Verify disk write failures are handled gracefully (stderr warning, buffer retained)."""
        writer = AuditWriter(audit_file)
        writer.user("test")
        # Create file and make it unwritable
        audit_file.write_text("")
        audit_file.chmod(0o000)
        # Flush should NOT call sys.exit(1) — graceful degradation
        writer.flush()  # Should log to stderr, retain buffer
        # Buffer should be retained (not cleared on failure)
        assert writer._buffer, "Buffer should be retained after write failure"
        # Verify stderr output
        captured = capsys.readouterr()
        assert "CRITICAL: Audit write failed" in captured.err
        assert "retained for retry" in captured.err
        # Restore permissions for cleanup
        audit_file.chmod(0o644)

    def test_session_start(self, audit_file):
        """Verify session_start writes system prompt."""
        writer = AuditWriter(audit_file)
        writer.session_start(
            model="test-model",
            cwd="/tmp",
            system_prompt="You are an agent.",
        )
        writer.flush()
        content = audit_file.read_text()
        assert "SESSION_START" in content
        assert "system_prompt:" in content
        assert "You are an agent." in content


class TestAuditWriterIntegration:
    """Integration tests for AuditWriter."""

    def test_turn_cycle(self, audit_file):
        """Simulate a full turn: user → assistant → REPL output → assistant → flush.

        Verify all records present and in correct order.
        """
        writer = AuditWriter(audit_file)
        writer.session_start(model="m", cwd="/tmp", system_prompt="sys")
        writer.user("read file.txt")
        writer.context_add(1, 2, 500)
        writer.user("[REPL output]\nfile contents here")
        writer.context_add(1, 3, 900)
        writer.flush()

        content = audit_file.read_text()
        # Verify all record types present
        assert "SESSION_START" in content
        assert "USER" in content
        assert "CONTEXT_ADD" in content
        # Verify ordering: session_start precedes user, which precedes context_add
        assert content.index("SESSION_START") < content.index("USER") < content.index("CONTEXT_ADD")


class TestAuditWriterRegression:
    """Regression tests for thread removal."""

    def test_no_threads_created(self, audit_file):
        """Verify AuditWriter does not spawn any threads."""
        before = threading.active_count()
        writer = AuditWriter(audit_file)
        writer.user("test")
        writer.flush()
        after = threading.active_count()
        assert before == after, (
            f"AuditWriter should not spawn threads "
            f"(before={before}, after={after})"
        )

    def test_close_is_flush(self, audit_file):
        """Verify close() flushes data (backward compatibility)."""
        writer = AuditWriter(audit_file)
        writer.user("before close")
        writer.close()
        assert audit_file.exists()
        assert "before close" in audit_file.read_text()


class TestAuditWriterMethods:
    """Tests for audit format and methods."""

    def test_format_nesting_first_field(self, audit_file):
        """Verify stack=SS is the first field after record type."""
        writer = AuditWriter(audit_file)
        writer.user("test message")
        writer.flush()
        content = audit_file.read_text()
        # Format: [TS] USER stack=.
        assert "USER stack=" in content

    def test_context_add(self, audit_file):
        """Test context_add method."""
        writer = AuditWriter(audit_file)
        writer.context_add(1, 10, 500)
        writer.flush()
        content = audit_file.read_text()
        assert "CONTEXT_ADD" in content

    def test_context_remove(self, audit_file):
        """Test context_remove method."""
        writer = AuditWriter(audit_file)
        writer.context_remove(1, 9, 450)
        writer.flush()
        content = audit_file.read_text()
        assert "CONTEXT_REMOVE" in content

    def test_context_merge(self, audit_file):
        """Test context_merge method."""
        writer = AuditWriter(audit_file)
        writer.context_merge("source", "target", 3)
        writer.flush()
        content = audit_file.read_text()
        assert "CONTEXT_MERGE" in content

    def test_context_snapshot(self, audit_file):
        """Test context_snapshot method."""
        writer = AuditWriter(audit_file)
        writer.context_snapshot(10, 500, 4096)
        writer.flush()
        content = audit_file.read_text()
        assert "CONTEXT_SNAPSHOT" in content

    def test_no_truncation(self, audit_file):
        """Verify no truncation — full content is logged."""
        writer = AuditWriter(audit_file)
        long_content = "x" * 500
        writer.user(long_content)
        writer.flush()
        content = audit_file.read_text()
        assert long_content in content

    def test_user_list_no_truncation(self, audit_file):
        """Verify list content is not truncated."""
        writer = AuditWriter(audit_file)
        long_text = "y" * 500
        content_list = [{"type": "text", "text": long_text}]
        writer.user(content_list)
        writer.flush()
        content = audit_file.read_text()
        assert long_text in content