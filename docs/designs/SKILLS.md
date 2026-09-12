# Skills — Implementation Guide

## Skill Contract

Create `skills/my_skill/SKILL.md`:

```markdown
# My Skill

What this skill does.

keywords: keyword1, keyword2, keyword3

## Functions

- `func1(args)` — description
```

Skills are **directories** containing a single `SKILL.md` file. The directory name matches the skill name.

## Key Rules

1. Skills are loaded via `load_skill("name")` in the REPL — injects skill content as instructions.
2. Skills provide **domain-specific instructions** — they don't add new capabilities, just knowledge.
3. `available_skills()` lists all discoverable skills.
4. `find_skills("query")` returns ranked matches without loading full content.
5. Skills are auto-discovered from the `skills/` directory, including both directory skills (`<name>/SKILL.md`) and flat skills (`<name>.md`).

## Skill Implementation Rules

| Rule | Details |
|------|---------|
| Format | Directory skill: `skills/<name>/SKILL.md`; flat skill: `skills/<name>.md` |
| Frontmatter | YAML frontmatter is supported (`name`, `description`, `category`, `keywords`). Plain markdown with `# Title`, description, and `keywords:` lines is still accepted as legacy. |
| Execution | `load_skill("name")` injects content into REPL context |
| Access | Full Python access (same as any REPL code) |
| Discovery | `available_skills()` scans `skills/` for directory and flat skills |
| Search | `find_skills(query)` ranks exact names, keywords, name substrings, then description matches |
| Collision rule | Directory skills win over flat `.md` files with the same stem |
| Current skills | Discoverable set is defined by `src/skills/`; use `/skills` or `available_skills()` for the live list |

## Nudge Pruning (telemetry-driven)

Skill nudges are pruned by usage telemetry: a skill that has been **nudged >=5x but
never loaded** drops out of the nudge set. This stops surfacing skills the model keeps
ignoring, reducing context noise while keeping actively-loaded skills visible.
