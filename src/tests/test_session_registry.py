"""Tests for agent_session_registry module.

Tests the SessionRegistry class and get_registry() function, covering:
- Registry creation and persistence
- Session registration and retrieval
- Listing sessions with filters
- Getting context/audit files
- Updating session metadata
- Archiving sessions
- Rebuilding registry from LOG_DIR
- Graceful degradation on errors
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_session_registry import SessionRegistry, get_registry


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    """Return a temporary path for the registry file."""
    return tmp_path / "registry.json"


@pytest.fixture
def registry(registry_path: Path) -> SessionRegistry:
    """Return a fresh SessionRegistry instance."""
    return SessionRegistry(registry_path=registry_path)


@pytest.fixture
def mock_session_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create mock session files in tmp_path."""
    ctx = tmp_path / "1234_20260801120000_1.context"
    audit = tmp_path / "1234_20260801120000_1.audit"
    plan = tmp_path / "1234_20260801120000_1.plan"
    ctx.write_text("[]")
    audit.write_text("")
    plan.write_text("")
    return ctx, audit, plan


# ── Registry creation and persistence ──────────────────────────────────────


class TestRegistryCreation:
    """Test registry creation and file persistence."""

    def test_creates_file_on_first_access(self, registry: SessionRegistry):
        """Registry file should be created when first accessed."""
        assert not registry._path.exists()
        registry._load()
        assert registry._path.exists()

    def test_initial_structure(self, registry: SessionRegistry):
        """Initial registry should have correct structure."""
        data = registry._load()
        assert data["version"] == 1
        assert "updated" in data
        assert data["sessions"] == {}

    def test_loads_existing_file(self, registry_path: Path):
        """Registry should load existing file content."""
        existing = {
            "version": 1,
            "updated": "2026-09-12T14:57:03+00:00",
            "sessions": {
                "test_123_1": {
                    "prefix": "test_123_1",
                    "context": "/path/to/context",
                    "audit": "/path/to/audit",
                    "plan": None,
                    "created": "2026-09-12T14:57:03+00:00",
                    "updated": "2026-09-12T14:57:03+00:00",
                    "status": "active",
                    "tags": [],
                    "metadata": {},
                }
            },
        }
        registry_path.write_text(json.dumps(existing))
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert "test_123_1" in data["sessions"]

    def test_handles_corrupt_file(self, registry_path: Path):
        """Registry should recover from corrupt JSON."""
        registry_path.write_text("{invalid json")
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert data["sessions"] == {}

    def test_missing_sessions_key(self, registry_path: Path):
        """Registry should handle missing 'sessions' key."""
        registry_path.write_text('{"version": 1, "updated": "2026-09-12T14:57:03+00:00"}')
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert data["sessions"] == {}


# ── Session registration and retrieval ─────────────────────────────────────


class TestSessionRegistration:
    """Test session registration and retrieval."""

    def test_register_session(self, registry: SessionRegistry, mock_session_files):
        """Register a new session."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)

        session = registry.get_session("1234_20260801120000_1")
        assert session is not None
        assert session["prefix"] == "1234_20260801120000_1"
        assert session["context"] == str(ctx)
        assert session["audit"] == str(audit)
        assert session["plan"] == str(plan)
        assert session["status"] == "active"

    def test_register_without_plan(self, registry: SessionRegistry, mock_session_files):
        """Register a session without a plan file."""
        ctx, audit, _ = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit)

        session = registry.get_session("1234_20260801120000_1")
        assert session is not None
        assert session["plan"] is None

    def test_get_nonexistent_session(self, registry: SessionRegistry):
        """Get a session that doesn't exist."""
        assert registry.get_session("nonexistent") is None

    def test_register_overwrites(self, registry: SessionRegistry, mock_session_files):
        """Registering the same prefix again should overwrite."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.register_session("1234_20260801120000_1", ctx, audit)

        session = registry.get_session("1234_20260801120000_1")
        assert session["plan"] is None


# ── Listing sessions ───────────────────────────────────────────────────────


class TestListingSessions:
    """Test listing sessions with filters."""

    def test_list_all_sessions(self, registry: SessionRegistry, mock_session_files):
        """List all sessions."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)

        sessions = registry.list_sessions()
        assert len(sessions) == 1

    def test_list_active_only(self, registry: SessionRegistry, mock_session_files):
        """List only active sessions."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.archive_session("1234_20260801120000_1", {})

        active = registry.list_sessions(status="active")
        assert len(active) == 0

        archived = registry.list_sessions(status="archived")
        assert len(archived) == 1

    def test_list_exclude_archived(self, registry: SessionRegistry, mock_session_files):
        """List sessions excluding archived."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.archive_session("1234_20260801120000_1", {})

        sessions = registry.list_sessions(include_archived=False)
        assert len(sessions) == 0

    def test_list_sorted_newest_first(self, registry: SessionRegistry, tmp_path: Path):
        """Sessions should be sorted newest first."""
        ctx1 = tmp_path / "1234_20260801120000_1.context"
        audit1 = tmp_path / "1234_20260801120000_1.audit"
        ctx2 = tmp_path / "1234_20260801130000_1.context"
        audit2 = tmp_path / "1234_20260801130000_1.audit"
        ctx1.write_text("[]")
        audit1.write_text("")
        ctx2.write_text("[]")
        audit2.write_text("")

        registry.register_session("1234_20260801120000_1", ctx1, audit1)
        registry.register_session("1234_20260801130000_1", ctx2, audit2)

        sessions = registry.list_sessions()
        assert sessions[0]["prefix"] == "1234_20260801130000_1"


