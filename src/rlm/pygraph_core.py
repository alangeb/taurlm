"""Python knowledge graph builder using AST — zero external dependencies."""

from __future__ import annotations

import ast
import os
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ── Data models ───────────────────────────────────────────────────

@dataclass
class Node:
    """A symbol in the codebase (function, class, variable, module)."""
    id: str                  # "file.py:SymbolName"
    kind: str               # "function" | "class" | "method" | "variable" | "module"
    file: str               # relative path
    line: int
    name: str               # symbol name
    docstring: str = ""
    parameters: List[str] = field(default_factory=list)
    return_type: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "name": self.name,
            "docstring": self.docstring,
            "parameters": self.parameters,
            "return_type": self.return_type,
        }


@dataclass
class Edge:
    """A relationship between two nodes."""
    from_id: str            # source node id
    to_id: str              # target node id
    kind: str               # "calls" | "imports" | "inherits" | "defines" | "assigns"
    file: str = ""          # file where the edge originates
    line: int = 0

    def to_dict(self) -> dict:
        d = {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "kind": self.kind,
        }
        if self.file:
            d["file"] = self.file
        if self.line:
            d["line"] = self.line
        return d


@dataclass
class Graph:
    """Knowledge graph: nodes + edges + query methods."""
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)

    # ── Index caches (built on demand) ────────────────────────────
    _by_id: Optional[Dict[str, Node]] = None
    _callers: Optional[Dict[str, List[str]]] = None      # who calls me?
    _callees: Optional[Dict[str, List[str]]] = None      # who do I call?
    _importers: Optional[Dict[str, List[str]]] = None    # who imports me?
    _importees: Optional[Dict[str, List[str]]] = None    # what do I import?
    _adjacency: Optional[Dict[str, Set[str]]] = None      # neighbors (all edge kinds)

    # ── Index builders ────────────────────────────────────────────
    def _build_by_id(self) -> Dict[str, Node]:
        if self._by_id is None:
            self._by_id = {n.id: n for n in self.nodes}
        return self._by_id

    def _build_callers(self) -> Dict[str, List[str]]:
        if self._callers is None:
            idx: Dict[str, List[str]] = defaultdict(list)
            for e in self.edges:
                if e.kind == "calls":
                    idx[e.to_id].append(e.from_id)
            self._callers = dict(idx)
        return self._callers

    def _build_callees(self) -> Dict[str, List[str]]:
        if self._callees is None:
            idx: Dict[str, List[str]] = defaultdict(list)
            for e in self.edges:
                if e.kind == "calls":
                    idx[e.from_id].append(e.to_id)
            self._callees = dict(idx)
        return self._callees

    def _build_importers(self) -> Dict[str, List[str]]:
        if self._importers is None:
            idx: Dict[str, List[str]] = defaultdict(list)
            for e in self.edges:
                if e.kind == "imports":
                    idx[e.to_id].append(e.from_id)
            self._importers = dict(idx)
        return self._importers

    def _build_importees(self) -> Dict[str, List[str]]:
        if self._importees is None:
            idx: Dict[str, List[str]] = defaultdict(list)
            for e in self.edges:
                if e.kind == "imports":
                    idx[e.from_id].append(e.to_id)
            self._importees = dict(idx)
        return self._importees

    def _build_adjacency(self) -> Dict[str, Set[str]]:
        if self._adjacency is None:
            adj: Dict[str, Set[str]] = defaultdict(set)
            for e in self.edges:
                adj[e.from_id].add(e.to_id)
                adj[e.to_id].add(e.from_id)  # undirected
            self._adjacency = dict(adj)
        return self._adjacency

    # ── Invalidate caches ─────────────────────────────────────────
    def _invalidate(self):
        self._by_id = None
        self._callers = None
        self._callees = None
        self._importers = None
        self._importees = None
        self._adjacency = None

    # ── Mutators ──────────────────────────────────────────────────
    def add_node(self, node: Node):
        self.nodes.append(node)
        self._invalidate()

    def add_edge(self, edge: Edge):
        self.edges.append(edge)
        self._invalidate()

    # ── Queries ───────────────────────────────────────────────────
    def node_by_id(self, node_id: str) -> Optional[Node]:
        return self._build_by_id().get(node_id)

    def node_by_name(self, name: str) -> List[Node]:
        """Find all nodes matching a name (partial match OK)."""
        return [n for n in self.nodes if name in n.name]

    def callers(self, node_id: str) -> List[str]:
        """Who calls this node?"""
        return self._build_callers().get(node_id, [])

    def callees(self, node_id: str) -> List[str]:
        """Who does this node call?"""
        return self._build_callees().get(node_id, [])

    def importers(self, node_id: str) -> List[str]:
        """Who imports this node?"""
        return self._build_importers().get(node_id, [])

    def importees(self, node_id: str) -> List[str]:
        """What does this node import?"""
        return self._build_importees().get(node_id, [])

    def neighbors(self, node_id: str) -> Set[str]:
        """All connected nodes (any edge kind)."""
        return self._build_adjacency().get(node_id, set())

    def shortest_path(self, from_name: str, to_name: str) -> List[str]:
        """BFS shortest path between two nodes (by name, partial match OK)."""
        from_nodes = self.node_by_name(from_name)
        to_nodes = self.node_by_name(to_name)

        if not from_nodes or not to_nodes:
            return []

        to_ids = {n.id for n in to_nodes}
        from_ids = {n.id for n in from_nodes}

        # BFS from all matching from_nodes
        adj = self._build_adjacency()
        queue = deque((fid, [fid]) for fid in from_ids)
        visited = set(from_ids)

        while queue:
            current, path = queue.popleft()
            if current in to_ids:
                return path
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return []

    def god_nodes(self, top: int = 10) -> List[Tuple[str, int]]:
        """Most-connected nodes by fan-in (called by most others)."""
        callers_idx = self._build_callers()
        fan_in = Counter()
        for target, callers in callers_idx.items():
            fan_in[target] = len(callers)
        return fan_in.most_common(top)

    def impact(self, node_id: str) -> Set[str]:
        """All nodes that would be affected if this node changes (transitive callers)."""
        impacted: Set[str] = set()
        queue = [node_id]
        callers_idx = self._build_callers()

        while queue:
            current = queue.pop()
            for caller in callers_idx.get(current, []):
                if caller not in impacted:
                    impacted.add(caller)
                    queue.append(caller)
        return impacted

    # ── Serialization ─────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "version": "1.0",
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "stats": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                "files": len({n.file for n in self.nodes}),
            },
        }


