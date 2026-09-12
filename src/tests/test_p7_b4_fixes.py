"""Pin tests for P7-B4-20 (graph node-id collisions) and P7-B4-27 (dead-code false positives).

These tests MUST fail before the fix and pass after.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── P7-B4-20: nested function ID collision ──────────────────────────────────

class TestNestedFunctionIdCollision:
    """A nested def and a top-level def with the same name must produce
    distinct graph nodes with independent callers/impact."""

    FIXTURE = textwrap.dedent("""\
        def helper():
            return 1

        def outer():
            def helper():
                return 2
            return helper()
    """)

    def _build(self, tmp_path: Path):
        from rlm.pygraph_core import build_graph
        mod = tmp_path / "fixture_mod.py"
        mod.write_text(self.FIXTURE)
        return build_graph(str(tmp_path))

    def test_two_distinct_nodes(self, tmp_path):
        g = self._build(tmp_path)
        ids = {n.id for n in g.nodes}
        assert "fixture_mod.py:helper" in ids, f"Missing top-level node. ids={ids}"
        assert "fixture_mod.py:outer.helper" in ids, f"Missing nested node. ids={ids}"

    def test_nested_caller_id_qualified(self, tmp_path):
        """Calls FROM inside a nested function must use the qualified caller_id."""
        src = textwrap.dedent("""\
            def helper():
                return 1

            def outer():
                def helper():
                    return target()
                return helper()

            def target():
                return 0
        """)
        mod = tmp_path / "caller_mod.py"
        mod.write_text(src)
        from rlm.pygraph_core import build_graph
        g = build_graph(str(tmp_path))
        # The call target() inside outer.helper should have caller_id
        # "caller_mod.py:outer.helper", NOT "caller_mod.py:helper"
        callers_of_target = g.callers("caller_mod.py:target")
        assert "caller_mod.py:outer.helper" in callers_of_target, \
            f"Call from nested fn should use qualified caller_id. callers={callers_of_target}"
        # And NOT be attributed to the top-level helper
        assert "caller_mod.py:helper" not in callers_of_target, \
            f"Call from nested fn must NOT attach to top-level helper. callers={callers_of_target}"

    def test_impact_isolated(self, tmp_path):
        g = self._build(tmp_path)
        top_impact = g.impact("fixture_mod.py:helper")
        nested_impact = g.impact("fixture_mod.py:outer.helper")
        # They are independent nodes
        assert top_impact != nested_impact

    def test_top_level_id_unchanged(self, tmp_path):
        """Backward compat: top-level defs keep file:name (no extra dots)."""
        g = self._build(tmp_path)
        node = g.node_by_id("fixture_mod.py:helper")
        assert node is not None
        assert node.name == "helper"

    def test_bare_call_resolves_top_level_only(self, tmp_path):
        """A bare call to 'helper' at module level must resolve to top-level
        helper, NOT to the nested outer.helper."""
        src = textwrap.dedent("""\
            def helper():
                return 1

            def outer():
                def helper():
                    return 2
                return helper()

            result = helper()
        """)
        mod = tmp_path / "bare_call_mod.py"
        mod.write_text(src)
        from rlm.pygraph_core import build_graph
        g = build_graph(str(tmp_path))
        callers_of_top = g.callers("bare_call_mod.py:helper")
        assert "bare_call_mod.py:__module__" in callers_of_top, \
            f"Module-level call should hit top-level. callers={callers_of_top}"
        callers_of_nested = g.callers("bare_call_mod.py:outer.helper")
        assert "bare_call_mod.py:__module__" not in callers_of_nested


# ── P7-B4-27: dead-code false positives on cross-file symbols ────────────────

class TestDeadCodeCrossFile:
    """Symbols used only cross-file must NOT be flagged dead."""

    def _make_project(self, tmp_path: Path):
        (tmp_path / "mod_a.py").write_text(
            "def alpha():\n    return 42\n\ndef truly_dead():\n    return 0\n"
        )
        (tmp_path / "mod_b.py").write_text(
            "from mod_a import alpha\n\ndef use_alpha():\n    return alpha()\n"
        )

    def test_cross_file_symbol_not_flagged(self, tmp_path):
        self._make_project(tmp_path)
        from rlm.pyanalyze_core import analyze_project
        report = analyze_project(str(tmp_path))
        lines = [l.strip() for l in report.split("\n")]
        dead_entries = [l for l in lines if l.startswith("- `") and "alpha" in l]
        assert len(dead_entries) == 0, f"alpha falsely flagged: {dead_entries}"

    def test_truly_dead_still_flagged(self, tmp_path):
        self._make_project(tmp_path)
        from rlm.pyanalyze_core import analyze_project
        report = analyze_project(str(tmp_path))
        assert "truly_dead" in report, f"truly_dead should be flagged. Report:\n{report}"

    def test_getattr_dispatch_not_dead(self, tmp_path):
        """Symbols dispatched via getattr must be UNREFERENCED not DEAD."""
        (tmp_path / "plugin.py").write_text(
            "def run_check():\n    return True\n"
        )
        (tmp_path / "dispatcher.py").write_text(
            "import plugin\n"
            "def dispatch(name):\n"
            "    fn = getattr(plugin, name)\n"
            "    return fn()\n"
        )
        from rlm.pyanalyze_core import analyze_project
        report = analyze_project(str(tmp_path))
        # run_check is dispatched via getattr -> must NOT appear as dead entry
        lines = [l.strip() for l in report.split("\n")]
        dead_entries = [l for l in lines if l.startswith("- `") and "run_check" in l]
        assert len(dead_entries) == 0, \
            f"getattr-dispatched symbol should not be flagged dead. Report:\n{report}"

    def test_all_export_not_flagged(self, tmp_path):
        """Symbols in __all__ must not be flagged."""
        (tmp_path / "api.py").write_text(
            "__all__ = [\'public_fn\']\n\ndef public_fn():\n    return 1\n"
        )
        (tmp_path / "consumer.py").write_text(
            "import api\n"
        )
        from rlm.pyanalyze_core import analyze_project
        report = analyze_project(str(tmp_path))
        lines = [l.strip() for l in report.split("\n")]
        dead_entries = [l for l in lines if l.startswith("- `") and "public_fn" in l]
        assert len(dead_entries) == 0, f"__all__ symbol falsely flagged: {dead_entries}"

    def test_output_wording_not_dead(self, tmp_path):
        """Report must say UNREFERENCED not DEAD."""
        (tmp_path / "lonely.py").write_text(
            "def orphan():\n    return 1\n"
        )
        from rlm.pyanalyze_core import analyze_project
        report = analyze_project(str(tmp_path))
        assert "DEAD" not in report, f"Report must not say DEAD. Got:\n{report}"
        assert "UNREFERENCED" in report, f"Report must say UNREFERENCED. Got:\n{report}"
