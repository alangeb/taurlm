"""Tests for the A2A agent_card response (agent_a2a.py).

Covers the core agent_card fields plus the A2A extension-004 session metadata:
``original_task``, ``start_time``, ``parent_pid``, ``llm_group``,
``max_context_tokens`` and ``session_id``.
"""

import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_a2a import A2AServer, get_agent_card

# Add parent directory to path (mirrors other test modules).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Minimal duck-typed agent stub with the extension-004 metadata fields
# ---------------------------------------------------------------------------
class _MetadataAgentStub:
    """Agent stub exposing the extension-004 metadata attributes."""

    def __init__(
        self,
        *,
        original_task=None,
        start_time=None,
        current_group_name="test",
        max_context_tokens=128000,
        prefix="stub_prefix",
        turn_active=False,
        nesting_count=0,
        audit_file=None,
    ):
        self.agent_name = "test-agent"
        self.model_name = "test-model"
        self.original_cwd = Path("/tmp")
        self.context = []
        self._start_time = start_time if start_time is not None else time.time()
        self.original_task = original_task
        self.current_group_name = current_group_name
        self.max_context_tokens = max_context_tokens
        self.input_queue = None
        self._pending_a2a_responses: dict = {}
        self._turn_active = turn_active
        self._nesting_count = nesting_count
        self.audit_file = audit_file

        class _Session:
            pass

        self._session = _Session()
        self._session.prefix = prefix

    @property
    def nesting_count(self):
        return self._nesting_count


@pytest.fixture
def sock_path():
    path = "/tmp/test_taua2a_metadata.sock"
    p = Path(path)
    if p.exists():
        p.unlink()
    yield path
    p = Path(path)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Field presence / value tests (direct _build_agent_card)
# ---------------------------------------------------------------------------
class TestAgentCardMetadataFields:
    """All six extension-004 fields must appear in the agent card."""

    REQUIRED_NEW_FIELDS = [
        "original_task",
        "start_time",
        "parent_pid",
        "llm_group",
        "max_context_tokens",
        "session_id",
    ]

    def test_card_includes_all_six_new_fields(self):
        """agent_card response includes all six extension-004 fields."""
        server = A2AServer(_MetadataAgentStub(), sock_path="/tmp/_unused.sock")
        card = server._build_agent_card()

        for field in self.REQUIRED_NEW_FIELDS:
            assert field in card, f"Missing field: {field}"

    def test_card_values_match_agent(self):
        """Each new field is sourced from the agent as specified."""
        now = time.time()
        agent = _MetadataAgentStub(
            original_task="Investigate the build system",
            start_time=now,
            current_group_name="cuda",
            max_context_tokens=180000,
            prefix="12345_20250115120000_1",
        )
        server = A2AServer(agent, sock_path="/tmp/_unused.sock")
        card = server._build_agent_card()

        assert card["original_task"] == "Investigate the build system"
        assert card["start_time"] == now
        assert card["parent_pid"] == os.getppid()
        assert card["llm_group"] == "cuda"
        assert card["max_context_tokens"] == 180000
        assert card["session_id"] == "12345_20250115120000_1"

    def test_start_time_is_valid_epoch_float(self):
        """start_time is a float within the last 60s of the test."""
        before = time.time()
        agent = _MetadataAgentStub()
        card = A2AServer(agent, sock_path="/tmp/_unused.sock")._build_agent_card()
        after = time.time()

        assert isinstance(card["start_time"], float)
        assert before <= card["start_time"] <= after
        # And it's within the last 60 seconds.
        assert card["start_time"] >= time.time() - 60

    def test_llm_group_matches_configured_group(self):
        """llm_group matches the agent's current_group_name."""
        agent = _MetadataAgentStub(current_group_name="production")
        card = A2AServer(agent, sock_path="/tmp/_unused.sock")._build_agent_card()
        assert card["llm_group"] == "production"

    def test_optional_fields_default_to_none_for_minimal_agent(self):
        """An agent without the new attrs still produces a valid card."""

        class _BareStub:
            agent_name = "bare"
            model_name = "bare-model"
            original_cwd = Path("/tmp")
            context = []
            _start_time = time.time()

        server = A2AServer(_BareStub(), sock_path="/tmp/_unused.sock")
        card = server._build_agent_card()

        assert card["original_task"] is None
        assert card["start_time"] == _BareStub._start_time
        assert card["parent_pid"] == os.getppid()
        assert card["llm_group"] is None
        assert card["max_context_tokens"] is None
        assert card["session_id"] is None

    def test_card_is_json_serialisable(self):
        """The full agent card must be JSON-serialisable (sent over sockets)."""
        import json

        card = A2AServer(_MetadataAgentStub(), sock_path="/tmp/_unused.sock")._build_agent_card()
        json.dumps(card)  # must not raise


