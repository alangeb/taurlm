"""Tests for REPL execution timeout (SIGALRM-based)."""

import signal
import sys
import time

import pytest


@pytest.fixture
def kernel():
    """Create a kernel with short timeout for fast tests."""
    from rlm.kernel import PythonKernel
    return PythonKernel(python_timeout_seconds=3.0, bash_timeout_seconds=5.0)


class TestPythonTimeout:
    def test_timeout_fires(self, kernel):
        """A blocking sleep should be interrupted by SIGALRM."""
        result = kernel.execute("import time; time.sleep(30)")
        assert result.success is False
        assert "timed out" in result.error.lower()
        assert result.exception_type == "_REPLTimeout"

    def test_timeout_preserves_output(self, kernel):
        """Output printed before the timeout should be captured."""
        code = "print('before timeout'); import time; time.sleep(30)"
        result = kernel.execute(code)
        assert result.success is False
        assert "before timeout" in result.output

    def test_timeout_preserves_namespace(self, kernel):
        """Variables set before the timeout should persist."""
        kernel.execute("x = 42")
        kernel.execute("import time; time.sleep(30)")
        assert kernel.namespace.get("x") == 42

    def test_timeout_restores_signal_handler(self, kernel):
        """Custom SIGALRM handler should be restored after timeout."""
        called = []

        def custom_handler(signum, frame):
            called.append(True)

        old = signal.signal(signal.SIGALRM, custom_handler)
        kernel.execute("import time; time.sleep(30)")
        current = signal.getsignal(signal.SIGALRM)
        assert current is custom_handler, "Signal handler was not restored"
        signal.signal(signal.SIGALRM, old)

    def test_no_timeout_on_fast_code(self, kernel):
        """Fast code should complete without timeout."""
        result = kernel.execute("y = 1 + 1")
        assert result.success is True
        assert kernel.namespace.get("y") == 2


class TestBashTimeout:
    def test_default_is_180(self):
        """Default bash timeout should be 180s."""
        from rlm.kernel import PythonKernel
        k = PythonKernel()
        assert k.bash_timeout_seconds == 180.0  # class default

    def test_config_default_180(self):
        """Config default should be 180s."""
        from agent_config import REPLConfig
        cfg = REPLConfig()
        assert cfg.bash_timeout_seconds == 180.0
        assert cfg.python_timeout_seconds == 180.0


class TestTimeoutClass:
    def test_is_base_exception(self):
        """_REPLTimeout must be BaseException, not Exception."""
        from rlm.kernel import _REPLTimeout
        assert issubclass(_REPLTimeout, BaseException)
        assert not issubclass(_REPLTimeout, Exception)


