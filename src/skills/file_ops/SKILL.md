---
name: file-ops
description: 'Read, write, list, and find files in the filesystem. Use when doing file/path operations. Keywords: file, read, write, list, directory, find, glob, path.'
category: system
keywords: 'file-ops, file, read, write, list, directory, find, glob, path'
---

# File Operations Skill

Read, write, list, and find files in the filesystem.


## Functions

- `read_file(path, lines=100)` — Read a file and return content (limited to N lines)
- `write_file(path, content)` — Write content to a file
- `list_dir(path, recursive=False)` — List directory contents
- `find_files(path, pattern='*.py')` — Find files matching a glob pattern

## Example

> **Import footgun:** loading this skill only PRINTS the doc (rlm/skills.py:_load_skill_into_namespace_inner) — nothing is auto-imported. Call the functions only after `from skills.<pkg> import ...` (verified: `PYTHONPATH=src`).


```python
from skills.file_ops import read_file, write_file, list_dir, find_files

# Read a file
content = read_file("README.md", lines=50)

# Write a file
write_file("output.txt", "Hello, world!")

# List directory
files = list_dir(".", recursive=True)

# Find Python files
py_files = find_files("src/", "*.py")
```

## Related Skills
- `text-utils` — transform content after reading it
- `data-processing` — JSON/CSV parsing
- `shell` — when a subprocess tool beats a Python loop
