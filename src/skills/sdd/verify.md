# Phase: VERIFY — read-only sync check

You are the auditor. You REPORT; you do not fix. The user decides what to fix and which
phase to run. **No spawns, no writes, no commits** — do this yourself with file reads and
grep.

1. **Scope.** A spec-id, or `all` (default). If `specs/` holds no spec files (ignoring
   `INDEX.md` and `check-report-*.md`), report "No specs found. Run the author phase first."
   and stop.
2. **Spec -> code.** For each ACTIVE spec, count its active (non-struck) REQs, then grep
   the spec's `source:` files for `# Spec: <spec-id>#<REQ-ID>`. Report X/Y and list the
   MISSING ones. For draft/deprecated/archived specs, list unimplemented REQs as
   UNIMPLEMENTED (expected), not MISSING.
3. **Code -> spec.** For each public symbol in those `source:` files, check its docstring
   for a `# Spec:` line. None = ORPHAN CODE. Where a ref exists, confirm the spec file and
   REQ id are real.
4. **Testspec -> tests.** For each TC in each test spec, grep its `Test file:` for
   `# TestSpec: <spec-id>#<TC-ID>`. No hit = MISSING TEST.
5. **Tests -> testspec.** For each test function in the test root, check for a
   `# TestSpec:` line. None = ORPHAN TEST (fixtures and helpers exempt). Where a ref
   exists, confirm the TC exists and its `Requirement:` names a real REQ; otherwise it is
   an INVALID TC.
6. **Cross-links.** Do every `source:` file, `depends:` spec and `tests:` file exist? Does
   every test spec's `spec:` point at a real spec? Any A->B and B->A dependency cycle?
7. **Staleness.** For each spec, compare its `updated:` date with the most recent
   `test: <spec-id>` commit (`git log --grep="^test: <spec-id>" -1 --format=%ai`). Newer
   than its last test commit, or no test commit at all, = stale.
8. **Report** compactly:

       === SPEC STATUS ===
       Specs: N total (M active, K draft, D deprecated, A archived)
       REQs:  X/Y implemented (Z%)
       TCs:   A/B implemented (C%)
       Orphan code: N     Orphan tests: M
       Broken cross-links: N     Invalid TCs: N     Cycles: N
       Stale specs: N

       <spec-id>: X/Y REQs, A/B TCs, N orphans

   Give the per-spec detail when there are ten or fewer specs, or the user asked for depth.
9. **Recommend.** Close with an ordered list of what to fix and which phase fixes each
   (author / review / testspec / impl / test). Then wait for the user's decision.
10. Only if the user explicitly asks for a saved artifact, write
    `specs/check-report-YYYY-MM-DD.md` and commit `check: <scope> -- N% spec coverage`.

## Rules

- Independent and read-only. Never modify code or specs here.
- Keep it cheap: no delegation, no test-suite run unless the user asks. Independent code
  review is a separate concern, not part of SDD.
