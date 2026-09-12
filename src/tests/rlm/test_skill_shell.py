"""Tests for the shell skill: run, run_bg, check_bg, which.

Pins the documented public API:
    from skills.shell import run, run_bg, check_bg, which
"""
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

# Mirror sibling tests: ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from skills.shell import run, run_bg, check_bg, which  # noqa: E402

TMUX = shutil.which("tmux")


class TestRun:
    def test_run_success(self):
        r = run(["echo", "hi"])
        assert isinstance(r, dict)
        assert r["rc"] == 0
        assert "hi" in r["out"]
        assert r["err"] == ""

    def test_run_nonzero_rc(self):
        r = run(["false"])
        assert r["rc"] != 0

    def test_run_stdin_is_devnull_no_hang(self):
        """`cat` reads stdin: with stdin=DEVNULL it returns empty instantly.

        If DEVNULL regresses to an inherited stdin, `cat` blocks until the
        subprocess timeout (5s here) — so the wall-clock bound fails fast.
        """
        t0 = time.monotonic()
        r = run(["cat"], timeout=5)
        elapsed = time.monotonic() - t0
        assert r["rc"] == 0
        assert r["out"] == ""
        assert elapsed < 5, f"run() blocked on stdin for {elapsed:.1f}s"

    def test_run_captures_stderr(self):
        r = run(["sh", "-c", "echo oops >&2"])
        assert r["rc"] == 0
        assert "oops" in r["err"]


class TestWhich:
    def test_which_found(self):
        p = which("sh")
        assert p
        assert Path(p).is_absolute()
        assert Path(p).exists()

    def test_which_missing_returns_none(self):
        assert which("definitely-not-a-binary-xyz") is None


@pytest.mark.skipif(not TMUX, reason="tmux not available")
class TestBackground:
    def test_run_bg_check_bg_roundtrip(self):
        session = f"tau-test-{uuid.uuid4().hex[:8]}"
        try:
            # NB: tmux destroys a session as soon as its command exits, so
            # a bare `echo` is gone before capture-pane reads it. Keep the
            # pane alive (run_bg joins cmd into a shell string).
            name = run_bg(["echo", "bgtask", ";", "sleep", "3"], session=session)
            assert name == session
            out = ""
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                out = check_bg(session)
                if "bgtask" in out:
                    break
                time.sleep(0.1)
            assert "bgtask" in out
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", session],
                stdin=subprocess.DEVNULL,
                capture_output=True,
            )

    def test_check_bg_missing_session_returns_empty(self):
        assert check_bg("tau-test-nonexistent-session-xyz") == ""
