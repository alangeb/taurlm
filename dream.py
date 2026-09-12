#!/usr/bin/env python3
"""dream.py — Programmatic orchestrator for Tau self-improvement loop.

Replaces dream.sh + _dream prompt with explicit code. Handles all deterministic
operations (file ops, git, testing, timeout, logging) and invokes tau.py only
for LLM-driven work.

Usage:
    ./dream.sh [--n N] [--llm MODEL] [--dry-run] [--agent PATH]    ← via tmux wrapper (recommended)
    python3 dream.py [--n N] [--llm MODEL]                         ← direct (no tmux, not recommended)

Options:
    --n N          Number of cycles (0 = infinite, default)
    --llm MODEL    LLM group (default: cuda)
    --dry-run      Skip all LLM invocations, simulate everything
    --agent PATH   Path to agent binary (default: src/tau.py)
"""

import argparse
import atexit
import json
import fcntl
import os
import select
import shutil
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import List

# ─── Constants ───────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR / "src"
TASKS_DIR = SCRIPT_DIR / "tasks"
LOG_FILE = SCRIPT_DIR / "dream.log"
STOP_FILE = SCRIPT_DIR / "dream.stop"
PID_FILE = SCRIPT_DIR / "dream.pid"

READ_POLL_SECONDS = 5  # how often to check shutdown flag while reading subprocess output

# ─── Single-instance lock ────────────────────────────────────────────────────

def _pid_exists(pid: int) -> bool:
    """Return True if a process with the given PID is running."""
    try:
        os.kill(pid, 0)  # sends no signal, just checks existence
        return True
    except (OSError, ProcessLookupError):
        return False


_lock_fh = None  # open handle — must stay open for the lifetime of the lock


def acquire_lock() -> bool:
    """Acquire single-instance lock via fcntl.flock (kernel-held, no TOCTOU race).

    Returns True if acquired, False if another instance holds the lock.
    The fd stays open for process lifetime; the OS releases it on exit.
    """
    global _lock_fh
    fh = open(PID_FILE, "a+")  # create if missing; do NOT truncate (would race)
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        try:
            pid = PID_FILE.read_text().strip() or "unknown"
        except OSError:
            pid = "unknown"
        print(f"ERROR: dream.py already running (PID {pid})", file=sys.stderr)
        return False
    # We hold the lock — rewrite PID under the lock.
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    os.fsync(fh.fileno())
    _lock_fh = fh
    return True


def release_lock():
    """Release single-instance lock."""
    global _lock_fh
    if _lock_fh is not None:
        try:
            fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
            _lock_fh.close()
        except OSError:
            pass
        _lock_fh = None
    PID_FILE.unlink(missing_ok=True)


# ─── Signal Handling ─────────────────────────────────────────────────────────

class ShutdownControl:
    """Graceful shutdown: SIGINT finishes current step, SIGTERM force-kills.

    Also tracks SIGHUP (terminal close) and SIGQUIT (Ctrl+\\) for observability.
    """

    def __init__(self):
        self.graceful_requested = False
        self.force_requested = False
        self.signal_received: str | None = None  # Track which signal triggered shutdown

    def handle_sigint(self, signum, frame):
        self.graceful_requested = True
        self.signal_received = "SIGINT"

    def handle_sigterm(self, signum, frame):
        self.force_requested = True
        self.signal_received = "SIGTERM"

    def handle_sighup(self, signum, frame):
        """Terminal closed or parent process died."""
        self.graceful_requested = True
        self.signal_received = "SIGHUP"

    def handle_sigquit(self, signum, frame):
        """Ctrl+\\ — treat as force shutdown."""
        self.force_requested = True
        self.signal_received = "SIGQUIT"

    def check(self):
        """Return True if any shutdown was requested."""
        return self.graceful_requested or self.force_requested

    def was_requested(self):
        """Return type of shutdown requested."""
        if self.force_requested:
            return "force"
        if self.graceful_requested:
            return "graceful"
        return None

    def description(self) -> str:
        """Return human-readable shutdown reason for logging."""
        if self.signal_received:
            kind = "force" if self.force_requested else "graceful"
            return f"{kind} shutdown via {self.signal_received}"
        return "unknown shutdown"


