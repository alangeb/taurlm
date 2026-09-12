"""Tests for RLM Python-Backed Skills."""

from pathlib import Path

from rlm.skills import SkillMetadata, SkillLoader

# Repo skills dir computed from __file__ (portable; no hardcoded home)
_SKILLS_DIR = str(Path(__file__).resolve().parents[3] / "src" / "skills")


class TestSkillMetadata:
    def test_creation(self):
        skill = SkillMetadata(name="test", description="A test skill")
        assert skill.name == "test"
        assert skill.description == "A test skill"

    def test_matches_name(self):
        skill = SkillMetadata(name="git")
        assert skill.matches("git") is True
        assert skill.matches("Use git") is True

    def test_matches_description(self):
        skill = SkillMetadata(name="test", description="code review")
        assert skill.matches("review") is True

    def test_matches_keywords(self):
        skill = SkillMetadata(name="test", keywords=["debug", "trace"])
        assert skill.matches("debug") is True
        assert skill.matches("trace") is True

    def test_no_match(self):
        skill = SkillMetadata(name="test", keywords=["debug"])
        assert skill.matches("something else") is False


class TestSkillLoader:
    def test_init_no_dir(self):
        loader = SkillLoader()
        assert loader.skills_dir is None

    def test_discover_empty(self):
        loader = SkillLoader()
        skills = loader.discover_skills()
        assert skills == []

    def test_load_skill_not_found(self):
        loader = SkillLoader()
        result = loader.load_skill("nonexistent")
        assert result is None

    def test_get_initial_namespace(self):
        loader = SkillLoader()
        ns = loader.get_initial_namespace()
        assert "available_skills" in ns
        assert "load_skill" in ns


