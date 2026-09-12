"""Regression tests for wave-2 fixes: B3, B5, B6, B8, B11, B12, B15, B16, B17, B18."""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── B3: async def detection ───────────────────────────────────────────────────

class TestB3AsyncDef:
    def test_find_functions_matches_async_def(self, tmp_path):
        from skills.code_analysis import _find_functions
        f = tmp_path / "mod.py"
        f.write_text(
            "def sync_fn():\n    pass\n\n"
            "async def async_fn(x):\n    pass\n\n"
            "    async def nested_async():\n        pass\n"
        )
        found = _find_functions(str(f))
        assert "sync_fn" in found
        assert "async_fn" in found, "async def must be detected"
        assert "nested_async" in found

    def test_find_functions_no_false_positive(self, tmp_path):
        from skills.code_analysis import _find_functions
        f = tmp_path / "m2.py"
        f.write_text("defx = 1\nmydef(2)\n")
        assert _find_functions(str(f)) == []


# ── B5 / B6: context store ────────────────────────────────────────────────────

class _Store:
    """Minimal host for ContextStoreMixin (bypasses message validation)."""

    def __init__(self, messages):
        from agent_context_store import ContextStoreMixin
        self._mixin = ContextStoreMixin()
        self._messages = messages
        self._metadata = {}

    def estimate_tokens(self, pending_tokens: int = 0) -> int:
        return type(self._mixin).estimate_tokens(self, pending_tokens)

    def save_to_file(self, path, force: bool = False) -> bool:
        return type(self._mixin).save_to_file(self, path, force)


class TestB5ForceSave:
    def test_short_context_not_saved_without_force(self, tmp_path):
        out = tmp_path / "ctx.json"
        store = _Store([{"role": "user", "content": "hi"}])
        assert store.save_to_file(out) is False
        assert not out.exists()

    def test_force_overrides_three_message_guard(self, tmp_path):
        import json
        out = tmp_path / "ctx.json"
        store = _Store([{"role": "user", "content": "hi"}])
        assert store.save_to_file(out, force=True) is True, "force=True must save"
        assert out.exists()
        data = json.loads(out.read_text())
        assert len(data["messages"]) == 1

    def test_force_true_empty_context(self, tmp_path):
        out = tmp_path / "empty.json"
        store = _Store([])
        assert store.save_to_file(out, force=True) is True
        assert out.exists()


class TestB6CjkAwareEstimate:
    def test_list_text_part_uses_cjk_estimator(self):
        from agent_context_store import _estimate_text_tokens
        text = "\u4f60\u597d" * 10  # 20 CJK chars
        store = _Store([{"role": "user", "content": [{"type": "text", "text": text}]}])
        assert store.estimate_tokens() == _estimate_text_tokens(text) + 15
        # len(text)//3 would be 6; CJK-aware gives 20
        assert store.estimate_tokens() == 35

    def test_reasoning_uses_cjk_estimator(self):
        from agent_context_store import _estimate_text_tokens
        reasoning = "\u4f60\u597d" * 10
        store = _Store([{"role": "assistant", "content": "", "reasoning": reasoning}])
        assert store.estimate_tokens() == _estimate_text_tokens(reasoning) + 15

    def test_ascii_paths_agree(self):
        ascii_text = "a" * 40
        store = _Store([{"role": "user", "content": ascii_text}])
        plain = store.estimate_tokens()
        store2 = _Store([{"role": "user", "content": [{"type": "text", "text": ascii_text}]}])
        assert store2.estimate_tokens() == plain


# ── B8: re.sub replacement template injection ─────────────────────────────────

class TestB8Substitution:
    def test_backreference_in_user_text_not_expanded(self):
        from rlm.command_dispatch import substitute_placeholders
        assert substitute_placeholders("Hello $1", ["a\\1b"]) == "Hello a\\1b"

    def test_group_reference_does_not_raise(self):
        from rlm.command_dispatch import substitute_placeholders
        assert substitute_placeholders("v=$1", ["\\g<0>"]) == "v=\\g<0>"

    def test_collective_star_backslash(self):
        from rlm.command_dispatch import substitute_placeholders
        assert substitute_placeholders("x $*", ["a\\1", "b"]) == "x a\\1 b"

    def test_range_plus_backslash(self):
        from rlm.command_dispatch import substitute_placeholders
        assert substitute_placeholders("$1+", ["a\\2", "b"]) == "a\\2 b"

    def test_normal_substitution_still_works(self):
        from rlm.command_dispatch import substitute_placeholders
        assert substitute_placeholders("$1 and $2", ["one", "two"]) == "one and two"
        assert substitute_placeholders("$*", ["a", "b"]) == "a b"


# ── B11: audit file with invalid UTF-8 ───────────────────────────────────────

class TestB11AuditEncoding:
    def test_non_utf8_audit_does_not_crash(self, tmp_path):
        from agent_console.audit_display import parse_audit_file
        p = tmp_path / "s.audit"
        p.write_bytes(
            b"[2026-09-12T14:57:03+00:00] USER msg=ok\n"
            b"[2026-09-12T14:57:03+00:00] ASSISTANT msg=bad \xff\xfe bytes\n"
        )
        records = list(parse_audit_file(p))
        assert len(records) == 2

    def test_valid_file_still_parses(self, tmp_path):
        from agent_console.audit_display import parse_audit_file
        p = tmp_path / "ok.audit"
        p.write_text("[2026-09-12T14:57:03+00:00] USER msg=ok\n", encoding="utf-8")
        recs = list(parse_audit_file(p))
        assert len(recs) == 1 and recs[0].record_type == "USER"


