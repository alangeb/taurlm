---
name: git-snapshot
description: 'Git state snapshot: combined diff and status for quick change inspection. Use for reviewing uncommitted work, checking what changed, pre-commit review, verifying deletions. Keywords: git snapshot, git diff, git status, uncommitted, working tree, changes, modified, staged, unstaged, pre-commit, what changed.'
category: development
keywords: 'git snapshot, git diff, git status, uncommitted, working tree, changes, modified, staged, unstaged, pre-commit, what changed'
---

# Git Snapshot

## What to Do
Fold everything into ONE snapshot call (below) — status + staged + unstaged, each truncated, so raw diffs never hit your context. Flag suspicious items: unexpected files, huge diffs, staged files you didn't touch (parallel work).

## Code Example

```python
import subprocess

def git_snapshot():
    status = subprocess.run(
        ['git', 'status', '--short'],
        capture_output=True, text=True, stdin=subprocess.DEVNULL
    ).stdout.strip()
    diff = subprocess.run(
        ['git', 'diff'],
        capture_output=True, text=True, stdin=subprocess.DEVNULL
    ).stdout
    staged = subprocess.run(
        ['git', 'diff', '--cached'],
        capture_output=True, text=True, stdin=subprocess.DEVNULL
    ).stdout
    print(f'STATUS: {status or "(clean)"}')
    if diff:
        print(f'\nUNSTAGED DIFF ({len(diff)} chars):')
        print(diff[:3000])
    if staged:
        print(f'\nSTAGED DIFF ({len(staged)} chars):')
        print(staged[:3000])

git_snapshot()
```

## Notes
- `stdin=subprocess.DEVNULL` on every git subprocess (terminal-inheritance deadlock).
- Porcelain `--short`: first column = staged, second = worktree (` M` unstaged mod, `M ` staged mod, `??` untracked) — misreading it causes wrong staging.
- **Untracked files show as `??` but their CONTENTS are NOT in the diff** — `git diff` and `git diff --cached` only cover tracked content. A brand-new untracked file is invisible to this snapshot; `git status` alone hides what's actually in it. To see it: `git diff --no-index /dev/null <file>` or `git add -N <file>` (intent-to-add) then re-diff.

## Related Skills

- `git` — full git workflow (commit, branch, rebase)
- `code-review` — reviewing the actual changes
- `sanity` — verify before committing
