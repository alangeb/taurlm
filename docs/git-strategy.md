# Git Strategy for RLM Transformation

## Branch Strategy

- **`rlm-transformation`** — Main work branch (current)
- ~~**`master`**~~ — ~~Original code, untouched (rollback anchor)~~ (branch does not exist — `rlm-transformation` is the only branch)

## Commit Convention

Every sub-phase ends with ONE atomic commit:

```
Phase N.M: <description> — PASS
```

Or on failure:

```
Phase N.M: <description> — FAIL: <reason>
```

### Examples
```
Phase 0.3: Git strategy docs — PASS
Phase 1.1: REPL kernel namespace design — PASS
Phase 1.2: REPL kernel exec engine — FAIL: timeout handling incomplete
```

## Tag Strategy

> **NOTE:** The tags below are **PLANNED**. As of now, only `phase-0-complete` exists. No other tags have been created.
> **Actual state:** Only `phase-0-complete` tag exists. No `phase-N-start` tags were created.

Tag each phase completion:
```bash
# At phase start
git tag phase-N-start

# At phase completion
git tag phase-N-complete
```

### Tag Examples
```
phase-0-start, phase-0-complete
phase-1-start, phase-1-complete
...
phase-18-start, phase-18-complete
```

## Stash Strategy

For work-in-progress that doesn't pass validation:
```bash
git stash push -m "Phase N.M: WIP — <reason>"
```

To recover:
```bash
git stash list
git stash pop stash@{N}
```

## Rebase Strategy

Keep history clean by rebasing after each completed phase:
```bash
git rebase -i master  # Only if master has changes
```

Do NOT rebase across multiple phases — keep tags stable.

## Branch Naming Convention

- `rlm-transformation` — Main work branch
- `rlm-experiment-N` — Experimental branches for trying approaches
- `rlm-hotfix-N` — Quick fixes during a phase

## File Organization

All RLM-related docs go in `docs/`:
- `git-strategy.md` — This file
- `rollback-procedure.md` — Rollback procedures
- `config-schema.md` — RLM config schema design
- ~~`phase-notes.md`~~ — ~~Per-phase notes and decisions~~ (file does not exist)

## Commit Frequency

- ONE commit per sub-phase (not per file change)
- Use `git add -p` to stage only relevant changes
- Do NOT commit partial implementations — use stash instead

## .gitignore

Already configured in `src/.gitignore`. Key exclusions:
- `__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `*.log`, `*.audit`, `*.context`
- `.tau/`