# ── AST collectors ────────────────────────────────────────────────

class _SymbolCollector(ast.NodeVisitor):
    """Collect all symbols (functions, classes, methods, module-level vars) from a file."""

    def __init__(self, filepath: str, rel_path: str):
        self.filepath = filepath
        self.rel_path = rel_path
        self.symbols: List[Node] = []
        self._class_stack: List[str] = []
        self._func_stack: List[str] = []  # P7-B4-20: lexical function scope

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._func_stack.append(node.name)
        self._add_function(node)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._func_stack.append(node.name)
        self._add_function(node)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef):
        docstring = ast.get_docstring(node) or ""
        # P7-B4-20: qualified id from full class scope (top-level stays bare).
        self._class_stack.append(node.name)
        qualified = ".".join(self._class_stack)
        self.symbols.append(Node(
            id=f"{self.rel_path}:{qualified}",
            kind="class",
            file=self.rel_path,
            line=node.lineno,
            name=qualified,
            docstring=docstring,
        ))
        self.generic_visit(node)
        self._class_stack.pop()

    def _add_function(self, node):
        is_method = len(self._class_stack) > 0
        kind = "method" if is_method else "function"
        docstring = ast.get_docstring(node) or ""

        # Build parameters
        params = []
        for arg in node.args.args:
            ann = ""
            if arg.annotation:
                ann = self._type_name(arg.annotation)
            params.append(f"{arg.arg}: {ann}" if ann else arg.arg)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")

        # Return type
        ret = self._type_name(node.returns) if node.returns else ""

        # P7-B4-20: build qualified name from full lexical scope.
        # Top-level -> "name" (backward compat). Nested -> "outer.name".
        # Method -> "Class.method" (unchanged). Nested-in-method -> "Class.method.inner".
        parts = list(self._class_stack) + list(self._func_stack)
        full_name = ".".join(parts)

        self.symbols.append(Node(
            id=f"{self.rel_path}:{full_name}",
            kind=kind,
            file=self.rel_path,
            line=node.lineno,
            name=full_name,
            docstring=docstring,
            parameters=params,
            return_type=ret,
        ))

    def _type_name(self, node) -> str:
        # Iterative to avoid recursion on deeply nested type annotations
        parts = []
        current = node
        while current is not None:
            if isinstance(current, ast.Name):
                parts.append(current.id)
                current = None
            elif isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            elif isinstance(current, ast.Constant):
                parts.append(repr(current.value))
                current = None
            elif isinstance(current, ast.Subscript):
                parts.append("[...]")
                current = current.value
            else:
                parts.append("Any")
                current = None
        return ".".join(reversed(parts)) if parts else "Any"


