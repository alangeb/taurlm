---
name: git
description: 'Git workflow: commit conventions, branch strategy, rebase vs merge, conflict resolution, safe operations. Use for committing, branching, rebasing, merging, resolving conflicts, inspecting history. Keywords: git, commit, branch, rebase, merge, conflict, history, diff, stash.'
category: operations
keywords: 'git, commit, branch, rebase, merge, conflict, history, diff, stash'
---

# Git Workflow

## Subprocess wrapper (always DEVNULL + timeout)

    import subprocess
    def git(*args, check=True):
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
        return r.stdout.strip()

    # Inspect before acting
    git("status", "--short")
    git("diff", "--stat")
    git("log", "--oneline", "-5")
    git("ls-files", "--others", "--exclude-standard")  # untracked — check before destructive ops

## Commit convention (match repo history)
Conventional-commit style, verified in `git log`: `type(scope): imperative summary` with types `feat|fix|refactor|review|audit|docs` (e.g. `fix(skills): ...`, `review(skills): ...`). No commit hooks / pre-commit / commitlint are configured here (no `.git/hooks/*` non-sample, no `.pre-commit-config.yaml`), so the convention is by-convention only — match the existing prefixes rather than inventing new ones.

## NEVER
- `git push --force` on main or shared branches
- `git reset --hard` without `git stash` or branch backup
- Any destructive op (reset/restore/checkout/clean/stash-drop) without a prior `git status --short`
- `git clean`, or reclaim disk space via git, OUTSIDE the working dir
- Commit secrets, .env, or generated files

## Related Skills
- **git-snapshot** — one-call status+diff fold for quick change inspection
- **code-review** — review diffs before committing
- **debug** — use `git bisect` to find regression commits
- **refactor** — commit before refactoring, verify after