class TestFlatMarkdownSkills:
    """Flat single-file markdown skills (e.g. ``wiki.md``) must be loadable."""

    @staticmethod
    def _make_skills_dir(root):
        """Build a skills dir with a flat skill, a dir skill, a collision and a README.

        Args:
            root: Temporary directory to populate

        Returns:
            The populated skills directory path
        """
        flat = root / "wiki.md"
        flat.write_text(
            "---\n"
            "name: wiki\n"
            "description: 'Wiki operations: store knowledge. Keywords: wiki, knowledge, memory.'\n"
            "category: knowledge\n"
            "---\n\n"
            "## When to Use\n\n- Store decisions\n",
            encoding="utf-8",
        )
        legacy = root / "legacy.md"
        legacy.write_text("# Legacy\n\nLegacy description.\n\nkeywords: foo, bar\n", encoding="utf-8")
        git_dir = root / "git"
        git_dir.mkdir()
        (git_dir / "SKILL.md").write_text(
            "---\nname: git\ndescription: DIR git skill\ncategory: version_control\n---\n",
            encoding="utf-8",
        )
        (root / "git.md").write_text(
            "---\nname: git\ndescription: FLAT git skill\n---\n", encoding="utf-8"
        )
        (root / "README.md").write_text("---\nname: readme\ndescription: not a skill\n---\n", encoding="utf-8")
        return root

    def test_discover_flat_skill(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        names = loader.discover_skills()
        assert {s.name for s in names} == {"wiki", "legacy", "git"}

    def test_flat_skill_metadata_from_frontmatter(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        skill = loader.load_skill("wiki")
        assert skill is not None
        assert skill.description.startswith("Wiki operations")
        assert skill.category == "knowledge"
        assert skill.keywords == ["wiki", "knowledge", "memory"]
        assert skill.path.endswith("wiki.md")

    def test_flat_skill_legacy_heuristics(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        skill = loader.load_skill("legacy")
        assert skill is not None
        assert skill.description == "Legacy description."
        assert skill.keywords == ["foo", "bar"]

    def test_directory_skill_wins_name_collision(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        skill = loader.load_skill("git")
        assert skill.description == "DIR git skill"
        assert skill.path.endswith("git")

    def test_readme_not_discovered(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        assert loader.load_skill("readme") is None
        assert "readme" not in loader._list_skills()

    def test_available_skills_lists_flat_skill(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        ns = loader.get_initial_namespace()
        assert "wiki" in ns["available_skills"]()

    def test_load_flat_skill_prints_markdown(self, tmp_path, capsys):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        out = loader.get_initial_namespace()["load_skill"]("wiki")
        assert "## When to Use" in out
        captured = capsys.readouterr()
        assert "[SKILL: wiki]" in captured.out


    def test_flat_skill_frontmatter_name_cannot_shadow_dir_skill(self, tmp_path):
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        (tmp_path / "other.md").write_text(
            "---\nname: git\ndescription: HIJACKER\n---\n", encoding="utf-8"
        )
        loader.discover_skills()
        assert loader.load_skill("git").description == "DIR git skill"
        assert "HIJACKER" not in [s.description for s in loader._cache.values()]

    def test_real_repo_wiki_skill_is_loadable(self):
        """Regression: src/skills/wiki.md must be discoverable via the repo skills dir."""
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        loader = SkillLoader(skills_dir)
        assert "wiki" in loader._list_skills()
        skill = loader.load_skill("wiki")
        assert skill is not None
        assert skill.path.endswith("wiki.md")
        assert loader.load_skill("docker") is not None
        # 'git' dir-form shadow was consolidated into the flat file (skill review);
        # dir-wins collision behavior is covered by the synthetic tests above.
        assert loader.load_skill("git").path.endswith("git.md")


class TestSkillMenu:
    """Test the standing skill-menu rendering for the system prompt."""

    def test_render_skill_menu_lists_skills(self, tmp_path):
        """render_skill_menu should list skill names and descriptions."""
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        menu = loader.render_skill_menu()
        assert "## Available Skills" in menu
        assert "wiki" in menu
        assert "git" in menu
        assert "legacy" in menu

    def test_render_skill_menu_empty(self):
        """render_skill_menu returns empty string when no skills exist."""
        loader = SkillLoader()
        assert loader.render_skill_menu() == ""

    def test_render_skill_menu_stable_order(self, tmp_path):
        """Menu order must be alphabetical (stable across calls)."""
        loader = SkillLoader(self._make_skills_dir(tmp_path))
        menu1 = loader.render_skill_menu()
        menu2 = loader.render_skill_menu()
        assert menu1 == menu2

    def test_read_system_prompt_contains_skill_menu(self):
        """read_system_prompt() output must include the ## Available Skills section."""
        from agent_subsystems import read_system_prompt
        prompt = read_system_prompt()
        assert "## Available Skills" in prompt
        # At least one real skill name from src/skills/
        assert "git" in prompt

    @staticmethod
    def _make_skills_dir(root):
        flat = root / "wiki.md"
        flat.write_text(
            "---\nname: wiki\ndescription: Wiki operations\n---\n",
            encoding="utf-8",
        )
        flat2 = root / "legacy.md"
        flat2.write_text(
            "---\nname: legacy\ndescription: Legacy skill\n---\n",
            encoding="utf-8",
        )
        git_dir = root / "git"
        git_dir.mkdir()
        (git_dir / "SKILL.md").write_text(
            "---\nname: git\ndescription: Git operations\n---\n",
            encoding="utf-8",
        )
        return root


class TestSkillNudge:
    """Tier 1: per-turn auto-SUGGEST nudge (never auto-load)."""

    @staticmethod
    def _dir(root):
        for nm, desc in [
            ("testing", "Run and debug the test suite with pytest"),
            ("git", "Version control: commit, branch, push, git operations"),
            ("wiki", "Wiki operations: store and retrieve knowledge"),
        ]:
            (root / f"{nm}.md").write_text(
                f"---\nname: {nm}\ndescription: {desc}\n---\n", encoding="utf-8"
            )
        return root

    def test_confident_query_emits_nudge(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        nudge = loader.render_skill_nudge("commit these changes to git and push")
        assert nudge  # non-empty
        assert "git" in nudge
        assert "load_skill" in nudge
        assert nudge.startswith("\n[skills:")  # single appended line

    def test_chitchat_no_nudge(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        assert loader.render_skill_nudge("hi there") == ""

    def test_below_threshold_no_nudge(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        # vague, no confident skill hit
        assert loader.render_skill_nudge("what is the weather like today outside") == ""

    def test_short_message_no_nudge(self, tmp_path):
        # UPDATED (FIX 3B): the short-query floor now DROPS to 6 chars for
        # confident (>=70) hits, so "git commit" (whole-token name hit, 80)
        # legitimately nudges. The <15-char noise floor is verified on a WEAK
        # short query instead: "test x git y" hits only weak scorer rows.
        loader = SkillLoader(self._dir(tmp_path))
        assert loader.render_skill_nudge("git commit") != ""  # strong hit, 10 chars
        # Weak short (6-14 char) queries keep the 15-char floor -> no nudge:
        assert loader.render_skill_nudge("foo bar baz") == ""

    def test_slash_command_no_nudge(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        assert loader.render_skill_nudge("/git commit these changes now") == ""

    def test_dedup_after_load(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        q = "commit these changes to git and push"
        assert "git" in loader.render_skill_nudge(q)
        # Simulate the model having loaded git -> next turn must not re-nudge it
        loader._loaded_names.add("git")
        after = loader.render_skill_nudge(q)
        assert "git" not in after

    def test_nudge_under_40_tokens(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        nudge = loader.render_skill_nudge("commit these changes to git and push to origin")
        assert len(nudge) // 4 <= 40


class TestNudgePrecision:
    """Regression: nudge precision fixes (a) no sub-threshold padding, (b) single
    skill when only one clears threshold, (c) meta-question guard. Use the REAL
    repo skills dir — these assert on real scorer/trigger behavior."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_a_no_subthreshold_padding(self):
        # refactor query must NOT drag in an unrelated weak scorer row (docker).
        n = self._loader().render_skill_nudge("refactor this function to be cleaner")
        assert "refactor" in n
        assert "docker" not in n

    def test_b_single_skill_when_only_one_clears(self):
        # Only refactor clears threshold -> exactly one skill, no comma padding.
        import re
        n = self._loader().render_skill_nudge("refactor this function to be cleaner")
        # exactly one "name(score)" pair (load_skill("...") paren must not count)
        assert len(re.findall(r"\w+\(\d+\)", n)) == 1

    def test_c_meta_question_stays_quiet(self):
        # Asking ABOUT a skill must not nudge the named skill.
        assert self._loader().render_skill_nudge(
            "why did git-snapshot get suggested"
        ) == ""

    def test_clear_intent_still_fires(self):
        # Regression: precision fixes must not silence genuine intent.
        n = self._loader().render_skill_nudge("please commit these changes and push to git")
        assert "git" in n and "load_skill" in n


class TestScorerDensity:
    """Regression: density-aware keyword scoring so the correct skill is not
    alphabetically evicted from the nudge's top-3 by a flat-60 tie."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_multi_keyword_hit_outranks_single(self):
        # 'testing' matches 'test' + 'unit test' -> must beat single-hit noise.
        rows = self._loader().find_skills("write a unit test for the parser module", top_n=4)
        assert rows[0]["name"] == "testing"
        assert rows[0]["score"] > rows[1]["score"]

    def test_correct_skill_surfaces_in_nudge(self):
        # Fresh loader (throttle not yet triggered) -> testing must be in nudge.
        n = self._loader().render_skill_nudge("write a unit test for the parser module")
        assert "testing" in n

    def test_density_breaks_flat_60_cluster(self):
        # A query hitting one skill on several keywords must rank it above
        # skills matching only one generic keyword.
        rows = self._loader().find_skills("count lines in this file and extract the docstrings", top_n=3)
        assert rows[0]["score"] >= 60
        assert rows[0]["name"] in {"text-utils", "code-analysis"}


class TestInvokeNudgeWiring:
    """Tier 1: invoke() must append the nudge to the NEWEST user message."""

    def test_invoke_appends_nudge_to_user_message(self):
        import types
        from agent_core import TauErgon

        recorded = {}

        class FakeCtx:
            def context_pct(self, _): return 0.1
            def append_user(self, content, **kw): recorded["content"] = content

        class FakeAudit:
            def user(self, _): pass
            def flush(self): pass

        loader = SkillLoader(_SKILLS_DIR)

        fake = types.SimpleNamespace(
            _turn_active=False,
            _session=types.SimpleNamespace(audit_writer=FakeAudit()),
            original_task=None,
            _skill_loader=loader,
            context=FakeCtx(),
            max_context_tokens=200000,
            invoke_loop=lambda: "ok",
        )
        TauErgon.invoke(fake, "commit these changes to git and push")
        assert "[skills:" in recorded["content"]
        assert "git" in recorded["content"]
        assert recorded["content"].startswith("commit these changes")

    def test_invoke_no_nudge_on_chitchat(self):
        import types
        from agent_core import TauErgon

        recorded = {}

        class FakeCtx:
            def context_pct(self, _): return 0.1
            def append_user(self, content, **kw): recorded["content"] = content

        class FakeAudit:
            def user(self, _): pass
            def flush(self): pass

        loader = SkillLoader(_SKILLS_DIR)
        fake = types.SimpleNamespace(
            _turn_active=False,
            _session=types.SimpleNamespace(audit_writer=FakeAudit()),
            original_task=None,
            _skill_loader=loader,
            context=FakeCtx(),
            max_context_tokens=200000,
            invoke_loop=lambda: "ok",
        )
        TauErgon.invoke(fake, "hi there")
        assert "[skills:" not in recorded["content"]
        assert recorded["content"] == "hi there"


class TestSkillTriggers:
    """Tier 2: curated intent triggers ride the Tier 1 nudge rail."""

    @staticmethod
    def _dir(root):
        for nm, desc in [
            ("git", "Version control: commit, branch, push, git operations"),
            ("git-snapshot", "Git state snapshot: combined diff and status"),
            ("code-review", "Code review workflow: structural analysis, correctness"),
            ("background", "Run long-running commands in detached tmux sessions"),
            ("model-serving", "Model serving: sglang/qwen3 deployment, health checks"),
            ("debug", "Debugging in TauRLM: REPL error recovery, timeouts"),
            ("wiki", "Wiki operations: store and retrieve knowledge"),
        ]:
            (root / f"{nm}.md").write_text(
                f"---\nname: {nm}\ndescription: {desc}\n---\n", encoding="utf-8"
            )
        return root

    def test_trigger_catches_scorer_miss(self, tmp_path):
        """Trigger catches a genuine scorer miss (synthetic fixture, not real skills).

        Regression: skill_maintenance adding `push` to git keywords made the
        scorer strong on the real skill set and broke this test when it pinned
        git's scorer weakness to _SKILLS_DIR. Keyword maintenance is the whole
        point of the loop, so the scorer-miss condition must be pinned to a
        synthetic fixture (self._dir), while the trigger firing is still
        asserted on the REAL set (invariant there: 'ship this' is not a git
        keyword, so the scorer can only ever reach ~30 < 40).
        """
        q = "ship this to main and push it"
        # Synthetic git.md (no 'ship'/'push' keywords) -> raw scorer MUST miss.
        synth = self._dir(tmp_path)
        s_loader = SkillLoader(synth)
        scorer = s_loader.find_skills(q, top_n=3, include_triggers=False)
        git_row = next((r for r in scorer if r["name"] == "git"), None)
        assert git_row is None or int(git_row["score"]) < 40
        # On the REAL skill set the trigger fires regardless of scorer strength.
        loader = SkillLoader(_SKILLS_DIR)
        nudge = loader.render_skill_nudge(q)
        assert "git" in nudge
        assert nudge.startswith("\n[skills: git")
        assert "load_skill(\"git\")" in nudge

    def test_trigger_respects_loaded_dedup(self):
        loader = SkillLoader(_SKILLS_DIR)
        q = "ship this to main and push it"
        assert "git" in loader.render_skill_nudge(q)
        loader._loaded_names.add("git")
        after = loader.render_skill_nudge(q)
        assert "git" not in after  # trigger hit deduped like scorer hits

    def test_no_trigger_no_match_emits_nothing(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        # No trigger phrase, no confident scorer hit.
        assert loader.render_skill_nudge("the quick brown fox jumps over") == ""

    def test_trigger_ranks_first(self, tmp_path):
        loader = SkillLoader(self._dir(tmp_path))
        nudge = loader.render_skill_nudge("run this long build in the background")
        # background (trigger, score 70) must lead the list
        assert nudge.startswith("\n[skills: background")  # trigger/scorer hit leads

    def test_trigger_table_only_real_skills(self):
        """Every trigger value must be a real skill in src/skills/."""
        loader = SkillLoader(_SKILLS_DIR)
        loader.discover_skills()
        from rlm.skill_triggers import TRIGGERS
        # Upper bound widened for FIX 4 (call-graph / long-running / run-tests triggers).
        assert 15 <= len(TRIGGERS) <= 40
        for phrase, skills in TRIGGERS.items():
            assert phrase == phrase.lower()
            for s in skills:
                assert s in loader._cache, f"trigger {phrase!r} -> unknown skill {s!r}"


class TestSkillTelemetry:
    """Tier 4: append-only skill-activation telemetry (fail-safe)."""

    def test_record_nudge_and_load_write_jsonl(self, tmp_path):
        import json
        from rlm.skill_telemetry import SkillTelemetry
        p = tmp_path / "tel.jsonl"
        tel = SkillTelemetry(p)
        tel.record_nudge(1, "ship this to main", [("git", 70), ("docker", 20)])
        tel.record_load("git")
        lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        nudge, load = lines
        assert nudge["event"] == "nudge"
        assert nudge["skills"] == ["git", "docker"]
        assert nudge["top"] == 70
        assert nudge["turn_id"] == 1
        assert load["event"] == "load"
        assert load["name"] == "git"
        assert "ts" in nudge and "ts" in load

    def test_summary_counts_and_nudged_not_loaded(self, tmp_path):
        from rlm.skill_telemetry import SkillTelemetry
        p = tmp_path / "tel.jsonl"
        tel = SkillTelemetry(p)
        # git nudged, then loaded -> NOT counted as nudged_not_loaded
        tel.record_nudge(1, "ship this", [("git", 70)])
        tel.record_load("git")
        # wiki nudged but never loaded -> counted once
        tel.record_nudge(2, "remember this decision", [("wiki", 70)])
        s = tel.summary()
        assert s["loads"] == {"git": 1}
        assert s["nudges"] == {"git": 1, "wiki": 1}
        assert s["nudged_not_loaded"] == {"wiki": 1}

    def test_broken_path_does_not_raise(self, tmp_path):
        """A directory in place of the file path makes writes fail; must be silent."""
        from rlm.skill_telemetry import SkillTelemetry
        bad = tmp_path / "sub"  # a dir, so open("a") on it raises IsADirectoryError
        tel = SkillTelemetry(bad)
        # None of these should raise:
        tel.record_nudge(1, "ship this to main", [("git", 70)])
        tel.record_load("git")
        s = tel.summary()
        assert s == {"loads": {}, "nudges": {}, "nudged_not_loaded": {}} or "loads" in s

    def test_summary_empty_when_no_file(self, tmp_path):
        from rlm.skill_telemetry import SkillTelemetry
        tel = SkillTelemetry(tmp_path / "missing.jsonl")
        assert tel.summary() == {"loads": {}, "nudges": {}, "nudged_not_loaded": {}}

    def test_nudge_and_load_wire_telemetry(self, tmp_path):
        """render_skill_nudge + load_skill must record via the loader's telemetry."""
        from rlm.skill_telemetry import SkillTelemetry
        loader = SkillLoader(self._dir(tmp_path))
        loader.telemetry = SkillTelemetry(tmp_path / "tel.jsonl")
        loader.render_skill_nudge("ship this to main and push it")
        loader._load_skill_into_namespace("git")
        s = loader.telemetry.summary()
        assert s["nudges"].get("git", 0) >= 1
        assert s["loads"].get("git", 0) == 1

    @staticmethod
    def _dir(root):
        for nm, desc in [
            ("git", "Version control: commit, branch, push, git operations"),
            ("wiki", "Wiki operations: store and retrieve knowledge"),
        ]:
            (root / f"{nm}.md").write_text(
                f"---\nname: {nm}\ndescription: {desc}\nkeywords: {nm}\n---\n",
                encoding="utf-8",
            )
        return root


class TestNudgePrune:
    """Telemetry prune loop: nudged-many-but-never-loaded skills drop out.

    Uses in-memory counters (loader._nudged_count / _loaded_names), NOT
    summary()/JSONL, so the check is O(1) per turn. Bootstrap-safe: a new
    skill has 0 nudges and is never pruned.
    """

    @staticmethod
    def _dir(root):
        for nm, desc in [
            ("git", "Version control: commit, branch, push, git operations"),
            ("testing", "Run and debug the test suite with pytest"),
            ("wiki", "Wiki operations: store and retrieve knowledge"),
        ]:
            (root / f"{nm}.md").write_text(
                f"---\nname: {nm}\ndescription: {desc}\nkeywords: {nm}\n---\n",
                encoding="utf-8",
            )
        return root

    def test_prune_drops_never_loaded(self, tmp_path):
        """git nudged 6x, never loaded -> pruned from the nudge candidate set."""
        loader = SkillLoader(self._dir(tmp_path))
        loader._nudged_count["git"] = 6  # seed: 6 nudges, 0 loads
        nudge = loader.render_skill_nudge("commit these changes to git and push")
        assert "git" not in nudge

    def test_prune_keeps_below_threshold(self, tmp_path):
        """A skill nudged 4x (<5) and never loaded is NOT pruned (bootstrap-safe)."""
        loader = SkillLoader(self._dir(tmp_path))
        loader._nudged_count["git"] = 4
        nudge = loader.render_skill_nudge("commit these changes to git and push")
        assert "git" in nudge

    def test_loaded_skill_exempt_from_prune(self, tmp_path):
        """A skill loaded once is exempt from the prune rule (loaded, not never-loaded)."""
        loader = SkillLoader(self._dir(tmp_path))
        loader._nudged_count["wiki"] = 6
        loader._loaded_names.add("wiki")
        loader._loaded_count["wiki"] = 1
        # Prune requires (nudged>=5 AND not loaded); wiki is loaded -> exempt.
        # It is still absent from the nudge via the loaded-dedup filter, which is
        # the correct outcome: a loaded skill must never be re-nudged.
        nudge = loader.render_skill_nudge("store this decision in the wiki knowledge base")
        assert "wiki" not in nudge
        assert loader._loaded_count["wiki"] == 1

    def test_prune_bootstrap_new_skill_stays(self, tmp_path):
        """A brand-new skill (0 nudges) is nudged normally."""
        loader = SkillLoader(self._dir(tmp_path))
        nudge = loader.render_skill_nudge("commit these changes to git and push")
        assert "git" in nudge
        assert loader._nudged_count.get("git", 0) >= 1  # increment wired in


class TestMetaGuardTightening:
    """FIX 1 regression: the meta-question guard must not fire on ordinary
    task sentences that merely contain the words 'skill'/'command', and must
    still suppress meta-questions (interrogative + meta-word + named skill)."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_task_sentence_with_command_still_nudges(self):
        # 'command' removed from the guard; no interrogative present either.
        n = self._loader().render_skill_nudge("run the git command to commit my staged changes")
        assert "git" in n
        assert n.startswith("\n[skills: git")

    def test_meta_question_with_interrogative_stays_quiet(self):
        # interrogative ('why') + meta word ('suggested') + skill named literally.
        assert self._loader().render_skill_nudge(
            "why was git-snapshot suggested in the skills nudge"
        ) == ""


class TestFindSkillsTriggers:
    """FIX 2 regression: find_skills() applies trigger merge by default, so the
    REPL helper sees the same intent-aware ranking as the nudge. Raw scorer
    remains inspectable via include_triggers=False."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_default_find_skills_surfaces_triggered_skill(self):
        rows = self._loader().find_skills("I want to ship this")
        assert rows and rows[0]["name"] == "git"
        assert rows[0]["score"] >= 40

    def test_include_triggers_false_returns_raw_scorer(self):
        rows = self._loader().find_skills("I want to ship this", include_triggers=False)
        assert "git" not in [r["name"] for r in rows[:3]]


class TestTokenScoringMultiWord:
    """FIX 3 regression: short real tokens must count and multi-word keyword
    overlap must score, so multi-word intent queries (e.g. 'run the tests')
    reach the test-runner instead of scoring 0 / ranking behind shell."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_multiword_query_reaches_test_runner(self):
        rows = self._loader().find_skills(
            "run the tests", top_n=5, include_triggers=False
        )
        tr = next((r for r in rows if r["name"] == "test-runner"), None)
        assert tr is not None and int(tr["score"]) >= 40, rows

    def test_stopwords_do_not_inflate_and_short_tokens_count(self):
        # 'tests' alone (len>2, not stopword) still matches test-runner's
        # 'run tests' keyword only via token overlap — must not exceed the
        # multi-word 45 band.
        skill = self._loader().load_skill("test-runner")
        assert skill.score("run the tests") == 45
        # stopwords-only query scores 0
        assert skill.score("the a an") == 0


class TestTriggerGapCoverage:
    """FIX 4 regression: curated triggers fill scorer gaps the scorer misses."""

    def _nudge(self, q):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir).render_skill_nudge(q)

    def test_call_graph_query_surfaces_rlm_analysis(self):
        n = self._nudge("what does this function call")
        assert "rlm-analysis" in n and n.startswith("\n[skills: rlm-analysis")

    def test_who_calls_surfaces_rlm_analysis(self):
        assert "rlm-analysis" in self._nudge("who calls get_user here")

    def test_run_tests_phrase_surfaces_test_runner(self):
        n = self._nudge("run tests for the parser and show failures")
        assert "test-runner" in n and n.startswith("\n[skills: test-runner")

    def test_long_running_surfaces_background(self):
        assert "background" in self._nudge("this is a long-running build task")


class TestTieBreakSpecificity:
    """FIX 5 regression: on a score tie, the skill matching the query on more
    distinct keywords (the more specific match) wins — not alphabetical order."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_file_ops_beats_code_search_on_underscore_query(self):
        rows = self._loader().find_skills(
            "file_ops", top_n=2, include_triggers=False
        )
        assert rows[0]["name"] == "file-ops"  # ties on score; more keyword hits win
        assert rows[0]["score"] == rows[1]["score"]

    def test_code_review_beats_code_analysis_on_review_code(self):
        rows = self._loader().find_skills(
            "review code", top_n=2, include_triggers=False
        )
        assert rows[0]["name"] == "code-review"

    def test_get_skill_for_query_uses_specificity_tiebreak(self):
        assert self._loader().get_skill_for_query("file_ops").name == "file-ops"


class TestMetaGuardWholeToken:
    """FIX 2A regression: interrogatives match WHOLE-TOKEN only, so substrings
    ('is' inside 'this/decision', 'what' inside 'whatever') cannot silently
    suppress a legitimate nudge."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_declarative_with_skill_and_substring_is_still_nudges(self):
        # 'skill' meta-word present, but NO whole-token interrogative: 'is'
        # appears only inside 'this'/'decision'. wiki must survive.
        n = self._loader().render_skill_nudge(
            "store this decision in the wiki skill base"
        )
        assert "wiki" in n and n.startswith("\n[skills: wiki")

    def test_whole_token_interrogative_suppresses(self):
        assert self._loader().render_skill_nudge(
            "why was git-snapshot suggested in the nudge line"
        ) == ""

    def test_how_do_i_use_skill_suppressed_by_design(self):
        # Documented intent: 'how do I use the git skill' is meta-ish
        # (asking about the skill), so git is suppressed; skill-authoring,
        # never named literally in the query, may still surface.
        n = self._loader().render_skill_nudge("how do I use the git skill")
        assert "git(" not in n


class TestFindSkillsTopNZero:
    """FIX 2B regression: top_n<=0 short-circuits to [] (pre-FIX-2 behavior),
    even for a trigger-hitting query."""

    def test_top_n_zero_returns_empty(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        loader = SkillLoader(skills_dir)
        assert loader.find_skills("I want to ship this", top_n=0) == []
        assert loader.find_skills("git", top_n=-1) == []

    def test_top_n_one_still_merges_trigger(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        loader = SkillLoader(skills_dir)
        rows = loader.find_skills("I want to ship this", top_n=1)
        assert [r["name"] for r in rows] == ["git"]


class TestShortQueryStrongHitFloor:
    """FIX 3A/3B regression: intent-rich short queries must not be silenced.
    A >=70 top candidate (trigger or strong scorer hit) unlocks nudging down
    to a 6-char floor; weak matches keep the old 15-char floor; queries under
    6 chars (or score 0) never nudge."""

    def _loader(self):
        skills_dir = Path(__file__).resolve().parents[2] / "skills"
        return SkillLoader(skills_dir)

    def test_run_the_tests_nudges_test_runner(self):
        n = self._loader().render_skill_nudge("run the tests")
        assert "test-runner" in n and n.startswith("\n[skills: test-runner")

    def test_short_strong_scorer_hit_nudges(self):
        # 'git commit': 10 chars, whole-token name hit at 80 >= 70.
        assert "git(" in self._loader().render_skill_nudge("git commit")

    def test_short_weak_query_stays_silent(self):
        assert self._loader().render_skill_nudge("legit") == ""       # score 0
        assert self._loader().render_skill_nudge("do it") == ""       # too short
        assert self._loader().render_skill_nudge("a") == ""          # 1 char
        assert self._loader().render_skill_nudge("tests") == ""      # 5 chars < floor 6
        assert self._loader().render_skill_nudge("foo bar baz") == ""  # 11 chars, weak

    def test_trigger_phrase_short_still_nudges(self):
        # 'ship this' = trigger hit (70) at 9 chars.
        assert "git(" in self._loader().render_skill_nudge("ship this")
