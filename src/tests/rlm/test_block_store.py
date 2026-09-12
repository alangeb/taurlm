"""Tests for the always-on _code{N}/_output{N} block store and tail-exec hint."""

import pytest
from rlm.kernel import PythonKernel
from rlm.kernel_types import REPLResult
from rlm.block_executor import BlockExecutor


class _Blk:
    def __init__(self, code, language="python"):
        self.code = code
        self.language = language


def _mk():
    return PythonKernel()


class TestBlockStore:
    def test_code_stored_on_success(self):
        k = _mk()
        r = k.execute("a = 5\nprint(a)")
        assert r.success
        assert k.namespace["_code1"] == ["a = 5", "print(a)"]
        assert r.code_seq == 1

    def test_code_stored_on_failure(self):
        k = _mk()
        r = k.execute("b = 1\nc = 2\nraise ValueError('boom')")
        assert not r.success
        assert k.namespace["_code1"][-1] == "raise ValueError('boom')"
        assert r.code_seq == 1

    def test_output_stored_python(self):
        k = _mk()
        k.execute("print('hello-out')")
        assert "hello-out" in k.namespace["_output1"]

    def test_output_stored_on_failure_partial(self):
        k = _mk()
        k.execute("print('partial')\nraise RuntimeError('x')")
        assert "partial" in k.namespace["_output1"]

    def test_output_stored_bash(self):
        k = _mk()
        r = k.execute_blocks([_Blk("echo bash-hello", "bash")])
        assert r.success
        assert "bash-hello" in k.namespace[f"_output{r.code_seq}"]

    def test_code_stored_bash(self):
        k = _mk()
        k.execute_blocks([_Blk("echo one\necho two", "bash")])
        assert k.namespace["_code1"] == ["echo one", "echo two"]

    def test_monotonic_across_blocks_and_langs(self):
        k = _mk()
        r = k.execute_blocks([
            _Blk("echo x", "bash"),
            _Blk("print('y')", "python"),
            _Blk("echo z", "bash"),
            _Blk("print('w')", "python"),
        ])
        assert r.success
        assert r.code_seq == 4
        assert k.block_seq == 4
        assert k.namespace["_code1"] == ["echo x"]
        assert k.namespace["_code4"] == ["print('w')"]
        assert "y" in k.namespace["_output2"]

    def test_monotonic_across_turns(self):
        k = _mk()
        k.execute("q = 1")
        r = k.execute("q = 2")
        assert r.code_seq == 2
        assert k.namespace["_code2"] == ["q = 2"]

    def test_no_purge_of_prior_entries(self):
        k = _mk()
        k.execute("print('first')")
        k.execute("print('second')")
        assert "first" in k.namespace["_output1"]
        assert "second" in k.namespace["_output2"]

    def test_clear_state_resets_seq(self):
        k = _mk()
        k.execute("x = 1")
        k.clear_state()
        assert k.block_seq == 0
        assert "_code1" not in k.namespace

    def test_legacy_last_code_gone(self):
        k = _mk()
        k.execute("x = 1")
        assert "_last_code" not in k.namespace
        k.execute("raise ValueError('z')")
        assert "_last_code" not in k.namespace

    def test_magic_stripped_before_store(self):
        k = _mk()
        k.execute("%timeout 30\nprint('tm')")
        assert k.namespace["_code1"] == ["print('tm')"]


class TestTailExecHint:
    def _err5(self, k):
        return k.execute("s1 = 1\ns2 = 2\ns3 = 1/0\ns4 = 4\ns5 = 5")

    def test_hint_present(self):
        k = _mk()
        r = self._err5(k)
        assert "_code1 holds your 5 lines; line 3 is the failing one." in r.error

    def test_already_ran_warning(self):
        k = _mk()
        r = self._err5(k)
        assert "Lines 1-2 ALREADY RAN" in r.error
        assert "do NOT re-run them" in r.error

    def test_context_lines_repr(self):
        k = _mk()
        r = self._err5(k)
        assert "   _code1[1] = 's2 = 2'" in r.error
        assert ">> _code1[2] = 's3 = 1/0'" in r.error
        assert "   _code1[3] = 's4 = 4'" in r.error

    def test_tail_exec_command(self):
        k = _mk()
        r = self._err5(k)
        assert 'exec(chr(10).join(_code1[2:]))' in r.error
        assert '_code1[2] = "<new line>"' in r.error

    def test_triple_quote_line_renders_safely(self):
        k = _mk()
        code = "a = 1\nb = 2\nc = \"\"\"oops\n1/0"
        r = k.execute(code)
        assert not r.success
        # repr() keeps the triple quotes copy-pasteable and unambiguous
        assert repr('c = \"\"\"oops') in r.error
        assert "run ONLY the tail" in r.error

    def test_fallback_when_lineno_out_of_range(self):
        k = _mk()
        r = k.execute("import ast\nbad = 'def f(:\\n  pass'\nast.parse(bad)")
        assert not r.success
        assert "run ONLY the tail" not in r.error

    def test_other_pieces_kept(self):
        k = _mk()
        r = self._err5(k)
        assert "ZeroDivisionError" in r.error
        assert "RECOVERABLE" in r.error
        assert "error #1" in r.error

    def test_seq_propagates_from_block_executor(self):
        k = _mk()
        r = k.execute_blocks([_Blk("p1 = 1\np2 = 1/0")])
        assert not r.success
        assert r.code_seq == 1
        assert "_code1" in r.error

    def test_tail_exec_idempotency(self):
        """Running _code{N}[k:] after a failure must NOT re-run the prefix."""
        k = _mk()
        code = "counter = 0\ncounter += 1\nraise ValueError('stop')\nafter = counter"
        r = k.execute(code)
        assert not r.success
        assert k.namespace["counter"] == 1
        # Recover: fix the failing line in place, run only the tail.
        k.namespace["_code1"][2] = "counter += 10"
        tail = "exec(chr(10).join(_code1[2:]))"
        r2 = k.execute(tail)
        assert r2.success, r2.error
        assert k.namespace["counter"] == 11   # 1 + 10, prefix did NOT re-run
        assert k.namespace["after"] == 11
