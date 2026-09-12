# Rollback Procedure for RLM Transformation

## Emergency Rollback (Full Reset)

**There is no pre-RLM rollback target.** The `rlm-transformation` branch is the only branch in this repository. There is nothing to roll back to.

If you need to undo specific changes, use `git log` and `git checkout <commit> -- <file>` to revert individual files.

## Phase-Level Rollback

> **NOTE:** No `phase-N-start` tags exist. Use `git log --oneline` to find commits.

To rollback to the start of a phase:

```bash
# Find the commit that started Phase N
git log --oneline | grep "Phase N"
# Checkout files from that commit
git checkout <commit-hash> -- .
```

## Sub-Phase Rollback

```bash
# Find the commit before the sub-phase
git log --oneline | grep "Phase N.M"
# Reset (destructive) or checkout specific files (safe)
git reset --hard <commit-hash>
git checkout <commit-hash> -- src/
```

## Data Migration Rollback

```bash
# Find the commit before the migration
git log --oneline -- src/tau.json | head -5
# Restore from a specific commit
git checkout <commit-hash> -- src/tau.json
```

## Safety Checklist

Before any rollback:
1. `git stash` any uncommitted changes
2. `git log --oneline -20` to note current position
3. `git diff --stat` to see what would change
4. After rollback: `git status` to verify
