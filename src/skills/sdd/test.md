# Phase: TEST — make the tests match the test spec

Tests are derived from the TEST SPEC, never from the implementation. You write missing
tests; you do not fix failing ones.

## Workflow

1. **Target.** Topic or `all`. Empty -> ask which tests to verify.
2. **Per test spec:**
   a. If `specs/tests/<spec-id>.md` is missing, report "Test spec not found. Run the
      testspec phase first." and continue to the next. If the parent spec is missing,
      report "Parent spec <id> not found. Delete the orphaned test spec or re-create the
      parent." and continue. If the parent's status is draft, deprecated or archived,
      report "Parent spec <id> is <status>. Tests skipped." and continue. Otherwise read
      the test spec and extract every TC and its `Test file:` location.
   b. For each TC, grep the named test file for `# TestSpec: <spec-id>#<TC-ID>`. No hit =
      MISSING TEST.
   c. For each missing test, use a **worker** (persistent: spawn on the first, reuse for
      the rest): "Write the test for TC-XX-NNN per `specs/tests/<spec-id>.md`. Test file:
      <path from the TC's `Test file:` field>; create it, with the project's usual package
      structure, if it does not exist. Add `# TestSpec: <spec-id>#<TC-ID>` to the test's
      docstring." Paste the FORMAT CONTRACT from SKILL.md.
3. **Orphans.** Grep the test root for test functions lacking a `# TestSpec:` ref. Fixtures
   and framework helpers are exempt.
4. **Run.** Spawn a fresh tester: run the project's test command; report pass / fail / skip
   counts and failure details with file and line.
5. **Compare** results against the test spec — every TC should have a passing test.
6. **Report**: N passed, M failed, K skipped, L missing tests now written, O orphan tests.
7. **Commit.** If nothing failed and `git status --porcelain` shows uncommitted test
   changes, commit `test: <topic> -- N passed, 0 failed`; otherwise skip. If anything
   failed, do NOT commit — report and let the user decide.
8. **Close** the tester and worker handles.

## Rules

- A failing test is not yours to fix. Report it; the user resolves it through the impl or
  author phase.
- If a TC cannot match the implementation, either the spec or the code is wrong — flag it.
- Do not modify test specs. That is the testspec phase.