class _CallCollector(ast.NodeVisitor):
    """Collect all function/method calls from a file."""

    def __init__(self, filepath: str, rel_path: str):
        self.filepath = filepath
        self.rel_path = rel_path
        self.calls: List[Tuple[str, str, int]] = []  # (caller_id, callee_name, line)
        self._context: List[str] = []  # current class/function stack
        self._current_class: str | None = None  # current class name for self.method resolution

    def visit_ClassDef(self, node: ast.ClassDef):
        self._context.append(node.name)
        self._current_class = node.name
        self.generic_visit(node)
        self._context.pop()
        self._current_class = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._context.append(node.name)
        self.generic_visit(node)
        self._context.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._context.append(node.name)
        self.generic_visit(node)
        self._context.pop()

    def visit_Call(self, node: ast.Call):
        callee = self._resolve_call(node.func)
        if callee:
            # P7-B4-20: caller id from FULL context path so nested
            # functions get qualified ids matching _SymbolCollector.
            if self._context:
                caller_name = ".".join(self._context)
            else:
                caller_name = "__module__"
            caller_id = f"{self.rel_path}:{caller_name}"
            self.calls.append((caller_id, callee, node.lineno))
        self.generic_visit(node)

    def _resolve_call(self, func) -> Optional[str]:
        """Resolve a Call.func to a symbol name."""
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            # self.method → resolve to "ClassName.method" when inside a class
            if isinstance(func.value, ast.Name):
                if func.value.id == "self":
                    # Resolve to full class.method name
                    if self._current_class:
                        return f"{self._current_class}.{func.attr}"
                    return func.attr
                return f"{func.value.id}.{func.attr}"
            # P7-CT-18: nested attribute chains (a.b.c) have an unknown
            # receiver type — collapsing to the last part fabricated edges
            # (e.g. self._set.add → Wiki.add). Emit no edge instead.
            return None
        return None

    def _attr_chain(self, node) -> List[str]:
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        parts.reverse()
        return parts


class _ImportCollector(ast.NodeVisitor):
    """Collect all imports from a file."""

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.imports: List[Tuple[str, str, int]] = []  # (source_module, target_name, line)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append((alias.name, alias.asname or alias.name, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module is None:
            return
        module = node.module
        for alias in node.names:
            target = f"{module}.{alias.name}" if alias.name != "*" else module
            self.imports.append((target, alias.asname or alias.name, node.lineno))
        self.generic_visit(node)


# ── Graph builder ─────────────────────────────────────────────────

