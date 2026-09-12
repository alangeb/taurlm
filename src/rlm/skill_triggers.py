"""Curated intent -> skill trigger table (Tier 2).

A small, high-precision map of human-intent phrases to skill names that
complements the keyword scorer in ``SkillLoader.find_skills``. The scorer
misses colloquial intent (e.g. "ship this" -> git); these triggers catch it.

The table is **data, not code**: a plain ``dict[str, list[str]]``. It is
consumed by ``SkillLoader.render_skill_nudge`` which merges trigger hits into
the same ranked candidate list the scorer produces (synthetic score 70 so
they rank first), then applies the existing threshold / dedup / guards. There
is NO second injection point — Tier 2 rides the Tier 1 nudge rail exactly.

Matching rule: **lowercase substring** match of the key against the user
message. Keys are chosen to be high-precision phrases (not bare single words)
so a wrong trigger is unlikely. All values MUST be real skill names present in
``src/skills/`` — a trigger pointing at a non-existent skill is filtered out at
merge time, but we keep the table clean regardless.
"""

from __future__ import annotations

# phrase (lowercased substring) -> skill names to surface, in priority order.
TRIGGERS: dict[str, list[str]] = {
    # git / shipping
    "ship this": ["git"],
    "push to": ["git"],
    "merge conflict": ["git"],
    "rebase": ["git"],
    "what changed": ["git-snapshot"],
    "call graph": ["rlm-analysis"],
    "what does this function call": ["rlm-analysis"],
    "who calls": ["rlm-analysis"],
    # review
    "code review": ["code-review"],
    "review this": ["code-review"],
    "review the diff": ["code-review"],
    # background / servers
    "in the background": ["background"],
    "long build": ["background"],
    "long running": ["background"],
    "long-running": ["background"],
    "run tests": ["test-runner"],
    "run the tests": ["test-runner"],
    "spin up": ["model-serving", "background"],
    "sglang": ["model-serving"],
    "deploy the model": ["model-serving"],
    # debugging
    "throwing an error": ["debug"],
    "why is it failing": ["debug"],
    # text / data
    "extract urls": ["text-utils"],
    "find and replace": ["text-utils"],
    "parse this json": ["data-processing"],
    "this csv": ["data-processing"],
    # research / memory
    "look it up online": ["web-research"],
    "search the web": ["web-research"],
    "remember this": ["wiki"],
    # delegation
    "delegate this": ["spawn"],
    "spawn a worker": ["spawn"],
}

__all__ = ["TRIGGERS"]
