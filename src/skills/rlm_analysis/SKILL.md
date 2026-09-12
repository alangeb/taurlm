---
name: rlm-analysis
description: 'AST-based Python code analysis — scan projects, build call graphs, trace callers, impact analysis, dead code detection, import checking. Use when tracing cross-file calls or impact. Keywords: ast, scan, graph, callers, callees, impact, dead code, imports, functions, classes, pygraph, pyscan, pyanalyze, pycheck'
category: analysis
keywords: 'ast, scan, graph, callers, callees, impact, dead code, imports, functions, classes, pygraph, pyscan, pyanalyze, pycheck'
---

# rlm-analysis

## When to Use
- Counting functions/classes/imports (NOT grep — use AST)
- Tracing who calls a function (callers/callees)
- Impact analysis: what breaks if I change X?
- Finding dead code or unused imports
- God nodes: most-connected symbols

## What to Do
1. ALWAYS use these tools for structural questions, never grep
2. `scan_project` — quick overview of a directory
3. `build_graph` — cross-file call relationships
4. `analyze_project` — dead code, unused imports
5. `check_project` — import validation
6. `query_graph` — specific graph queries (callers, callees)

## Code Examples
```python
from rlm.pyscan_core import scan_project
from rlm.pygraph_core import build_graph
from rlm.pyanalyze_core import analyze_project
from rlm.pycheck_core import check_project
from rlm.pygraph_query import query_graph

# Project overview
print(scan_project("src/", compact=True))

# Who calls this function? node ids are "file.py:Name" — a BARE name silently
# returns []/set() for BOTH callers() and impact(). Resolve first via node_by_name
# (returns [] if absent -> guard before [0]).
g = build_graph("src/")
nodes = g.node_by_name("process_request")
print(g.callers(nodes[0].id) if nodes else "not found")

# What breaks if I change this class?
nodes = g.node_by_name("DatabaseManager")
print(g.impact(nodes[0].id) if nodes else "not found")

# Most-connected symbols
print(g.god_nodes(5))

# Dead code and unused imports
print(analyze_project("src/"))

# Import check
print(check_project("src/"))

# Specific query
print(query_graph("src/", "callers", "initialize"))
```

## Related Skills
- `code-search` — TEXT grep only; use this for structure
- `code-analysis` — quick per-file counts
- `refactor` — impact mapping before extraction
