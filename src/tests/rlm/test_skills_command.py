"""Unit tests for the /skills command (src/commands/skills.py).

Verifies the command lists available skills via the shared SkillLoader and
that the optional keyword argument narrows the result.
"""
from __future__ import annotations

from types import SimpleNamespace

import importlib
skills_cmd = importlib.import_module("commands.skills")


def _agent():
    return SimpleNamespace()


class TestSkillsCommand:
    def test_run_registered_in_commands(self):
        from commands import COMMANDS
        assert "skills" in COMMANDS
        assert callable(COMMANDS["skills"].run)

    def test_lists_skills_non_empty(self):
        out = skills_cmd.run(_agent(), [])
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert out.startswith("Skills (")
        # header + at least a few skill rows
        assert len(lines) > 3

    def test_listing_includes_known_skill(self):
        out = skills_cmd.run(_agent(), [])
        assert "debug" in out
        assert "testing" in out

    def test_sorted_by_name(self):
        out = skills_cmd.run(_agent(), [])
        names = [ln.strip().split()[0] for ln in out.splitlines()
                 if ln.startswith("  ")]
        assert names == sorted(names, key=str.lower)

    def test_filter_narrows_results(self):
        full = skills_cmd.run(_agent(), [])
        filtered = skills_cmd.run(_agent(), ["git"])
        full_rows = [ln for ln in full.splitlines() if ln.startswith("  ")]
        filt_rows = [ln for ln in filtered.splitlines() if ln.startswith("  ")]
        assert 0 < len(filt_rows) < len(full_rows)
        assert "git" in filtered

    def test_filter_no_match(self):
        out = skills_cmd.run(_agent(), ["zzznotarealskill"])
        assert "(0)" in out
        assert "none" in out
