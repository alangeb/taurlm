"""RLM Python knowledge graph queries — cross-file call analysis.

Adapted from tau/src/tools/pygraph.py. Query layer on pygraph_core.
Answers: who calls this? what breaks? shortest path between symbols?
"""
from __future__ import annotations

from rlm.pygraph_core import Graph







def _summary(g: Graph) -> str:
    lines = [
        "## Knowledge Graph Summary",
        f"- **Nodes:** {len(g.nodes)}",
        f"- **Edges:** {len(g.edges)}",
        f"- **Files:** {len({n.file for n in g.nodes})}",
        "",
    ]
    from collections import Counter
    kinds = Counter(n.kind for n in g.nodes)
    lines.append("### Node Types")
    for kind, count in kinds.most_common():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    edge_kinds = Counter(e.kind for e in g.edges)
    lines.append("### Edge Types")
    for kind, count in edge_kinds.most_common():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    god = g.god_nodes(5)
    if god:
        lines.append("### Most Connected (by fan-in)")
        for node_id, count in god:
            node = g.node_by_id(node_id)
            name = node.name if node else node_id
            lines.append(f"- {count} callers ← `{name}` ({node.file if node else '?'})")
        lines.append("")
    return "\n".join(lines)


def _callers(g: Graph, symbol: str, top: int = 20) -> str:
    if not symbol:
        return "Usage: callers(symbol='name')"
    matches = _resolve(g, symbol)
    if not matches:
        return f"No nodes matching '{symbol}'"
    results = []
    for node in matches:
        callers = g.callers(node.id)
        if callers:
            results.append(f"📞 {node.name} ({node.file}:{node.line}) is called by:")
            for c in callers[:top]:
                results.append(f"   ← {c}")
            if len(callers) > top:
                results.append(f"   ... and {len(callers) - top} more")
        else:
            results.append(f"📞 {node.name} ({node.file}:{node.line}) — no callers found")
    return "\n".join(results)


def _callees(g: Graph, symbol: str, top: int = 20) -> str:
    if not symbol:
        return "Usage: callees(symbol='name')"
    matches = _resolve(g, symbol)
    if not matches:
        return f"No nodes matching '{symbol}'"
    results = []
    for node in matches:
        callees = g.callees(node.id)
        if callees:
            results.append(f"→ {node.name} ({node.file}:{node.line}) calls:")
            for c in callees[:top]:
                results.append(f"   → {c}")
            if len(callees) > top:
                results.append(f"   ... and {len(callees) - top} more")
        else:
            results.append(f"→ {node.name} ({node.file}:{node.line}) — no callees found")
    return "\n".join(results)


def _path(g: Graph, from_symbol: str, to_symbol: str) -> str:
    if not from_symbol or not to_symbol:
        return "Usage: path(from='A', to='B')"
    from_matches = _resolve(g, from_symbol)
    to_matches = _resolve(g, to_symbol)
    if not from_matches:
        return f"No nodes matching '{from_symbol}'"
    if not to_matches:
        return f"No nodes matching '{to_symbol}'"
    # Try all combinations, return first path found
    for fn in from_matches:
        for tn in to_matches:
            p = g.shortest_path(fn.id, tn.id)
            if p:
                return f"Path from '{fn.name}' to '{tn.name}' ({len(p)} hops):\n" + "\n→ ".join(p)
    return f"No path found between '{from_symbol}' and '{to_symbol}'"


def _impact(g: Graph, symbol: str) -> str:
    if not symbol:
        return "Usage: impact(symbol='name')"
    matches = _resolve(g, symbol)
    if not matches:
        return f"No nodes matching '{symbol}'"
    results = []
    for node in matches:
        impacted = g.impact(node.id)
        results.append(f"💥 Changing '{node.name}' would affect {len(impacted)} node(s):")
        for i in sorted(impacted)[:30]:
            results.append(f"   ⚠ {i}")
        if len(impacted) > 30:
            results.append(f"   ... and {len(impacted) - 30} more")
    return "\n".join(results)


def _god(g: Graph, top: int) -> str:
    nodes = g.god_nodes(top)
    if not nodes:
        return "No call edges found"
    results = [f"🔥 Top {top} most-called nodes (by fan-in):"]
    for node_id, count in nodes:
        node = g.node_by_id(node_id)
        name = node.name if node else node_id
        results.append(f"   {count} callers ← {name}")
    return "\n".join(results)


def _resolve(g: Graph, symbol: str) -> list:
    """Resolve symbol name, supporting 'exact_' prefix for exact match."""
    if symbol.startswith("exact_"):
        target = symbol[6:]
        return [n for n in g.nodes if n.name == target]
    return g.node_by_name(symbol)


def query_graph(path: str, action: str = "summary", symbol: str = "", top: int = 10, to_symbol: str = "") -> str:
    """Query the Python knowledge graph.
    
    Actions: summary, callers, callees, path, impact, god, resolve
    """
    from rlm.pygraph_core import build_graph
    g = build_graph(path)
    if action == "summary":
        return _summary(g)
    elif action == "callers":
        return _callers(g, symbol, top)
    elif action == "callees":
        return _callees(g, symbol, top)
    elif action == "path":
        return _path(g, symbol, to_symbol)
    elif action == "impact":
        return _impact(g, symbol)
    elif action == "god":
        return _god(g, top)
    elif action == "resolve":
        results = _resolve(g, symbol)
        return "\n".join(str(r) for r in results)
    else:
        return f"Unknown action: {action}. Use: summary, callers, callees, path, impact, god, resolve"
