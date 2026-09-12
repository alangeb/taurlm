
# Skill Maintenance

Periodic loop: mine audit logs for repeated uncovered work, audit the skill tree,
fix findability, create missing skills. Authoring schema lives in the
`skill-authoring` skill — `load_skill("skill-authoring")` before editing.
Skills live in `src/skills/` (flat `<name>.md` + dir `<name>/SKILL.md`).
LOG_DIR is `~/.local/taurlm/log`, files `<pid>_<timestamp>.audit`.

## Phase 1: Mine the audit logs

### 1.1 Tool-call frequency (what do we actually do?)
```bash
for f in ~/.local/taurlm/log/*.audit; do
  grep -oP "final_name='[^']*" "$f" 2>/dev/null | sed "s/final_name='"//
done | sort | uniq -c | sort -rn | head -40
```

### 1.2 Skill-load frequency (which skills get pulled, which never?)
```bash
grep -rh "final_name='skill'" ~/.local/taurlm/log/*.audit 2>/dev/null | \
  grep -oP "skill_name[^,]*" | sort | uniq -c | sort -rn
```
Skills that NEVER appear despite matching frequent tool patterns = findability bug (fix keywords/description).

### 1.3 Repeated uncovered patterns
Look for high-frequency tool SEQUENCES with no corresponding skill. Those are the new-skill candidates.

### 1.4 Recurring user prompts (what is the human really asking?)
```bash
for f in ~/.local/taurlm/log/*.audit; do
  grep -A1 "^\[.*\] USER" "$f" 2>/dev/null | grep "|" | \
    grep -vE "Think hard|EVERY TIME" | head -3
done | sort -u | head -40
```

## Phase 2: Audit the skill tree

### 2.1 Inventory (both layouts)
```python
import sys; sys.path.insert(0, "src")
from rlm.skills import SkillLoader
l = SkillLoader("src/skills"); skills = l.discover_skills()
print(len(skills), "skills")
for s in sorted(skills, key=lambda x: x.name):
    print(f"{s.name:20} [{s.category}] kw={len(s.keywords)} :: {s.description[:60]}")
```
Flag: missing category, 0 keywords, or description shorter than ~40 chars.

### 2.2 Per-skill checklist (see `skill-authoring` for the schema)
- [ ] frontmatter `name` (hyphenated), `description`, `category`, `keywords`
- [ ] `description` = capability + "Use for <task>" + Keywords tail
- [ ] `category` from the agreed vocabulary (no new near-dupe synonyms)
- [ ] `keywords` = the exact words users type (these drive ranking)
- [ ] content is project-specific, non-obvious, concise — no tutorials
- [ ] `## Related Skills` cross-refs present and bidirectional
- [ ] importable helpers actually import (`PYTHONPATH=src`), or marked copy-paste

### 2.3 Rewrite (caveman style)
Drop articles/fillers/pleasantries; fragments OK; code unchanged. Keep only
project-specific knowledge + verified `file:line` refs. Remove generic pedagogy.

## Phase 3: Findability

### 3.1 Does the keyword actually rank?
```python
import sys; sys.path.insert(0, "src")
from rlm.skills import SkillLoader
l = SkillLoader("src/skills"); l.discover_skills()
for q in ["create skill", "config", "pip install", "deploy", "train"]:
    print(f"{q!r:18} -> {[m['name'] for m in l.find_skills(q)[:3]]}")
```
Any high-value query returning `[]` = a missing skill or a missing keyword. Fix it.

### 3.2 Bidirectional cross-refs
```bash
for f in src/skills/*.md src/skills/*/SKILL.md; do
  grep -q "## Related Skills" "$f" || echo "MISSING Related Skills: $f"
done
```

## Phase 4: Create missing skills
For each uncovered repeated pattern from Phase 1.3 / a dead query from 3.1:
1. `load_skill("skill-authoring")` for the schema + checklist.
2. Write `src/skills/<name>.md` (flat) or `<name>/SKILL.md` (if it ships code).
3. Hyphenate `name:`; keep import dirs underscore; pick category from vocabulary.
4. Add `## Related Skills`.
5. Verify it is discovered + findable (Phase 3.1) and `./tau.py /skills <name>` lists it.

## Phase 5: Verify

### 5.1 Discovery + ranking
```python
import sys; sys.path.insert(0, "src")
from rlm.skills import SkillLoader
l = SkillLoader("src/skills"); l.discover_skills()
print("count:", len(l._cache))
print("find skill-authoring:", [m['name'] for m in l.find_skills('skill authoring')[:3]])
```

### 5.2 Tests + lint
```bash
PYTHONPATH=src python -m pytest -q src/tests/rlm/test_skills_discoverability.py
```

### 5.3 Final checklist
- [ ] every skill: frontmatter (name/description/category/keywords) + Related Skills
- [ ] every high-value query resolves to the right skill
- [ ] cross-refs bidirectional
- [ ] new skills created for identified gaps
- [ ] this prompt itself kept current (paths, API names)

## Output: SKILL MAINTENANCE REPORT
- Audit-log summary (top tools, never-loaded skills, uncovered patterns)
- Skill-audit results (what was rewritten/flagged)
- Findability fixes (queries that were dead, now resolve)
- New skills created
- Recommendations / deferred items

**Use Python code for all actions. Delegate bulk audit work with `spawn()` for parallel/isolated passes. Do NOT emit slash commands as text.**
