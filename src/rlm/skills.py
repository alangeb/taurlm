"""Python-Backed Skills for RLM

This module implements the skill loading system for RLM. Skills are
importable Python packages that provide specialized capabilities to
the agent.

Architecture
------------
Skills:
1. Discovered from skills/ directory
2. Each skill has a SKILL.md for metadata and instructions
2b. Skills may also be flat single-file markdown (e.g. skills/wiki.md); the
    directory form always wins on a name collision (it can also ship code)
3. Python package with __init__.py and main module
4. Loaded into kernel namespace on demand
5. Only metadata in startup prompt, full skill loaded on use

Key Classes
-----------
SkillLoader     : Skill discovery and loading
SkillMetadata   : Skill metadata from SKILL.md

Example
-------
    loader = SkillLoader(skills_dir="skills/")
    skills = loader.discover_skills()
    skill = loader.load_skill("git")
    # skill now available in kernel namespace

See Also
--------
- rlm/kernel.py: Persistent Python kernel
- rlm/spawn.py: Unified agent delegation (spawn)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rlm.skills_parse import (
    _first_paragraph,  # noqa: F401
    _keywords_from_body,  # noqa: F401
    _keywords_from_description,  # noqa: F401
    _parse_frontmatter,  # noqa: F401
    _parse_skill_md as _parse_skill_md_impl,
    _split_keywords,  # noqa: F401
)
from rlm.skills_score import (
    SkillMetadata,
    _keyword_hit_count,  # noqa: F401
    _whole_token,  # noqa: F401
)
from rlm.skills_render import merge_triggers as _merge_triggers_impl
from rlm.skills_render import render_skill_menu as _render_menu
from rlm.skills_render import render_skill_nudge as _render_nudge

__all__ = [
    "SkillMetadata",
    "SkillLoader",
]


# Max characters of SKILL.md loaded/printed verbatim into the
# kernel namespace (generous; larger files are truncated WITH a notice).
_SKILL_MAX_CHARS = 200_000


class SkillLoader:
    """Skill discovery and loading.

    Discovers skills from a directory and loads them into the kernel
    namespace on demand.

    Attributes:
        skills_dir: Path to skills directory
    """

    # Synthetic score for curated intent-trigger hits (Tier 2). High
    # enough to rank first and clear the default threshold (40).
    _TRIGGER_SCORE = 70

    def __init__(self, skills_dir: str | Path | None = None, fence_style: str = "std"):
        """Initialize skill loader.

        Args:
            skills_dir: Path to skills directory
            fence_style: Fence style for normalizing SKILL.md code examples
        """
        self.skills_dir = Path(skills_dir) if skills_dir else None
        self._fence_style = fence_style
        self._cache: dict[str, SkillMetadata] = {}
        self._loaded_names: set[str] = set()
        # In-memory prune counters (avoid O(log-size) summary() per nudge).
        # _nudged_count[name]: times nudged this session; _loaded_count[name]:
        # times loaded. Prune drops skills with >=5 nudges and 0 loads.
        self._nudged_count: dict[str, int] = {}
        self._loaded_count: dict[str, int] = {}
        # Tier 4 telemetry (lazy: never touches disk until first record).
        self.telemetry = None
        # Throttle (d): per-skill nudge cooldown. _turn_seq ticks once per
        # render_skill_nudge call (~one user turn); _last_nudge_turn maps a
        # skill to the turn it was last nudged. A skill is not re-nudged
        # within _nudge_cooldown turns (kills the 'git nudged 27x, loaded 0x'
        # nag). The Tier 0 menu is NEVER throttled, so discovery stays on.
        self._turn_seq = 0
        self._last_nudge_turn: dict[str, int] = {}
        self._nudge_cooldown = 6

    def discover_skills(self) -> list[SkillMetadata]:
        """Discover skills in skills directory.

        Two skill layouts are supported:

        1. **Directory skills** — ``<skills_dir>/<name>/SKILL.md`` (may also
           ship an ``__init__.py`` with importable helpers).
        2. **Flat single-file skills** — ``<skills_dir>/<name>.md`` with YAML
           frontmatter (e.g. ``wiki.md``, ``docker.md``).

        Name-collision rule: the directory form always wins over a flat file
        with the same name (a directory skill can additionally ship code), so
        ``git/SKILL.md`` shadows ``git.md``. ``README.md`` is never treated as
        a skill.

        Returns:
            List of discovered skills (directory skills first, then flat ones)
        """
        if not self.skills_dir or not self.skills_dir.exists():
            return []

        skills: list[SkillMetadata] = []

        # Pass 1: directory skills (authoritative on name collisions).
        for item in sorted(self.skills_dir.iterdir()):
            if item.is_dir() and (item / "SKILL.md").exists():
                metadata = self._parse_skill_md(item / "SKILL.md")
                if metadata:
                    metadata.path = str(item)
                    skills.append(metadata)
                    self._cache[metadata.name.lower()] = metadata

        # Pass 2: flat single-file skills; skip names already taken by a dir.
        for item in sorted(self.skills_dir.glob("*.md")):
            if not item.is_file() or item.name.lower() == "readme.md":
                continue
            if item.stem.lower() in self._cache:
                continue
            metadata = self._parse_skill_md(item)
            if metadata and metadata.name.lower() not in self._cache:
                metadata.path = str(item)
                skills.append(metadata)
                self._cache[metadata.name.lower()] = metadata

        return skills

    def render_skill_menu(self) -> str:
        """Render a compact skill menu for the system prompt.

        Delegates to rlm.skills_render.render_skill_menu (moved verbatim).
        """
        return _render_menu(self)

    def _merge_triggers(self, query: str, rows: list[dict], width: int = 3) -> list[dict]:
        """Prepend curated intent-trigger hits onto ranked scorer rows.

        Delegates to rlm.skills_render.merge_triggers (moved verbatim).
        """
        return _merge_triggers_impl(self, query, rows, width)


    def _get_telemetry(self):
        """Lazily construct the session telemetry recorder (guarded).

        Returns None if telemetry cannot be initialized, so a broken log
        dir never affects skill behavior. Tests may pre-set self.telemetry.
        """
        if self.telemetry is None:
            try:
                from rlm.skill_telemetry import SkillTelemetry
                self.telemetry = SkillTelemetry()
            except Exception:
                self.telemetry = None
        return self.telemetry

    def render_skill_nudge(self, query: str, threshold: int = 40) -> str:
        """Render a one-line skill nudge for a user turn, or empty string.

        Delegates to rlm.skills_render.render_skill_nudge (moved verbatim).
        """
        return _render_nudge(self, query, threshold)


    def load_skill(self, name: str) -> SkillMetadata | None:
        """Load a skill by name.

        Args:
            name: Skill name

        Returns:
            Skill metadata if found, None otherwise
        """
        key = name.strip().lower()
        # Check cache first
        if key in self._cache:
            return self._cache[key]

        # Discover if not cached
        if not self._cache:
            self.discover_skills()

        return self._cache.get(key)

    def get_skill_for_query(self, query: str) -> SkillMetadata | None:
        """Find the best-ranked skill that matches a query.

        Args:
            query: Query string to match against

        Returns:
            Matching skill if found, None otherwise
        """
        if not self._cache:
            self.discover_skills()

        matches = [(skill.score(query), skill) for skill in self._cache.values()]
        matches = [(score, skill) for score, skill in matches if score > 0]
        if not matches:
            return None
        matches.sort(key=lambda item: (
            -item[0],
            -_keyword_hit_count(item[1].keywords, query),
            item[1].name.lower(),
        ))
        return matches[0][1]

    def find_skills(
        self, query: str, top_n: int = 20, include_triggers: bool = True
    ) -> list[dict[str, object]]:
        """Return ranked skill matches without loading full skill content.

        Args:
            query: Search query
            top_n: Maximum number of rows to return.
            include_triggers: When True (default), curated Tier 2 intent
                triggers are merged into the ranked rows (synthetic score,
                ranked first) so callers — including the ``find_skills`` REPL
                helper — see the same intent-aware ranking the nudge does.
                Pass False to inspect the RAW scorer output only.

        Returns:
            List of dicts with name, description, category, path, and int score.
        """
        if top_n <= 0:
            return []
        if not self._cache:
            self.discover_skills()

        rows = []
        for skill in self._cache.values():
            score = skill.score(query)
            if score > 0:
                rows.append({
                    "name": skill.name,
                    "description": skill.description,
                    "category": skill.category,
                    "path": skill.path,
                    "score": score,
                })
        # FIX 2E: precompute keyword-hit counts ONCE per row (not per sort
        # comparison) for the specificity tie-break.
        hits = {
            row["name"].lower(): _keyword_hit_count(
                self._cache[row["name"].lower()].keywords, query
            )
            for row in rows
            if row["name"].lower() in self._cache
        }
        rows.sort(key=lambda row: (
            -int(row["score"]),
            -hits.get(row["name"].lower(), 0),
            row["name"].lower(),
        ))
        rows = rows[:top_n]
        if include_triggers:
            # Merge at full width so the trigger-intelligence matches the
            # nudge path without truncating a large top_n request.
            rows = self._merge_triggers(query, rows, width=top_n)
        return rows

    def get_initial_namespace(self) -> dict[str, Any]:
        """Get initial namespace for kernel with skills.

        Returns:
            Dict with skill-related functions
        """
        return {
            "available_skills": self._list_skills,
            "load_skill": self._load_skill_into_namespace,
            "find_skills": self._find_skills,
        }

    def _list_skills(self) -> list[str]:
        """List available skill names."""
        if not self._cache:
            self.discover_skills()
        return sorted(self._cache.keys())

    def _find_skills(self, query: str, top_n: int = 20) -> list[dict[str, object]]:
        """Find skills by query without loading full content."""
        return self.find_skills(query, top_n=top_n)

    def _load_skill_into_namespace(self, name: str) -> str:
        """Load skill into context by printing its full markdown content.

        When called with a specific skill name, prints the entire SKILL.md
        (or the flat ``<name>.md`` file for single-file skills) so it appears
        in REPL output (and thus in the agent's context).
        For .py files in the skill directory, import them manually:
            from skills.{name} import run

        Args:
            name: Skill name (e.g., 'file_ops', 'system_info')

        Returns:
            The full SKILL.md content as a string, or an error message.
        """
        # L-Pa5: circular import guard
        if name in _loading_skills:
            msg = f"Skill '{name}' is already being loaded (circular import)"
            print(msg)
            return msg
        _loading_skills.add(name)
        try:
            return self._load_skill_into_namespace_inner(name)
        finally:
            _loading_skills.discard(name)

    def _load_skill_into_namespace_inner(self, name: str) -> str:
        skill = self.load_skill(name)
        if skill is not None:
            self._loaded_names.add(skill.name)
            self._loaded_count[skill.name] = self._loaded_count.get(skill.name, 0) + 1
            _tel = self._get_telemetry()
            if _tel is not None:
                _tel.record_load(skill.name)
        if skill is None:
            available = ", ".join(sorted(self._cache.keys())) if self._cache else "none"
            msg = f"Skill '{name}' not found. Available: {available}"
            print(msg)
            return msg

        # Read the full skill markdown (normalize fences to configured style).
        # Directory skills store SKILL.md inside the dir; flat skills point at
        # the .md file itself.
        skill_path = Path(skill.path)
        skill_md = skill_path if skill_path.suffix.lower() == ".md" else skill_path / "SKILL.md"
        if skill_md.exists():
            raw = skill_md.read_text(encoding="utf-8")
            # Generous size cap: never silently drop content — if the skill
            # exceeds the cap, truncate and emit an explicit NOTICE.
            if len(raw) > _SKILL_MAX_CHARS:
                _omitted = len(raw) - _SKILL_MAX_CHARS
                raw = raw[:_SKILL_MAX_CHARS] + (
                    f"\n\n[TRUNCATION NOTICE: SKILL.md for '{name}' exceeded "
                    f"{_SKILL_MAX_CHARS} chars; {_omitted} more chars were omitted. "
                    "Read the file directly for the full text.]"
                )
            if self._fence_style != "std":
                from agent_repl_parse import normalize_fences
                raw = normalize_fences(raw, self._fence_style)
            print(f"\n[SKILL: {name}]\n{raw}")
            return raw

        # Fallback: return metadata as string
        fallback = f"Skill: {skill.name}\nDescription: {skill.description}\nKeywords: {', '.join(skill.keywords)}"
        print(f"\n[SKILL: {name}]\n{fallback}")
        return fallback

    def _parse_skill_md(self, path: Path) -> SkillMetadata | None:
        """Parse a skill markdown file.

        Delegates to rlm.skills_parse._parse_skill_md (moved verbatim).
        """
        return _parse_skill_md_impl(path)


# --- Re-exports of moved seams (kept so import sites are unchanged) ---
render_skill_menu = _render_menu
render_skill_nudge = _render_nudge
merge_triggers = _merge_triggers_impl


_loading_skills: set[str] = set()  # L-Pa5: circular import guard