shutdown = ShutdownControl()


def setup_signals():
    signal.signal(signal.SIGINT, shutdown.handle_sigint)
    signal.signal(signal.SIGTERM, shutdown.handle_sigterm)
    signal.signal(signal.SIGHUP, shutdown.handle_sighup)
    signal.signal(signal.SIGQUIT, shutdown.handle_sigquit)


# ─── Logger ──────────────────────────────────────────────────────────────────

class Logger:
    """Dual output: terminal + dream.log. Tracks timing."""

    def __init__(self, log_path: Path, dry_run: bool = False):
        self.log_path = log_path
        self.dry_run = dry_run
        self.start_time = time.time()
        # Rotate previous log (preserve crash data from SIGKILL'd runs)
        if log_path.exists() and log_path.stat().st_size > 0:
            old_log = log_path.with_suffix(".log.old")
            try:
                shutil.move(str(log_path), str(old_log))
            except OSError:
                log_path.write_text("")  # Fallback: just clear
        log_path.write_text("")

    def _elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _fmt(self, prefix: str, msg: str) -> str:
        ts = time.strftime("%H:%M:%S")
        elapsed = self._elapsed()
        line = f"[{ts}] +{elapsed} {prefix} {msg}"
        return line

    def log(self, prefix: str, msg: str):
        line = self._fmt(prefix, msg)
        print(line, flush=True)
        self._write_log(line + "\n")

    def _write_log(self, text: str):
        with open(self.log_path, "a") as f:
            f.write(text)

    def header(self, title: str):
        sep = "=" * 70
        for line in [sep, title, sep]:
            formatted = self._fmt("", line)
            print(formatted, flush=True)
            self._write_log(formatted + "\n")

    def step_result(self, step: str, status: str, elapsed: float):
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        time_str = f"{h:02d}:{m:02d}:{s:02d}"
        icon = {"PASS": "✅", "FAIL": "❌", "TIMEOUT": "⏰", "SKIP": "⏭️", "REVERT": "↩️"}.get(status, "?")
        self.log(f"[{icon} {step}]", f"{status} ({time_str})")


# ─── Git Helpers ─────────────────────────────────────────────────────────────

class GitHelper:
    """Git operations: status, commit, revert."""

    def __init__(self, cwd: Path, logger: Logger, dry_run: bool = False):
        self.cwd = cwd
        self.log = logger
        self.dry_run = dry_run

    def _run(self, cmd: list, capture: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=str(self.cwd),
            capture_output=capture,
            text=True,
            timeout=30,
        )

    def is_clean(self) -> bool:
        """Check if working tree is clean (no uncommitted changes)."""
        try:
            r = self._run(["git", "status", "--porcelain"])
            return r.stdout.strip() == ""
        except Exception:
            return False

    def get_status_short(self) -> str:
        r = self._run(["git", "status", "--short"])
        return r.stdout.strip()

    def commit(self, msg: str) -> bool:
        """Commit all changes in src/. Returns True on success."""
        if self.dry_run:
            self.log.log("[DRY-RUN]", f"would commit: {msg}")
            return True
        # Scope to src/ — 'git add -A .' only stages files under cwd
        r_add = self._run(["git", "add", "-A", "."])
        if r_add.returncode != 0:
            self.log.log("[git-ERROR]", f"git add failed: {r_add.stderr.strip()}")
            return False
        r_commit = self._run(["git", "commit", "-m", msg])
        if r_commit.returncode != 0:
            self.log.log("[git-ERROR]", f"git commit failed: {r_commit.stderr.strip()}")
            return False
        self.log.log("[git]", f"committed: {msg}")
        return True

    def revert(self) -> bool:
        """Revert all uncommitted changes in src/. Returns True on success."""
        if self.dry_run:
            self.log.log("[DRY-RUN]", "would revert all changes in src/")
            return True
        # Scope to src/ — explicit pathspec prevents repo-wide damage
        r_checkout = self._run(["git", "checkout", "--", "."])
        if r_checkout.returncode != 0:
            self.log.log("[git-ERROR]", f"git checkout failed: {r_checkout.stderr.strip()}")
            return False
        r_clean = self._run(["git", "clean", "-fd", "."])
        if r_clean.returncode != 0:
            self.log.log("[git-ERROR]", f"git clean failed: {r_clean.stderr.strip()}")
            return False
        self.log.log("[git]", "reverted all changes in src/")
        return True

    # ─── Step Result ─────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    name: str
    success: bool
    timed_out: bool = False
    elapsed: float = 0.0
    detail: str = ""


