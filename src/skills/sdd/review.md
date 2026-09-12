# Phase: REVIEW — audit a spec for clarity, completeness, consistency

You are the manager. You delegate the review to a fresh-context spawn, then apply fixes.
This reviews the SPEC, not the code. You do not add requirements — new requirements go
back to `author.md`.

## Workflow

1. **Target.** Topic (spec-id) or `all`. Empty -> ask which specs. For `all`, process one
   at a time, skipping missing files. For a named spec that does not exist, report
   "Spec not found: <file>. Available specs: <list>" and stop.
2. **Spawn a spec-reviewer** (fresh context, no inherited history). Prompt it with the
   FORMAT CONTRACT from SKILL.md plus:

   > Read `specs/<file>.md`, `specs/INDEX.md`, `specs/tests/INDEX.md`, and every spec in
   > its `depends:` list. List the `.md` files in `specs/` and `specs/tests/` (excluding
   > `INDEX.md` and `check-report-*.md`) and verify the INDEX rows match the actual files.
   > Review for:
   > - **Clarity** — could a new developer apply each REQ without asking questions?
   > - **Implementability** — exact signatures, exact error behavior, exact edge cases?
   > - **Completeness** — edge cases, error paths, constraints covered?
   > - **Consistency** — do specs contradict each other? Do interlinks resolve?
   > - **Format** — frontmatter complete, body matches the weight?
   > - **Index** — does INDEX.md match what is on disk?
   >
   > Report each finding as: `[SEVERITY] <file>:<section> -- <issue> -- <suggested fix>`

   Feed it the spec files, never the source code.
3. **Close** the reviewer handle once you have the findings.
4. **Fix** each finding, or ask the user where it is a judgment call (notably any
   contradiction between two specs — you do not resolve that alone).
5. Show the user the diff if the changes are significant.
6. Regenerate INDEX.md if the file set changed.
7. **Commit** only if something changed: `spec-review: <topic> -- fixed N issues`. With no
   findings, skip the commit and report "No issues found."