# ---------------------------------------------------------------------------
# End-to-end over the Unix socket (client -> server -> agent_card)
# ---------------------------------------------------------------------------
class TestAgentCardOverSocket:
    """Verify the agent card round-trips through the socket protocol."""

    def test_get_agent_card_returns_new_fields(self, sock_path):
        """get_agent_card() returns the six new fields end-to-end."""
        start = time.time()
        server = A2AServer(_MetadataAgentStub(), sock_path=sock_path)
        server.start()
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect(sock_path)
            card = get_agent_card(client)
            client.close()

            assert card["type"] == "agent_card"
            for field in TestAgentCardMetadataFields.REQUIRED_NEW_FIELDS:
                assert field in card
            assert card["parent_pid"] == os.getppid()
            assert card["start_time"] >= start
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Real TauErgon integration: tracking + real _build_agent_card
# ---------------------------------------------------------------------------
class TestRealAgentMetadata:
    """Tests against a real TauErgon instance."""

    def test_start_time_set_on_init(self, test_config):
        """TauErgon.__init__ records _start_time as a recent epoch float."""
        from agent_core import TauErgon

        before = time.time()
        with patch.object(TauErgon, "invoke_loop"):
            agent = TauErgon(
                config=test_config,
                base_url="http://test:8000/v1",
                model="test-model",
            )
        after = time.time()

        assert hasattr(agent, "_start_time")
        assert isinstance(agent._start_time, float)
        assert before <= agent._start_time <= after

    def test_original_task_set_on_first_user_message(self, test_config):
        """original_task is set to the content of the first user message."""
        from agent_core import TauErgon

        with patch.object(TauErgon, "invoke_loop", return_value="ok"):
            agent = TauErgon(
                config=test_config,
                base_url="http://test:8000/v1",
                model="test-model",
            )
            assert agent.original_task is None
            agent.invoke("Investigate the build system")
            agent.invoke("A follow-up question")

        assert agent.original_task == "Investigate the build system"

    def test_real_agent_card_metadata(self, test_config):
        """A real TauErgon agent card exposes the configured metadata."""
        from agent_core import TauErgon

        with patch.object(TauErgon, "invoke_loop"):
            agent = TauErgon(
                config=test_config,
                base_url="http://test:8000/v1",
                model="test-model",
            )
        server = A2AServer(agent, sock_path="/tmp/_unused.sock")
        card = server._build_agent_card()

        # test_config uses group "test" with max_context_tokens=200000.
        assert card["llm_group"] == "test"
        assert card["max_context_tokens"] == 200000
        assert isinstance(card["start_time"], float)
        assert card["parent_pid"] == os.getppid()
        # original_task is None until the first user message.
        assert card["original_task"] is None
        # session_id comes from the session prefix.
        assert card["session_id"] is not None
        assert isinstance(card["session_id"], str)