# ── Getting context/audit files ────────────────────────────────────────────


class TestGettingFiles:
    """Test getting context and audit files."""

    def test_get_context_files(self, registry: SessionRegistry, mock_session_files):
        """Get all context files."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)

        files = registry.get_context_files()
        assert len(files) == 1
        assert files[0] == ctx

    def test_get_context_files_excludes_missing(self, registry: SessionRegistry, tmp_path: Path, monkeypatch):
        """Get context files, excluding missing files."""
        ctx = tmp_path / "1234_20260801120000_1.context"
        audit = tmp_path / "1234_20260801120000_1.audit"
        ctx.write_text("[]")
        audit.write_text("")
        registry.register_session("1234_20260801120000_1", ctx, audit)

        # Delete context file
        ctx.unlink()

        # Mock LOG_DIR to prevent fallback from picking up real files
        import agent_session_registry
        original_log_dir = agent_session_registry.LOG_DIR
        agent_session_registry.LOG_DIR = tmp_path
        # Invalidate cache so it reloads with new LOG_DIR
        registry._invalidate()

        try:
            files = registry.get_context_files()
            assert len(files) == 0
        finally:
            agent_session_registry.LOG_DIR = original_log_dir

    def test_get_audit_files(self, registry: SessionRegistry, mock_session_files):
        """Get all audit files."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)

        files = registry.get_audit_files()
        assert len(files) == 1
        assert files[0] == audit


# ── Updating session metadata ──────────────────────────────────────────────


class TestUpdatingSession:
    """Test updating session metadata."""

    def test_update_status(self, registry: SessionRegistry, mock_session_files):
        """Update session status."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.update_session("1234_20260801120000_1", status="archived")

        session = registry.get_session("1234_20260801120000_1")
        assert session["status"] == "archived"

    def test_update_tags_list(self, registry: SessionRegistry, mock_session_files):
        """Update session tags with list."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.update_session("1234_20260801120000_1", tags=["test", "dream"])

        session = registry.get_session("1234_20260801120000_1")
        assert "test" in session["tags"]
        assert "dream" in session["tags"]

    def test_update_metadata_dict(self, registry: SessionRegistry, mock_session_files):
        """Update session metadata with dict."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.update_session(
            "1234_20260801120000_1", metadata={"error_count": 5, "tool_calls": 100}
        )

        session = registry.get_session("1234_20260801120000_1")
        assert session["metadata"]["error_count"] == 5
        assert session["metadata"]["tool_calls"] == 100

    def test_update_nonexistent_session(self, registry: SessionRegistry):
        """Update a session that doesn't exist should be a no-op."""
        registry.update_session("nonexistent", status="archived")
        assert registry.get_session("nonexistent") is None


# ── Archiving sessions ─────────────────────────────────────────────────────


