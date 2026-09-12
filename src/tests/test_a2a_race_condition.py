"""Test the A2A server startup race condition fix.

Verifies that A2AServer.start() blocks until the socket is actually bound
and accepting connections, eliminating the race window where clients could
see the socket file (or find nothing) before the server was ready.
"""

import socket
import threading
import time
from pathlib import Path

import pytest

from agent_a2a import A2AServer, get_agent_card


# ---------------------------------------------------------------------------
# Minimal duck-typed agent stub for A2AServer
# ---------------------------------------------------------------------------
class _AgentStub:
    """Minimal agent-like object that satisfies A2AServer's duck-typed interface."""

    def __init__(self):
        self.agent_name = "test-agent"
        self.model_name = "test-model"
        self.original_cwd = Path("/tmp")
        self.context = []
        self._start_time = time.time()
        self.input_queue = None
        self._pending_a2a_responses: dict = {}
        # Extension-004 agent card metadata fields
        self.original_task = None
        self.current_group_name = "test"
        self.max_context_tokens = 128000
        self._session = None


@pytest.fixture
def agent_stub():
    return _AgentStub()


@pytest.fixture
def sock_path():
    path = "/tmp/test_taua2a_race.sock"
    p = Path(path)
    if p.exists():
        p.unlink()
    yield path
    p = Path(path)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Test 1: start() blocks until socket is ready
# ---------------------------------------------------------------------------
class TestStartReadyEvent:
    """start() should block until the socket is bound and listening."""

    def test_start_blocks_until_listen(self, agent_stub, sock_path):
        """The socket must be connectable the moment start() returns."""
        _server = A2AServer(agent_stub, sock_path=sock_path)
        _server.start()  # <-- should block until ready

        # Immediately try to connect — this should NOT raise
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        try:
            client.connect(sock_path)  # Must not hang or raise
        finally:
            client.close()

        _server.stop()

    def test_start_handles_stale_socket_file(self, agent_stub, sock_path):
        """If a stale socket file exists from a previous run, start() should handle it."""
        # Create a stale socket file that doesn't have a listener
        p = Path(sock_path)
        p.touch()  # Create empty stale socket file

        _server = A2AServer(agent_stub, sock_path=sock_path)
        _server.start()  # Should succeed despite stale file

        # Verify it's actually usable
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        try:
            client.connect(sock_path)
        finally:
            client.close()

        _server.stop()


# ---------------------------------------------------------------------------
# Test 2: No socket found if server hasn't started yet
# ---------------------------------------------------------------------------
class TestNoRaceToListAgents:
    """list_agents() should not find agents until start() is fully ready."""

    def test_server_not_found_before_start(self, agent_stub, sock_path):
        """Before start(), the socket file should not exist."""
        _server = A2AServer(agent_stub, sock_path=sock_path)
        # Don't call start() — verify no socket exists

        # The socket shouldn't exist because _accept_loop hasn't run yet
        assert not Path(sock_path).exists()

    def test_server_found_after_start(self, agent_stub, sock_path):
        """After start(), the socket file should exist and be connectable."""
        _server = A2AServer(agent_stub, sock_path=sock_path)
        _server.start()

        # Socket should exist
        assert Path(sock_path).exists(), "Socket file should exist after start()"

        # Should be connectable
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        try:
            client.connect(sock_path)
        finally:
            client.close()

        _server.stop()


# ---------------------------------------------------------------------------
# Test 3: Concurrent access — client connects while server starts
# ---------------------------------------------------------------------------
class TestConcurrentConnect:
    """Test that clients connecting concurrently with server startup don't fail."""

    def test_client_connects_immediately_after_start(self, agent_stub, sock_path):
        """Multiple clients can connect immediately after start() returns."""
        _server = A2AServer(agent_stub, sock_path=sock_path)
        _server.start()

        # Fire off 3 concurrent connections immediately
        connections = []

        def connect_one():
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(3)
            c.connect(sock_path)
            connections.append(c)

        threads = [threading.Thread(target=connect_one) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All connections should succeed
        assert (
            len(connections) == 3
        ), f"Expected 3 successful connections, got {len(connections)}"

        for c in connections:
            c.close()
        _server.stop()

    def test_agent_card_works_after_start(self, agent_stub, sock_path):
        """get_agent_card should return valid data immediately after start()."""
        _server = A2AServer(agent_stub, sock_path=sock_path)
        _server.start()

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(3)
        try:
            client.connect(sock_path)
            card = get_agent_card(client)
            assert card["type"] == "agent_card"
            assert card["name"] == "test-agent"
            assert card["model"] == "test-model"
        finally:
            client.close()

        _server.stop()


# ---------------------------------------------------------------------------
# Test 4: Multiple rapid starts (simulate restart scenario)
# ---------------------------------------------------------------------------
class TestRapidStartStop:
    """Server should survive rapid start/stop cycles."""

    def test_rapid_start_stop(self, agent_stub, sock_path):
        """Multiple start/stop cycles should all succeed."""
        for i in range(3):
            _server = A2AServer(agent_stub, sock_path=sock_path)
            _server.start()

            # Verify socket is ready
            assert Path(sock_path).exists()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2)
            try:
                client.connect(sock_path)
            finally:
                client.close()

            _server.stop()
            assert not Path(
                sock_path
            ).exists(), f"Socket should be cleaned up after stop() (iteration {i})"


# ---------------------------------------------------------------------------
# Test 5: Verify the old race window is eliminated
# ---------------------------------------------------------------------------
class TestRaceWindowEliminated:
    """Verify the specific race that caused 'empty list_agents' is gone."""

    def test_start_returns_only_when_listeners_are_ready(self, agent_stub, sock_path):
        """
        The key property: start() returns only when _accept_loop has called
        bind() and listen(), so the socket file exists AND the kernel accepts
        connections.
        """
        ready_at_start = None
        ready_at_end = [None]

        def start_and_measure():
            nonlocal ready_at_start
            ready_at_start = time.time()
            _server = A2AServer(agent_stub, sock_path=sock_path)
            _server.start()  # This blocks until ready
            ready_at_end[0] = time.time()
            return _server

        # Run start() in a thread so we can measure
        t = threading.Thread(target=start_and_measure)
        t.start()
        t.join(timeout=6)

        assert t.is_alive() is False, "start() should complete within timeout"

        elapsed = ready_at_end[0] - ready_at_start
        # Should be fast (< 0.5s normally, > 0 because _accept_loop does work)
        assert elapsed < 1.0, f"start() took too long: {elapsed:.2f}s"