class DreamState:
    """Structured cross-cycle memory persisted as JSON.

    Tracks rearch areas, test results, and cycle stats to enable
    convergence detection and adaptive prioritization.
    """
    STATE_FILE = SCRIPT_DIR / "dream_state.json"

    def __init__(self):
        self.data = {
            "total_cycles": 0,
            "total_committed": 0,
            "total_reverted": 0,
            "last_rearch_areas": [],
            "rearch_area_history": {},
            "last_test_failures": [],
            "last_health_score": None,
            "last_doc_sync": None,
        }
        self._load()

    def _load(self):
        try:
            if self.STATE_FILE.exists():
                self.data.update(json.loads(self.STATE_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass

    def save(self):
        try:
            self.STATE_FILE.write_text(json.dumps(self.data, indent=2))
        except OSError:
            pass

    def record_cycle(self, results: list, cycle_num: int):
        self.data["total_cycles"] = cycle_num
        for r in results:
            if r.success and r.detail == "committed":
                self.data["total_committed"] += 1
            elif not r.success:
                self.data["total_reverted"] += 1

    def record_rearch_area(self, area: str, cycle_num: int):
        self.data["last_rearch_areas"].append(area)
        self.data["rearch_area_history"].setdefault(area, []).append(cycle_num)
        self.data["last_rearch_areas"] = self.data["last_rearch_areas"][-10:]

    def rearch_convergence(self, area: str) -> int:
        history = self.data.get("rearch_area_history", {}).get(area, [])
        last_5 = [c for c in history if c >= self.data["total_cycles"] - 5]
        return len(last_5)

    def should_skip_rearch(self) -> bool:
        for area, cycles in self.data.get("rearch_area_history", {}).items():
            recent = [c for c in cycles if c >= self.data["total_cycles"] - 5]
            if len(recent) >= 3:
                return True
        return False

    def budget_remaining(self, max_minutes: int, elapsed: float) -> float:
        if max_minutes <= 0:
            return 1.0
        total = max_minutes * 60
        return max(0.0, 1.0 - (elapsed / total))




# ─── Main ────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Dream loop — programmatic Tau self-improvement orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    dream.py                  # run forever, spark LLM
    dream.py --n 3           # 3 cycles
    dream.py --llm deepseek  # use deepseek
    dream.py --dry-run       # simulate without LLM calls
    dream.py --n 1 --dry-run # single dry-run cycle
    dream.py --agent /path/to/tau.py  # use custom agent binary
""",
    )
    p.add_argument("--n", type=int, default=0, help="Number of cycles (0 = infinite, default)")
    p.add_argument("--llm", default="cuda", help="LLM group (default: cuda)")
    p.add_argument("--dry-run", action="store_true", help="Skip LLM invocations, simulate everything")
    p.add_argument("--agent", type=str, default=None, help="Path to agent binary (default: src/tau.py)")
    p.add_argument("--max-cycle-minutes", type=int, default=0,
                   help="Max minutes per cycle (0 = unlimited). Lower-priority steps skipped when budget low.")
    return p.parse_args()


def main():
    # Lazy import to avoid circular dependency (dream.py runs as __main__)
    from dream_steps import ensure_tasks_dirs, run_cycle

    args = parse_args()
    setup_signals()

    # Resolve agent binary path
    agent_bin = Path(args.agent) if args.agent else SRC_DIR / "tau.py"
    if not args.dry_run and not agent_bin.exists():
        print(f"ERROR: Agent binary not found: {agent_bin}", file=sys.stderr)
        sys.exit(1)

    # Acquire single-instance lock
    if not acquire_lock():
        sys.exit(1)

    # Setup logging
    logger = Logger(LOG_FILE, dry_run=args.dry_run)
    mode = " [DRY-RUN]" if args.dry_run else ""
    logger.header(f"dream.py started{mode} (llm={args.llm}, agent={agent_bin}, cycles={'inf' if args.n == 0 else args.n})")
    logger.log("[info]", f"cwd: {os.getcwd()}")
    logger.log("[info]", f"src: {SRC_DIR}")
    logger.log("[info]", f"tasks: {TASKS_DIR}")

    # Atexit handler — log ANY exit (normal, signal, exception)
    _exit_cycle = [0]  # Mutable container for atexit closure
    def _log_exit():
        """Log exit reason — called by atexit on any exit path."""
        try:
            elapsed = time.time() - logger.start_time
            h, rem = divmod(int(elapsed), 3600)
            m, s = divmod(rem, 60)
            reason = "unknown"
            if shutdown.signal_received:
                reason = shutdown.description()
            elif _exit_cycle[0] > 0 and args.n > 0 and _exit_cycle[0] >= args.n:
                reason = f"completed {_exit_cycle[0]} cycles"
            elif STOP_FILE.exists():
                reason = "stop file detected"
            logger.log("[exit]", f"dream.py exiting: {reason} (cycles={_exit_cycle[0]}, time={h:02d}:{m:02d}:{s:02d})")
        except Exception:
            pass  # atexit must not raise
    atexit.register(_log_exit)

    # Ensure task directories exist
    ensure_tasks_dirs(dry_run=args.dry_run)

    # Setup git
    git = GitHelper(SRC_DIR, logger, dry_run=args.dry_run)

    # Pre-flight: check git is clean
    if not args.dry_run:
        if not git.is_clean():
            status = git.get_status_short()
            logger.log("[ERROR]", f"Git not clean. Refusing to start.\n{status}")
            release_lock()
            sys.exit(1)
        logger.log("[git]", "working tree clean")

    # Cross-cycle state
    state = DreamState()

    cycle = 0
    try:
        while True:
            # Check stop file
            if STOP_FILE.exists():
                logger.log("[stop]", f"stop file detected: {STOP_FILE}")
                break

            cycle += 1
            _exit_cycle[0] = cycle  # Update for atexit handler
            results = run_cycle(cycle, logger, git, args.llm, args.dry_run, agent_bin, state=state, max_cycle_minutes=args.max_cycle_minutes)

            # Summary
            passed = sum(1 for r in results if r.success)
            total = len(results)
            failed = [r for r in results if not r.success]
            if failed:
                logger.log("[summary]", f"Failed steps: {', '.join(r.name for r in failed)}")

            logger.log("[cycle]", f"Cycle {cycle}: {passed}/{total} passed")

            # Persist cross-cycle state
            state.record_cycle(results, cycle)
            state.save()

            # Check limits
            if args.n > 0 and cycle >= args.n:
                logger.log("[done]", f"Reached {args.n} cycles")
                break

            # Check shutdown
            if shutdown.check():
                logger.log("[shutdown]", f"{shutdown.description()} — exiting")
                break

            # Brief pause between cycles
            if not args.dry_run:
                logger.log("[wait]", "pausing 10s before next cycle...")
                time.sleep(10)
    except Exception:
        tb = traceback.format_exc()
        logger.log("[CRASH]", f"Unhandled exception after {cycle} cycle(s):\n{tb}")
        # Also print to stderr so it's visible in terminal
        print(f"\n!!! DREAM CRASH (logged to {LOG_FILE}):\n{tb}", file=sys.stderr, flush=True)
        release_lock()
        sys.exit(1)

    # Final summary
    logger.header("Dream loop ended")
    logger.log("[total]", f"Completed {cycle} cycles")
    h, rem = divmod(int(time.time() - logger.start_time), 3600)
    m, s = divmod(rem, 60)
    logger.log("[total]", f"Total time: {h:02d}:{m:02d}:{s:02d}")
    logger.log("[log]", f"Full log: {LOG_FILE}")

    # Release lock on clean exit
    release_lock()


if __name__ == "__main__":
    main()