class TestArchivingSessions:
    """Test archiving sessions."""

    def test_archive_session(self, registry: SessionRegistry, mock_session_files):
        """Archive a session."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.archive_session(
            "1234_20260801120000_1",
            {"context": "/archive/path/context", "audit": "/archive/path/audit"},
        )

        session = registry.get_session("1234_20260801120000_1")
        assert session["status"] == "archived"
        assert session["context"] == "/archive/path/context"
        assert session["audit"] == "/archive/path/audit"

    def test_archive_nonexistent_session(self, registry: SessionRegistry):
        """Archive a session that doesn't exist should be a no-op."""
        registry.archive_session("nonexistent", {"context": "/path"})
        assert registry.get_session("nonexistent") is None


# ── Removing sessions ──────────────────────────────────────────────────────


class TestRemovingSessions:
    """Test removing sessions."""

    def test_remove_session(self, registry: SessionRegistry, mock_session_files):
        """Remove a session."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.remove_session("1234_20260801120000_1")

        assert registry.get_session("1234_20260801120000_1") is None

    def test_remove_nonexistent_session(self, registry: SessionRegistry):
        """Remove a session that doesn't exist should be a no-op."""
        registry.remove_session("nonexistent")


# ── Rebuilding registry ────────────────────────────────────────────────────


class TestRebuildingRegistry:
    """Test rebuilding registry from LOG_DIR."""

    def test_rebuild_finds_new_sessions(self, tmp_path: Path):
        """Rebuild should find sessions not in registry."""
        # Create mock session files
        ctx = tmp_path / "1234_20260801120000_1.context"
        audit = tmp_path / "1234_20260801120000_1.audit"
        ctx.write_text("[]")
        audit.write_text("")

        # Create registry with tmp_path as LOG_DIR
        reg = SessionRegistry(registry_path=tmp_path / "registry.json")

        # Mock LOG_DIR to use tmp_path
        import agent_session_registry
        original_log_dir = agent_session_registry.LOG_DIR
        agent_session_registry.LOG_DIR = tmp_path

        try:
            found = reg.rebuild()
            assert found == 1
            assert reg.get_session("1234_20260801120000_1") is not None
        finally:
            agent_session_registry.LOG_DIR = original_log_dir

    def test_rebuild_skips_existing_sessions(self, tmp_path: Path):
        """Rebuild should not overwrite existing sessions."""
        ctx = tmp_path / "1234_20260801120000_1.context"
        audit = tmp_path / "1234_20260801120000_1.audit"
        ctx.write_text("[]")
        audit.write_text("")

        reg = SessionRegistry(registry_path=tmp_path / "registry.json")

        import agent_session_registry
        original_log_dir = agent_session_registry.LOG_DIR
        agent_session_registry.LOG_DIR = tmp_path

        try:
            reg.register_session("1234_20260801120000_1", ctx, audit)
            reg.update_session("1234_20260801120000_1", status="archived")

            found = reg.rebuild()
            assert found == 0  # No new sessions found

            session = reg.get_session("1234_20260801120000_1")
            assert session["status"] == "archived"  # Not overwritten
        finally:
            agent_session_registry.LOG_DIR = original_log_dir


# ── Graceful degradation ───────────────────────────────────────────────────


class TestGracefulDegradation:
    """Test graceful degradation on errors."""

    def test_save_handles_io_error(self, registry: SessionRegistry, monkeypatch):
        """Save should not crash on IOError."""
        registry._load()  # Create initial data

        def mock_write_text(*args, **kwargs):
            raise IOError("Disk full")

        monkeypatch.setattr(Path, "write_text", mock_write_text)

        # Should not raise
        registry._save()

    def test_get_context_files_fallback(self, registry: SessionRegistry, tmp_path: Path):
        """get_context_files should fallback to scanning LOG_DIR."""
        # Empty registry
        files = registry.get_context_files()
        # Should return empty list, not crash
        assert isinstance(files, list)


# ── Tag operations ─────────────────────────────────────────────────────────


