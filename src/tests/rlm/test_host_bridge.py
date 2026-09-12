"""Tests for RLM Host Bridge (rlm/host_bridge.py)

Tests cover:
- HostResponse creation and attributes
- HostBridge request routing
- Goal handler (set/get/update/clear)
- Heartbeat handler (set/get/clear)
- Compact handler
- Agent message handler (send/receive)
- Error handling for invalid requests
- Edge cases
- Thread safety
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from rlm.host_bridge import HostResponse, HostBridge


class TestHostResponse:
    """Tests for HostResponse dataclass."""

    def test_create_response(self):
        """Test creating a basic HostResponse."""
        resp = HostResponse(success=True, data={"result": "ok"})
        assert resp.success is True
        assert resp.data == {"result": "ok"}
        assert resp.error == ""

    def test_response_error(self):
        """Test HostResponse with error."""
        resp = HostResponse(success=False, error="Something went wrong")
        assert resp.success is False
        assert resp.error == "Something went wrong"

    def test_response_empty_data(self):
        """Test HostResponse with empty data."""
        resp = HostResponse(success=True, data={})
        assert resp.data == {}

    def test_response_default_values(self):
        """Test HostResponse with default values."""
        resp = HostResponse(success=True)
        assert resp.data == {}
        assert resp.error == ""

    def test_response_complex_data(self):
        """Test HostResponse with complex data."""
        data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        resp = HostResponse(success=True, data=data)
        assert resp.data == data

    def test_response_both_data_and_error(self):
        """Test HostResponse with both data and error."""
        resp = HostResponse(success=True, data={"partial": True}, error="warning")
        assert resp.success is True
        assert resp.data == {"partial": True}
        assert resp.error == "warning"


class TestHostBridge:
    """Tests for HostBridge class."""

    def test_create_bridge(self):
        """Test creating a HostBridge."""
        bridge = HostBridge()
        assert bridge is not None

    def test_handle_goal_set_action(self):
        """Test handling goal set action."""
        bridge = HostBridge()
        resp = bridge.request("goal", "set", content="test goal")
        assert resp.success is True
        assert resp.data["content"] == "test goal"
        assert resp.data["status"] == "active"
        assert "created_at" in resp.data
        assert "updated_at" in resp.data

    def test_handle_goal_get_action(self):
        """Test handling goal get action."""
        bridge = HostBridge()
        # Set a goal first
        bridge.request("goal", "set", content="my goal")
        # Get it back
        resp = bridge.request("goal", "get")
        assert resp.success is True
        assert resp.data["content"] == "my goal"

    def test_handle_goal_update_action(self):
        """Test handling goal update action."""
        bridge = HostBridge()
        bridge.request("goal", "set", content="original")
        resp = bridge.request("goal", "update", progress=50)
        assert resp.success is True
        assert resp.data["progress"] == 50
        assert resp.data["content"] == "original"

    def test_handle_goal_clear_action(self):
        """Test handling goal clear action."""
        bridge = HostBridge()
        bridge.request("goal", "set", content="to clear")
        resp = bridge.request("goal", "clear")
        assert resp.success is True
        assert resp.data["cleared"] is True
        # Verify goal is gone
        resp = bridge.request("goal", "get")
        assert resp.data == {}

    def test_handle_heartbeat_set_action(self):
        """Test handling heartbeat set action."""
        bridge = HostBridge()
        resp = bridge.request("heartbeat", "set", interval_seconds=60)
        assert resp.success is True
        assert resp.data["enabled"] is True
        assert resp.data["interval_seconds"] == 60
        assert "next_beat" in resp.data

    def test_handle_heartbeat_get_action(self):
        """Test handling heartbeat get action."""
        bridge = HostBridge()
        bridge.request("heartbeat", "set", interval_seconds=120)
        resp = bridge.request("heartbeat", "get")
        assert resp.success is True
        assert resp.data["interval_seconds"] == 120

    def test_handle_heartbeat_clear_action(self):
        """Test handling heartbeat clear action."""
        bridge = HostBridge()
        bridge.request("heartbeat", "set", interval_seconds=30)
        resp = bridge.request("heartbeat", "clear")
        assert resp.success is True
        assert resp.data["cleared"] is True

    def test_handle_agent_message_send_action(self):
        """Test handling agent_message send action."""
        bridge = HostBridge()
        resp = bridge.request("agent_message", "send", content="hello", sender="test")
        assert resp.success is True
        assert resp.data["sent"] is True
        assert "file" in resp.data

    def test_handle_agent_message_receive_action(self):
        """Test handling agent_message receive action."""
        bridge = HostBridge()
        # Send a message first
        bridge.request("agent_message", "send", content="test msg", sender="sender1")
        # Receive it
        resp = bridge.request("agent_message", "receive")
        assert resp.success is True
        assert resp.data["count"] >= 1
        assert len(resp.data["messages"]) >= 1

    def test_handle_unknown_request_type(self):
        """Test handling unknown request type returns error response."""
        bridge = HostBridge()
        resp = bridge.request("unknown_type", "action")
        assert resp.success is False
        assert "Unknown request type" in resp.error
        assert "unknown_type" in resp.error

    def test_handle_empty_request_type(self):
        """Test handling empty request type."""
        bridge = HostBridge()
        resp = bridge.request("", "action")
        assert resp.success is False
        assert "Unknown request type" in resp.error

    def test_handle_unknown_goal_action(self):
        """Test handling unknown goal action."""
        bridge = HostBridge()
        resp = bridge.request("goal", "unknown_action")
        assert resp.success is False
        assert "Unknown goal action" in resp.error

    def test_multiple_requests(self):
        """Test handling multiple requests."""
        bridge = HostBridge()
        for i in range(10):
            resp = bridge.request("goal", "set", content=f"goal {i}")
            assert resp.success is True
            assert resp.data["content"] == f"goal {i}"

    def test_concurrent_requests(self):
        """Test handling concurrent requests (thread safety)."""
        bridge = HostBridge()
        results = []
        errors = []

        def make_request(idx):
            try:
                resp = bridge.request("goal", "set", content=f"goal {idx}")
                results.append(resp)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=make_request, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 10
        assert all(r.success for r in results)

    def test_exception_in_handler_returns_error_response(self):
        """Test that exceptions in handlers return error responses."""
        bridge = HostBridge()
        # Monkey patch to raise exception
        original = bridge._handle_goal
        bridge._handle_goal = lambda *a, **k: 1/0
        try:
            resp = bridge.request("goal", "set", content="test")
            assert resp.success is False
            assert "ZeroDivisionError" in resp.error
        finally:
            bridge._handle_goal = original

    def test_namespace_injection(self):
        """Test that host_request is injectable into namespace."""
        bridge = HostBridge()
        ns = bridge.get_initial_namespace()
        assert "host_request" in ns
        assert callable(ns["host_request"])
    def test_host_request_in_namespace_works(self):
        """Test that host_request from namespace works."""
        bridge = HostBridge()
        ns = bridge.get_initial_namespace()
        resp = ns["host_request"]("goal", "set", content="test")
        assert resp.success is True
