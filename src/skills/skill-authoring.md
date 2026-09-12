---
name: skill-authoring
description: 'Author, audit, and maintain TauRLM skills: frontmatter schema (name/description/category/keywords), dir-vs-flat layouts, collision rule, hyphen-name vs underscore-import-dir convention, score() ranking rules so keywords actually rank, importability, discoverability checks. Use when creating a new skill, fixing findability, or running skill maintenance. Keywords: skill, authoring, create skill, SKILL.md, frontmatter, keywords, category, discoverability, findability, skill maintenance, add skill, audit skills, review skills, skill review, skill format.'
category: development
keywords: 'skill, authoring, create skill, SKILL.md, frontmatter, keywords, category, discoverability, findability, skill maintenance, add skill, audit skills, review skills, skill review, skill format'
---

# Skill Authoring & Maintenance

Skills are loadable domain docs the agent pulls via `load_skill("name")`.
This skill = how to write one so it is actually FOUND and stays honest.
NOT about SDD specs (see `sdd`). Maintenance loop: `prompts/skillmaintenance.md`.

## Layouts (both discovered by `SkillLoader.discover_skills()`)
- **Flat**: `src/skills/<name>.md` — single file. Default choice.
- **Directory**: `src/skills/<name>/SKILL.md` (+ optional `__init__.py` with importable helpers, other `.md` reference files).
- **Collision rule**: directory form WINS over a flat `.md` with the same stem. `README.md` is never a skill.

## Naming convention (hyphen vs underscore)
- User-facing identity = frontmatter `name:` → use **hyphens** (`file-ops`, `code-analysis`). This is what `find_skills`/`load_skill`/`/skills` show.
- On-disk **package directory** must stay a valid Python identifier → **underscores** (`file_ops/`). Tests import `from skills.<pkg> import run`; hyphenating the dir is a SyntaxError.
- Flat `.md` files: filename stem may use hyphens (`code-review.md`) — not imported as a package.
- Net rule: **hyphenate the `name:` and the doc title; keep the import path underscore** for importable dir skills.

## Frontmatter schema (YAML, flat `key: value` only)
```
---
name: my-skill              # hyphenated, unique, lowercase
description: 'What it does + WHEN to use it + Keywords: a, b, c.'
category: development       # see vocabulary below
keywords: 'a, b, c, d'      # comma list; drives find_skills ranking
version: 1.0.0              # optional
author: ''                  # optional
---
```
- `description` is the #1 findability signal. Lead with capability, then "Use for <task>." Then a `Keywords:` tail.
- If `description`/`keywords` are missing the parser falls back to first-paragraph / body scan and emits a stderr WARNING — avoid the warning, declare them.

## Category vocabulary (keep it small; don't invent near-dupes)
Prefer: `development`, `analysis`, `operations`, `rlm`, `ml`, `system`, `knowledge`, `data`, `infrastructure`.
Real drift today: `system`/`infrastructure`/`operations` are near-dupes (all three in use). When adding, pick from the list above; do not add a new synonym.

## How ranking works (write keywords that RANK)
`SkillMetadata.score(query)` tiers:
1. exact `name` == query → 100
2. exact keyword == query → 90
3. name appears as a **whole token** in query → 80
4. keyword whole-token / (len>=4 substring) → 60
5. query substring of name → 50
6. short query (<20) substring of description → 40
7. token overlap with keywords → 30, with description → 20
- Whole-token guard kills false positives: `yaml` does NOT match `ml`, `legit` does NOT match `git`.
- So: put the exact words users will type into `keywords:` (exact keyword = 90). A word only in prose = weak (20).
- Short names collide: `git` vs `git-snapshot` — exact name wins (100 > 80), which is why exact `name:` matters.

## Importability rule
- If a skill ships `__init__.py` with real functions, users do `from skills.<pkg> import run` (underscore pkg). Verify it imports (`PYTHONPATH=src`).
- If helpers are copy-paste patterns only, say so explicitly in the doc ("NOT importable — inline it"). Do not imply an import that 404s.