# ---------------------------------------------------------------------------
# AgentSessionManager.prefix property (session_id source)
# ---------------------------------------------------------------------------
class TestSessionPrefix:
    """The session prefix property exposes session_id."""

    def test_prefix_from_explicit_context_file(self, tmp_path):
        """prefix falls back to the context file stem when not set up."""
        from agent_session import AgentSessionManager

        ctx = tmp_path / "12345_20250115120000_1.context"
        mgr = AgentSessionManager(setup_files=False, context_file=ctx)
        assert mgr.prefix == "12345_20250115120000_1"

    def test_prefix_none_without_setup(self):
        """prefix is None when no files were set up and none provided."""
        from agent_session import AgentSessionManager

        mgr = AgentSessionManager(setup_files=False)
        assert mgr.prefix is None


# ---------------------------------------------------------------------------
# TAU_007 — --list-sessions CLI command (session metadata from LOG_DIR)
# ---------------------------------------------------------------------------
# Required fields in each session dict (mirrors A2A_EXTENSION_007 spec).
REQUIRED_SESSION_FIELDS = [
    "id",
    "pid",
    "status",
    "agent_name",
    "model",
    "working_dir",
    "context_length",
    "message_count",
    "start_time",
    "uptime",
    "parent_pid",
    "llm_group",
    "max_context_tokens",
    "socket_path",
    "context_file",
    "audit_file",
    "is_sanity",
]
# Regex for the context-file naming convention: {ppid}_{YYYYMMDDHHMMSS}_{N}
_SESSION_RE = re.compile(r"^(\d+)_\d+_\d+\.context$")


