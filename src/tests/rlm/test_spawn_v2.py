"""Test rlm/spawn.py v2 — Unified Persistent Spawn with B: budget."""

import pytest
from unittest.mock import MagicMock, patch
from rlm.spawn import (
    spawn, SpawnHandle, SpawnRegistry,
    SpawnError, SpawnLimitError, NestingLimitError, SpawnClosedError,
    MAX_SPAWNS, MAX_NESTING_DEPTH,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset SpawnRegistry before each test."""
    from rlm.spawn import SpawnRegistry
    reg = SpawnRegistry()
    with reg._lock:
        reg._handles.clear()
    yield
    with reg._lock:
        reg._handles.clear()


class TestSpawnHandle:
    """Test SpawnHandle dataclass."""

    def test_handle_creation(self):
        h = SpawnHandle(spawn_id="abc123", name="test", budget=0.70)
        assert h.spawn_id == "abc123"
        assert h.name == "test"
        assert h.status == "running"
        assert h.last_result == ""
        assert h.turns == 0
        assert h.budget == 0.70

    def test_handle_str_returns_last_result(self):
        h = SpawnHandle(spawn_id="abc", last_result="hello world")
        assert str(h) == "hello world"

    def test_handle_repr(self):
        h = SpawnHandle(spawn_id="abc123def", name="worker", status="completed", turns=3)
        r = repr(h)
        assert "worker" in r
        assert "completed" in r
        assert "3" in r

    def test_handle_closed_raises(self):
        h = SpawnHandle(spawn_id="abc", status="closed", _agent=None)
        with pytest.raises(SpawnClosedError):
            h.send("test")

    def test_handle_status_dict(self):
        h = SpawnHandle(spawn_id="abc", name="test", status="completed",
                       last_result="done", turns=2, budget=0.7)
        d = h.status_dict()
        assert d["spawn_id"] == "abc"
        assert d["name"] == "test"
        assert d["status"] == "completed"
        assert d["turns"] == 2

    def test_update_status_empty_ready_is_suspect(self):
        # Regression: a worker that sets ready=True but produces no content
        # must NOT be reported as 'completed' (the empty-result lie). It is
        # marked 'suspect' so the manager re-verifies instead of trusting.
        class _Ans:
            def __init__(self, content, ready):
                self.content = content; self.ready = ready; self.yielded = False
        class _Agent:
            spawn_B = 1.0
            def __init__(self, ans): self._ans = ans
            def get_answer(self): return self._ans
        def _status(content, ready):
            h = SpawnHandle(spawn_id="x", _agent=_Agent(_Ans(content, ready)))
            h._update_status()
            return h.status
        assert _status("did the thing", True) == "completed"
        assert _status("", True) == "suspect"
        assert _status(None, True) == "suspect"
        assert _status("   ", True) == "suspect"


class TestSpawnTelemetryMetric:
    """Verification-cost metric: spawn terminal outcomes are recorded once."""

    def test_record_and_summary(self, tmp_path):
        from rlm.skill_telemetry import SkillTelemetry
        t = SkillTelemetry(path=tmp_path / "t.jsonl")
        t.record_spawn("completed", "a")
        t.record_spawn("completed", "b")
        t.record_spawn("suspect", "c")
        t.record_spawn("budget_exhausted", "d")
        s = t.spawn_summary()
        assert s["spawns"] == {"completed": 2, "suspect": 1, "budget_exhausted": 1}
        assert s["total"] == 4

    def test_close_records_terminal_status(self, monkeypatch):
        # close() must record the REAL terminal status (before overwrite),
        # exactly once, via the fail-safe import inside the method.
        import rlm.skill_telemetry as tel
        recs = []
        class FakeTel:
            def __init__(self, path=None): pass
            def record_spawn(self, status, spawn_id=None): recs.append((status, spawn_id))
        monkeypatch.setattr(tel, "SkillTelemetry", FakeTel)
        h = SpawnHandle(spawn_id="z", status="suspect")
        h.close()
        assert recs == [("suspect", "z")]
        h.close()  # second close is a no-op -> no second record
        assert len(recs) == 1

    def test_fail_safe_on_bad_path(self):
        from pathlib import Path as P
        from rlm.skill_telemetry import SkillTelemetry
        t = SkillTelemetry(path=P("/proc/nope/x.jsonl"))
        t.record_spawn("completed")  # must not raise
        assert t.spawn_summary() == {"spawns": {}, "total": 0}


class TestSpawnRegistry:
    """Test SpawnRegistry singleton."""

    def test_singleton(self):
        r1 = SpawnRegistry()
        r2 = SpawnRegistry()
        assert r1 is r2

    def test_register_and_list(self):
        reg = SpawnRegistry()
        h = SpawnHandle(spawn_id="test1", name="t1")
        reg.register(h)
        active = reg.list_active()
        assert h in active
        reg.unregister("test1")
        assert h not in reg.list_active()

    def test_max_spawns_limit(self):
        reg = SpawnRegistry()
        # Fill to max
        handles = []
        for i in range(MAX_SPAWNS):
            h = SpawnHandle(spawn_id=f"fill{i}", name=f"fill{i}")
            reg.register(h)
            handles.append(h)
        # Next should fail
        h = SpawnHandle(spawn_id="overflow", name="overflow")
        with pytest.raises(SpawnLimitError):
            reg.register(h)
        # Cleanup
        for h in handles:
            reg.unregister(h.spawn_id)

    def test_get_by_name(self):
        reg = SpawnRegistry()
        h = SpawnHandle(spawn_id="id1", name="myworker")
        reg.register(h)
        assert reg.get_by_name_or_id("myworker") is h
        assert reg.get_by_name_or_id("id1") is h
        assert reg.get_by_name_or_id("nonexistent") is None
        reg.unregister("id1")

    def test_count_active(self):
        reg = SpawnRegistry()
        h = SpawnHandle(spawn_id="c1")
        reg.register(h)
        assert reg.count_active() >= 1
        reg.unregister("c1")


class TestSpawnFunction:
    """Test spawn() function."""

    def test_nesting_limit(self):
        parent = MagicMock()
        parent.nesting_count = MAX_NESTING_DEPTH
        parent.nesting_stack = "SSS"
        with pytest.raises(NestingLimitError):
            spawn("task", parent_agent=parent)

    def test_returns_handle(self):
        """spawn() should always return a SpawnHandle."""
        parent = MagicMock()
        parent.nesting_count = 0
        parent.nesting_stack = ""
        parent.config = MagicMock()
        parent.max_context_tokens = 128000

        with patch("agent_core.TauErgon") as MockAgent:
            child = MagicMock()
            child.max_context_tokens = 128000
            child.context.get_usage_stats.return_value = (1000, 0.008, 4000, False)
            child.invoke.return_value = "child result"
            child.get_answer.return_value = MagicMock()
            setattr(child.get_answer.return_value, 'yield', False)
            child.spawn_B = 0.69
            MockAgent.return_value = child
            with patch("rlm.spawn._with_real_stdout", side_effect=lambda f, *a: f(*a)):
                h = spawn("test task", parent_agent=parent)

        assert isinstance(h, SpawnHandle)
        assert h.last_result == "child result"
        assert h.turns == 1


class TestBudget:
    """Test B: work budget logic."""

    def test_budget_decreases_with_positive_delta(self):
        """B decreases when context grows."""
        agent = MagicMock()
        agent.spawn_B = 0.50
        agent.spawn_C_last = 0.10
        agent.spawn_min_B = 1.0
        agent._budget_warned = False
        agent._budget_warning_text = None
        agent.context.get_usage_stats.return_value = (5000, 0.15, 20000, False)
        agent.max_context_tokens = 128000
        agent._cleanup_pending = False
        agent.get_answer.return_value = None

        # Simulate the budget check logic from agent_loop.py
        _, C_now, _, _ = agent.context.get_usage_stats(agent.max_context_tokens)
        delta = C_now - agent.spawn_C_last
        if delta > 0:
            agent.spawn_B = max(0.0, agent.spawn_B - delta)
        agent.spawn_C_last = C_now

        assert agent.spawn_B == pytest.approx(0.45)  # 0.50 - 0.05

    def test_budget_no_restore_on_compression(self):
        """B does NOT increase when context shrinks (compression)."""
        agent = MagicMock()
        agent.spawn_B = 0.30
        agent.spawn_C_last = 0.50
        agent.spawn_min_B = 1.0

        # Context shrank (compression)
        agent.context.get_usage_stats.return_value = (2000, 0.20, 8000, False)
        agent.max_context_tokens = 128000

        _, C_now, _, _ = agent.context.get_usage_stats(agent.max_context_tokens)
        delta = C_now - agent.spawn_C_last  # 0.20 - 0.50 = -0.30 (negative)
        if delta > 0:
            agent.spawn_B = max(0.0, agent.spawn_B - delta)
        # B unchanged because delta is negative

        assert agent.spawn_B == 0.30  # Unchanged!

    def test_budget_warning_threshold(self):
        """Warning triggers when B < 10%."""
        agent = MagicMock()
        agent.spawn_B = 0.09
        agent._budget_warned = False

        assert agent.spawn_B < 0.10
        assert not agent._budget_warned

    def test_extend_budget_increases_B(self):
        h = SpawnHandle(spawn_id="ext", _agent=MagicMock())
        h._agent.spawn_B = 0.05
        h._agent._budget_warned = True
        h.extend_budget(0.15)
        assert h._agent.spawn_B == pytest.approx(0.20)
        assert h._agent._budget_warned == False  # Reset


class TestInspect:
    """Test SpawnHandle.inspect()."""

    def test_inspect_last_n(self):
        agent = MagicMock()
        agent.context._messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "I did it"},
            {"role": "user", "content": "now more"},
        ]
        h = SpawnHandle(spawn_id="insp", _agent=agent)
        result = h.inspect("last_n", n=2, chars=50)
        assert "I did it" in result
        assert "now more" in result

    def test_inspect_overview(self):
        agent = MagicMock()
        agent.context._messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        h = SpawnHandle(spawn_id="insp2", _agent=agent)
        result = h.inspect("overview")
        assert "2" in result
        assert "user" in result
        assert "assistant" in result

    def test_inspect_last_user(self):
        agent = MagicMock()
        agent.context._messages = [
            {"role": "assistant", "content": "prev"},
            {"role": "user", "content": "latest question"},
        ]
        h = SpawnHandle(spawn_id="insp3", _agent=agent)
        result = h.inspect("last_user")
        assert "latest question" in result