class TestTagOperations:
    """Test tag operations."""

    def test_add_tags(self, registry: SessionRegistry, mock_session_files):
        """Add tags to a session."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.add_tags("1234_20260801120000_1", ["test", "dream"])

        session = registry.get_session("1234_20260801120000_1")
        assert "test" in session["tags"]
        assert "dream" in session["tags"]

    def test_add_tags_no_duplicates(self, registry: SessionRegistry, mock_session_files):
        """Adding existing tags should not create duplicates."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.add_tags("1234_20260801120000_1", ["test"])
        registry.add_tags("1234_20260801120000_1", ["test"])

        session = registry.get_session("1234_20260801120000_1")
        assert session["tags"].count("test") == 1

    def test_remove_tags(self, registry: SessionRegistry, mock_session_files):
        """Remove tags from a session."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.add_tags("1234_20260801120000_1", ["test", "dream"])
        registry.remove_tags("1234_20260801120000_1", ["test"])

        session = registry.get_session("1234_20260801120000_1")
        assert "test" not in session["tags"]
        assert "dream" in session["tags"]

    def test_search_by_tags_match_all(self, registry: SessionRegistry, tmp_path: Path):
        """Search by tags with match_all=True."""
        ctx1 = tmp_path / "1234_20260801120000_1.context"
        audit1 = tmp_path / "1234_20260801120000_1.audit"
        ctx2 = tmp_path / "1234_20260801130000_1.context"
        audit2 = tmp_path / "1234_20260801130000_1.audit"
        ctx1.write_text("[]")
        audit1.write_text("")
        ctx2.write_text("[]")
        audit2.write_text("")

        registry.register_session("1234_20260801120000_1", ctx1, audit1)
        registry.register_session("1234_20260801130000_1", ctx2, audit2)
        registry.add_tags("1234_20260801120000_1", ["test", "dream"])
        registry.add_tags("1234_20260801130000_1", ["test"])

        results = registry.search_by_tags(["test", "dream"], match_all=True)
        assert len(results) == 1
        assert results[0]["prefix"] == "1234_20260801120000_1"

    def test_search_by_tags_match_any(self, registry: SessionRegistry, tmp_path: Path):
        """Search by tags with match_all=False."""
        ctx1 = tmp_path / "1234_20260801120000_1.context"
        audit1 = tmp_path / "1234_20260801120000_1.audit"
        ctx2 = tmp_path / "1234_20260801130000_1.context"
        audit2 = tmp_path / "1234_20260801130000_1.audit"
        ctx1.write_text("[]")
        audit1.write_text("")
        ctx2.write_text("[]")
        audit2.write_text("")

        registry.register_session("1234_20260801120000_1", ctx1, audit1)
        registry.register_session("1234_20260801130000_1", ctx2, audit2)
        registry.add_tags("1234_20260801120000_1", ["test", "dream"])
        registry.add_tags("1234_20260801130000_1", ["dream"])

        results = registry.search_by_tags(["test", "dream"], match_all=False)
        assert len(results) == 2

    def test_search_by_tags_no_match(self, registry: SessionRegistry, mock_session_files):
        """Search by tags with no matches."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)
        registry.add_tags("1234_20260801120000_1", ["test"])

        results = registry.search_by_tags(["nonexistent"])
        assert len(results) == 0

    def test_add_tags_nonexistent_session(self, registry: SessionRegistry):
        """Add tags to nonexistent session should be no-op."""
        registry.add_tags("nonexistent", ["test"])

    def test_remove_tags_nonexistent_session(self, registry: SessionRegistry):
        """Remove tags from nonexistent session should be no-op."""
        registry.remove_tags("nonexistent", ["test"])


