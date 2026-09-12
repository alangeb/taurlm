"""code_analysis: Analyze Python code files."""

import os
import re


__all__ = [
    'run'
]

def _count_loc(file_path: str) -> dict:
    """Count lines, words, characters in a file."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    lines = content.split("\n")
    return {
        "lines": len(lines),
        "words": len(content.split()),
        "characters": len(content),
        "blank_lines": sum(1 for line in lines if not line.strip()),
        "code_lines": sum(1 for line in lines if line.strip() and not line.strip().startswith("#")),
    }


def _find_functions(file_path: str) -> list:
    """Find all function definitions in a Python file."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    pattern = r"^\s*(?:async\s+)?def\s+(\w+)\s*\("
    return [m.group(1) for m in re.finditer(pattern, content, re.MULTILINE)]


def _find_classes(file_path: str) -> list:
    """Find all class definitions in a Python file."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    pattern = r"^\s*class\s+(\w+)"
    return [m.group(1) for m in re.finditer(pattern, content, re.MULTILINE)]


def _find_imports(file_path: str) -> list:
    """Find all imports in a Python file."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    imports = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("import ") or line.startswith("from "):
            imports.append(line)
    return imports


def run(
    action="count_loc",
    file_path="",
    dir_path="",
    extension=".py",
    **kwargs,
):
    """Execute code analysis operation.

    Args:
        action: count_loc, find_functions, find_classes, find_imports, analyze, scan_dir
        file_path: Target file path (for file actions)
        dir_path: Target directory path (for scan_dir action)
        extension: File extension to scan (default .py)

    Returns:
        dict with 'success' and analysis results
    """
    try:
        if action == "count_loc":
            if not file_path:
                return {"success": False, "error": "file_path is required"}
            return {"success": True, **_count_loc(file_path)}

        elif action == "find_functions":
            if not file_path:
                return {"success": False, "error": "file_path is required"}
            return {"success": True, "functions": _find_functions(file_path)}

        elif action == "find_classes":
            if not file_path:
                return {"success": False, "error": "file_path is required"}
            return {"success": True, "classes": _find_classes(file_path)}

        elif action == "find_imports":
            if not file_path:
                return {"success": False, "error": "file_path is required"}
            return {"success": True, "imports": _find_imports(file_path)}

        elif action == "analyze":
            if not file_path:
                return {"success": False, "error": "file_path is required"}
            return {
                "success": True,
                "file": file_path,
                "loc": _count_loc(file_path),
                "functions": _find_functions(file_path),
                "classes": _find_classes(file_path),
                "imports": _find_imports(file_path),
            }

        elif action == "scan_dir":
            if not dir_path:
                return {"success": False, "error": "dir_path is required"}
            results = {}
            for root, dirs, files in os.walk(dir_path):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".") and d not in ("__pycache__", "venv", "node_modules")
                ]
                for f in files:
                    if f.endswith(extension):
                        path = os.path.join(root, f)
                        results[path] = _count_loc(path)
            return {"success": True, "files": results}

        else:
            return {"success": False, "error": f"Unknown action: {action}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
