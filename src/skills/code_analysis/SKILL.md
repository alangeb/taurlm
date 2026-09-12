---
name: code-analysis
description: 'Analyze Python code files: count lines, find functions/classes/imports, scan directories. Use when inspecting code structure. Keywords: code, analysis, analyze, functions, classes, imports, lines, loc, scan, count.'
category: analysis
keywords: 'code, analysis, analyze, functions, classes, imports, lines, loc, scan, count'
---

# code-analysis

Analyze Python code files — count lines, find functions/classes, scan directories. For structural questions (callers/impact) use `rlm-analysis` instead.



> **Import footgun:** loading this skill only PRINTS the doc (rlm/skills.py:_load_skill_into_namespace_inner) — nothing is auto-imported. Call the functions only after `from skills.<pkg> import ...` (verified: `PYTHONPATH=src`).

> **Path footgun:** `file_path`/`dir_path` are CWD-relative, NOT `src/`-relative. `file_path="agent_core.py"` returns `{'success': False, 'error': '... No such file ...'}` from the repo root (the file is `src/agent_core.py`). Pass `src/...` paths or `cd src` first. Failures come back as `{'success': False, 'error': ...}` dicts, NOT exceptions — check the dict.

## Usage

Exported callable is `run`; alias it:

```python
from skills.code_analysis import run as code_analysis

# Count lines in a file
code_analysis(action="count_loc", file_path="src/agent_core.py")

# Find functions in a file
code_analysis(action="find_functions", file_path="src/agent_core.py")

# Find classes / imports
code_analysis(action="find_classes", file_path="src/agent_core.py")
code_analysis(action="find_imports", file_path="src/agent_core.py")

# Analyze a file comprehensively
code_analysis(action="analyze", file_path="src/agent_core.py")

# Scan a directory
code_analysis(action="scan_dir", dir_path="src/")
```

## Related Skills
- `rlm-analysis` — callers/impact/dead code (AST graph, not this skill)
- `code-search` — TEXT grep across files
- `refactor` — impact mapping before extraction
