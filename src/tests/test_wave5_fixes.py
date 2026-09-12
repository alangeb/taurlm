"""Regression tests for the 13 verified fixes (R1,R2,R3,S1,S2,S3,L1,L2,L3,W1,PB1,HB1,C1).

Each test targets one fix ID and asserts the specific corrected behavior.
No sanity tests are touched.
"""
from __future__ import annotations

import gc
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── R1: compressed context must end on USER (D5 reverted) ───────────────────
class TestR1CompressedEndsOnUser:
    def test_conversation_summary_ends_user(self):
        from agent_context_compress.steps.conversation_summary import (
            compress_conversation_summary,
        )
        from tests.test_compression_pipeline import _make_large_context
        out, meta = compress_conversation_summary(
            context=_make_large_context(num_turns=6), client=None,
            model_name="m", target_size_bytes=1,
        )
        assert out[-1]["role"] == "user"

    def test_full_reset_ends_user(self):
        import agent_context_compress.steps.full_reset as fr
        from tests.test_compression_pipeline import _make_large_context

        class _R:
            def __init__(self, t): self.text = t

        def fake(client, model_name, messages, **kw):
            sp = messages[0].get("content", "")
            if "summarizer" in sp.lower():
                return _R("summary " * 30)
            return _R("Step 1: do X. Step 2: do Y. Step 3: verify Z. Step 4: finalize the deliverable.")

        orig = fr._invoke_llm_with_retry_compression
        fr._invoke_llm_with_retry_compression = fake
        try:
            out, status = fr.compress_full_reset(
                context=_make_large_context(num_turns=6), client=None,
                model_name="m", target_size_bytes=1,
            )
        finally:
            fr._invoke_llm_with_retry_compression = orig
        assert out[-1]["role"] == "user"
        # no synthetic assistant dummy anywhere
        for m in out:
            assert "Acknowledged" not in str(m.get("content", ""))

    def test_pipeline_output_ends_user(self):
        from agent_context_compress import compress_context
        from tests.test_compression_pipeline import _make_large_context
        result, _, _ = compress_context(
            context=_make_large_context(num_turns=20), client=None,
            model_name="test-model", compression_factor=0.5, verbose=False,
            last_known_tokens=100000, max_context_tokens=128000,
            max_output_tokens=4096,
        )
        assert result[-1]["role"] == "user"

    def test_framework_no_assistant_append(self):
        # The D5 choke-point append must be gone from framework.py source.
        src = (Path(__file__).resolve().parents[1]
               / "agent_context_compress" / "steps" / "framework.py").read_text()
        assert "D5 INVARIANT" not in src
        assert "Acknowledged. I\'ll continue from the compressed context." not in src


# ── R2: answer_ready requires ready is True ─────────────────────────────────
class TestR2ReadyIsTrue:
    def test_truthy_non_true_not_ready(self):
        from rlm.block_executor import BlockExecutor
        ns = {"answer": {"content": "", "ready": False}}
        be = BlockExecutor(
            namespace=ns, python_timeout=10, bash_timeout=10,
            max_output_chars=1000,
            handle_magic=lambda c: (c, "", "", None),
            format_error=lambda *a, **k: "err",
            get_answer=lambda: ns.get("answer"),
        )

        class _Blk:
            language = "python"
            code = "answer['ready'] = 'false'; answer['content'] = 'x'"

        res = be.execute([_Blk()])
        assert res.answer_ready is False, "truthy-non-True ready must NOT mark ready"

    def test_true_is_ready(self):
        from rlm.block_executor import BlockExecutor
        ns = {"answer": {"content": "", "ready": False}}
        be = BlockExecutor(
            namespace=ns,
            python_timeout=10, bash_timeout=10, max_output_chars=1000,
            handle_magic=lambda c: (c, "", "", None),
            format_error=lambda *a, **k: "err",
            get_answer=lambda: ns.get("answer"),
        )

        class _Blk:
            language = "python"
            code = "answer['ready'] = True; answer['content'] = 'done'"

        res = be.execute([_Blk()])
        assert res.answer_ready is True


# ── R3: timeout during reload must not NameError ────────────────────────────
class TestR3ReloadTimeoutNoNameError:
    def test_block_executor_timeout_during_reload(self):
        from rlm.block_executor import BlockExecutor, _REPLTimeout
        be = BlockExecutor(
            namespace={}, python_timeout=10, bash_timeout=10,
            max_output_chars=1000,
            handle_magic=lambda c: (c, "", "", None),
            format_error=lambda *a, **k: "err",
            get_answer=lambda: {},
        )

        class _Blk:
            language = "python"
            code = "print('hi')"

        with patch.object(BlockExecutor, "_auto_reload_stale_modules",
                          side_effect=_REPLTimeout()):
            res = be.execute([_Blk()])
        assert res.exception_type == "_REPLTimeout"
        assert "NameError" not in res.error
        assert "capture" not in res.error

    def test_kernel_timeout_during_reload(self):
        from rlm.kernel import PythonKernel
        import rlm.kernel as kmod
        k = PythonKernel()
        with patch.object(kmod, "reload_stale_modules",
                          side_effect=kmod._REPLTimeout()):
            res = k.execute("print('hi')")
        assert res.exception_type == "_REPLTimeout"
        assert "NameError" not in res.error


