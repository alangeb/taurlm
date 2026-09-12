"""Skills Command — /skills

Lists all available skills (name + description + category), sorted by name.
Reuses the existing SkillLoader for discovery — no reimplementation.

Usage:
    /skills              — List all available skills
    /skills <keyword>    — Filter by substring in name, description, or keywords
"""

from pathlib import Path

__all__ = ["run"]

# Max description chars shown per line (keeps output compact for LLM context)
_DESC_MAX = 72


def _resolve_skills_dir() -> str | None:
    """Find the skills directory using the same candidate order as the loader
    wiring (rlm/namespace.py, rlm/kernel_wiring.py). Returns the first path
    that exists, or None.
    """
    candidates = [
        Path("skills"),
        Path(__file__).resolve().parent.parent / "skills",
        Path.home() / "taurlm" / "skills",
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return None


def _render(skills, header: str) -> str:
    """Render an aligned, compact skill listing."""
    if not skills:
        return header.rstrip(":") + ": none"
    name_w = max(len(s.name) for s in skills)
    lines = [header]
    for s in skills:
        desc = " ".join(s.description.split())
        if len(desc) > _DESC_MAX:
            desc = desc[: _DESC_MAX - 1].rstrip() + "\u2026"
        cat = f"[{s.category}] " if s.category else ""
        lines.append(f"  {s.name:<{name_w}}  {cat}{desc}")
    return "\n".join(lines)


def run(agent, args: list[str] = None) -> str:
    """Execute /skills command.

    Args:
        agent: The TauErgon agent instance (unused; skills are global).
        args: Command arguments. Optional single keyword filters the list.

    Returns:
        Command output string listing skills.
    """
    from rlm.skills import SkillLoader

    skills_dir = _resolve_skills_dir()
    loader = SkillLoader(skills_dir)
    skills = loader.discover_skills()

    parts = args or []
    if parts:
        keyword = " ".join(parts)
        scored = [(s.score(keyword), s) for s in skills]
        scored = [(sc, s) for sc, s in scored if sc > 0]
        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        skills = [s for _, s in scored]
        header = f"Skills matching {keyword!r} ({len(skills)}):"
    else:
        header = f"Skills ({len(skills)}):"
        skills = sorted(skills, key=lambda s: s.name.lower())

    return _render(skills, header)
