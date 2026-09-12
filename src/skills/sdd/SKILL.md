---
name: sdd
description: 'Spec-Driven Development: author specs, derive test specs, implement from spec, write tests, verify sync. Use when authoring or syncing specs/test specs in any language. Keywords: spec, sdd, spec-driven, requirements, REQ, test spec, testspec, TC, spec review, spec impl, implement spec, spec check, coverage, traceability.'
category: development
keywords: 'spec, sdd, spec-driven, requirements, REQ, test spec, testspec, TC, spec review, spec impl, implement spec, spec check, spec status, coverage, traceability, spec archive, spec deprecate, draft spec, verify spec, orphan, # Spec:, # TestSpec:'
---

# Spec-Driven Development

One playbook, six phases. The spec is the ONLY source of truth. Code and tests are
derived artifacts. Git history is the development record.

You are the manager: you plan, decide, quality-check and talk to the user. Heavy lifting
— reading large files, implementing, independent review, running suites — goes to spawns.

## Phase router

The user's words win. If they name a phase, run that phase. Otherwise infer it from repo
state for the topic in question:

| State | Phase | Read |
|---|---|---|
| `specs/` missing or empty | author (bootstrap) | `author.md` |
| `specs/<id>.md` missing | author (new) | `author.md` |
| spec exists, `status: draft` | author (revise) | `author.md` |
| `status: active`, no test spec yet | testspec | `testspec.md` |
| active REQs with no `# Spec:` hit in `source:` files | impl | `impl.md` |
| active TCs with no `# TestSpec:` hit in test files | test | `test.md` |
| everything in sync, or "where are we?" | verify | `verify.md` |
| "deprecate this" / "archive this" | archive (below) | — |

Read exactly ONE phase file — the one that matches. Never read them all.

## Project profile — discover once, then stop asking

Nothing here is assumed. Establish it from the repo (grep manifests, CI config, README,
Makefile), confirm in one line, and reuse it for the rest of the session. Ask only if
discovery genuinely fails.

- **source root** and **test root**
- **test command** (how this project runs its suite) and **syntax/type check command**
- **code-conventions doc** — whatever it is called; if none exists, ask once where style
  rules live and record the answer
- **comment style** for traceability refs (docstring, line comment, block comment)
- **privacy convention** (what marks a symbol non-public) — those symbols are ref-exempt
- **test-framework exemptions** — the framework's fixtures, helpers and shared-fixture files are ref-exempt

## Layout

    specs/
      INDEX.md            <- auto-generated
      <spec-id>.md
      check-report-*.md   <- only if the user asks verify to save a report
      tests/
        INDEX.md          <- auto-generated
        <spec-id>.md      <- test spec, mirrors the code spec id

## IDs — permanent, never renumbered

- **spec-id**: kebab-case. Default: derived from the source path (`src/order_service.py` ->
  `order-service`); a project may override. A feature spanning several files gets the feature
  name, not a file name. Must equal the filename minus `.md`.
- **REQ**: `REQ-<ABBR>-<NNN>` — ABBR is 2-4 letters of the spec id, NNN zero-padded from
  001. One REQ = one atomic behavior a developer could implement without asking questions.
  Removal = strikethrough plus dated note, e.g. `~~REQ-AL-003~~ (deprecated 2026-09-12,
  replaced by REQ-AL-007)`. Never reuse a number.
- **TC**: `TC-<ABBR>-<NNN>` for unit and integration (shared namespace, separated only by
  the section heading), `TC-<ABBR>-E2E-<NNN>` for end-to-end.

## Code spec frontmatter

    ---
    id: <spec-id>
    version: 1.0            # semver major.minor; bump on breaking spec change
    status: draft|active|deprecated|archived
    weight: light|full
    source: [ <files> ]     # required, may be empty
    tests: [ <test specs> ] # required, may be empty
    depends: [ <spec ids> ] # required, may be empty
    created: YYYY-MM-DD
    updated: YYYY-MM-DD     # bump on EVERY edit
    ---

## Test spec frontmatter

    ---
    id: tests-<spec-id>
    spec: <spec-id>
    version: 1.0
    ---

## Body shapes

**full** (complex, or the unit is more than ~50 lines): `# Title`, `## Purpose` (1-3
sentences), `## Requirements` (the REQs), `## Interface` (every public signature),
`## Behavior` (normal flow, error cases, edge cases), `## Constraints`, `## References`.

