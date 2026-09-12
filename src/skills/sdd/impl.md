# Phase: IMPL — implement code from a spec

The spec is the ONLY source of truth. You are the manager; implementation goes to a
worker. Never implement beyond the spec — if something "should" be there but is not, flag
it rather than adding it.

## Workflow

1. **Target.** Topic, optionally with a REQ filter ("only REQ-PL-003 and REQ-PL-004").
   Empty -> ask which spec.
2. **Gate.** If `specs/<spec-id>.md` is missing, report "Spec not found: <spec-id>.
   Available specs: <list>" and stop. If its status is draft, deprecated or archived,
   report "Spec <id> is <status>. Not implementing." and stop.
3. **Find unimplemented REQs.** For each active, non-struck REQ, grep the spec's `source:`
   files for `# Spec: <spec-id>#<REQ-ID>`. No hit = unimplemented. A listed source file
   that does not exist means all of its REQs are unimplemented.
   If nothing is unimplemented: report "All REQs already implemented.", run the project's
   test command once to confirm, and stop.
4. **Units.** Group the unimplemented REQs into units — one REQ, or a small set of tightly
   related REQs. Use ONE worker for all units, sequentially. Worker, tester and reviewer
   are persistent: spawn each on first use, reuse with `send()` thereafter.
5. **Per unit:**
   a. **Worker** (fresh context): "Implement REQ-XX-NNN per `specs/<spec-id>.md`. Target
      source file(s): the relevant entries from the spec's `source:` list; create the file
      if it does not exist, following the project's existing conventions. Read the spec
      first — it is your only source of truth. Read the project's code-conventions doc for
      style. If the spec is ambiguous, STOP and report the ambiguity; do not guess."
      Paste the FORMAT CONTRACT from SKILL.md into the prompt.
   b. **Tester** (fresh context): syntax/type check the files the worker reported changing,
      then run any existing tests for that module.
   c. **Reviewer** (fresh context): "Review the changed files against `specs/<spec-id>.md`.
      Does the implementation match the spec exactly? Any gaps? Any over-implementation?"
      Findings only — no edits.
   d. **Ambiguity reported?** STOP. Present it to the user. On clarification, update the
      spec, commit `spec: <topic> -- clarified REQ-XX-NNN`, and resume the worker. On
      cancel, close every handle and report "Implementation paused. Re-run to resume."
   e. **Corrections.** Send the reviewer's findings to the worker; re-run tester and
      reviewer. Tester failures count toward the same limit. **Maximum 3 cycles.** If issues
      persist, report them, stop the entire run (do not start later units), close all
      handles, and revert this unit's uncommitted changes (`git checkout -- <files>`) so the
      REQs read as unimplemented on the next run. Git is the rollback mechanism; the spec's
      status is left alone.
   f. **Commit**: `impl: <topic> -- REQ-XX-NNN`.
6. **After all units**, send the persistent tester: run the project's full test command.
   Report pass / fail / skip counts.
7. **Final commit.** If nothing failed and `git status --porcelain` shows uncommitted
   source changes, commit `impl: <topic> -- complete`; otherwise skip. If anything failed,
   do NOT commit — report the failures and let the user decide.
8. **Close** worker, tester and reviewer handles.

## Rules

- Ambiguity is a SPEC BUG. Fix the spec through the user; never work around it.
- Do not modify the spec during implementation, except the clarification loop in 5d.
- Read files before modifying them. Private symbols need no refs.