# ── S1: GC'd unclosed handle unregisters (no slot leak) ─────────────────────
class TestS1DelUnregisters:
    def test_gc_drops_active_count(self):
        from rlm.spawn import SpawnHandle, SpawnRegistry
        reg = SpawnRegistry()
        before = reg.count_active()
        h = SpawnHandle(spawn_id="s1test", _agent=MagicMock())
        reg.register(h)
        assert reg.count_active() == before + 1
        del h
        gc.collect()
        assert reg.count_active() == before


# ── S2: non-ready exit is 'incomplete', not 'completed' ─────────────────────
class TestS2IncompleteStatus:
    def test_non_ready_exit_not_completed(self):
        from rlm.spawn import SpawnHandle

        class _Ans:
            content = "partial output"
            ready = False
            yielded = False

        class _Agent:
            spawn_B = 0.5
            def get_answer(self): return _Ans()

        h = SpawnHandle(spawn_id="s2", _agent=_Agent())
        h._update_status()
        assert h.status == "incomplete"

    def test_ready_content_still_completed(self):
        from rlm.spawn import SpawnHandle

        class _Ans:
            content = "real result"
            ready = True
            yielded = False

        class _Agent:
            spawn_B = 0.5
            def get_answer(self): return _Ans()

        h = SpawnHandle(spawn_id="s2b", _agent=_Agent())
        h._update_status()
        assert h.status == "completed"


# ── S3: B==0 spawn is budget-capped ─────────────────────────────────────────
class TestS3BZeroCapped:
    def test_b_zero_spawn_capped(self):
        from agent_pipeline import check_work_budget
        agent = MagicMock()
        agent.spawn_id = "child-xyz"  # spawned child
        agent.spawn_B = 0.0
        agent.spawn_C_last = 0.10
        agent.spawn_min_B = 0.0
        agent._budget_warned = True
        agent.max_context_tokens = 128000
        agent.context.get_usage_stats.return_value = (5000, 0.10, 20000, False)
        agent.get_answer.return_value = None
        out = check_work_budget(agent)
        assert out is not None, "B=0 spawn must be budget-capped"

    def test_root_agent_not_capped(self):
        from agent_pipeline import check_work_budget
        agent = MagicMock()
        agent.spawn_id = ""  # root agent
        agent.spawn_B = 0.0
        agent.spawn_C_last = 0.0
        agent.max_context_tokens = 128000
        agent.context.get_usage_stats.return_value = (5000, 0.10, 20000, False)
        out = check_work_budget(agent)
        assert out is None, "root agent (no spawn_id) must not be capped"


# ── L1: transient socket URLErrors map to APIConnectionError (retried) ──────
class TestL1TransientSocketRetry:
    def _client(self):
        from agent_llm_client import SimpleOpenAIClient
        return SimpleOpenAIClient(base_url="http://127.0.0.1:1/api")

    def test_dns_reset_brokenpipe_map_to_connection_error(self):
        from agent_llm_client import APIConnectionError
        c = self._client()
        for reason in (
            OSError("Name or service not known"),
            ConnectionResetError("reset"),
            BrokenPipeError("broken pipe"),
        ):
            err = urllib.error.URLError(reason)
            assert c._is_transient_socket_error(err) is True, reason
            # simulate the handler mapping
            if not (c._is_timeout(err) or c._is_connection_refused(err)):
                assert c._is_transient_socket_error(err)

    def test_plain_api_error_still_for_non_transient(self):
        c = self._client()
        err = urllib.error.URLError(OSError("something weird"))
        assert c._is_transient_socket_error(err) is False

    def test_handler_maps_dns_to_connection_error(self):
        # End-to-end: a URLError with getaddrinfo reason must raise
        # APIConnectionError (which the retry loop retries), not APIError.
        from agent_llm_client import APIConnectionError
        c = self._client()
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError(
                       OSError("Name or service not known"))):
            with pytest.raises(APIConnectionError):
                c.chat_completions_create(model="m", messages=[])


# ── L2: disable_thinking_after propagates to streaming kwargs ───────────────
class TestL2StreamingExtraKwargs:
    def test_streaming_uses_passed_extra_kwargs(self):
        import agent_llm_invoke as inv
        captured = {}

        def fake_build(model, messages, stream, extra):
            captured["extra"] = extra
            return {"model": model, "messages": messages}

        cfg = MagicMock()
        cfg.extra_kwargs = {"chat_template_kwargs": {"enable_thinking": True}}
        cfg.on_token = None
        cfg.on_reasoning = None
        cfg.abort_checker = None
        cfg.max_output_tokens = 100

        client = MagicMock()
        # streaming iterator that yields nothing (empty stream)
        client.chat.completions.create_stream.return_value = iter([])
        client.chat.completions.create.return_value = iter([])

        with patch.object(inv, "_build_call_kwargs", side_effect=fake_build):
            try:
                inv._invoke_llm_streaming(
                    client, "m", [{"role": "user", "content": "x"}], cfg, [0],
                    extra_kwargs={"chat_template_kwargs": {"enable_thinking": False}},
                )
            except Exception:
                pass
        # The streaming call must have received the MUTATED extra, not the config copy
        assert captured.get("extra") == {
            "chat_template_kwargs": {"enable_thinking": False}
        }, captured


