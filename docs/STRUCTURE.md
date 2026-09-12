# Project Structure & Cleanup Principles

> The rules this repo follows to stay clean, and the shape they produce.
> Status-quo only: this describes what the project IS, never its history.

## Principles

1. **Status-quo only, no history.** The repo describes what it is now, never what
   it used to be. No changelogs, run logs, pre/post baselines, removal-impact
   analyses, enhancement-suggestion notes, or per-pass review dumps.

2. **Root is a showcase, not a workbench.** Repo root holds only user-facing
   essentials (README, LICENSE, DISCLAIMER, SECURITY, VERSION, pyproject.toml,
   .gitignore) plus the live root entrypoints (dream.py/.sh, dream_steps.py) and
   the live prompts/ folder. Everything else lives in src/ or docs/.

3. **README is user-facing and fully interlinked.** What the project is, install,
   quickstart, config basics, license/disclaimer/security, plus a Documentation
   section linking every other relevant .md. Developer deep-dives live in
   docs/designs, not README.

4. **src/ = code + live runtime assets only.** Keep what is actually
   loaded/executed: AGENT_RLM.md (system prompt), commands/*.md (auto-globbed
   command defs), skills/** (live skill library), TAU.md (dev index). Anything
   else in src/ that is just documentation belongs in docs/.

5. **docs/ is structured, flattened, indexed, linked.** Subdirs only when they
   add meaning (docs/rlm/ was removed — everything there is RLM, so the wrapper
   was redundant). docs/INDEX.md lists+links everything with one-line
   descriptions. Design records live in docs/designs/.

6. **Runtime state and byproducts are never tracked.** Three cases:
   - Regenerating runtime state (.goal.json, src/messages/, messages/) ->
     `git rm --cached` + .gitignore, KEEP on disk.
   - Genuinely dead artifact nothing regenerates (uv.lock under a
     setuptools/stdlib project, benchmarks.sqlite, .ralph_loop from a removed
     feature, committed test-message JSONs) -> `git rm` (full) + gitignore.
   - Live-but-ignored working dir (tasks/, read by dream_steps.py) -> leave it;
     distinct from dead session scratch (LIST/MEMORY/PROGRESS) which is removed.

7. **Verify before delete — distrust a narrow grep.** Grep for references
   (imports, open(), path-building, tests). Distinguish a dict-key lookup
   (`data.get("messages")`) from a directory path (`messages/`), and comment
   prose ("See designs/DECISIONS.md") from a runtime file read. When a safety
   gate trips, READ the cited lines and tighten the check rather than blindly
   deleting or blindly aborting.

8. **Verify before move.** Confirm nothing reads the path at runtime; move with
   `git mv` (preserve history); then fix EVERY cross-reference (.py comments,
   .md links, INDEX files, README) and re-grep for residual old paths.

9. **An empty "unpushed" list is not proof of a commit.** Verify with
   `git log` + `git status`, not just `origin..HEAD`.

10. **Keep guard tests even when the feature is gone.** e.g.
    test_dead_tests_removed.py asserts a removed test stays removed — a
    regression fence earns its keep.

## Expected shape

ROOT (user-facing + live entrypoints): README.md, LICENSE, DISCLAIMER.md,
SECURITY.md, VERSION, pyproject.toml, .gitignore, dream.py, dream.sh,
dream_steps.py, prompts/.

src/ (code + live runtime assets): *.py, AGENT_RLM.md, TAU.md, commands/*.md,
skills/**.

docs/ (flattened reference + one meaningful subdir, indexed): INDEX.md,
CONVENTIONS.md, config-schema.md, git-strategy.md, loop-design.md,
package-inventory.md, rlm-call-design.md, rollback-procedure.md,
streaming-spec.md, trust-model.md, and designs/ (design records).

specs/ (spec system): README.md, INDEX.md, fence-pipeline-v2.md, tests/INDEX.md.

Correctly ABSENT: no CHANGELOG, run logs, pre/post baselines,
removal-impact analysis, session scratch (LIST/MEMORY/PROGRESS), committed
runtime state, benchmark artifact (src/data), dead-feature runtime
(.ralph_loop), redundant docs/rlm/ wrapper, or root CONVENTIONS.

**Litmus test:** a newcomer reads README, follows interlinks to docs/INDEX then
docs/designs, and finds exactly one home for every fact — no history, no cruft,
no runtime junk, every doc reachable and every link valid.
