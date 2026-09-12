"""Version detection module for TauErgon.

Reads VERSION file from repo root and detects current git branch/hash.
Returns structured version info for console and audit display.

VERSION file format: "<int> <optional_git_hash>"
  Example: "1 aa75f6b3"
  Only the first whitespace-delimited token (parsed as int) is used.
  The hash is informational and silently discarded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Any

# Cache to avoid repeated subprocess calls
__all__ = [
    'Any',
    'Dict',
    'Path',
    'annotations',
    'get_version_info'
]

_version_cache: Dict[str, Any] | None = None


def _find_repo_root() -> Path | None:
    """Find the git repository root by walking up from cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def _read_version_file(repo_root: Path) -> int:
    """Read version number from VERSION file.

    VERSION file format: "<int> <optional_git_hash>"
    Only the first whitespace-delimited token is parsed as int.
    """
    version_file = repo_root / "VERSION"
    try:
        content = version_file.read_text().strip()
        if content:
            return int(content.split()[0])
    except (ValueError, IndexError, OSError):
        pass
    return 0


def _git_info() -> tuple[str, str]:
    """Get current branch and commit hash using two subprocess calls.

    One call for short hash (git show), one for branch (git rev-parse).
    Cannot combine because branch requires --abbrev-ref which is incompatible
    with --format.
    """
    branch = "unknown"
    hash_short = "unknown"
    try:
        # Get short hash directly (avoids multi-line body parsing issues)
        result = subprocess.run(
            ["git", "show", "-s", "--format=%h", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            hash_short = result.stdout.strip()[:8]

        # Get branch separately (can't combine with show)
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch_result.returncode == 0:
            branch = branch_result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return branch, hash_short


def get_version_info() -> Dict[str, Any]:
    """Get version information from VERSION file and git.

    Returns dict with:
      - version: integer version number (0 if VERSION file missing)
      - branch: current git branch name
      - hash: current git commit hash (short)
      - version_str: formatted display string
    """
    global _version_cache

    if _version_cache is not None:
        return _version_cache

    repo_root = _find_repo_root()
    if repo_root is None:
        _version_cache = {
            "version": 0,
            "branch": "unknown",
            "hash": "unknown",
            "version_str": "Tau v0 (unknown unknown)",
        }
        return _version_cache

    version = _read_version_file(repo_root)
    branch, hash_short = _git_info()

    version_str = f"Tau v{version} ({branch} {hash_short})"

    _version_cache = {
        "version": version,
        "branch": branch,
        "hash": hash_short,
        "version_str": version_str,
    }
    return _version_cache