def _write_context(path, messages_or_data):
    """Write a context file (bare array or metadata-wrapped dict)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages_or_data, f)


class TestListSessions:
    """Tests for the --list-sessions session-metadata API."""

    @pytest.fixture
    def stale_dir(self, tmp_path):
        """A LOG_DIR containing only stale (no-socket) context files."""
        d = tmp_path / "log_sanity"
        d.mkdir()
        # Bare-array format (legacy, no metadata).
        _write_context(
            d / "12345_20250115120000_1.context",
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ],
        )
        # Metadata-wrapped format (TAU_005).
        _write_context(
            d / "12346_20250115120000_1.context",
            {
                "metadata": {
                    "session_id": "12346_20250115120000_1",
                    "pid": 12346,
                    "parent_pid": 1000,
                    "working_dir": "/home/user/project",
                    "start_time": 1721034680.0,
                    "model": "qwertron-32b",
                    "llm_group": "cuda",
                    "agent_name": "default",
                    "original_task": "What is 1+1?",
                },
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ],
            },
        )
        # Non-matching filename (should be ignored).
        _write_context(
            d / "not_a_session.context",
            [{"role": "system", "content": "ignored"}],
        )
        return d

    @pytest.fixture
    def logtest_dir(self, tmp_path):
        """A LOG_DIR whose path contains 'logtest'."""
        d = tmp_path / "logtest_sessions"
        d.mkdir()
        _write_context(
            d / "12345_20250115120000_1.context",
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q1"},
            ],
        )
        return d

    def test_list_sessions_json_output(self, stale_dir, capsys):
        """--list-sessions prints JSON with a 'sessions' array and exits 0."""
        from agent_a2a import _list_sessions_json

        with patch("agent_session.LOG_DIR", stale_dir):
            with pytest.raises(SystemExit) as exc_info:
                _list_sessions_json()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        assert len(data["sessions"]) == 2  # two matching context files

    def test_each_session_has_all_required_fields(self, stale_dir):
        """Each session dict contains all 16 required fields from the spec."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        for session in sessions:
            for field in REQUIRED_SESSION_FIELDS:
                assert field in session, f"Missing field: {field}"

    def test_is_sanity_true_for_logtest_dir(self, logtest_dir):
        """is_sanity is True when the log directory path contains 'logtest'."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(logtest_dir)

        assert len(sessions) == 1
        assert sessions[0]["is_sanity"] is True

    def test_is_sanity_false_for_normal_dir(self, stale_dir):
        """is_sanity is False when the log directory is not a sanity path."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        for session in sessions:
            assert session["is_sanity"] is False

    def test_status_stale_when_no_socket(self, stale_dir):
        """Sessions without a socket file have status 'stale'."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        for session in sessions:
            assert session["status"] == "stale"

    def test_status_unreachable_with_dead_socket(self, tmp_path):
        """A socket file that doesn't accept connections → 'unreachable'."""
        from agent_a2a import _scan_sessions

        d = tmp_path / "dead_sock_dir"
        d.mkdir()
        _write_context(
            d / "19999_20250115120000_1.context",
            [{"role": "system", "content": "sys"}],
        )

        dead_sock = Path("/tmp/taua2a-19999.sock")
        if dead_sock.exists():
            dead_sock.unlink()
        dead_sock.write_text("not a socket")
        try:
            sessions = _scan_sessions(d)
            assert len(sessions) == 1
            assert sessions[0]["status"] == "unreachable"
        finally:
            if dead_sock.exists():
                dead_sock.unlink()

    def test_status_active_with_real_socket(self, tmp_path):
        """A live A2A server → status 'active' with card values."""
        from agent_a2a import _scan_sessions

        d = tmp_path / "active_dir"
        d.mkdir()
        _write_context(
            d / "19998_20250115120000_1.context",
            [{"role": "system", "content": "sys"}],
        )

        pid = 19998
        sock_path = f"/tmp/taua2a-{pid}.sock"
        p = Path(sock_path)
        if p.exists():
            p.unlink()

        server = A2AServer(_MetadataAgentStub(), sock_path=sock_path)
        server.start()
        try:
            sessions = _scan_sessions(d)
            active = [s for s in sessions if s["pid"] == pid]
            assert len(active) == 1
            assert active[0]["status"] == "active"
            # Active sessions get values from the agent card.
            assert active[0]["agent_name"] == "test-agent"
            assert active[0]["model"] == "test-model"
            assert active[0]["working_dir"] == "/tmp"
            assert active[0]["max_context_tokens"] == 128000
        finally:
            server.stop()

    def test_metadata_values_used_for_stale(self, stale_dir):
        """When the context file has a metadata block, its values are used."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        # The metadata-wrapped file (pid 12346).
        meta_session = next(s for s in sessions if s["pid"] == 12346)

        assert meta_session["agent_name"] == "default"
        assert meta_session["model"] == "qwertron-32b"
        assert meta_session["working_dir"] == "/home/user/project"
        assert meta_session["start_time"] == 1721034680.0
        assert meta_session["parent_pid"] == 1000
        assert meta_session["llm_group"] == "cuda"
        assert meta_session["context_length"] == 2
        assert meta_session["message_count"] == 2
        assert meta_session["uptime"] is not None
        assert meta_session["status"] == "stale"

    def test_bare_array_context_no_metadata(self, stale_dir):
        """A bare-array context file (no metadata) yields None for metadata fields."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        bare_session = next(s for s in sessions if s["pid"] == 12345)

        assert bare_session["agent_name"] is None
        assert bare_session["model"] is None
        assert bare_session["working_dir"] is None
        assert bare_session["start_time"] is None
        assert bare_session["parent_pid"] is None
        assert bare_session["llm_group"] is None
        assert bare_session["max_context_tokens"] is None
        # Message count comes from the array length.
        assert bare_session["context_length"] == 4
        assert bare_session["message_count"] == 4

    def test_socket_path_and_file_fields(self, stale_dir):
        """socket_path, context_file, and audit_file are correctly populated."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        for session in sessions:
            assert session["socket_path"] == f"/tmp/taua2a-{session['pid']}.sock"
            assert session["context_file"].endswith(f"{session['id']}.context")
            assert session["audit_file"].endswith(f"{session['id']}.audit")

    def test_id_and_pid_from_filename(self, stale_dir):
        """id is the prefix and pid is the first number of the context filename."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        for session in sessions:
            match = _SESSION_RE.match(f"{session['id']}.context")
            assert match is not None
            assert session["pid"] == int(match.group(1))

    def test_non_matching_filenames_ignored(self, stale_dir):
        """Context files not matching the naming convention are skipped."""
        from agent_a2a import _scan_sessions

        sessions = _scan_sessions(stale_dir)

        ids = {s["id"] for s in sessions}
        assert "not_a_session" not in ids
        assert len(sessions) == 2

    def test_empty_log_dir_returns_empty(self, tmp_path):
        """An empty or non-existent LOG_DIR yields an empty sessions list."""
        from agent_a2a import _scan_sessions

        empty_dir = tmp_path / "empty_log"
        empty_dir.mkdir()
        assert _scan_sessions(empty_dir) == []

        nonexistent = tmp_path / "does_not_exist"
        assert _scan_sessions(nonexistent) == []


