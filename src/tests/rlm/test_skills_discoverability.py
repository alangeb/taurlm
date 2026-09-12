"""Tests for skill discoverability, ranking, and search APIs."""

from commands.skills import _resolve_skills_dir, run as skills_command
from rlm.namespace import _make_skills_namespace
from rlm.skills import SkillLoader


def _loader() -> SkillLoader:
    skills_dir = _resolve_skills_dir()
    assert skills_dir is not None
    return SkillLoader(skills_dir)


def test_get_initial_namespace_exposes_find_skills():
    loader = _loader()
    ns = loader.get_initial_namespace()
    assert callable(ns["available_skills"])
    assert callable(ns["load_skill"])
    assert callable(ns["find_skills"])


def test_namespace_no_agent_exposes_find_skills():
    ns = _make_skills_namespace(None)
    assert callable(ns["available_skills"])
    assert callable(ns["find_skills"])
    assert "git" in ns["available_skills"]()


def test_find_skills_exact_name_beats_substring():
    loader = _loader()
    rows = loader.find_skills("git")
    assert rows
    assert rows[0]["name"] == "git"
    assert isinstance(rows[0]["score"], int)


def test_load_skill_is_case_insensitive():
    loader = _loader()
    skill = loader.load_skill("GIT")
    assert skill is not None
    assert skill.name.lower() == "git"


def test_find_skills_keyword_only_queries():
    loader = _loader()
    for query, expected in [
        ("requirements", "sdd"),
        ("platform", "system-info"),
        ("subagent", "delegation"),
    ]:
        rows = loader.find_skills(query)
        assert rows, query
        assert expected in [row["name"] for row in rows[:3]], (query, rows[:3])


def test_skills_command_filters_by_keywords():
    for query, expected in [
        ("requirements", "sdd"),
        ("platform", "system-info"),
        ("subagent", "delegation"),
    ]:
        out = skills_command(None, [query])
        assert expected in out, out


def test_find_skills_one_letter_not_noisy():
    loader = _loader()
    rows = loader.find_skills("a")
    assert rows == []


def test_find_skills_top_n(tmp_path):
    (tmp_path / "alpha.md").write_text(
        "---\nname: alpha\ndescription: alpha skill\nkeywords: alpha, shared\n---\n# Alpha\n"
    )
    (tmp_path / "beta.md").write_text(
        "---\nname: beta\ndescription: beta skill\nkeywords: beta, shared\n---\n# Beta\n"
    )
    loader = SkillLoader(tmp_path)
    rows = loader.find_skills("shared", top_n=1)
    assert len(rows) == 1


def test_find_skills_no_short_name_substring_false_positive():
    loader = _loader()
    assert "ml" not in [r["name"] for r in loader.find_skills("yaml")]
    assert "git" not in [r["name"] for r in loader.find_skills("legit")]


def test_find_skills_hyphenated_dir_skill_loadable():
    loader = _loader()
    assert loader.load_skill("file-ops") is not None
    assert loader.load_skill("system-info") is not None
    assert loader.load_skill("code-analysis") is not None
    # underscore identity must no longer resolve
    assert loader.load_skill("file_ops") is None


def test_stub_skill_helpers_are_importable(tmp_path):
    from skills.code_search import grep, find_defs, count_occurrences
    from skills.shell import run, which
    (tmp_path / "x.py").write_text("def foo():\n    pass\n")
    assert any("def foo" in line for line in grep("def foo", str(tmp_path)))
    assert any("foo" in line for line in find_defs("foo", str(tmp_path)))
    assert isinstance(count_occurrences("def", str(tmp_path)), list)
    assert run(["echo", "ok"])["out"].strip() == "ok"
    assert which("python") is None or isinstance(which("python"), str)


def test_malformed_frontmatter_warns(tmp_path, capsys):
    (tmp_path / "bad.md").write_text(
        "---\nname: bad\ndescription: bad skill\nkeywords: bad\n"
    )
    loader = SkillLoader(tmp_path)
    loader.discover_skills()
    captured = capsys.readouterr()
    assert "unterminated frontmatter" in captured.err


def test_missing_keywords_warns(tmp_path, capsys):
    (tmp_path / "nokw.md").write_text(
        "---\nname: nokw\ndescription: no keyword skill\n---\n# No Keywords\n"
    )
    loader = SkillLoader(tmp_path)
    loader.discover_skills()
    captured = capsys.readouterr()
    assert "no keywords" in captured.err