## Content style (this repo)
- Self-audience: assume the reader knows Python/git basics. No tutorials, no obvious steps.
- Project-specific footguns and verified `file:line` refs > generic pedagogy.
- Caveman: drop articles/fillers, fragments OK, code unchanged.
- Add a `## Related Skills` section with bidirectional cross-refs.

## Reviewing / maintaining skills — worker methodology
Asked to "fix skill format" or "review the skills"? Do NOT do it inline. You are the manager — run it through ONE **persistent** spawned worker: `spawn()` once, reuse it via `send()` for every task (never spawn fresh per skill).
**Task granularity: ONE skill per task, TWO tasks per skill, in sequence:**
1. **FIX task** — "Bring `<skill>` to the format above: valid frontmatter (`name` hyphenated/unique/lowercase; `description` = capability + "Use for …" + `Keywords:` tail; `category` from the vocabulary; `keywords` present); `## Related Skills` cross-refs. Do NOT change content substance. Verify it loads. Report ONE line."
2. **REVIEW task** — "Review `<skill>` CONTENT: correctness vs source (cite `file:line` for every fix), concise + non-obvious only (cut generic filler any engineer/LLM already knows), no dead cross-refs, consistent with `spawn.md` authority. Edit only real defects — do not churn a good file. Verify loads + pytest. Report ONE line."
Batch sends to protect YOUR context (4-6 skills per send) but keep each skill's fix and review a **discrete, well-defined** task and demand **one line back per skill**. Never paste file contents into your context — the worker's context absorbs them; you get summaries.
**Per-skill verify (worker runs):** loader discovery + `find_skills` + `PYTHONPATH=src python -m pytest -q src/tests/rlm/test_skills_discoverability.py`. **Whole batch:** full `PYTHONPATH=src python -m pytest -q src/tests` (NEVER `--timeout`), loader count / no new shadowing / names unique.
**Surgical git:** stage ONLY skill files you edited; NEVER `skills.py` or parallel files; never weaken a test assertion (update an expected `name:` only if you legitimately renamed).
**Definition of a correct skill:** passes the Authoring checklist above AND every concrete claim verified against source at `file:line`.


## Authoring checklist
- [ ] `name:` hyphenated, unique, matches how you'd search for it
- [ ] `description:` capability + "Use for ..." + Keywords tail
- [ ] `category:` from the vocabulary above
- [ ] `keywords:` = the exact terms users type
- [ ] content is non-obvious, project-specific, concise
- [ ] `## Related Skills` cross-refs present
- [ ] importable helpers actually import, or marked copy-paste

## Verify a new/edited skill (run these)
```python
import sys; sys.path.insert(0, "src")
from rlm.skills import SkillLoader
l = SkillLoader("src/skills"); l.discover_skills()
print("skill-authoring" in l._cache)          # discovered?
print([m["name"] for m in l.find_skills("create skill")[:3]])  # findable?
# load_skill("skill-authoring") works via the kernel namespace
```
Then: `PYTHONPATH=src python -m pytest -q src/tests/rlm/test_skills_discoverability.py`
Live: `./tau.py /skills skill` should list it.

## Maintenance loop
Periodic drift-detection prompt: `prompts/skillmaintenance.md`
(audits `~/.local/taurlm/log/*.audit` for tool-call gaps + skill-load frequency, then
audits frontmatter/findability and creates skills for repeated uncovered patterns).
Keep it pointing at the real LOG_DIR (`~/.local/taurlm/log`, files `<pid>_<ts>_<turn>.audit`).

## Related Skills
- `sdd` — spec-driven dev (NOT skill authoring; different artifact).
- `debug` — error/traceback work.
- `code-review` — reviewing code changes.
- `test-runner` — running this repo's pytest.
