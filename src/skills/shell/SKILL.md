---
name: shell
description: 'Safe subprocess and shell command execution — run, timeout, background tmux, check exit codes, which, env. Use when running shell/subprocess commands safely. Keywords: subprocess, shell, command, run, execute, tmux, background, timeout, stdin, DEVNULL, pipe, capture'
category: system
keywords: 'subprocess, shell, command, run, execute, tmux, background, timeout, stdin, DEVNULL, pipe, capture'
---

# shell

## When to Use
- Running builds, installs, system commands
- Checking if a binary exists
- Running long tasks that exceed 180s
- Any operation that isn't pure Python

## What to Do
1. ALWAYS set `stdin=subprocess.DEVNULL` — prevents terminal blocking
2. ALWAYS set `timeout` (default 30s, max 3600s)
3. NEVER call `input()` or blocking reads without timeout
4. For tasks > 180s: use `tmux new-session -d` + poll
5. Check `returncode` before using stdout
6. Use `capture_output=True, text=True` for clean results

## Code Examples
```python
import subprocess

def run(cmd: list, timeout=30) -> dict:
    """Safe subprocess execution."""
    r = subprocess.run(cmd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}

def run_bg(cmd: list, session="task") -> str:
    """Background task via tmux."""
    cmd_str = " ".join(cmd)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, cmd_str],
                   stdin=subprocess.DEVNULL, timeout=10)
    return session

def check_bg(session="task") -> str:
    """Check background task output."""
    r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
    return r.stdout.strip()

def which(binary: str) -> str | None:
    r = subprocess.run(["which", binary], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=5)
    return r.stdout.strip() if r.returncode == 0 else None
```

> **Importable:** `from skills.shell import run, run_bg, check_bg, which` (same signatures as above). Loading the skill only PRINTS the doc; nothing auto-imports.

## Related Skills
- `test-runner` — subprocess folding pattern for pytest
- `spawn` — delegate long command runs to keep your context lean
- `environment` — project config, .venv interpreter, TAU_ env vars.