class TestOrphanCleanup:
    @staticmethod
    def _sleeps_in_pgroup(pgroup_pids: set) -> set:
        """Sleep PIDs whose process group was created by our tracked Popen(s).

        The kernel launches bash with start_new_session=True, so the bash leader
        is its own process-group leader (pgid == pid) and every descendant sleep
        inherits that pgid. Checking ONLY these groups makes the test immune to
        unrelated sleeps from other concurrent agent sessions on the same box
        (root cause of repeated false FAILs: global `pgrep sleep` deltas picked
        up other sessions' background sleeps that started inside the window).
        """
        if not pgroup_pids:
            return set()
        import subprocess as sp
        r = sp.run(["ps", "-eo", "pid=,pgid=,comm="], capture_output=True, text=True)
        found = set()
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            pid, pgid, comm = parts[0], parts[1], parts[2]
            if comm == "sleep" and pgid in pgroup_pids:
                found.add(pid)
        return found

    def _run_bash_tracked(self, code: str, bash_timeout: float):
        """Execute one bash block; return (result, set-of-launched-pgroup-pids).

        Tracks Popen PIDs created with start_new_session=True (the kernel's
        bash sessions only — subprocess.run inside tests never uses it).
        """
        import sys
        sys.path.insert(0, 'src')
        import subprocess as sp
        from rlm.kernel import PythonKernel

        launched: list[str] = []
        real_popen = sp.Popen

        def _track(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            if kwargs.get("start_new_session"):
                launched.append(str(proc.pid))
            return proc

        sp.Popen = _track
        try:
            k = PythonKernel(bash_timeout_seconds=bash_timeout)
            blk = type('B', (), {'language': 'bash', 'code': code})
            result = k.execute_blocks([blk])
        finally:
            sp.Popen = real_popen
        return result, set(launched)

    def test_orphan_cleanup(self):
        """A timed-out bash block must not leave children in ITS process group.

        Detection is restricted to the process group the kernel itself spawned
        (start_new_session=True ⇒ pgid == bash pid), not a global sleep scan —
        global scans false-positived on other concurrent sessions' sleeps.
        """
        import time
        result, launched = self._run_bash_tracked("sleep 5 & sleep 10", 3.0)
        assert not result.success

        # Give killed-group members a moment to disappear, then judge what is left.
        deadline = time.monotonic() + 2.0
        orphans = self._sleeps_in_pgroup(launched)
        while orphans and time.monotonic() < deadline:
            time.sleep(0.25)
            orphans = self._sleeps_in_pgroup(launched)
        assert not orphans, f"Orphan sleep PIDs left behind: {sorted(orphans)}"

class TestTimeoutCommand:
    def test_timeout_command(self):
        import sys, time
        sys.path.insert(0, 'src')
        from rlm.kernel import PythonKernel
        k = PythonKernel(python_timeout_seconds=180.0)
        start = time.monotonic()
        result = k.execute("%timeout 3\nimport time; time.sleep(10)")
        elapsed = time.monotonic() - start
        assert not result.success and elapsed < 8

    def test_timeout_command_invalid_zero(self):
        import sys
        sys.path.insert(0, 'src')
        from rlm.kernel import PythonKernel
        k = PythonKernel()
        result = k.execute("%timeout 0\nprint('hi')")
        assert not result.success
        assert "between 1 and 3600" in (result.error or "")

    def test_timeout_command_invalid_max(self):
        import sys
        sys.path.insert(0, 'src')
        from rlm.kernel import PythonKernel
        k = PythonKernel()
        result = k.execute("%timeout 9999\nprint('hi')")
        assert not result.success
        assert "between 1 and 3600" in (result.error or "")

    def test_timeout_command_reverts(self):
        import sys
        sys.path.insert(0, 'src')
        from rlm.kernel import PythonKernel
        k = PythonKernel(python_timeout_seconds=180.0)
        r1 = k.execute("%timeout 3\nimport time; time.sleep(10)")
        assert not r1.success
        r2 = k.execute("print('default')")
        assert r2.success

    def test_error_message_contains_tmux(self):
        import sys
        sys.path.insert(0, 'src')
        from rlm.kernel import PythonKernel
        k = PythonKernel(python_timeout_seconds=2.0)
        result = k.execute("import time; time.sleep(10)")
        assert not result.success
        assert "tmux" in (result.error or "").lower()
        assert "AGENT_RLM.md" in (result.error or "")

    def test_normal_completion_orphan_cleanup(self):
        # Same pgroup-restricted check as test_orphan_cleanup (global `pgrep -f
        # "sleep 3"` also matched unrelated command lines on a busy box).
        import time
        result, launched = TestOrphanCleanup()._run_bash_tracked("sleep 3 & echo done", 10.0)
        assert result.success
        assert "done" in result.output
        deadline = time.monotonic() + 2.0
        orphans = TestOrphanCleanup._sleeps_in_pgroup(launched)
        while orphans and time.monotonic() < deadline:
            time.sleep(0.25)
            orphans = TestOrphanCleanup._sleeps_in_pgroup(launched)
        assert not orphans, f"Orphan found: {sorted(orphans)}"
