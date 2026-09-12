"""RLM Python import checker — catches NameError bugs before runtime.

Adapted from tau/src/tools/pycheck.py. AST-based detection of missing
and unused imports. Provides check_project() for directory scanning.
"""
from __future__ import annotations


import ast
import builtins
import json
from dataclasses import dataclass, field
from pathlib import Path




# ── AST helpers ──────────────────────────────────────────────────

def _is_type_checking_test(node: ast.expr) -> bool:
    """Check if an AST node represents a TYPE_CHECKING condition."""
    if isinstance(node, ast.Name) and node.id == "TYPE_CHECKING":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING":
        return True
    return False


def _get_imports(tree: ast.AST) -> set[str]:
    """Collect imported names, skipping TYPE_CHECKING blocks.

    For ``import X as Y``, only ``Y`` is added (the alias), not ``X``.
    For ``from X import Y as Z``, only ``Z`` is added (the alias), or ``Y`` if no alias.
    """
    imports: set[str] = set()

    class _ImportVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_type_checking = False

        def visit_If(self, node: ast.If) -> None:
            if _is_type_checking_test(node.test):
                old = self._in_type_checking
                self._in_type_checking = True
                for child in node.body:
                    self.visit(child)
                self._in_type_checking = old
                # else branch is runtime code, not TYPE_CHECKING
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            if self._in_type_checking:
                return
            for alias in node.names:
                if alias.asname:
                    imports.add(alias.asname)
                else:
                    imports.add(alias.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if self._in_type_checking:
                return
            if not node.module or node.module == "__future__":
                return
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.asname:
                    imports.add(alias.asname)
                else:
                    imports.add(alias.name)

    _ImportVisitor().visit(tree)
    return imports


def _get_names(tree: ast.AST) -> set[str]:
    """Collect all Name nodes EXCEPT those inside type hint contexts.

    Skips names in:
    - Function return annotations
    - Function argument annotations (including defaults)
    - Variable annotations (AnnAssign)
    - Class base classes and keyword bases

    Used for missing imports check — type hint names shouldn't cause false positives.
    """
    return _collect_names(tree, skip_annotations=True)


def _get_names_all(tree: ast.AST) -> set[str]:
    """Collect ALL Name nodes, including those inside type hint contexts.

    Used for unused imports check — imports used only in type hints should not
    be flagged as unused.
    """
    return _collect_names(tree, skip_annotations=False)


def _collect_names(tree: ast.AST, skip_annotations: bool) -> set[str]:
    """Collect Name nodes, optionally skipping those inside type hint contexts."""
    names: set[str] = set()

    class _NameVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_annotation = 0  # Depth counter for annotation contexts

        def _visit_annotation(self, node: ast.AST) -> None:
            """Visit a node that is a type annotation."""
            if skip_annotations:
                self._in_annotation += 1
            self.visit(node)  # Use visit() to dispatch to visit_Name(), etc.
            if skip_annotations:
                self._in_annotation -= 1

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Visit the function body normally
            for child in node.body:
                self.visit(child)
            # Visit decorators normally
            for decorator in node.decorator_list:
                self.visit(decorator)
            # Visit return annotation as annotation context
            if node.returns:
                self._visit_annotation(node.returns)
            # Visit argument annotations as annotation context
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation:
                    self._visit_annotation(arg.annotation)
            if node.args.vararg and node.args.vararg.annotation:
                self._visit_annotation(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation:
                self._visit_annotation(node.args.kwarg.annotation)
            # Visit default values normally (they're runtime expressions)
            for default in node.args.defaults + node.args.kw_defaults:
                if default is not None:
                    self.visit(default)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            # Lambda body is visited normally
            self.visit(node.body)
            # Argument annotations are annotation contexts
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation:
                    self._visit_annotation(arg.annotation)
            if node.args.vararg and node.args.vararg.annotation:
                self._visit_annotation(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation:
                self._visit_annotation(node.args.kwarg.annotation)
            # Default values are runtime expressions
            for default in node.args.defaults + node.args.kw_defaults:
                if default is not None:
                    self.visit(default)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            # Annotation is an annotation context
            if node.annotation:
                self._visit_annotation(node.annotation)
            # Value is a runtime expression
            if node.value:
                self.visit(node.value)
            # Target is a name definition, not a usage
            # (handled by _get_defined_names)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            # Visit the class body normally
            for child in node.body:
                self.visit(child)
            # Visit decorators normally
            for decorator in node.decorator_list:
                self.visit(decorator)
            # Base classes are runtime expressions (not annotation contexts)
            for base in node.bases:
                self.visit(base)
            # Keyword bases (metaclass=...) are also runtime expressions
            for keyword in node.keywords:
                self.visit(keyword.value)

        def visit_Name(self, node: ast.Name) -> None:
            if self._in_annotation == 0:
                names.add(node.id)

    _NameVisitor().visit(tree)
    return names


def _collect_target_names(node: ast.AST, defined: set[str]) -> None:
    """Recursively collect names from assignment targets (handles nested tuples/lists)."""
    if isinstance(node, ast.Name):
        defined.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _collect_target_names(elt, defined)
    elif isinstance(node, ast.Starred):
        _collect_target_names(node.value, defined)


def _get_defined_names(tree: ast.AST) -> set[str]:
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            # Collect all function arguments as defined names
            for arg in node.args.args:
                defined.add(arg.arg)
            for arg in node.args.posonlyargs:
                defined.add(arg.arg)
            for arg in node.args.kwonlyargs:
                defined.add(arg.arg)
            if node.args.vararg:
                defined.add(node.args.vararg.arg)
            if node.args.kwarg:
                defined.add(node.args.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _collect_target_names(target, defined)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Lambda):
            for arg in node.args.args:
                defined.add(arg.arg)
            for arg in node.args.posonlyargs:
                defined.add(arg.arg)
            for arg in node.args.kwonlyargs:
                defined.add(arg.arg)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                _collect_target_names(gen.target, defined)
        elif isinstance(node, ast.For):
            _collect_target_names(node.target, defined)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars:
                    _collect_target_names(item.optional_vars, defined)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        # Python 3.10+ match/case patterns
        elif hasattr(ast, "Match") and isinstance(node, ast.Match):
            _collect_match_patterns(node.subject, defined)
            for case in node.cases:
                _collect_match_patterns(case.pattern, defined)
    return defined


def _collect_match_patterns(node: ast.AST, defined: set[str]) -> None:
    """Collect names from match/case pattern nodes (Python 3.10+)."""
    if isinstance(node, ast.Name):
        defined.add(node.id)
    elif isinstance(node, (ast.MatchValue, ast.MatchSingleton)):
        pass  # No new names defined
    elif isinstance(node, ast.MatchSequence):
        for pattern in node.patterns:
            _collect_match_patterns(pattern, defined)
    elif isinstance(node, ast.MatchMapping):
        # MatchMapping keys must be constants (string literals, numbers).
        # Variable keys are a syntax error, so we skip key processing.
        for pattern in node.patterns:
            _collect_match_patterns(pattern, defined)
    elif isinstance(node, ast.MatchClass):
        if node.cls:
            pass  # Class reference, not a definition
        for pattern in node.patterns:
            _collect_match_patterns(pattern, defined)
        # kwd_attrs are strings (e.g., 'x', 'y'), kwd_patterns hold the actual
        # captured variable patterns (e.g., MatchAs(name='px'))
        for pattern in node.kwd_patterns:
            _collect_match_patterns(pattern, defined)
    elif isinstance(node, ast.MatchStar):
        if node.name:
            defined.add(node.name)
    elif isinstance(node, ast.MatchAs):
        if node.name:
            defined.add(node.name)
        if node.pattern:
            _collect_match_patterns(node.pattern, defined)
    elif isinstance(node, ast.MatchOr):
        for pattern in node.patterns:
            _collect_match_patterns(pattern, defined)


# ── File checking ────────────────────────────────────────────────

_BUILTIN_NAMES = set(dir(builtins)) | {"__name__", "__doc__", "__file__", "__annotations__", "self", "cls"}

# Modules whose imports affect parser behavior or are type-only — can't be detected as "used" by AST
_IGNORE_UNUSED = {"__future__", "typing"}


def check_file(filepath: Path) -> dict:
    result: dict = {"file": str(filepath), "missing_imports": [], "unused_imports": [], "errors": []}

    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError as e:
        result["errors"].append(f"Syntax error: {e}")
        return result
    except Exception as e:
        result["errors"].append(f"Cannot read file: {e}")
        return result

    imports = _get_imports(tree)
    # For missing imports: skip names in type hints (they don't cause NameError)
    names_runtime = _get_names(tree)
    # For unused imports: include names in type hints (imports used only in hints are not unused)
    names_all = _get_names_all(tree)
    defined = _get_defined_names(tree)

    # Missing imports: names used at runtime but not imported or defined
    missing = names_runtime - imports - _BUILTIN_NAMES - defined
    if missing:
        result["missing_imports"] = sorted(missing)

    # Unused imports: imports not used anywhere (including type hints)
    if imports:
        used_names_all = names_all - defined
        unused = (imports - _IGNORE_UNUSED) - (imports & used_names_all)
        if unused:
            result["unused_imports"] = sorted(unused)

    return result


# ── Formatting ───────────────────────────────────────────────────

def _format_markdown(results: dict) -> str:
    s = results["summary"]
    output = [
        "# Python Import Check Report\n",
        f"**Path**: {results['path']}\n",
        f"**Files Checked**: {results['files_checked']}\n",
    ]

    if s["total_missing_imports"] > 0 or s["total_unused_imports"] > 0:
        output.append("\n## ⚠️ Issues Found\n")
    else:
        output.append("\n## ✅ No Issues Found\n")

    if s["total_missing_imports"] > 0:
        output.append(
            f"\n### Missing Imports\n**{s['files_with_missing']} file(s) with {s['total_missing_imports']} missing import(s)**\n"
        )
        for issue in results["issues"]:
            if issue["type"] == "missing_import":
                output.append(
                    f"- **{issue['file']}**: {', '.join(f'`{imp}`' for imp in issue['imports'])}"
                )

    if s["total_unused_imports"] > 0:
        output.append(
            f"\n### Unused Imports\n**{s['files_with_unused']} file(s) with {s['total_unused_imports']} unused import(s)**\n"
        )
        for issue in results["issues"]:
            if issue["type"] == "unused_import":
                output.append(
                    f"- **{issue['file']}**: {', '.join(f'`{imp}`' for imp in issue['imports'])}"
                )

    for issue in results["issues"]:
        if issue["type"] == "error":
            output.append(f"\n### Errors in {issue['file']}\n")
            for error in issue["details"]:
                output.append(f"- {error}")

    output.append("\n## 💡 Recommendations\n")
    if s["total_missing_imports"] > 0:
        output.append("- Add missing imports to prevent NameError at runtime")
    if s["total_unused_imports"] > 0:
        output.append("- Remove unused imports to clean up code; keep type-hint imports")

    return "\n".join(output)


# ── Execution ────────────────────────────────────────────────────


def check_project(path: str) -> str:
    """Check all .py files under path for missing and unused imports.

    Args:
        path: Directory to scan.

    Returns:
        Markdown report of issues found.
    """
    from pathlib import Path as P
    root = P(path)
    results = []
    for f in sorted(root.rglob("*.py")):
        if any(part in (".git", "venv", "__pycache__", "node_modules") for part in f.parts):
            continue
        try:
            r = check_file(f)
            if r.get("missing_imports") or r.get("unused_imports"):
                results.append((str(f), r))
        except Exception:
            pass
    if not results:
        return f"No import issues found in {len(list(root.rglob('*.py')))} files under {path}."
    files_checked = len(list(root.rglob("*.py")))
    issues = []
    total_missing = total_unused = files_missing = files_unused = 0
    for f, r in results:
        if r["missing_imports"]:
            issues.append({"type": "missing_import", "file": str(f), "imports": r["missing_imports"]})
            total_missing += len(r["missing_imports"])
            files_missing += 1
        if r["unused_imports"]:
            issues.append({"type": "unused_import", "file": str(f), "imports": r["unused_imports"]})
            total_unused += len(r["unused_imports"])
            files_unused += 1
    return _format_markdown({
        "summary": {"total_missing_imports": total_missing, "total_unused_imports": total_unused,
                    "files_with_missing": files_missing, "files_with_unused": files_unused},
        "path": path,
        "files_checked": files_checked,
        "issues": issues,
    })
