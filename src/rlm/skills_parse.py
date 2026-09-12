"""Skill markdown parsing (extracted verbatim from rlm/skills.py).

SKILL.md / flat .md frontmatter + heuristics -> SkillMetadata. Pure move —
no behavior change; rlm.skills re-exports these helpers.
"""
from __future__ import annotations

import sys
from pathlib import Path

from rlm.skills_score import SkillMetadata

__all__ = ["_parse_skill_md", "_parse_frontmatter"]


def _parse_skill_md(path: Path) -> SkillMetadata | None:
    """Parse a skill markdown file (``SKILL.md`` or a flat ``<name>.md``).

    Metadata resolution order: YAML frontmatter (``name``, ``description``,
    ``category``, ``keywords``) first, then legacy heuristics (first
    paragraph as description, a ``keywords:`` line anywhere in the body).
    Keywords embedded in a frontmatter description
    (``... Keywords: a, b, c.``) are also harvested.

    Args:
            path: Path to the skill markdown file

    Returns:
            Skill metadata if parsed successfully, None on read/parse failure
    """
    try:
            content = path.read_text(encoding="utf-8")
            name = path.parent.name if path.name == "SKILL.md" else path.stem

            frontmatter, body = _parse_frontmatter(content)
            if not frontmatter and content.lstrip().startswith("---"):
                print(f"WARNING: Skill {path} has an unterminated frontmatter fence; using heuristics", file=sys.stderr)
            if frontmatter.get("name"):
                name = frontmatter["name"].strip()

            description = frontmatter.get("description", "").strip()
            category = frontmatter.get("category", "").strip()
            keywords = _split_keywords(frontmatter.get("keywords", ""))

            if not description:
                description = _first_paragraph(body)
                if not description:
                    print(f"WARNING: Skill {path} has no usable description", file=sys.stderr)
            if not keywords:
                keywords = _keywords_from_body(body)
            if not keywords:
                keywords = _keywords_from_description(description)
            if not keywords:
                print(f"WARNING: Skill {path} has no keywords; discoverability may be poor", file=sys.stderr)

            return SkillMetadata(
                name=name,
                description=description,
                keywords=keywords,
                version=frontmatter.get("version", "1.0.0").strip() or "1.0.0",
                author=frontmatter.get("author", "").strip(),
                category=category,
            )
    except (OSError, UnicodeDecodeError) as e:
            print(f"WARNING: Failed to parse skill {path}: {e}", file=sys.stderr)
            return None


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Split leading YAML-ish frontmatter from a markdown document.

    Only flat ``key: value`` pairs are understood (the format used by skill
    files); nested YAML is not supported and is left in the body.

    Args:
        content: Full markdown text

    Returns:
        Tuple of (frontmatter mapping, remaining body). Empty mapping when the
        document has no leading ``---`` fence.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, content

    fields: dict[str, str] = {}
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            return fields, "\n".join(lines[idx + 1:])
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line.startswith((" ", "\t")):
            fields[key.strip().lower()] = value.strip().strip(chr(39)).strip(chr(34))

    # Unterminated fence: treat everything as body, no frontmatter.
    return {}, content


def _first_paragraph(body: str) -> str:
    """Return the first non-heading paragraph line of a markdown body.

    Args:
        body: Markdown text (frontmatter already stripped)

    Returns:
        First prose line, or "" when none is found. Headings (``#``) are
        skipped and a ``##`` heading ends the search, matching the legacy
        heuristic used before frontmatter support was added.
    """
    for line in body.split("\n")[:20]:
        line = line.strip()
        if line.startswith("##"):
            break
        if not line or line.startswith("#") or line == "---":
            continue
        return line
    return ""


def _split_keywords(value: str) -> list[str]:
    """Split a comma-separated keyword string into a list.

    Args:
        value: Raw keyword string from frontmatter

    Returns:
        Non-empty, stripped keywords
    """
    return [kw.strip() for kw in value.split(",") if kw.strip()]


def _keywords_from_body(body: str) -> list[str]:
    """Extract keywords from a ``keywords:`` line in the body.

    Args:
        body: Markdown text

    Returns:
        Keywords found, or [] when the line is absent
    """
    for line in body.split("\n"):
        if "keywords:" in line.lower():
            return _split_keywords(line.split(":", 1)[1])
    return []


def _keywords_from_description(description: str) -> list[str]:
    """Extract keywords embedded in a description as ``Keywords: a, b, c.``.

    Flat single-file skills keep their keyword list inside the description
    string; harvesting it makes ``SkillMetadata.matches()`` work the same way
    as for directory skills.

    Args:
        description: Skill description text

    Returns:
        Keywords found, or [] when the marker is absent
    """
    marker = "keywords:"
    lowered = description.lower()
    idx = lowered.find(marker)
    if idx == -1:
        return []
    tail = description[idx + len(marker):].split(".")[0]
    return _split_keywords(tail)