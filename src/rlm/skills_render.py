"""Skill prompt rendering (extracted verbatim from rlm/skills.py).

Everything that writes skill text into the agent prompt: the Tier-0 menu,
Tier-2 trigger merge, and the per-turn nudge. Operates on a SkillLoader
passed in as `loader` (duck-typed). Pure move — no behavior change.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from rlm.skills_score import _whole_token

if TYPE_CHECKING:
    from rlm.skills import SkillLoader

__all__ = ["render_skill_menu", "render_skill_nudge", "merge_triggers"]


# Absolute floor for nudge eligibility; below this, no query nudges.
# Queries 6-14 chars may still nudge when the top candidate is a strong
# hit (>= _TRIGGER_SCORE) — see the deferred guard in render_skill_nudge.
_SHORT_QUERY_FLOOR = 6

# Meta-question guard vocabulary (used by render_skill_nudge). Meta-words are
# matched as substrings; interrogatives MUST be whole-token matched (see the
# guard) so 'is' does not match inside 'this'/'decision'.
_META_WORDS = ("skill", "nudge", "suggested", "load_skill")
_INTERROGATIVES = (
    "why", "what", "how", "when", "did", "does", "is", "which",
    "should", "could", "would",
)


def render_skill_menu(loader: "SkillLoader") -> str:
    """Render a compact '## Available Skills' menu for the system prompt.

    Lists each discovered skill as ``- name: description`` in stable
    (alphabetical) order.  Descriptions are truncated to ~80 chars to
    keep the total menu under ~600 tokens.

    Returns:
        Markdown string (empty string if no skills found).
    """
    if not loader._cache:
        loader.discover_skills()
    if not loader._cache:
        return ""
    lines = ["\n## Available Skills\n"]
    for name in sorted(loader._cache.keys()):
        meta = loader._cache[name]
        desc = meta.description.strip()
        if len(desc) > 60:
            desc = desc[:57] + "..."
        lines.append(f"- **{meta.name}**: {desc}")
    lines.append("")
    return "\n".join(lines)


def merge_triggers(loader: "SkillLoader", query: str, rows: list[dict],
                 width: int = 3) -> list[dict]:
    """Prepend curated intent-trigger hits onto ranked scorer rows.

    Lowercase substring match of each trigger key against *query*. For
    every matched skill (in table priority order) that exists in the
    cache and is not already listed, prepend a synthetic row scored
    ``_TRIGGER_SCORE`` so it ranks first. Scorer rows are appended after,
    deduped against trigger hits. Result is capped at the caller's
    candidate width (3).

    Args:
        query: Raw user message text.
        rows: Scorer-ranked rows from find_skills().

    Returns:
        Merged candidate rows (trigger hits first).
    """
    try:
        from rlm.skill_triggers import TRIGGERS
    except Exception:
        return rows
    q = query.lower()
    by_name = {r["name"]: r for r in rows}
    trigger_rows: list[dict] = []
    seen: set[str] = set()
    for phrase, skills in TRIGGERS.items():
        if phrase in q:
            for name in skills:
                if name in seen or name not in loader._cache:
                    continue
                if name in by_name:
                    # Trigger is the confident signal: upgrade the scorer
                    # score so a weak substring hit (e.g. git@20) clears
                    # the threshold and ranks first.
                    by_name[name]["score"] = max(
                        int(by_name[name]["score"]), loader._TRIGGER_SCORE
                    )
                    trigger_rows.append(by_name[name])
                else:
                    meta = loader._cache[name]
                    trigger_rows.append({
                        "name": meta.name,
                        "description": meta.description,
                        "category": meta.category,
                        "path": meta.path,
                        "score": loader._TRIGGER_SCORE,
                    })
                seen.add(name)
    if not trigger_rows:
        return rows
    # Trigger hits first (in priority order), then remaining scorer rows.
    rest = [r for r in rows if r["name"] not in seen]
    return (trigger_rows + rest)[:max(1, width)]


def render_skill_nudge(loader: "SkillLoader", query: str,
                       threshold: int = 40) -> str:
    """Render a one-line skill nudge for a user turn, or "" if none.

    Runs find_skills() on *query*; if the top match scores >= *threshold*
    (a confident hit), emits a single line listing up to 3 ranked skills
    so the model is reminded it can load_skill(). The model stays in
    control — this is a suggestion, never an auto-load.

    Guards: returns "" for very short queries (a 15-char floor for weak
    matches, lowered to _SHORT_QUERY_FLOOR=6 when the top candidate is a
    confident >=70 trigger/scorer hit — so 'run the tests'/'git commit'
    nudge but 'do it' never does), slash-commands, and when every
    candidate was already loaded this session (dedup).

    Args:
        query: The raw user message text.
        threshold: Minimum top-match score to emit a nudge (default 40).

    Returns:
        A single nudge line (no leading newline) or "".
    """
    if not query or len(query.strip()) < _SHORT_QUERY_FLOOR:
        return ""
    if query.strip().startswith("/"):
        return ""
    if not loader._cache:
        loader.discover_skills()
    if not loader._cache:
        return ""
    # One tick per considered turn (drives the cooldown window).
    loader._turn_seq += 1
    _cur_turn = loader._turn_seq
    try:
        # Raw scorer rows only: _merge_triggers below applies the Tier 2
        # merge once, at the nudge's width (3).
        rows = loader.find_skills(query, top_n=3, include_triggers=False)
    except Exception:
        return ""
    # Tier 2: merge curated intent triggers (catches colloquial intent the
    # scorer misses, e.g. "ship this" -> git). Trigger hits are prepended
    # with a synthetic score (70) so they rank first; the EXISTING dedup +
    # threshold + guards below are unchanged, so Tier 2 rides the Tier 1
    # rail exactly (no second injection point).
    rows = loader._merge_triggers(query, rows)
    # FIX 3B deferred length guard: queries between the hard floor (6)
    # and 15 chars nudge ONLY on a confident top hit (>= trigger score,
    # which covers both Tier 2 triggers and strong scorer matches like
    # whole-token name hits at 80). Weak/no matches keep the 15-char floor.
    _strong = bool(rows) and int(rows[0]["score"]) >= loader._TRIGGER_SCORE
    if len(query.strip()) < 15 and not _strong:
        return ""
    # (c) Meta-question guard: when the user is asking ABOUT skills/
    # commands (not doing the task), don't nudge a skill they merely
    # named — e.g. 'why did git-snapshot get suggested' must stay quiet
    # instead of nudging git/git-snapshot. Only fires on explicit
    # meta-language, and only drops skills named literally in the query.
    _ql = query.lower()
    # Guard = meta-word + WHOLE-TOKEN interrogative + skill named literally
    # in the query. Whole-token matching (not substring) is essential:
    # substring 'is' matches 'this'/'decision' and silently kills nudges
    # for ordinary task sentences. Note: 'how do I use the git skill' is
    # suppressed by design (meta-ish phrasing about using the skill).
    if (
        any(m in _ql for m in _META_WORDS)
        and any(_whole_token(w, _ql) for w in _INTERROGATIVES)
    ):
        rows = [r for r in rows if r["name"].lower() not in _ql]
    # Drop skills already loaded this session (dedup across turns).
    rows = [r for r in rows if r["name"] not in loader._loaded_names]
    # Throttle (d): drop skills nudged in the last _nudge_cooldown turns.
    rows = [
        r for r in rows
        if _cur_turn - loader._last_nudge_turn.get(r["name"], -10**9) > loader._nudge_cooldown
    ]
    # Prune (telemetry loop): a skill nudged >=5x but never loaded has
    # proven it doesn't need nudging -> drop it from the candidate set.
    # In-memory counters only (no summary()/JSONL read per turn). Bootstrap-
    # safe: a brand-new skill has 0 nudges so it stays nudged.
    rows = [
        r for r in rows
        if not (
            loader._nudged_count.get(r["name"], 0) >= 5
            and r["name"] not in loader._loaded_names
        )
    ]
    if not rows:
        return ""
    # (a) Gate EVERY listed skill at threshold, not just the top, so weak
    # scorer padding (e.g. docker(20) riding along on a git/refactor
    # query) never pollutes the nudge. (b) follows naturally: if only one
    # skill clears threshold, only one is listed — no forced 3-row padding.
    rows = [r for r in rows if int(r["score"]) >= threshold]
    if not rows:
        return ""
    top = rows[0]
    parts = ", ".join(f'{r["name"]}({int(r["score"])})' for r in rows[:3])
    # Throttle (d): remember this turn for every skill we just named.
    for _r in rows[:3]:
        loader._last_nudge_turn[_r["name"]] = _cur_turn
        loader._nudged_count[_r["name"]] = loader._nudged_count.get(_r["name"], 0) + 1
    _tel = loader._get_telemetry()
    if _tel is not None:
        _matches = [(r["name"], int(r["score"])) for r in rows[:3]]
        _tel.record_nudge(_tel.next_turn_id(), query, _matches)
    return (
        f"\n[skills: {parts} — load_skill(\"{top['name']}\") if relevant]"
    )
