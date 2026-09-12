# Phase: TESTSPEC — derive test specs from a code spec

You write the test SPEC, not the test code. Test code is `test.md`.

## Workflow

1. **Target.** Topic or `all`. Empty -> ask. For each target: if `specs/<file>.md` is
   missing, report "Spec not found: <file>. Available specs: <list>" and continue to the
   next. If its status is draft, deprecated or archived, report "Spec <id> is <status>.
   Tests not derived." and continue. Extract the REQs, edge cases and error paths.
2. **Spawn a test-designer** (fresh context). Prompt it with the FORMAT CONTRACT from
   SKILL.md plus:

   > Read `specs/<file>.md` and derive test cases from the spec ONLY — do not read the
   > source code. Rules:
   > - every REQ gets at least one TC
   > - every edge case in the spec gets its own TC
   > - every error path gets a TC
   > - where several REQs interact, add an E2E scenario
   > - follow the project's test file naming conventions for the `Test file:` field
   > - format: Requirement / Given / When / Then / Test file
   >
   > Return the completed test spec.

3. **Review** its output for coverage gaps. Fill gaps yourself or send a follow-up to the
   same handle.
4. **Existing test spec?** Read it, show the current state, discuss. Do not overwrite.
   Merge: preserve every existing TC and its ID, append new TCs at the next free numbers.
   If the designer returned a duplicate ID, skip it and report the conflict in your
   summary. Write the result to `specs/tests/<spec-id>.md`, then **close the handle**.
5. **Show** the user the test spec (or a summary: N TCs, M E2E) and get approval.
6. Regenerate `specs/tests/INDEX.md`. Add the new path to the parent spec's `tests:`
   field, bump the parent's `updated:`, regenerate `specs/INDEX.md`.
7. **Commit**: `testspec: <topic> -- N TCs derived`.

## Rules

- The designer sees the SPEC, never the code. Tests verify the spec, not the implementation.
- If the spec is too ambiguous to derive a test, flag it and send the user back to
  `author.md`. Do not invent behavior to make a test possible.
