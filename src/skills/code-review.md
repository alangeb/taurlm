---
name: code-review
description: 'Code review workflow: structural analysis, correctness, style, security. Use for reviewing diffs, PRs, or files. Keywords: code, review, PR, diff, correctness, security, style, lint, audit, quality, refactor opportunity.'
category: development
keywords: 'code, review, PR, diff, correctness, security, style, lint, pylint, ruff, audit, quality, refactor opportunity'
---

# code-review

## When to Use

- Reviewing a diff or PR before merge
- Auditing a file or module for quality issues
- Checking security implications of a change
- Validating that a refactor preserved behavior

## Workflow
1. **Scope the diff first** — `git diff --stat`, review changed hunks before whole-tree scans.
2. **Structural** — size/coupling/dead code via `rlm-analysis` (AST, never grep for structure).
3. **Correctness** — logic, edge cases, error handling; check impact: `g.impact("Symbol")` = what breaks.
4. **Security** — injection, path traversal, secret exposure, unsafe deserialization.
5. **Style** — pylint/ruff (both installed here).
6. **Report** — severity-ranked findings with file:line references.
7. **Delegate big diffs** — a worker reviews the full diff; you get ranked findings, not 5k lines of context (see **spawn**).

## Examples

```python
# Quick structural scan before deep review
from rlm.pyscan_core import scan_project
print(scan_project("src/", compact=True))

# Check for callers that might break (node ids are "file.py:Name" —
# a bare name silently returns []; resolve it first with node_by_name)
from rlm.pygraph_core import build_graph
g = build_graph("src/")
nodes = g.node_by_name("function_under_review")  # [] if symbol absent -> [0] IndexError
print(g.callers(nodes[0].id) if nodes else 'symbol not found')

# Lint (pylint + ruff both installed)
import subprocess
r = subprocess.run(["python", "-m", "pylint", "src/module.py", "--score=n"],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
print(r.stdout[-2000:])
```

## Related Skills

- `code-analysis` — quick per-file structure (count_loc, find_functions)
- `rlm-analysis` — AST-based call graphs, impact analysis, dead code
- `refactor` — if review finds structural issues, plan the refactor
- `testing` — verify behavior is pinned before approving
- `spawn` — delegate whole-diff reviews to keep your context lean
- `verification-discipline` — independent re-run; prove "pre-existing" by reverting at HEAD