class GraphBuilder:
    """Builds a knowledge graph from Python source files."""

    DEFAULT_EXCLUDE_DIRS = {
        ".git", "venv", "__pycache__", "dist", "build",
        ".idea", ".vscode", "node_modules", ".tox", "tests", "test",
    }

    def __init__(self, target_path: str, exclude_dirs: Set[str] | None = None,
                 include_tests: bool = False):
        self.target_path = os.path.abspath(target_path)
        self.is_single_file = os.path.isfile(self.target_path)
        self.exclude_dirs = set(self.DEFAULT_EXCLUDE_DIRS)
        if exclude_dirs:
            self.exclude_dirs.update(exclude_dirs)
        if include_tests:
            self.exclude_dirs -= {"tests", "test"}

        self.graph = Graph()
        # Maps: simple_name → list of (file, full_id) for resolution
        self._name_registry: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        # Maps: module_name → file_path for import resolution
        self._module_map: Dict[str, str] = {}
        # Raw data for resolution pass
        self._raw_calls: List[Tuple[str, str, int]] = []
        self._raw_imports: List[Tuple[str, str, int, str]] = []  # (source_module, local_name, line, source_file)

    def build(self) -> Graph:
        # Prevent RecursionError on large/deeply-nested codebases
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 2000))
        try:
            if self.is_single_file:
                self._process_file(self.target_path, os.path.basename(self.target_path))
            else:
                for root, dirs, files in os.walk(self.target_path):
                    dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
                    for filename in sorted(files):
                        if not filename.endswith(".py"):
                            continue
                        filepath = os.path.join(root, filename)
                        rel = os.path.relpath(filepath, self.target_path)
                        self._process_file(filepath, rel)

            self._resolve_calls()
            self._resolve_imports()
            return self.graph
        finally:
            sys.setrecursionlimit(old_limit)

    def _process_file(self, filepath: str, rel_path: str):
        source = self._read_file(filepath)
        if source is None:
            return
        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return

        # Register module-level name for import resolution
        module_name = Path(rel_path).stem
        self._module_map[module_name] = rel_path

        # Collect symbols
        collector = _SymbolCollector(filepath, rel_path)
        collector.visit(tree)
        for sym in collector.symbols:
            self.graph.add_node(sym)
            # P7-B4-20: only register the bare/simple name for top-level
            # symbols and methods. Nested functions (kind=="function" with
            # dots in name) must NOT be reachable by bare call.
            simple_name = sym.name.split(".")[-1]
            if sym.kind == "method" or "." not in sym.name:
                self._name_registry[simple_name].append((rel_path, sym.id))
            # P7-CT-18: also register the qualified name (e.g. "Wiki.add")
            # so qualified calls resolve exactly instead of falling back to
            # the bare last component (which matched every symbol named 'add').
            if "." in sym.name and sym.name != simple_name:
                self._name_registry[sym.name].append((rel_path, sym.id))

        # Collect calls (store raw for later resolution)
        call_collector = _CallCollector(filepath, rel_path)
        call_collector.visit(tree)
        self._raw_calls.extend(call_collector.calls)

        # Collect imports
        import_collector = _ImportCollector(rel_path)
        import_collector.visit(tree)
        self._raw_imports.extend(
            (src, tgt, ln, rel_path) for src, tgt, ln in import_collector.imports
        )

    def _resolve_calls(self):
        """Resolve raw calls to actual symbol IDs and create edges."""
        for caller_id, callee_name, line in self._raw_calls:
            resolved = self._resolve_name(callee_name, caller_id)
            if resolved:
                # If multiple matches, prefer same-file
                caller_file = caller_id.split(":")[0]
                same_file = [(f, iid) for f, iid in resolved if f == caller_file]
                target = same_file[0][1] if same_file else resolved[0][1]
                self.graph.add_edge(Edge(
                    from_id=caller_id,
                    to_id=target,
                    kind="calls",
                    file=caller_file,
                    line=line,
                ))

    def _resolve_imports(self):
        """Resolve raw imports and create import edges."""
        for target_module, local_name, line, source_file in self._raw_imports:
            # Find the source file for this module
            resolved_file = self._resolve_module(target_module)
            if resolved_file:
                # Edge: source_file imports from resolved_file
                self.graph.add_edge(Edge(
                    from_id=f"{source_file}:__module__",
                    to_id=f"{resolved_file}:__module__",
                    kind="imports",
                    file=source_file,
                    line=line,
                ))

    def _resolve_module(self, module_name: str) -> Optional[str]:
        """Resolve a module name to a file path."""
        # Direct match
        if module_name in self._module_map:
            return self._module_map[module_name]
        # Try splitting dotted names
        parts = module_name.split(".")
        for i in range(len(parts), 0, -1):
            partial = ".".join(parts[:i])
            if partial in self._module_map:
                return self._module_map[partial]
        return None

    def _resolve_name(self, name: str, caller_id: str) -> List[Tuple[str, str]]:
        """Resolve a symbol name to (file, id) pairs."""
        if name in self._name_registry:
            return self._name_registry[name]
        # P7-CT-18: NO last-component fallback for dotted names. A qualified
        # call like 'wiki.add' must not bind to every symbol named 'add'
        # (fabricated edges: callers('Wiki.add') was 49). Unknown receivers
        # resolve to no edge — absence beats fabrication.
        return []

    @staticmethod
    def _read_file(filepath: str) -> str | None:
        for encoding in ("utf-8", "latin-1"):
            try:
                with open(filepath, encoding=encoding) as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                continue
        return None