# ── Edge cases (fixed issues) ──────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases for robustness."""

    def test_handles_empty_json_content(self, registry_path: Path):
        """Registry should recover from empty JSON content."""
        registry_path.write_text("")
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert data["sessions"] == {}

    def test_handles_null_json_content(self, registry_path: Path):
        """Registry should recover from null JSON content."""
        registry_path.write_text("null")
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert data["sessions"] == {}

    def test_handles_array_json_content(self, registry_path: Path):
        """Registry should recover from array JSON content."""
        registry_path.write_text("[1, 2, 3]")
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert data["sessions"] == {}

    def test_handles_string_json_content(self, registry_path: Path):
        """Registry should recover from string JSON content."""
        registry_path.write_text('"just a string"')
        reg = SessionRegistry(registry_path=registry_path)
        data = reg._load()
        assert data["sessions"] == {}

    def test_atomic_write_uses_temp_file(self, registry: SessionRegistry, mock_session_files):
        """Registry should use atomic write (temp file + os.replace)."""
        ctx, audit, plan = mock_session_files
        registry.register_session("1234_20260801120000_1", ctx, audit, plan)

        # Verify no .tmp file remains
        tmp_path = registry._path.with_suffix(".tmp")
        assert not tmp_path.exists()

    def test_get_context_files_handles_broken_symlinks(self, registry: SessionRegistry, tmp_path: Path):
        """get_context_files should handle broken symlinks gracefully."""
        # Create a context file
        ctx = tmp_path / "1234_20260801120000_1.context"
        ctx.write_text("[]")
        audit = tmp_path / "1234_20260801120000_1.audit"
        audit.write_text("")

        registry.register_session("1234_20260801120000_1", ctx, audit)

        # Create a broken symlink
        broken = tmp_path / "5678_20260801120000_1.context"
        broken.symlink_to(tmp_path / "nonexistent.context")
        registry.register_session("5678_20260801120000_1", broken, tmp_path / "5678_20260801120000_1.audit")

        # Should not crash
        files = registry.get_context_files()
        # Only the valid file should be returned (broken symlink doesn't exist())
        assert len(files) == 1
        assert files[0] == ctx


# ── Thread safety ──────────────────────────────────────────────────────────


class TestThreadSafety:
    """Test thread safety of singleton."""

    def test_get_registry_thread_safe(self):
        """get_registry should be thread-safe (double-checked locking)."""
        import agent_session_registry
        agent_session_registry._registry = None

        import threading

        results = []
        errors = []

        def get_registry_worker():
            try:
                r = get_registry()
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_registry_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # All results should be the same instance
        assert all(r is results[0] for r in results)


# ── Singleton ──────────────────────────────────────────────────────────────


class TestSingleton:
    """Test singleton behavior."""

    def test_get_registry_returns_same_instance(self):
        """get_registry should return the same instance."""
        # Reset singleton
        import agent_session_registry
        agent_session_registry._registry = None

        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_get_registry_creates_instance(self):
        """get_registry should create a new instance if None."""
        import agent_session_registry
        agent_session_registry._registry = None

        r = get_registry()
        assert isinstance(r, SessionRegistry)


# ── Cleanup Orphans ──────────────────────────────────────────────────────


class TestCleanupOrphans:
    """Test cleanup_orphans() method."""

    def test_removes_sessions_with_no_files(self, registry: SessionRegistry, tmp_path: Path):
        """Remove sessions where all files are missing."""
        # Register session with non-existent files
        registry.register_session(
            prefix="1234_20260801120000_1",
            context=tmp_path / "nonexistent.context",
            audit=tmp_path / "nonexistent.audit",
        )
        registry.register_session(
            prefix="5678_20260801120000_1",
            context=tmp_path / "also_nonexistent.context",
            audit=tmp_path / "also_nonexistent.audit",
        )

        removed = registry.cleanup_orphans()
        assert removed == 2
        assert len(registry.list_sessions()) == 0

    def test_keeps_sessions_with_existing_files(self, registry: SessionRegistry, tmp_path: Path):
        """Keep sessions where at least one file exists."""
        ctx = tmp_path / "1234_20260801120000_1.context"
        ctx.write_text("[]")

        registry.register_session(
            prefix="1234_20260801120000_1",
            context=ctx,
            audit=tmp_path / "nonexistent.audit",
        )

        removed = registry.cleanup_orphans()
        assert removed == 0
        assert len(registry.list_sessions()) == 1

    def test_keeps_sessions_with_audit_only(self, registry: SessionRegistry, tmp_path: Path):
        """Keep sessions where only audit file exists."""
        audit = tmp_path / "1234_20260801120000_1.audit"
        audit.write_text("")

        registry.register_session(
            prefix="1234_20260801120000_1",
            context=tmp_path / "nonexistent.context",
            audit=audit,
        )

        removed = registry.cleanup_orphans()
        assert removed == 0

    def test_keeps_sessions_with_plan_only(self, registry: SessionRegistry, tmp_path: Path):
        """Keep sessions where only plan file exists."""
        plan = tmp_path / "1234_20260801120000_1.plan"
        plan.write_text("")

        registry.register_session(
            prefix="1234_20260801120000_1",
            context=tmp_path / "nonexistent.context",
            audit=tmp_path / "nonexistent.audit",
            plan=plan,
        )

        removed = registry.cleanup_orphans()
        assert removed == 0

    def test_mixed_cleanup(self, registry: SessionRegistry, tmp_path: Path):
        """Clean up orphaned sessions while keeping valid ones."""
        ctx = tmp_path / "1234_20260801120000_1.context"
        ctx.write_text("[]")

        registry.register_session(
            prefix="1234_20260801120000_1",
            context=ctx,
            audit=tmp_path / "nonexistent.audit",
        )
        registry.register_session(
            prefix="5678_20260801120000_1",
            context=tmp_path / "nonexistent2.context",
            audit=tmp_path / "nonexistent2.audit",
        )

        removed = registry.cleanup_orphans()
        assert removed == 1
        assert len(registry.list_sessions()) == 1
        assert registry.get_session("1234_20260801120000_1") is not None


