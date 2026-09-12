---
name: code-search
description: 'Cross-file text search, grep, regex patterns, find definitions, search across directories. Use when searching code by text/pattern. Keywords: grep, search, find, pattern, regex, text, define, usage, occurrences, file, directory, glob, walk'
category: analysis
keywords: 'grep, search, find, pattern, regex, text, define, usage, occurrences, file, directory, glob, walk'
---

# code-search

## When to Use
- Finding all files containing a string or pattern
- Locating function/class definitions across a codebase
- Text search (NOT structural analysis — use `rlm-analysis` for that)
- Counting occurrences of a pattern in files

## What to Do
**Boundary: grep is for TEXT only.** Structural questions (callers/impact/dead code/counts of defs) -> `rlm-analysis` (AST). Always `--include=*.py`, truncate output (first N), exclude `__pycache__`/`.git`/`node_modules`, `stdin=DEVNULL` + `timeout=30`.

## Code Examples
```python
import subprocess

def grep(pattern: str, path=".", include="*.py", context=0, max_results=20):
    """Search files for a text pattern."""
    cmd = ["grep", "-rn"]
    if context: cmd += ["-C", str(context)]
    cmd += ["--include=" + include, pattern, path]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    lines = r.stdout.strip().split("\n")
    return lines[:max_results]

def find_defs(name: str, path=".", include="*.py"):
    """Find function/class definitions."""
    r = subprocess.run(["grep", "-rn", f"def {name}\\b\\|class {name}\\b",
                        "--include=" + include, path],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    return r.stdout.strip().split("\n") if r.stdout.strip() else []

def count_occurrences(pattern: str, path=".", include="*.py"):
    """Count files containing pattern."""
    r = subprocess.run(["grep", "-rl", "--include=" + include, pattern, path],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    return r.stdout.strip().split("\n") if r.stdout.strip() else []
```

> **Importable:** `from skills.code_search import grep, find_defs, count_occurrences` (same signatures as above — the inline copies are for editing/ad-hoc use). Loading the skill only PRINTS the doc; nothing auto-imports.

## Related Skills
- `rlm-analysis` — structural (AST) questions; grep here is TEXT-only
- `shell` — DEVNULL+timeout subprocess rules used above
- `code-analysis` — quick per-file counts
