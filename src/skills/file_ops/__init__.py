"""File Operations Skill — read, write, edit, list files."""

import os
from pathlib import Path


__all__ = [
    'Path',
    'find_files',
    'list_dir',
    'read_file',
    'write_file'
]

def read_file(path: str, lines: int = 100) -> str:
    """Read a file and return content.

    Args:
        path: Path to the file
        lines: Maximum number of lines to return

    Returns:
        File content (truncated to N lines if needed)
    """
    with open(path, 'r') as f:
        content = f.read()
    # Limit to N lines
    line_list = content.split('\n')
    if len(line_list) > lines:
        return '\n'.join(line_list[:lines]) + f'\n... ({len(line_list) - lines} more lines)'
    return content


def write_file(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path: Path to the file
        content: Content to write

    Returns:
        Success message
    """
    resolved = Path(path).resolve()
    cwd = Path.cwd().resolve()
    # P7-B11-02: startswith() on strings is bypassed by sibling-prefix dirs
    # (/work/taurlm-evil passes startswith('/work/taurlm')). is_relative_to()
    # compares resolved path *components*, closing the escape.
    if not resolved.is_relative_to(cwd):
        raise ValueError(f'Unsafe path: {path!r} (must be within working directory)')
    if '..' in Path(path).parts:
        raise ValueError(f'Unsafe path: {path!r} (contains ..)')
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    return f"Wrote {len(content)} chars to {path}"


def list_dir(path: str = '.', recursive: bool = False) -> str:
    """List directory contents.

    Args:
        path: Directory path
        recursive: If True, list recursively

    Returns:
        Newline-separated list of file paths
    """
    p = Path(path)
    if recursive:
        files = [str(f) for f in p.rglob('*') if f.is_file()]
    else:
        files = [str(f) for f in p.iterdir()]
    return '\n'.join(sorted(files))


def find_files(path: str = '.', pattern: str = '*.py') -> str:
    """Find files matching a glob pattern.

    Args:
        path: Directory to search
        pattern: Glob pattern (e.g., '*.py')

    Returns:
        Newline-separated list of matching file paths
    """
    files = [str(f) for f in Path(path).rglob(pattern)]
    return '\n'.join(sorted(files))


# Skill metadata
__skill_name__ = "file_ops"
__skill_description__ = "File operations: read, write, list, find files"
__skill_commands__ = ["read_file", "write_file", "list_dir", "find_files"]