# ---------------------------------------------------------------------------
# A2A status handler tests
# ---------------------------------------------------------------------------
class TestStatusHandler:
    """Tests for the ``{"type": "status"}`` A2A handler."""

    _REQUIRED_FIELDS = [
        "type",
        "pid",
        "turn_active",
        "context_length",
        "uptime",
        "nesting_count",
        "last_audit_mtime",
    ]

    def _send_status_request(self, sock_path):
        """Connect, send a status request, and return the parsed response."""
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect(sock_path)
        client.send(json.dumps({"type": "status"}).encode() + b"\n")
        raw = b""
        while True:
            chunk = client.recv(4096)
            raw += chunk
            if b"\n" in raw:
                break
        client.close()
        return json.loads(raw.decode())

    def test_status_returns_all_required_fields(self, sock_path):
        """Status response contains all 7 required fields."""
        server = A2AServer(_MetadataAgentStub(), sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            for field in self._REQUIRED_FIELDS:
                assert field in resp, f"Missing field: {field}"
        finally:
            server.stop()

    def test_status_type_is_status_response(self, sock_path):
        """type field is 'status_response'."""
        server = A2AServer(_MetadataAgentStub(), sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert resp["type"] == "status_response"
        finally:
            server.stop()

    def test_status_pid_matches_os_getpid(self, sock_path):
        """pid in status response matches os.getpid()."""
        server = A2AServer(_MetadataAgentStub(), sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert resp["pid"] == os.getpid()
        finally:
            server.stop()

    def test_status_context_length_matches_agent(self, sock_path):
        """context_length matches len(agent.context)."""
        agent = _MetadataAgentStub()
        agent.context = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        server = A2AServer(agent, sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert resp["context_length"] == len(agent.context)
        finally:
            server.stop()

    def test_status_nesting_count_matches_agent(self, sock_path):
        """nesting_count matches agent.nesting_count."""
        agent = _MetadataAgentStub(nesting_count=5)
        server = A2AServer(agent, sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert resp["nesting_count"] == 5
        finally:
            server.stop()

    def test_status_turn_active_matches_agent(self, sock_path):
        """turn_active matches agent._turn_active."""
        agent = _MetadataAgentStub(turn_active=True)
        server = A2AServer(agent, sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert resp["turn_active"] is True
        finally:
            server.stop()

    def test_status_audit_mtime_none_when_no_audit_file(self, sock_path):
        """last_audit_mtime is None when agent has no audit_file."""
        agent = _MetadataAgentStub(audit_file=None)
        server = A2AServer(agent, sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert resp["last_audit_mtime"] is None
        finally:
            server.stop()

    def test_status_audit_mtime_valid_when_audit_file_exists(self, sock_path, tmp_path):
        """last_audit_mtime is a valid float when audit file exists."""
        audit = tmp_path / "test.audit"
        audit.write_text("some audit data")
        agent = _MetadataAgentStub(audit_file=str(audit))
        server = A2AServer(agent, sock_path=sock_path)
        server.start()
        try:
            resp = self._send_status_request(sock_path)
            assert isinstance(resp["last_audit_mtime"], float)
            assert resp["last_audit_mtime"] > 0
        finally:
            server.stop()
