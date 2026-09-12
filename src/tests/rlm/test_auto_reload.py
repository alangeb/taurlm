"""Regression test: auto-reload of stale project modules between blocks."""

import os
import sys
import time
import importlib
from pathlib import Path

import pytest

from rlm.kernel import PythonKernel
from rlm.block_executor import BlockExecutor


class _Blk:
    def __init__(self, code, language="python"):
        self.code = code
        self.language = language


# Temp module lives under src/rlm/ so it falls inside _SRC_ROOT
_TMP_MODULE = Path(__file__).resolve().parent.parent.parent / "rlm" / "_test_reload_tmp.py"
_TMP_MODULE_NAME = "rlm._test_reload_tmp"


@pytest.fixture(autouse=True)
def _cleanup_tmp_module():
    """Ensure temp module file and sys.modules entry are removed after test."""
    yield
    if _TMP_MODULE.exists():
        _TMP_MODULE.unlink()
    sys.modules.pop(_TMP_MODULE_NAME, None)


class TestAutoReload:
    """After a block edits a .py file on disk, a later block must see fresh code."""

    def test_reload_sees_new_value(self):
        # Write initial module
        _TMP_MODULE.write_text("VALUE = 42\n")

        k = PythonKernel()

        # Block 1: import the module (first encounter records mtime, no reload)
        r1 = k.execute(f"import {_TMP_MODULE_NAME}\nprint({_TMP_MODULE_NAME}.VALUE)")
        assert r1.success
        assert "42" in r1.output

        # Ensure mtime granularity: sleep so getmtime() sees a change
        time.sleep(0.05)

        # Rewrite the module on disk
        _TMP_MODULE.write_text("VALUE = 99\n")

        # Block 2: should auto-reload and see 99
        r2 = k.execute(f"import {_TMP_MODULE_NAME}\nprint({_TMP_MODULE_NAME}.VALUE)")
        assert r2.success
        assert "99" in r2.output

    def test_never_reload_blocklist_respected(self):
        """Modules in _NEVER_RELOAD must NOT be reloaded even if mtime changes."""
        # rlm.kernel is in the blocklist; touching its file should not reload it.
        kernel_file = Path(__file__).resolve().parent.parent.parent / "rlm" / "kernel.py"
        original = kernel_file.read_text()

        k = PythonKernel()
        # Force kernel module into sys.modules & record its mtime
        import rlm.kernel as _km
        k._module_mtimes["rlm.kernel"] = os.path.getmtime(str(kernel_file))

        # Bump mtime (touch)
        time.sleep(0.05)
        kernel_file.write_text(original + "\n# touched\n")

        # Run a harmless block; _auto_reload should skip rlm.kernel
        r = k.execute("print('ok')")
        assert r.success
        assert "ok" in r.output

        # kernel module should NOT have been reloaded (still same object)
        assert sys.modules["rlm.kernel"] is _km

        # Restore
        kernel_file.write_text(original)

    def test_reload_failure_does_not_break_block(self):
        """If reload raises, the block must still execute normally."""
        _TMP_MODULE.write_text("VALUE = 1\n")
        k = PythonKernel()
        k.execute(f"import {_TMP_MODULE_NAME}")

        time.sleep(0.05)
        # Write syntactically broken code so reload will fail
        _TMP_MODULE.write_text("def broken(:\n")

        # Block should still succeed (reload failure is swallowed)
        r = k.execute("print('alive')")
        assert r.success
        assert "alive" in r.output

    def test_non_src_modules_untouched(self):
        """Modules outside src/ (e.g. stdlib) must never be reloaded."""
        k = PythonKernel()
        # os is in sys.modules but not under src/
        os_mod = sys.modules["os"]
        k.execute("print('x')")
        assert sys.modules["os"] is os_mod  # same object, not reloaded
