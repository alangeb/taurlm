"""RLM Python dead code detector — finds unused functions and imports.

Adapted from tau/src/tools/pyanalyze.py. AST-based usage analysis.
Provides analyze_project() for directory scanning.

P7-B4-27: cross-file awareness. A symbol referenced from another file
(import, dotted access, or — when a graph is available — a call edge) is
NOT flagged. The report wording is "UNREFERENCED (verify: dynamic dispatch?)"
rather than "DEAD" so the tool never invites deleting live code.
"""
from __future__ import annotations


import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ── AST helpers ──────────────────────────────────────────────────

def _collect_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            obj = node
            while isinstance(obj, ast.Attribute):
                obj = obj.value
            if isinstance(obj, ast.Name):
                names.add(obj.id)
    return names


def _collect_imported_symbols(tree: ast.AST) -> set[str]:
    """Symbols imported FROM other modules (used cross-file by another file).

    Captures both `from mod import a, b` and `import mod` (so a dotted
    `mod.thing` access in another file marks `thing` used).
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _collect_dotted_attrs(tree: ast.AST) -> set[str]:
    """Right-hand attributes of dotted accesses (mod.thing -> 'thing').

    Used to detect cross-file references like `mod_a.alpha()` where the
    symbol lives in another module.
    """
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attrs.add(node.attr)
    return attrs


def _collect_getattr_targets(tree: ast.AST) -> set[str]:
    """String-literal names passed to getattr(obj, 'name') — dynamic dispatch."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "getattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                names.add(node.args[1].value)
    return names


def _collect_dunder_all(tree: ast.AST) -> set[str]:
    """Names listed in a module-level __all__ = [...] assignment."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                names.add(el.value)
    return names


def _is_entry_point(name: str) -> bool:
    """Skill/command/REPL entry points are invoked dynamically — never DEAD."""
    return name in {"main", "run", "execute", "register", "get_commands"}


def _is_test_artifact(path: Path) -> bool:
    """pytest fixtures/test helpers are invoked by the test runner, not by
    project code — never scan them as candidates (their evidence still counts)."""
    return (
        path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name.startswith("conftest")
        or "tests" in path.parts
    )


# ── Execution ────────────────────────────────────────────────────


def analyze_project(path: str, graph=None) -> str:
    """Analyze all .py files under path for unused functions and imports.

    Args:
        path: Directory to scan.
        graph: Optional pygraph_core.Graph. When provided, a symbol is only
            flagged when graph.callers(node.id) is empty AND no import edge
            targets it AND it is not __all__/entry-point. Optional so the
            tool still works standalone without the graph.

    Returns:
        Markdown report of unreferenced code candidates.
    """
    from pathlib import Path as P
    import ast as _ast
    root = P(path)

    # Pass 1: gather cross-file evidence (imports, dotted access, dispatch).
    imported_elsewhere: set[str] = set()
    dotted_attrs_elsewhere: set[str] = set()
    getattr_names: set[str] = set()
    trees: dict = {}
    for f in sorted(root.rglob("*.py")):
        if any(part in (".git", "venv", "__pycache__", "node_modules") for part in f.parts):
            continue
        try:
            tree = _ast.parse(f.read_text())
        except Exception:
            continue
        if _is_test_artifact(f):
            # Test artifacts are never candidates, but still contribute
            # cross-file evidence (tests import/dispatch real symbols).
            imported_elsewhere |= _collect_imported_symbols(tree)
            dotted_attrs_elsewhere |= _collect_dotted_attrs(tree)
            getattr_names |= _collect_getattr_targets(tree)
            continue
        trees[f] = tree
        imported_elsewhere |= _collect_imported_symbols(tree)
        dotted_attrs_elsewhere |= _collect_dotted_attrs(tree)
        getattr_names |= _collect_getattr_targets(tree)

    # Optional graph lookup: node id -> has callers / has import edge.
    graph_callers: dict = {}
    graph_importers: dict = {}
    if graph is not None:
        try:
            for n in graph.nodes:
                graph_callers[n.id] = graph.callers(n.id)
                graph_importers[n.id] = graph.importers(n.id)
        except Exception:
            graph_callers = {}
            graph_importers = {}

    # Pass 2: flag unreferenced top-level symbols.
    all_results = {}
    for f, tree in trees.items():
        names = _collect_names(tree)
        dunder_all = _collect_dunder_all(tree)
        module_stem = f.stem
        unreferenced = []
        for node in tree.body:  # top-level only
            if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name.startswith("_"):
                continue
            # (d) dynamic-dispatch / entry-point carve-outs
            if _is_entry_point(name):
                continue
            if name in getattr_names:
                continue
            # (a) named in __all__
            if name in dunder_all:
                continue
            # (b) imported by name in ANY file
            if name in imported_elsewhere:
                continue
            # (c) dotted access from another file (mod.name)
            if name in dotted_attrs_elsewhere:
                continue
            # In-file usage
            if name in names:
                continue
            # LAYER 2 (optional): graph confirms no callers / no import edge
            if graph is not None:
                node_id = f"{module_stem}.py:{name}"
                callers = graph_callers.get(node_id)
                importers = graph_importers.get(node_id)
                if callers is not None:
                    if callers or importers:
                        continue
            unreferenced.append(name)
        if unreferenced:
            all_results[str(f)] = unreferenced

    if not all_results:
        return f"No unreferenced functions found under {path}."
    lines_out = [f"## Unreferenced Functions in {path}", ""]
    for fname, funcs in all_results.items():
        lines_out.append(f"**{fname}**")
        for fn in funcs:
            lines_out.append(f"  - UNREFERENCED (verify: dynamic dispatch?) `{fn}`")
        lines_out.append("")
    return "\n".join(lines_out)
