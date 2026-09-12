"""code_search skill: text search helpers (subprocess-based).

These are importable: `from skills.code_search import grep, find_defs, count_occurrences`.
For structural (AST) questions use rlm_analysis instead.
"""
import subprocess


def grep(pattern, path=".", include="*.py", context=0, max_results=20):
    """Search files for a text pattern; returns up to max_results lines."""
    cmd = ["grep", "-rn"]
    if context:
        cmd += ["-C", str(context)]
    cmd += ["--include=" + include, pattern, path]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
    return lines[:max_results]


def find_defs(name, path=".", include="*.py"):
    """Find function/class definitions for *name*."""
    r = subprocess.run(
        ["grep", "-rn", f"def {name}\\b\\|class {name}\\b", "--include=" + include, path],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    return r.stdout.strip().split("\n") if r.stdout.strip() else []


def count_occurrences(pattern, path=".", include="*.py"):
    """Return list of files containing *pattern*."""
    r = subprocess.run(["grep", "-rl", "--include=" + include, pattern, path],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    return r.stdout.strip().split("\n") if r.stdout.strip() else []