# ── L3: timeout branch calls backoff.wait() ─────────────────────────────────
class TestL3TimeoutBackoff:
    def test_timeout_branch_waits(self):
        import agent_llm_invoke as inv
        from agent_llm_models import LLMCallConfig, APITimeoutError

        waits = []

        class _Backoff:
            def __init__(self, *a, **k): pass
            def next_wait(self, attempt): return 0
            def wait(self, attempt): waits.append(attempt)

        client = MagicMock()
        # every call times out
        err = APITimeoutError("timeout")
        client.chat.completions.create.side_effect = err
        client.chat.completions.create_stream.side_effect = err

        cfg = LLMCallConfig(max_retries=2, min_response_bytes=0)
        with patch("agent_llm_client.RetryBackoff", _Backoff):
            with pytest.raises(Exception):
                inv._invoke_llm_with_retry(
                    client=client, model_name="m",
                    messages=[{"role": "user", "content": "hi"}],
                    stream=False, config=cfg,
                )
        assert len(waits) >= 1, "timeout branch must call backoff.wait()"


# ── W1: add() tolerates non-UTF8 bytes ──────────────────────────────────────
class TestW1WikiNonUtf8:
    def test_add_to_non_utf8_file_no_raise(self, tmp_path):
        from wiki import Wiki
        w = Wiki(path=str(tmp_path / "wiki"))
        w._git = lambda *a, **k: None  # avoid git
        topic = "topic"
        d = tmp_path / "wiki" / topic
        d.mkdir(parents=True)
        from datetime import datetime, timezone
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fpath = d / f"{topic}-{date}.md"
        fpath.write_bytes(b"existing \xff\xfe bytes not utf8")
        # must not raise UnicodeDecodeError
        w.add(topic, "new content", entry_type="note")
        assert "new content" in fpath.read_text(errors="replace")


# ── PB1: single-pass substitution (no re-expansion) ─────────────────────────
class TestPB1NoReExpansion:
    def test_arg_containing_star_not_reexpanded(self):
        from rlm.command_dispatch import substitute_placeholders
        # $1's value contains "$*"; a single pass must NOT expand it.
        out = substitute_placeholders("$1", ["$*"])
        assert out == "$*"

    def test_arg_containing_positional_not_reexpanded(self):
        from rlm.command_dispatch import substitute_placeholders
        out = substitute_placeholders("$1", ["$2"])
        assert out == "$2"

    def test_normal_substitution_still_works(self):
        from rlm.command_dispatch import substitute_placeholders
        assert substitute_placeholders("$1 and $*", ["hi", "there"]) == "hi and hi there"
        assert substitute_placeholders("$1+ rest", ["a", "b", "c"]) == "a b c rest"


# ── HB1: goal 'set' acquires the fcntl lock ─────────────────────────────────
class TestHB1SetLocks:
    def test_set_acquires_lock(self, tmp_path):
        from rlm.host_bridge import HostBridge
        hb = HostBridge()
        hb._get_data_path = lambda fn: tmp_path / fn
        locked = {"acquired": False}
        import rlm.host_bridge as hbmod

        real_flock = hbmod.fcntl.flock

        def spy_flock(fd, op):
            if op & hbmod.fcntl.LOCK_EX:
                locked["acquired"] = True
            return real_flock(fd, op)

        with patch.object(hbmod.fcntl, "flock", side_effect=spy_flock):
            resp = hb.request("goal", "set", content="do the thing")
        assert resp.success
        assert locked["acquired"] is True, "goal set must take the exclusive lock"


# ── C1: repl writer audits exactly once ─────────────────────────────────────
class TestC1SingleAudit:
    def test_repl_output_single_audit(self):
        import agent_audit_bridge as bridge
        from agent_console.templates import repl_output

        class _Stub:
            def __init__(self): self.recs = []
            def _console_error(self, m): self.recs.append(("ERR", m))
            def _console_warning(self, m): self.recs.append(("WARN", m))
            def _console_info(self, m): self.recs.append(("INFO", m))
            def _console_success(self, m): self.recs.append(("OK", m))

        s = _Stub()
        orig = bridge._audit_writer
        bridge.set_audit_writer(s)
        try:
            repl_output("line one")
        finally:
            bridge.set_audit_writer(orig)
        assert len(s.recs) == 1, f"expected exactly one audit record, got {s.recs}"
