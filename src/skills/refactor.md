---
name: refactor
description: 'Refactoring in TauRLM: pin behavior with tests, map impact with AST graphs, extract incrementally, verify with pytest after each step. Use for code extraction, module splitting, renames, API cleanup. Keywords: refactor, extract, rename, split, restructure, move, decompose, impact, callers, dead code, module.'
category: development
keywords: 'refactor, extract, rename, split, restructure, move, decompose, impact, callers, dead code, module'
---

# Refactoring

Workflow: pin behavior (tests pass BEFORE) -> map dependencies (AST, not grep) -> one extraction at a time -> `pytest -x` after EACH step -> fix refs/imports. Non-obvious parts only:

## Impact mapping (rlm-analysis — always AST for structural questions)
```python
from rlm.pygraph_core import build_graph
g = build_graph("src/")
# node ids are "file.py:Name"; a BARE name silently returns []/set() for BOTH
# callers() AND impact() — verified: impact("ContextManager")->set(), impact(full-id)->2.
nid = g.node_by_name("Name")[0].id   # resolve first; [] if symbol absent -> [0] IndexError
g.callers(nid)               # who breaks if I change this
g.impact(nid)                # transitive blast radius
g.god_nodes(5)               # most-connected symbols (refactor candidates)
from rlm.pyanalyze_core import analyze_project   # dead code / unused imports
from rlm.pycheck_core import check_project       # import validation after moves
```

## Verify loop (this repo)
```python
import subprocess
import os
env = dict(os.environ, PYTHONPATH="src")   # belt-and-braces; src/pytest.ini already sets pythonpath=.
r = subprocess.run(["python","-m","pytest","src/tests","-x","-q"],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL,
                   env=env, timeout=300)
assert r.returncode == 0, r.stdout[-500:]
```

- Commit before refactoring, verify-and-commit after each green step (see **git**) — bisectable history is your rollback.
- Renames: `g.callers()` + grep for strings (config/CLI/docs reference symbols as text too).

## Related Skills
- `rlm-analysis` — graph API surface
- `testing` — what "pinned behavior" means (3 gates for kernel/loop changes)
- `code-review` — review the refactor diff
- `git` — commit-per-step discipline