# ── Broken Symlink Handling ──────────────────────────────────────────────


class TestBrokenSymlinks:
    """Test broken symlink handling in get_context_files()."""

    def test_get_context_files_skips_broken_symlinks(
        self, registry: SessionRegistry, tmp_path: Path
    ):
        """Broken symlinks are excluded from get_context_files()."""
        ctx = tmp_path / "1234_20260801120000_1.context"
        ctx.write_text("[]")

        # Create a symlink to a non-existent target
        broken = tmp_path / "5678_20260801120000_1.context"
        broken.symlink_to(tmp_path / "nonexistent_target.context")

        registry.register_session(
            prefix="1234_20260801120000_1",
            context=ctx,
            audit=tmp_path / "1234_20260801120000_1.audit",
        )
        registry.register_session(
            prefix="5678_20260801120000_1",
            context=broken,
            audit=tmp_path / "5678_20260801120000_1.audit",
        )

        files = registry.get_context_files()
        # Only the valid file should be returned
        assert len(files) == 1
        assert files[0] == ctx

    def test_get_context_files_includes_valid_symlinks(
        self, registry: SessionRegistry, tmp_path: Path
    ):
        """Valid symlinks are included in get_context_files()."""
        ctx = tmp_path / "1234_20260801120000_1.context"
        ctx.write_text("[]")

        # Create a valid symlink
        link = tmp_path / "5678_20260801120000_1.context"
        link.symlink_to(ctx)

        registry.register_session(
            prefix="1234_20260801120000_1",
            context=ctx,
            audit=tmp_path / "1234_20260801120000_1.audit",
        )
        registry.register_session(
            prefix="5678_20260801120000_1",
            context=link,
            audit=tmp_path / "5678_20260801120000_1.audit",
        )

        files = registry.get_context_files()
        # Both files should be returned (original + valid symlink)
        assert len(files) == 2


# ── Integration: agent_context_utils ──────────────────────────────────────


class TestContextUtilsIntegration:
    """Test integration with agent_context_utils."""

    def test_is_valid_path_returns_true_for_regular_file(self, tmp_path: Path):
        """_is_valid_path returns True for regular files."""
        from agent_context_utils import _is_valid_path

        f = tmp_path / "test.txt"
        f.write_text("test")
        assert _is_valid_path(f) is True

    def test_is_valid_path_returns_true_for_valid_symlink(self, tmp_path: Path):
        """_is_valid_path returns True for valid symlinks."""
        from agent_context_utils import _is_valid_path

        target = tmp_path / "target.txt"
        target.write_text("test")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert _is_valid_path(link) is True

    def test_is_valid_path_returns_false_for_broken_symlink(self, tmp_path: Path):
        """_is_valid_path returns False for broken symlinks."""
        from agent_context_utils import _is_valid_path

        link = tmp_path / "link.txt"
        link.symlink_to(tmp_path / "nonexistent.txt")
        assert _is_valid_path(link) is False

    def test_is_valid_path_returns_false_for_missing_file(self, tmp_path: Path):
        """_is_valid_path returns False for missing files."""
        from agent_context_utils import _is_valid_path

        f = tmp_path / "nonexistent.txt"
        assert _is_valid_path(f) is False
