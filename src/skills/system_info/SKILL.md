---
name: system-info
description: 'Get system information: OS, Python, disk usage, environment variables. Use when checking OS/env/disk facts. Keywords: system, os, python, disk, env, environment, info, platform.'
category: system
keywords: 'system, os, python, disk, env, environment, info, platform'
---

# System Info Skill

Get system information: OS, Python, disk usage, environment variables.


## Functions

- `get_os_info()` — Get OS information (system, release, machine)
- `get_python_info()` — Get Python information (version, executable, path)
- `get_disk_usage(path='/')` — Get disk usage for a path
- `get_env_vars(prefix=None)` — Get environment variables (optionally filtered)
- `get_cwd()` — Get current working directory
- `get_memory_info()` / `get_cpu_info()` — RAM / CPU stats
- `run_command(cmd)` — `shell=True`, timeout hardcoded 30s (NO timeout kwarg, NO `stdin=DEVNULL` — see __init__.py:129-144); prefer the `shell` skill's wrapper
- `run(action="summary", **kwargs)` — combined snapshot; valid actions: `summary|disk_usage|memory|cpu|env|run` (NOT `os`/`python`/`cwd` — those are the standalone getters only)

## Example

```python
from skills.system_info import get_os_info, get_python_info, get_disk_usage, get_env_vars, get_cwd

# Get OS info
os_info = get_os_info()

# Get Python info
py_info = get_python_info()

# Get disk usage
disk = get_disk_usage('/')

# Get environment variables
env = get_env_vars(prefix='TAU')

# Get current directory
cwd = get_cwd()
```

> **Import footgun:** loading this skill only PRINTS the doc (rlm/skills.py:_load_skill_into_namespace_inner) — nothing is auto-imported. Call the functions only after `from skills.<pkg> import ...` (verified: `PYTHONPATH=src`).

## Related Skills
- `shell` — run arbitrary commands (this skill is facts-only)
- `file-ops` — filesystem contents
- `debug` — env/GPU facts when a run behaves oddly