# ── B12: lazy __getattr__ masking ImportError ─────────────────────────────────

class TestB12SubmoduleImportError:
    def test_broken_submodule_error_is_surfaced(self, tmp_path, monkeypatch, caplog):
        broken = tmp_path / "broken_sub_mod_xyz.py"
        broken.write_text("raise ImportError('boom-broken-submodule')\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("broken_sub_mod_xyz", None)
        import agent_console as ac
        monkeypatch.setattr(ac, "_attr_cache", {})
        monkeypatch.setattr(ac, "_SUBMODULE_PATHS", ("broken_sub_mod_xyz",))
        with caplog.at_level(logging.WARNING, logger="agent_console"):
            with pytest.raises(AttributeError) as exc:
                ac.some_missing_symbol_xyz
        msg = str(exc.value)
        assert "boom-broken-submodule" in msg, "ImportError must not be masked"
        assert any("broken_sub_mod_xyz" in r.getMessage() or "boom" in r.getMessage()
                   for r in caplog.records), "ImportError must be logged"

    def test_normal_attribute_resolution_still_works(self):
        from agent_console import audit_display
        import agent_console as ac
        assert ac.parse_audit_file is audit_display.parse_audit_file


# ── B15: subprocess.run(shell=True) needs stdin=DEVNULL ───────────────────────

class TestB15StdinDevnull:
    def test_run_command_passes_stdin_devnull(self, monkeypatch):
        mod = importlib.import_module("skills.system_info")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "out", "")

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        mod.run_command("echo hi")
        assert captured.get("stdin") is subprocess.DEVNULL
        assert captured.get("shell") is True

    def test_run_command_still_returns_output(self, monkeypatch):
        mod = importlib.import_module("skills.system_info")
        monkeypatch.setattr(
            mod.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "hello", ""),
        )
        res = mod.run_command("echo hello")
        assert res["returncode"] == 0 and res["stdout"] == "hello"


# ── B16: /refine apply prefix mis-parse ───────────────────────────────────────

class TestB16RefineApply:
    @pytest.fixture()
    def refine_mod(self, tmp_path, monkeypatch):
        import commands.refine as refine
        monkeypatch.setattr(refine, "REFINEMENT_FILE", tmp_path / ".refinements.json")
        return refine

    def test_applied_is_not_parsed_as_apply(self, refine_mod):
        out = refine_mod.run("applied")
        assert "Usage:" in out
        assert refine_mod._load_refinements() == []

    def test_apply_with_text(self, refine_mod):
        out = refine_mod.run("apply fix the bug")
        assert "fix the bug" in out
        assert refine_mod._load_refinements()[0].content == "fix the bug"

    def test_apply_bare_shows_usage(self, refine_mod):
        assert "Usage:" in refine_mod.run("apply")
        assert refine_mod._load_refinements() == []

    def test_apply_tab_separated(self, refine_mod):
        refine_mod.run("apply\treal text")
        assert refine_mod._load_refinements()[0].content == "real text"


# ── B17: entropy warning double-count ─────────────────────────────────────────

class TestB17EntropyDoubleCount:
    def test_display_count_not_double_counted(self):
        from agent_loop_detect import LoopDetector
        d = LoopDetector()
        d.total_warnings = 5
        d.entropy_warnings = 2
        assert d._display_warning_count() == 5

    def test_entropy_warning_counted_once(self):
        from agent_loop_detect import LoopDetector
        d = LoopDetector(repeat_threshold=1000, window_size=30)
        for _ in range(20):
            d.record_code_execution("print(1)")
        assert d.entropy_warnings >= 1
        # Each entropy warning must add exactly 1 to total_warnings (no double
        # count), so total equals entropy_warnings when no repeat warnings fire.
        assert d.total_warnings == d.entropy_warnings
        assert d._display_warning_count() == d.total_warnings
        assert d.get_escalation_info()["repeat_warnings"] == 0

    def test_repeat_warning_display_count(self):
        from agent_loop_detect import LoopDetector
        d = LoopDetector(repeat_threshold=2)
        d.record_code_execution("x = 1")
        d.record_code_execution("x = 1")
        assert d.total_warnings == 1 and d.entropy_warnings == 0
        assert d._display_warning_count() == 1


# ── B18: truthy string ready must not end turn ────────────────────────────────

class TestB18ReadyIsTrue:
    def test_truthy_string_ready_does_not_end_turn(self):
        from rlm.kernel import PythonKernel
        k = PythonKernel()
        res = k.execute("answer['ready'] = 'false'")
        assert res.answer_ready is False

    def test_ready_true_ends_turn(self):
        from rlm.kernel import PythonKernel
        k = PythonKernel()
        res = k.execute("answer['content'] = 'x'\nanswer['ready'] = True")
        assert res.answer_ready is True

    def test_non_bool_truthy_values_rejected(self):
        from rlm.kernel import PythonKernel
        for val in ("yes", "1", "1.0"):
            k = PythonKernel()
            res = k.execute(f"answer['ready'] = '{val}'")
            assert res.answer_ready is False, val


def test_v1_ctx_dump_last_real_user_has_format_in_scope():
    """P7-V1: format_single_message was imported only inside run(), so
    _dump_last_real_user raised NameError. Assert it resolves in that scope."""
    import inspect
    from commands import ctx
    src = inspect.getsource(ctx._dump_last_real_user)
    assert "from agent_context_dump import" in src and "format_single_message" in src