**light** (unit expected under ~50 lines): `# Title`, `## Purpose` (1 sentence),
`## Interface`, `## Requirements`. Behavior and constraints live inside the REQs.

**test spec**: `## Unit Tests`, `## Integration Tests`, `## E2E Scenarios`, each case:

    ### TC-XX-NNN: <name>
    - **Requirement:** REQ-XX-NNN
    - **Given:** <precondition>
    - **When:** <action>
    - **Then:** <expected outcome>
    - **Test file:** <path>::<test-name>

Every TC references at least one REQ.

## Traceability strings — exact, case-sensitive

Code symbol, inside its docstring/doc-comment, one REQ per line:

    # Spec: <spec-id>#<REQ-ID>

Test function, inside its docstring/doc-comment:

    # TestSpec: <spec-id>#<TC-ID>

No path prefixes. Every public symbol needs at least one code ref; every test function
needs at least one test ref. Private symbols and framework fixtures are exempt.

## Spec units

One spec per source file by default; one unified spec for a feature spanning several
files. No spec for package-init files, pure re-export modules, test helpers or fixtures.

## INDEX.md — auto-generated, never hand-edited

    | Spec | Status | Weight | Source Files | Test Spec | Updated |
    |------|--------|--------|--------------|-----------|---------|

Built from the frontmatter of every `specs/*.md` (skipping `INDEX.md` and
`check-report-*.md`). Regenerate after any add, remove or rename.

## Git

Format `<phase>: <topic> -- <detail>`, phases: `spec`, `spec-review`, `testspec`, `impl`,
`test`, `check`, `fix` (`fix:` is for manual user-initiated work outside the lifecycle).
Commit per logical unit, never with failing tests. The log IS the development history.

## Universal rules

- Close every spawn before you finish; reuse persistent handles within a phase.
- Skip deprecated (struck-through) REQs everywhere.
- Ambiguity is a SPEC BUG: stop and ask. Never guess, never fill in the blank.
- Never implement beyond the spec — flag what "should" be there instead.
- Read files before modifying them.
- Each phase writes only its own artifact: author -> `specs/`, testspec -> `specs/tests/`,
  impl -> source, test -> tests. `verify` writes nothing. Cross-reference edits (refs,
  parent frontmatter) are allowed where a phase names them.

## FORMAT CONTRACT — paste verbatim into every spawn prompt

A fresh spawn sees none of this file. Copy the block below into its instructions:

    IDs are permanent: REQ-<ABBR>-<NNN> (never renumber or reuse; removal = strikethrough
    plus dated note). Test cases: TC-<ABBR>-<NNN>, end-to-end TC-<ABBR>-E2E-<NNN>.
    Code traceability, exact and case-sensitive, one per line, in the docstring:
      # Spec: <spec-id>#<REQ-ID>
    Test traceability, exact and case-sensitive, in the test docstring:
      # TestSpec: <spec-id>#<TC-ID>
    Every public function/class needs at least one code ref; private symbols are exempt.
    Every test function needs at least one test ref; fixtures and helpers are exempt.
    Test case format: Requirement / Given / When / Then / Test file.
    The spec is the ONLY source of truth. If it is ambiguous, STOP and report the
    ambiguity rather than guessing. Do not invent requirements.

## Archive / deprecate

- **Deprecate**: set `status: deprecated`, keep the file, strike through removed REQs with
  a dated note, regenerate INDEX, commit `spec: <id> -- deprecated`.
- **Archive**: set `status: archived`, same handling. The INDEX row stays.
- Never delete a spec file to tidy up — history is the record. Deleting files is the
  user's call, not yours.

## Phase files

They live beside this file, in the skill directory reported by `load_skill` — read exactly
one, by path, not by name:

    from pathlib import Path
    skill_dir = Path(next(Path(".").rglob("skills/sdd/SKILL.md")).parent)
    print((skill_dir / "impl.md").read_text())      # or author / review / testspec / test / verify

`author.md` — write or revise a code spec
`review.md` — independent audit of a spec's clarity and completeness
`testspec.md` — derive test specs from a code spec
`impl.md` — implement REQs in source
`test.md` — write missing tests from the test spec
`verify.md` — read-only sync check and recommendation

## Related Skills
- `skill-authoring` — authoring skills, not specs (different artifact).
- `test-runner` — running the pytest suite the test specs map to.
- `code-review` — reviewing implemented code after spec-impl.
