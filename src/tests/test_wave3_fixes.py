"""Regression tests for wave-3 fixes: B1, I8, B2, B9, B10, B13, B15b."""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))          # src/
sys.path.insert(0, str(_ROOT.parent))   # repo root (dream.py, dream_steps.py)


# ── B1: dream.py PID-file lock (fcntl, no TOCTOU) ────────────────────────────

class TestB1DreamLock:
    def _holder(self, pid_file):
        """Subprocess that holds an flock on pid_file for a few seconds."""
        code = (
            "import fcntl,sys,time\n"
            "f=open(sys.argv[1],'a+')\n"
            "fcntl.flock(f,fcntl.LOCK_EX)\n"
            "print('held',flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n"
        )
        p = subprocess.Popen(
            [sys.executable, "-c", code, str(pid_file), "3"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True,
        )
        assert p.stdout.readline().strip() == "held"  # lock acquired
        return p

    def test_lock_is_fcntl_based_not_check_then_write(self):
        import dream
        src = Path(dream.__file__).read_text()
        fn = src[src.index("def acquire_lock"):src.index("def release_lock")]
        assert "fcntl.flock" in fn, "acquire_lock must use fcntl locking"
        assert "PID_FILE.unlink(missing_ok=True)" not in fn, "no unlink-race cleanup"

    def test_second_instance_refused_while_lock_held(self, tmp_path):
        import dream
        pid_file = tmp_path / "dream.pid"
        with patch.object(dream, "PID_FILE", pid_file):
            holder = self._holder(pid_file)
            try:
                assert dream.acquire_lock() is False, "must refuse when lock held"
            finally:
                holder.kill()
                holder.wait(timeout=5)

    def test_acquire_writes_pid_and_release_clears(self, tmp_path):
        import dream
        pid_file = tmp_path / "dream.pid"
        with patch.object(dream, "PID_FILE", pid_file):
            assert dream.acquire_lock() is True
            assert int(pid_file.read_text().strip()) == __import__("os").getpid()
            dream.release_lock()
            assert not pid_file.exists()
            # re-acquirable after release
            assert dream.acquire_lock() is True
            dream.release_lock()


# ── I8: record_rearch_area wired into step_rearch ────────────────────────────

class TestI8RearchArea:
    def _state(self, tmp_path, dream):
        st = dream.DreamState()
        st.STATE_FILE = tmp_path / "state.json"
        st.data["rearch_area_history"] = {}
        st.data["last_rearch_areas"] = []
        return st

    def test_step_rearch_records_area(self, tmp_path):
        import dream
        import dream_steps
        logger = dream.Logger(tmp_path / "dream.log", dry_run=True)
        git = dream.GitHelper(tmp_path, logger, dry_run=True)
        fake = SimpleNamespace(stdout="src/agent_loop.py\nsrc/agent_core.py\n",
                               returncode=0, stderr="")
        ok = dream.StepResult("rearch:1/1", True, elapsed=0.0, detail="committed")
        with patch.object(git, "_run", lambda *a, **k: fake), \
             patch.object(dream_steps, "_run_tau_and_test",
                          lambda *a, **k: (False, 0.0, True)), \
             patch.object(dream_steps, "_commit_or_revert",
                          lambda *a, **k: ok):
            state = self._state(tmp_path, dream)
            res = dream_steps.step_rearch(logger, git, "test", False,
                                          Path("tau.py"), n=1, state=state,
                                          cycle_num=4)
        assert res and res[0].success
        assert state.data["rearch_area_history"] == {"agent_loop": [4]}, \
            "record_rearch_area must be wired into step_rearch"
        assert state.data["last_rearch_areas"] == ["agent_loop"]

    def test_convergence_skip_after_three_same_area(self, tmp_path):
        import dream
        st = self._state(tmp_path, dream)
        st.data["total_cycles"] = 5
        for c in (3, 4, 5):
            st.record_rearch_area("agent_loop", c)
        assert st.rearch_convergence("agent_loop") == 3
        assert st.should_skip_rearch() is True

    def test_run_cycle_passes_state_into_rearch(self):
        import dream_steps
        src = Path(dream_steps.__file__).read_text()
        assert "step_rearch(logger, git, llm_group, dry_run, agent_bin, n=3, state=state" in src


# ── B2: wiki atomic + locked read-modify-write ───────────────────────────────

class TestB2WikiAtomic:
    def test_add_then_append_keeps_both_entries(self, tmp_path):
        from wiki import Wiki
        w = Wiki(str(tmp_path / "wiki"))
        w.add("topic", "first")
        w.add("topic", "second")
        files = list((tmp_path / "wiki" / "topic").glob("*.md"))
        assert len(files) == 1
        body = files[0].read_text()
        assert "first" in body and "second" in body
        assert "[topic](topic/)" in (tmp_path / "wiki" / "INDEX.md").read_text()

    def test_concurrent_log_appends_not_dropped(self, tmp_path):
        from wiki import Wiki
        w = Wiki(str(tmp_path / "wiki"))
        n = 8
        ts = [threading.Thread(target=w.log, args=(f"entry-{i}",)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        log = (tmp_path / "wiki" / "log.md").read_text()
        for i in range(n):
            assert f"entry-{i}" in log, f"concurrent append dropped entry-{i}"

    def test_concurrent_add_same_topic_not_dropped(self, tmp_path):
        from wiki import Wiki
        w = Wiki(str(tmp_path / "wiki"))
        ts = [threading.Thread(target=w.add, args=("t", f"x{i}")) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        files = list((tmp_path / "wiki" / "t").glob("*.md"))
        body = "".join(f.read_text() for f in files)
        for i in range(6):
            assert f"x{i}" in body, f"concurrent add dropped x{i}"

    def test_atomic_write_leaves_no_tmp_and_uses_replace(self, tmp_path):
        from wiki import Wiki
        w = Wiki(str(tmp_path / "wiki"))
        calls = []
        real_replace = __import__("os").replace
        with patch("wiki.os.replace", side_effect=lambda a, b: (calls.append(a), real_replace(a, b))[1]):
            w._atomic_write(tmp_path / "f.md", "hello")
        assert calls, "_atomic_write must go through os.replace"
        assert (tmp_path / "f.md").read_text() == "hello"
        assert not list(tmp_path.glob("*.tmp*"))

    def test_lock_file_not_inside_wiki_dir(self, tmp_path):
        from wiki import Wiki
        w = Wiki(str(tmp_path / "wiki"))
        w.add("t", "x")
        assert not list((tmp_path / "wiki").glob(".wiki.lock*"))
        assert (tmp_path / ".wiki.lock").exists()


# ── B9: host_bridge._save_json atomic ────────────────────────────────────────

class TestB9SaveJsonAtomic:
    def _bridge(self):
        from rlm.host_bridge import HostBridge
        return HostBridge.__new__(HostBridge)

    def test_roundtrip(self, tmp_path):
        hb = self._bridge()
        p = tmp_path / "d.json"
        hb._save_json(p, {"a": 1})
        assert hb._load_json(p) == {"a": 1}

    def test_failed_dump_keeps_previous_content(self, tmp_path):
        import rlm.host_bridge as hb_mod
        hb = self._bridge()
        p = tmp_path / "d.json"
        hb._save_json(p, {"keep": True})

        def boom(obj, f, **kw):
            f.write("{\"partial\": ")  # partial write then explode
            raise RuntimeError("boom")

        with patch.object(hb_mod.json, "dump", boom):
            with pytest.raises(RuntimeError):
                hb._save_json(p, {"keep": False})
        assert hb._load_json(p) == {"keep": True}, "truncation must not corrupt"
        assert not list(tmp_path.glob("*.tmp*")), "temp file must be cleaned up"


# ── B10: a2a_cli --pid/--name without --card/query must not start an agent ───

def _cli_args(**kw):
    d = dict(list=False, list_all=False, listjson=False, listjson_all=False,
             list_sessions=False, pid=None, name=None, card=False,
             inputs=[], timeout=5.0)
    d.update(kw)
    return SimpleNamespace(**d)


class TestB10CliFallthrough:
    def test_pid_without_card_or_query_exits(self):
        import a2a_cli
        with patch.object(a2a_cli, "_handle_cli_query") as q, \
             patch.object(a2a_cli, "_handle_cli_card") as c:
            with pytest.raises(SystemExit) as ei:
                a2a_cli.a2a_cli_mode(_cli_args(pid=4242))
            assert ei.value.code != 0
            assert not q.called and not c.called

    def test_name_without_card_or_query_exits(self):
        import a2a_cli
        with patch.object(a2a_cli, "list_agents",
                          lambda: [{"name": "bob", "pid": 999}]):
            with pytest.raises(SystemExit) as ei:
                a2a_cli.a2a_cli_mode(_cli_args(name="bob"))
            assert ei.value.code != 0

    def test_card_path_still_works(self):
        import a2a_cli
        with patch.object(a2a_cli, "_handle_cli_card") as c:
            with pytest.raises(SystemExit):
                a2a_cli.a2a_cli_mode(_cli_args(pid=4242, card=True))
            assert c.called

    def test_query_path_still_works(self):
        import a2a_cli
        with patch.object(a2a_cli, "_handle_cli_query") as q:
            with pytest.raises(SystemExit):
                a2a_cli.a2a_cli_mode(_cli_args(pid=4242, inputs=["hi"]))
            assert q.called


# ── B13: failure branch keeps partial stdout ─────────────────────────────────

class TestB13PartialOutputOnFailure:
    def test_failure_branch_appends_output_before_error(self):
        import ast
        src = (_ROOT / "agent_loop.py").read_text()
        tree = ast.parse(src)
        target = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "run_rlm_loop")
        branches = [n for n in ast.walk(target)
                    if isinstance(n, ast.If)
                    and isinstance(n.test, ast.UnaryOp)
                    and isinstance(n.test.operand, ast.Attribute)
                    and n.test.operand.attr == "success"]
        assert branches, "failure branch (not repl_result.success) not found"
        body = branches[0].body

        def appends(stmts):
            found = []
            for st in stmts:
                found.extend(n for n in ast.walk(st)
                             if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "append")
            return found

        def arg_key(call):
            a = call.args[0] if call.args else None
            return getattr(a, "attr", None) or getattr(a, "value", None) or ast.dump(a)[:40]

        keys = [arg_key(c) for c in appends(body)]
        assert "output" in keys, "failure branch must append repl_result.output"
        assert any("error_msg" in k for k in keys), "error message still appended"
        assert keys.index("output") < next(i for i, k in enumerate(keys) if "error_msg" in k), \
            "partial output must precede the error"


# ── B15b: session probe uses the agent pid, not the filename ppid ────────────

class _Stub:
    agent_name = "probe-agent"
    model_name = "probe-model"
    original_cwd = Path("/tmp")
    context = []
    original_task = None
    current_group_name = "test"
    max_context_tokens = 128000
    input_queue = None
    _pending_a2a_responses = {}
    _turn_active = False
    audit_file = None

    class _Session:
        prefix = "stub_prefix"

    _session = _Session()

    @property
    def nesting_count(self):
        return 0


class TestB15bSessionProbe:
    def test_probe_uses_metadata_pid_not_filename_ppid(self, tmp_path):
        from a2a_server import A2AServer
        from a2a_sessions import _scan_sessions

        agent_pid, ppid = 4711, 4712
        ctx = tmp_path / f"{ppid}_20250115120000_1.context"
        ctx.write_text(json.dumps({
            "metadata": {"pid": agent_pid, "parent_pid": ppid,
                         "agent_name": "meta-agent", "start_time": time.time()},
            "messages": [{"role": "user", "content": "hi"}],
        }))

        sock = f"/tmp/taua2a-{agent_pid}.sock"
        if Path(sock).exists():
            Path(sock).unlink()
        server = A2AServer(_Stub(), sock_path=sock)
        server.start()
        try:
            sessions = _scan_sessions(tmp_path)
            assert len(sessions) == 1
            s = sessions[0]
            assert s["pid"] == agent_pid, "must report the agent's own pid"
            assert s["socket_path"] == sock
            assert s["status"] == "active", \
                "probe must hit the getpid socket, not the ppid one"
            # card wins for shared fields; parent_pid must not be the agent pid
            assert s["parent_pid"] != agent_pid
        finally:
            server.stop()
            if Path(sock).exists():
                Path(sock).unlink()

    def test_legacy_bare_context_falls_back_to_filename_pid(self, tmp_path):
        from a2a_sessions import _scan_sessions
        ctx = tmp_path / "99999_20250115120000_1.context"
        ctx.write_text(json.dumps([{"role": "user", "content": "x"}]))
        s = _scan_sessions(tmp_path)[0]
        assert s["pid"] == 99999
        assert s["status"] == "stale"
