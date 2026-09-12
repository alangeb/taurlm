"""Regression tests for wave-4 fixes (I1-I9).

Covers:
  I1  dream_steps task-prompt token injection guard (no silent no-op)
  I2  dead disk context stack removed from agent_context_manager
  I3  dead prepare_fork_context removed from agent_context_turn
  I4  skills.py SKILL.md size cap with explicit truncation NOTICE
  I5  dead _analyze_usage removed from pyscan_core
  I6  dead _format_markdown removed from pyanalyze_core
  I7  real coverage for pygraph_core / pyscan_core / pycheck_core / pyanalyze_core
  I9  tau-sanity.py is a REQUIRED duplicate (src/sanity.sh cp's tau.py onto it)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── I1: dream_steps token injection guard ────────────────────────────────

class TestDreamTaskTokenGuard:
    def test_prompt_contains_injection_token(self):
        import dream_steps
        assert "TASK: Execute the task file in ../tasks/2_inprogress/" in dream_steps.PROMPT_DOTASK

    def test_replacement_actually_injects_filename(self):
        import dream_steps
        token = "TASK: Execute the task file in ../tasks/2_inprogress/"
        out = dream_steps.PROMPT_DOTASK.replace(token, "TASK: Execute the task file: ../tasks/2_inprogress/x.md")
        assert "x.md" in out
        assert token not in out

    def test_missing_token_raises_not_silent_noop(self, tmp_path, monkeypatch):
        import dream_steps

        todo = tmp_path / "1_todo"
        todo.mkdir()
        task = todo / "t1.md"
        task.write_text("# task\n", encoding="utf-8")

        monkeypatch.setattr(dream_steps, "PROMPT_DOTASK", "no token here at all")
        monkeypatch.setattr(dream_steps, "get_todo_files", lambda: [task])
        monkeypatch.setattr(dream_steps.shutdown, "check", lambda: False)

        class _Log:
            def log(self, *a, **k):
                pass

        with pytest.raises(ValueError):
            dream_steps.step_process_tasks(_Log(), None, "grp", True, Path("tau.py"))

    def test_run_tau_and_test_not_reached_on_bad_token(self, tmp_path, monkeypatch):
        import dream_steps

        todo = tmp_path / "1_todo"
        todo.mkdir()
        (todo / "t.md").write_text("# t\n", encoding="utf-8")
        monkeypatch.setattr(dream_steps, "PROMPT_DOTASK", "nope")
        monkeypatch.setattr(dream_steps, "get_todo_files", lambda: [todo / "t.md"])
        monkeypatch.setattr(dream_steps.shutdown, "check", lambda: False)

        def _boom(*a, **k):
            raise AssertionError("_run_tau_and_test must not run when token missing")

        monkeypatch.setattr(dream_steps, "_run_tau_and_test", _boom)

        class _Log:
            def log(self, *a, **k):
                pass

        with pytest.raises(ValueError):
            dream_steps.step_process_tasks(_Log(), None, "grp", True, Path("tau.py"))


# ── I2/I3/I5/I6: dead code removal locks ─────────────────────────────────

class TestDeadCodeRemoved:
    def test_context_manager_disk_stack_gone(self):
        from agent_context_manager import ContextManager
        for attr in ("_CONTEXT_STACK_FILE", "_MAX_STACK_DEPTH", "_get_stack_file",
                     "_load_stack", "_save_stack", "pop"):
            assert not hasattr(ContextManager, attr), f"dead symbol still present: {attr}"

    def test_prepare_fork_context_gone(self):
        from agent_context_turn import TurnLifecycleMixin
        assert not hasattr(TurnLifecycleMixin, "prepare_fork_context")

    def test_pyscan_usage_analyzer_gone(self):
        import rlm.pyscan_core as m
        assert not hasattr(m, "_analyze_usage")

    def test_pyanalyze_format_markdown_gone(self):
        import rlm.pyanalyze_core as m
        assert not hasattr(m, "_format_markdown")

    def test_context_manager_still_functional(self):
        # Removing the stack block must not break the live surface.
        from agent_context_manager import ContextManager
        assert hasattr(ContextManager, "clear")
        assert hasattr(ContextManager, "undo")
        assert hasattr(ContextManager, "copy_audit_file")


# ── I4: SKILL.md size cap with explicit notice ───────────────────────────

class TestSkillLoadCap:
    @staticmethod
    def _loader(tmp_path, body: str):
        from rlm.skills import SkillLoader
        root = tmp_path / "skills"
        root.mkdir()
        d = root / "big"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: big\ndescription: 'Big. Keywords: big, test.'\ncategory: t\n---\n\n" + body,
            encoding="utf-8",
        )
        return SkillLoader(skills_dir=root)

    def test_small_skill_not_truncated(self, tmp_path, capsys):
        loader = self._loader(tmp_path, "SMALL BODY CONTENT")
        out = loader._load_skill_into_namespace("big")
        assert "SMALL BODY CONTENT" in out
        assert "TRUNCATION NOTICE" not in out

    def test_large_skill_truncated_with_notice(self, tmp_path, monkeypatch, capsys):
        import rlm.skills as skills_mod
        monkeypatch.setattr(skills_mod, "_SKILL_MAX_CHARS", 120)
        body = "X" * 5000
        loader = self._loader(tmp_path, body)
        out = loader._load_skill_into_namespace("big")
        assert "TRUNCATION NOTICE" in out
        assert len(out) < len(body)
        # explicit, not silent: notice states how much was omitted
        assert "omitted" in out
        printed = capsys.readouterr().out
        assert "TRUNCATION NOTICE" in printed

    def test_cap_is_generous(self):
        import rlm.skills as skills_mod
        assert skills_mod._SKILL_MAX_CHARS >= 100_000


# ── I7: real coverage for the AST analysis cores ─────────────────────────

@pytest.fixture()
def synth_tree(tmp_path):
    """Tiny two-file project: alpha->helper, beta->alpha."""
    (tmp_path / "a.py").write_text(
        "def helper(x):\n    return x + 1\n\n"
        "def alpha(x):\n    return helper(x) * 2\n\n"
        "def orphan(x):\n    return x\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "from a import alpha\n\n"
        "def beta(x):\n    return alpha(x)\n",
        encoding="utf-8",
    )
    return tmp_path


class TestPygraphCore:
    def test_build_graph_and_callers(self, synth_tree):
        from rlm.pygraph_core import build_graph
        g = build_graph(str(synth_tree))
        helper = [n for n in g.nodes if n.name == "helper"]
        assert helper, "helper node missing"
        hid = helper[0].id
        callers = g.callers(hid)
        assert any("alpha" in c for c in callers), callers

    def test_impact_is_transitive(self, synth_tree):
        from rlm.pygraph_core import build_graph
        g = build_graph(str(synth_tree))
        hid = [n for n in g.nodes if n.name == "helper"][0].id
        impacted = g.impact(hid)
        assert any("alpha" in i for i in impacted)
        assert any("beta" in i for i in impacted), "transitive caller beta missing"

    def test_callees_and_god_nodes(self, synth_tree):
        from rlm.pygraph_core import build_graph
        g = build_graph(str(synth_tree))
        aid = [n for n in g.nodes if n.name == "alpha"][0].id
        assert any("helper" in c for c in g.callees(aid))
        gods = g.god_nodes(top=3)
        assert gods and gods[0][1] >= 1

    def test_importers_and_query_graph(self, synth_tree):
        from rlm.pygraph_core import build_graph, query_graph
        g = build_graph(str(synth_tree))
        out = query_graph(g, "callers helper")
        assert "alpha" in out
        empty = query_graph(g, "callers nonexistent_symbol_xyz")
        assert "No nodes matching" in empty


class TestPyscanCore:
    def test_scan_project_lists_symbols(self, synth_tree):
        from rlm.pyscan_core import scan_project
        out = scan_project(str(synth_tree))
        assert "helper" in out and "alpha" in out and "beta" in out

    def test_scan_project_compact_smaller(self, synth_tree):
        from rlm.pyscan_core import scan_project
        full = scan_project(str(synth_tree))
        compact = scan_project(str(synth_tree), compact=True)
        assert "helper" in compact
        assert len(compact) <= len(full)


class TestPycheckCore:
    def test_check_project_flags_unused_import(self, tmp_path):
        from rlm.pycheck_core import check_project
        (tmp_path / "u.py").write_text("import os\nimport json\nprint(json.dumps({}))\n", encoding="utf-8")
        out = check_project(str(tmp_path))
        assert "unused" in out.lower()
        assert "os" in out

    def test_check_project_clean_tree(self, tmp_path):
        from rlm.pycheck_core import check_project
        (tmp_path / "ok.py").write_text("import json\nprint(json.dumps({}))\n", encoding="utf-8")
        out = check_project(str(tmp_path))
        assert "No import issues" in out

    def test_check_file_missing_import(self, tmp_path):
        from rlm.pycheck_core import check_file
        f = tmp_path / "m.py"
        f.write_text("def f():\n    return os.path\n", encoding="utf-8")
        r = check_file(f)
        assert "os" in r["missing_imports"]


class TestPyanalyzeCore:
    def test_analyze_project_finds_dead_function(self, synth_tree):
        from rlm.pyanalyze_core import analyze_project
        out = analyze_project(str(synth_tree))
        assert "orphan" in out

    def test_analyze_project_skips_test_artifacts(self, tmp_path):
        """Test artifacts (fixtures/helpers) must never appear as findings."""
        from rlm.pyanalyze_core import analyze_project
        (tmp_path / "conftest.py").write_text("def fixture_fn():\n    return 1\n", encoding="utf-8")
        (tmp_path / "test_mod.py").write_text("def helper_fn():\n    return 2\n", encoding="utf-8")
        sub = tmp_path / "tests"
        sub.mkdir()
        (sub / "test_deep.py").write_text("def deep_helper():\n    return 3\n", encoding="utf-8")
        (tmp_path / "mod.py").write_text("def orphan():\n    return 4\n", encoding="utf-8")
        out = analyze_project(str(tmp_path))
        for name in ("fixture_fn", "helper_fn", "deep_helper"):
            assert name not in out, f"test artifact symbol flagged: {out}"
        assert "orphan" in out, f"real dead symbol lost: {out}"

    def test_analyze_project_clean(self, tmp_path):
        from rlm.pyanalyze_core import analyze_project
        (tmp_path / "c.py").write_text("def used(): pass\ndef go(): return used()\ngo()\n", encoding="utf-8")
        out = analyze_project(str(tmp_path))
        assert "No unreferenced functions" in out


# ── I9: tau-sanity.py is required by src/sanity.sh ───────────────────────

class TestTauSanityDuplicate:
    def test_tau_sanity_exists_for_sanity_sh(self):
        # src/sanity.sh does `cp -f tau.py tau-sanity.py` and runs the copy so
        # pkill targets only the test process. Deleting it breaks sanity.sh.
        sanity = Path(__file__).resolve().parents[1] / "sanity.sh"
        dut = Path(__file__).resolve().parents[1] / "tau-sanity.py"
        assert "tau-sanity.py" in sanity.read_text(encoding="utf-8")
        assert dut.exists()
