"""Skill scoring rules (extracted verbatim from rlm/skills.py).

Tier-1 relevance scorer: SkillMetadata plus whole-token / keyword-hit
helpers. Pure move — no behavior change; rlm.skills re-exports these names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["SkillMetadata"]


@dataclass
class SkillMetadata:
    """Skill metadata extracted from SKILL.md.

    Attributes:
        name: Skill name
        description: Short description
        keywords: List of keywords for matching
        version: Skill version
        author: Skill author
        path: Path to skill directory, or to the flat .md file for
            single-file skills
        category: Optional category from skill frontmatter
    """
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    author: str = ""
    path: str = ""
    category: str = ""

    def score(self, query: str) -> int:
        """Return a relevance score for *query*; 0 means no match.

        Exact names beat name substrings, exact keywords beat keyword
        substrings, and description matches are weak. This keeps short queries
        such as ``git`` from being stolen by ``git-snapshot``.
        """
        q = query.strip().lower()
        if not q:
            return 0
        name = self.name.lower()
        desc = self.description.lower()
        kws = [kw.lower() for kw in self.keywords if kw]

        if q == name:
            return 100
        if q in kws:
            return 90
        # Single-character queries are too noisy for substring matching; only
        # exact name/keyword matches above are allowed.
        if len(q) == 1:
            return 0
        # Whole-token match only: prevents 'ml' matching 'yaml' and 'git'
        # matching 'legit' while still letting 'file-ops' match as one token.
        if _whole_token(name, q):
            return 80
        # Keyword match, DENSITY-AWARE. A single whole-token keyword hit
        # scores 60; each ADDITIONAL distinct keyword hit adds 10 (cap 85),
        # so a skill matching the query on several keywords ('testing' on
        # 'test' + 'unit test') outranks one matching a single generic
        # keyword ('write' -> data-processing). This collapses the flat-60
        # cluster that let an alphabetically-earlier skill evict the right
        # one from the nudge's top-3. A loose substring hit (query is a
        # substring of a keyword) is demoted to 55 so it never ties a
        # genuine whole-token match.
        kw_hits = sum(1 for kw in kws if kw == q or _whole_token(kw, q))
        if kw_hits:
            return min(60 + 10 * (kw_hits - 1), 85)
        if any(len(q) >= 4 and q in kw for kw in kws):
            return 55
        if q in name:
            return 50
        if len(q) < 20 and q in desc:
            return 40
        # Token-set scoring: drop stopwords, keep short real tokens (len>2)
        # like 'run'/'git'/'add' so multi-word queries score sensibly.
        _STOPWORDS = {
            "the", "a", "an", "this", "that", "for", "and", "or", "of", "to",
            "in", "on", "with", "is", "are", "was", "were", "do", "does",
            "did", "i", "you", "we", "my", "our",
        }
        q_tokens = {
            tok for tok in q.replace(",", " ").split()
            if len(tok) > 2 and tok not in _STOPWORDS
        }
        if q_tokens:
            kw_tokens = {tok for kw in kws for tok in kw.replace(",", " ").split()}
            desc_tokens = {
                tok for tok in desc.replace(",", " ").split()
                if len(tok) > 2 and tok not in _STOPWORDS
            }
            kw_overlap = q_tokens & kw_tokens
            # >=2 distinct keyword-token overlaps is a strong multi-word signal
            # (e.g. 'run'+'tests' -> test-runner); beats a bare single overlap.
            if len(kw_overlap) >= 2:
                return 45
            if kw_overlap:
                return 30
            if q_tokens & desc_tokens:
                return 20
        return 0

    def matches(self, query: str) -> bool:
        """Check if skill matches a query.

        Args:
            query: Query string to match against

        Returns:
            True if skill matches the query
        """
        return self.score(query) > 0


def _whole_token(needle: str, haystack: str) -> bool:
    """Return True if *needle* appears in *haystack* as a whole token.

    Token boundaries are start/end or any non-alphanumeric character, so a
    hyphenated skill name like ``file-ops`` still matches as one token while a
    short name like ``ml`` does NOT match inside ``yaml`` and ``git`` does not
    match inside ``legit``. Both arguments must already be lowercased.
    """
    if not needle:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack) is not None


def _keyword_hit_count(keywords: list[str], query: str) -> int:
    """Distinct keywords matching *query* as a whole token (or exact).

    Separator-insensitive so a hyphenated name/keyword ('file-ops') matches an
    underscored query ('file_ops') and vice versa. Used for tie-breaking:
    the skill matching the query on MORE distinct keywords is the more
    specific match and should rank first.
    """
    q = query.strip().lower()
    if not q:
        return 0
    hits = 0
    for kw in keywords:
        k = kw.lower()
        if not k:
            continue
        if _whole_token(k, q) or _whole_token(k.replace("-", "_"), q):
            hits += 1
    return hits
