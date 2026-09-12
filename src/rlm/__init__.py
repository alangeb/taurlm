"""RLM (Recursive Language Model) Module

This module implements the RLM paradigm where the model works inside a
persistent Python REPL (kernel) instead of calling separate tools.

Trust Model
-----------
RLM operates on TRUST — no security sandbox. The model has full Python
access by design. See docs/trust-model.md for rationale.

Core Components
---------------
- kernel: Persistent Python execution state across turns
- answer: answer dict management (content + ready flag)
- spawn: Unified agent spawning via spawn() function
- skills: Python-backed skills loading
- pygraph_core: AST knowledge graph (callers, callees, impact, god_nodes)
- pyscan_core: AST project scanner (full symbol index)
- pycheck_core: Import checker (missing/unused imports)
- pyanalyze_core: Dead code detector (unused functions)
- pygraph_query: Graph query layer (who calls, what breaks, shortest path)
- host_bridge: Host bridge for external capabilities

Key Differences from Tool-Based Agents
--------------------------------------
1. Main model has NO tools — only Python REPL
2. Sub-LLMs spawned via rlm() get full tool access
3. Answer delivered via answer["content"] + answer["ready"] = True
4. Python state persists across turns (variables, imports, functions)

See Also
--------
- RLM blog: https://www.primeintellect.ai/blog/rlm
- prime-agent: https://github.com/PrimeIntellect-ai/prime-agent
- docs/trust-model.md: Trust model rationale
"""

__all__ = []
