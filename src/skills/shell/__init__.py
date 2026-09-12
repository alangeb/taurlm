"""shell skill: safe subprocess + background tmux helpers.

Importable: `from skills.shell import run, run_bg, check_bg, which`.
"""
import subprocess


def run(cmd, timeout=30):
    """Safe subprocess execution -> {rc, out, err}."""
    r = subprocess.run(cmd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}


def run_bg(cmd, session="task"):
    """Start a background task via tmux; returns the session name."""
    cmd_str = " ".join(cmd)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, cmd_str],
                   stdin=subprocess.DEVNULL, timeout=10)
    return session


def check_bg(session="task"):
    """Capture a tmux background session's pane output."""
    r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
    return r.stdout.strip()


def which(binary):
    """Return the resolved path of *binary*, or None."""
    r = subprocess.run(["which", binary], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=5)
    return r.stdout.strip() if r.returncode == 0 else None