# ── Standalone CLI ────────────────────────────────────────────────

def build_graph(
    path: str = ".",
    exclude_dirs: str = "",
    include_tests: bool = False,
) -> Graph:
    """Build a knowledge graph from a Python project."""
    exclude_set = (
        set(d.strip() for d in exclude_dirs.split(",") if d.strip())
        if exclude_dirs else set()
    )
    builder = GraphBuilder(path, exclude_dirs=exclude_set, include_tests=include_tests)
    return builder.build()


def query_graph(graph: Graph, query: str) -> str:
    """Execute a query against the graph and return results."""
    parts = query.strip().split()
    if not parts:
        return "Usage: query <callers|callees|path|impact|god> <symbol> [args...]"

    cmd = parts[0].lower()

    if cmd in ("callers", "who-calls"):
        symbol = " ".join(parts[1:]) if len(parts) > 1 else ""
        if not symbol:
            return "Usage: callers <symbol_name>"
        matches = graph.node_by_name(symbol)
        if not matches:
            return f"No nodes matching '{symbol}'"
        results = []
        for node in matches:
            callers = graph.callers(node.id)
            if callers:
                results.append(f"📞 {node.name} ({node.file}:{node.line}) is called by:")
                for c in callers[:20]:  # limit output
                    results.append(f"   ← {c}")
            else:
                results.append(f"📞 {node.name} ({node.file}:{node.line}) — no callers found")
        return "\n".join(results)

    elif cmd in ("callees", "calls"):
        symbol = " ".join(parts[1:]) if len(parts) > 1 else ""
        if not symbol:
            return "Usage: callees <symbol_name>"
        matches = graph.node_by_name(symbol)
        if not matches:
            return f"No nodes matching '{symbol}'"
        results = []
        for node in matches:
            callees = graph.callees(node.id)
            if callees:
                results.append(f"→ {node.name} ({node.file}:{node.line}) calls:")
                for c in callees[:20]:
                    results.append(f"   → {c}")
            else:
                results.append(f"→ {node.name} ({node.file}:{node.line}) — no callees found")
        return "\n".join(results)

    elif cmd == "path":
        if len(parts) < 3:
            return "Usage: path <from> <to>"
        from_name = parts[1]
        to_name = parts[2]
        path = graph.shortest_path(from_name, to_name)
        if path:
            return f"Path from '{from_name}' to '{to_name}' ({len(path)} hops):\n" + "\n→ ".join(path)
        return f"No path found between '{from_name}' and '{to_name}'"

    elif cmd == "impact":
        symbol = " ".join(parts[1:]) if len(parts) > 1 else ""
        if not symbol:
            return "Usage: impact <symbol_name>"
        matches = graph.node_by_name(symbol)
        if not matches:
            return f"No nodes matching '{symbol}'"
        results = []
        for node in matches:
            impacted = graph.impact(node.id)
            results.append(f"💥 Changing '{node.name}' would affect {len(impacted)} node(s):")
            for i in sorted(impacted)[:30]:
                results.append(f"   ⚠ {i}")
            if len(impacted) > 30:
                results.append(f"   ... and {len(impacted) - 30} more")
        return "\n".join(results)

    elif cmd in ("god", "hubs", "top"):
        top = 10
        if len(parts) > 1:
            try:
                top = int(parts[1])
            except ValueError:
                pass
        nodes = graph.god_nodes(top)
        if not nodes:
            return "No call edges found"
        results = [f"🔥 Top {top} most-called nodes (by fan-in):"]
        for node_id, count in nodes:
            node = graph.node_by_id(node_id)
            name = node.name if node else node_id
            results.append(f"   {count} callers ← {name}")
        return "\n".join(results)

    else:
        return f"Unknown query: '{cmd}'. Use: callers, callees, path, impact, god"

