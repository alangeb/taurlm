"""dream_steps.py — Step functions and prompts for the dream orchestrator.

Split from dream.py. Contains all LLM-invoking step functions, their helpers,
and the embedded prompt templates.
"""

import fcntl
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import List

from prompts import get_prompt

from dream import (
    Logger,
    GitHelper,
    StepResult,
    DreamState,
    ShutdownControl,
    shutdown,
    SCRIPT_DIR,
    SRC_DIR,
    TASKS_DIR,
)

__all__ = [
    "TAU_RETRY_COUNT",
    "TAU_RETRY_DELAY",
    "TIMEOUT_SECONDS",
    "PROMPT_VERSION",
    "READ_POLL_SECONDS",
    "PROMPT_DOTASK",
    "PROMPT_REARCH",
    "PROMPT_TESTSANITY",
    "PROMPT_SKILLMAINTENANCE",
    "PROMPT_DOC",
    "_strip_yaml_frontmatter",
    "run_tau",
    "_terminate_subprocess",
    "parse_task_frontmatter",
    "has_modified_files",
    "run_tests",
    "_append_failure_note",
    "ensure_tasks_dirs",
    "get_todo_files",
    "move_task",
    "step_process_tasks",
    "step_rearch",
    "step_single",
    "_run_tau_and_test",
    "_commit_or_revert",
    "step_log_rotate",
    "step_test_sanity",
    "step_skill_maintenance",
    "step_doc_sync",
    "run_health_check",
    "run_cycle",
]

# ─── Constants (moved from dream.py) ─────────────────────────────────────────

TIMEOUT_SECONDS = 6 * 3600  # 6 hours per step
PROMPT_VERSION = "2026-09-12-v2"  # Bump when prompts change — logged per step
READ_POLL_SECONDS = 5  # how often to check shutdown flag while reading subprocess output
# ─── Tau Runner ──────────────────────────────────────────────────────────────

TAU_RETRY_COUNT = 2  # retries on crash (not timeout)
TAU_RETRY_DELAY = 30  # seconds between retries



# Full command prompts embedded here to avoid slash-command lookup.
# Sourced from tau/commands/*.md with nested slash commands inlined.
# YAML frontmatter is stripped before passing as positional args to tau.py.

PROMPT_DOTASK = get_prompt("dotask")

PROMPT_REARCH = get_prompt("rearch")

PROMPT_TESTSANITY = get_prompt("testsanity")

PROMPT_SKILLMAINTENANCE = get_prompt("skillmaintenance")

PROMPT_DOC = get_prompt("doc")

def _strip_yaml_frontmatter(text: str) -> str:
    """Strip YAML frontmatter (--- ... ---) from prompt text."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i+1:]).strip()
    return text.strip()


def run_tau(
    command: str,
    llm_group: str,
    logger: Logger,
    dry_run: bool,
    agent_bin: Path,
    retry_count: int = TAU_RETRY_COUNT,
    retry_delay: int = TAU_RETRY_DELAY,
    prompt: str | None = None,
) -> subprocess.CompletedProcess:
    """Run agent with a command, streaming output to terminal + log.

    Uses select() to poll the output pipe so shutdown signals can be handled
    during long-running agent invocations. Checks shutdown.check() every
    READ_POLL_SECONDS.

    On crash (non-zero exit), retries up to retry_count times with retry_delay
    between attempts. Timeouts are NOT retried (command is stuck).

    Returns CompletedProcess. Raises subprocess.TimeoutExpired on timeout.
    """
    if dry_run:
        logger.log("[DRY-RUN]", f"would run: {agent_bin.name} --llm {llm_group} {command}")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="DRY-RUN: skipped", stderr="")

    # Build command: agent_bin --llm GROUP PROMPT1 [PROMPT2 ...]
    # Prompts are passed as positional arguments (each becomes a separate user message)
    if prompt:
        # Strip YAML frontmatter, split on double-newlines for multi-prompt support
        prompt_text = _strip_yaml_frontmatter(prompt)
        cmd = [str(agent_bin), "--llm", llm_group, prompt_text]
        logger.log("[tau]", f"running with prompt (command: {command})")
    else:
        cmd = [str(agent_bin), "--llm", llm_group, command]

    for attempt in range(retry_count + 1):
        if attempt > 0:
            logger.log("[retry]", f"tau.py retry {attempt}/{retry_count} after {retry_delay}s delay (command: {command})")
            time.sleep(retry_delay)

        logger.log("[tau]", f"running (attempt {attempt+1}/{retry_count+1}): {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd,
            cwd=str(SRC_DIR),
            stdin=subprocess.DEVNULL,  # FIX: don't inherit stdin — prevents blocking on interactive reads
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # FIX: new process group so we can kill entire tree
        )

        logger.log("[tau]", f"started tau.py (PID {proc.pid}) — waiting for completion...")

        # Stream output live using select() so we can check for shutdown signals
        output_lines = []
        stdout_fd = proc.stdout.fileno()

        # FIX: Set the fd to non-blocking mode so os.read() never blocks.
        # This prevents readline() from hanging on partial lines (no newline).
        old_flags = fcntl.fcntl(stdout_fd, fcntl.F_GETFL)
        fcntl.fcntl(stdout_fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

        # Buffer for partial lines across reads
        partial_buf = ""

        try:
            deadline = time.time() + TIMEOUT_SECONDS
            while True:
                # Check shutdown before each poll
                if shutdown.check():
                    logger.log("[shutdown]", f"{shutdown.was_requested()} shutdown requested — terminating tau.py (PID {proc.pid})")
                    _terminate_subprocess(proc, logger)
                    # Drain any remaining output
                    remaining = proc.stdout.read()
                    if remaining:
                        for line in remaining.rstrip("\n").split("\n"):
                            line = line.rstrip("\n")
                            output_lines.append(line)
                            print(f"  {line}", flush=True)
                            logger._write_log(f"  {line}\n")
                    raise subprocess.TimeoutExpired(cmd, 0, "\n".join(output_lines))

                # Use select to wait for output with a timeout
                # Wall-clock timeout check
                if time.time() > deadline:
                    logger.log("[timeout]", f"tau.py (PID {proc.pid}) exceeded {TIMEOUT_SECONDS}s — killing")
                    _terminate_subprocess(proc, logger)
                    raise subprocess.TimeoutExpired(cmd, TIMEOUT_SECONDS, "\n".join(output_lines))

                try:
                    ready, _, _ = select.select([stdout_fd], [], [], READ_POLL_SECONDS)
                except (ValueError, OSError):
                    # File descriptor was closed (process exited)
                    break

                if not ready:
                    # No output for READ_POLL_SECONDS — loop back to check shutdown
                    continue

                # FIX: Use non-blocking os.read() instead of readline().
                # readline() can block indefinitely if data has no newline,
                # even after select() says the fd is ready.
                eof = False
                while True:
                    try:
                        data = os.read(stdout_fd, 65536)
                    except BlockingIOError:
                        # No more data available right now — not EOF
                        break
                    except OSError:
                        # Pipe closed — treat as EOF
                        eof = True
                        break

                    if not data:
                        # EOF — process closed stdout
                        eof = True
                        break

                    # Decode and append to buffer
                    text = data.decode("utf-8", errors="replace")
                    partial_buf += text

                    # Split on newlines and emit complete lines
                    while "\n" in partial_buf:
                        line, _, partial_buf = partial_buf.partition("\n")
                        output_lines.append(line)
                        print(f"  {line}", flush=True)
                        logger._write_log(f"  {line}\n")

                # Only break outer loop on true EOF
                if eof:
                    break

            # Flush any remaining partial line (no trailing newline)
            if partial_buf:
                output_lines.append(partial_buf)
                print(f"  {partial_buf}", flush=True)
                logger._write_log(f"  {partial_buf}\n")
                partial_buf = ""

            # Restore original flags (cleanup)
            fcntl.fcntl(stdout_fd, fcntl.F_SETFL, old_flags)

            # FIX: Use short timeout for proc.wait() — the select loop already
            # drained all output. If the process hasn't exited by now, it's stuck.
            WAIT_AFTER_EOF = 30  # seconds
            try:
                proc.wait(timeout=WAIT_AFTER_EOF)
            except subprocess.TimeoutExpired:
                logger.log("[timeout]", f"tau.py (PID {proc.pid}) didn't exit after {WAIT_AFTER_EOF}s — killing process group")
                _terminate_subprocess(proc, logger)
                raise subprocess.TimeoutExpired(cmd, 0, "\n".join(output_lines))

            logger.log("[tau]", f"tau.py (PID {proc.pid}) exited with code {proc.returncode}")

            if proc.returncode == 0:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=proc.returncode,
                    stdout="\n".join(output_lines),
                )

            # Non-zero exit — log crash details
            logger.log("[crash]", f"tau.py exited with code {proc.returncode} (attempt {attempt+1}/{retry_count+1})")
            # Log last 10 lines of output for debugging
            for line in output_lines[-10:]:
                logger.log("[crash-output]", line)

            # Don't retry on last attempt
            if attempt >= retry_count:
                logger.log("[crash]", f"tau.py failed after {retry_count+1} attempts — giving up")
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=proc.returncode,
                    stdout="\n".join(output_lines),
                )

        except subprocess.TimeoutExpired:
            raise
        except Exception:
            # Ensure entire process group is cleaned up on any unexpected error
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
                proc.wait()
            raise


def _terminate_subprocess(proc: subprocess.Popen, logger: Logger):
    """Terminate a subprocess and its entire process group: SIGTERM first, SIGKILL after 10s."""
    if proc.poll() is not None:
        return  # already exited
    try:
        # Kill the entire process group (start_new_session=True creates a new PGID)
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        logger.log("[shutdown]", f"sent SIGTERM to process group {proc.pid}")
    except OSError as e:
        # Process group may not exist (process already died)
        logger.log("[shutdown]", f"SIGTERM failed: {e}, trying direct terminate")
        try:
            proc.terminate()  # SIGTERM fallback
        except OSError:
            return
    # Wait up to 10 seconds for graceful exit
    for _ in range(100):
        if proc.poll() is not None:
            logger.log("[shutdown]", f"tau.py (PID {proc.pid}) terminated gracefully")
            return
        time.sleep(0.1)
    # Force kill if still alive
    logger.log("[shutdown]", f"tau.py (PID {proc.pid}) did not terminate — sending SIGKILL to process group")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def parse_task_frontmatter(task_path: Path) -> dict:
    """Parse YAML-like frontmatter from a task file. Returns dict of key:value pairs."""
    result = {}
    try:
        content = task_path.read_text()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 2:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        result[k.strip().lower()] = v.strip()
    except Exception:
        pass
    return result


def has_modified_files(cwd: Path) -> bool:
    """Check if git has any modified (not just added) files."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().split("\n"):
            if not line:
                continue
            # Status format: "XY filename" where X = index, Y = working tree
            # M = modified, A = added, D = deleted, etc.
            status = line[:2].strip()
            if "M" in status or "D" in status or "R" in status:
                return True
        return False
    except Exception:
        return True  # default to running sanity if we can't check


def _append_failure_note(task_file: "Path | None", header: str, detail: str) -> None:
    """Persist failure evidence into the task file so the next attempt sees WHY.

    Task files live under tasks/ (gitignored, outside src/), so notes survive
    the git revert that follows a failed step.
    """
    if task_file is None or not task_file.exists():
        return
    try:
        with open(task_file, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n## Dream failure report ({time.strftime('%Y-%m-%d %H:%M:%S')})\n\n")
            f.write(f"**Failure:** {header}\n\n```\n{detail.strip()}\n```\n")
    except OSError:
        pass


def run_tests(logger: Logger, dry_run: bool, skip_sanity: bool = False,
              task_file: "Path | None" = None) -> tuple:
    """Run pytest + sanity.sh. Returns (pytest_ok, sanity_ok).

    If task_file is given, failure evidence (tail of failing output) is appended
    to it BEFORE any subsequent revert, so the next cycle's agent can see what
    broke instead of starting from zero.
    """
    if dry_run:
        logger.log("[DRY-RUN]", "would run: pytest + sanity.sh")
        return True, True

    # Run pytest
    logger.log("[test]", "running pytest...")
    pytest_ok = False
    try:
        r = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--tb=short", "-q"],
            cwd=str(SRC_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        pytest_ok = r.returncode == 0
        logger.log("[test]", f"pytest: {'PASS' if pytest_ok else 'FAIL'} (exit={r.returncode})")
        if not pytest_ok:
            tail = "\n".join(r.stdout.strip().split("\n")[-60:])
            for line in r.stdout.strip().split("\n")[-20:]:
                logger.log("[pytest]", line)
            _append_failure_note(task_file, f"pytest failed (exit={r.returncode})", tail)
    except subprocess.TimeoutExpired:
        logger.log("[test]", "pytest: TIMEOUT")
        pytest_ok = False
        _append_failure_note(task_file, "pytest TIMEOUT (>300s)", "pytest exceeded 300s and was killed.")

    # Run sanity.sh (skip if requested or only new files were created)
    if skip_sanity:
        logger.log("[test]", "sanity: SKIPPED (skip_sanity flag set)")
        return pytest_ok, True
    if not has_modified_files(SRC_DIR):
        logger.log("[test]", "sanity: SKIPPED (no modified files, new files only)")
        return pytest_ok, True
    logger.log("[test]", "running sanity.sh...")
    sanity_ok = False
    try:
        r = subprocess.run(
            ["bash", "sanity.sh"],
            cwd=str(SRC_DIR),
            capture_output=True,
            text=True,
            timeout=1800,
        )
        sanity_ok = r.returncode == 0
        logger.log("[test]", f"sanity: {'PASS' if sanity_ok else 'FAIL'} (exit={r.returncode})")
        if not sanity_ok:
            tail = "\n".join(r.stdout.strip().split("\n")[-60:])
            for line in r.stdout.strip().split("\n")[-20:]:
                logger.log("[sanity]", line)
            _append_failure_note(task_file, f"sanity.sh failed (exit={r.returncode})", tail)
    except subprocess.TimeoutExpired:
        logger.log("[test]", "sanity: TIMEOUT")
        sanity_ok = False
        _append_failure_note(task_file, "sanity.sh TIMEOUT (>1800s)", "sanity.sh exceeded 1800s and was killed.")

    return pytest_ok, sanity_ok


# ─── Task Management ─────────────────────────────────────────────────────────

def ensure_tasks_dirs(dry_run: bool = False):
    """Create task directories if they don't exist."""
    for subdir in ["1_todo", "2_inprogress", "3_done", "3_failed"]:
        d = TASKS_DIR / subdir
        if not d.exists():
            if dry_run:
                print(f"  [DRY-RUN] would create: {d}")
            else:
                d.mkdir(parents=True, exist_ok=True)


def get_todo_files() -> List[Path]:
    """Get .md files from 1_todo, sorted by priority (high > medium > low > none)."""
    files = list(TASKS_DIR.glob("1_todo/*.md"))
    priority_order = {"high": 0, "medium": 1, "low": 2}
    def _priority(f: Path) -> tuple:
        try:
            fm = parse_task_frontmatter(f)
            p = fm.get("priority", "none").lower()
            return (priority_order.get(p, 3), f.name)
        except Exception:
            return (3, f.name)
    return sorted(files, key=_priority)


def move_task(src: Path, dest_subdir: str):
    """Move a task file to a subdirectory."""
    dest = TASKS_DIR / dest_subdir / src.name
    shutil.move(str(src), str(dest))


# ─── Step Functions ──────────────────────────────────────────────────────────

def step_process_tasks(logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path) -> List[StepResult]:
    """Process all tasks in 1_todo: pick up, implement, test, commit/revert, move."""
    results = []
    files = get_todo_files()
    if not files:
        logger.log("[tasks]", "no tasks in 1_todo — skipping")
        results.append(StepResult("process_tasks", True, elapsed=0, detail="no tasks"))
        return results

    for f in files:
        if shutdown.check():
            break

        step_name = f"task:{f.stem}"
        t0 = time.time()

        # Parse task frontmatter for skip_sanity flag (BEFORE move — file must exist)
        fm = parse_task_frontmatter(f)
        skip_sanity = fm.get("skip_sanity", "false").lower() == "true"

        # Move to inprogress (skip in dry-run to avoid side effects)
        logger.log("[tasks]", f"picking up: {f.name}")
        if not dry_run:
            if not f.exists():
                logger.log("[tasks]", f"{f.name} no longer in 1_todo — skipping (already handled)")
                continue
            move_task(f, "2_inprogress")

        # Inject task filename into prompt (agent needs to know WHICH file)
        _TASK_TOKEN = "TASK: Execute the task file in ../tasks/2_inprogress/"
        if _TASK_TOKEN not in PROMPT_DOTASK:
            logger.log(
                "[tasks]",
                f"ERROR: PROMPT_DOTASK missing injection token for {f.name} "
                "— refusing silent no-op task injection",
            )
            raise ValueError(
                "PROMPT_DOTASK missing expected task token; task file was NOT injected"
            )
        task_prompt = PROMPT_DOTASK.replace(
            _TASK_TOKEN,
            f"TASK: Execute the task file: ../tasks/2_inprogress/{f.name}"
        )
        assert f.name in task_prompt, "task filename injection failed"

        # Run tau + tests (pass task_file so failure evidence is saved to it
        # BEFORE revert/move — tasks/ is gitignored so it survives the revert)
        task_file = TASKS_DIR / "2_inprogress" / f.name
        timed_out, elapsed, all_ok = _run_tau_and_test(
            step_name, "do_task", llm_group, logger, dry_run, agent_bin, task_prompt, t0,
            skip_sanity=skip_sanity, task_file=task_file
        )

        # Task-specific callbacks for commit/revert
        def on_done(tf=task_file):
            if tf.exists():
                move_task(tf, "3_done")
            else:
                logger.log("[tasks]", f"{f.name} already moved to 3_done (tau handled it)")
        def on_fail(tf=task_file):
            if tf.exists():
                move_task(tf, "3_failed")
            else:
                logger.log("[tasks]", f"{f.name} not in 2_inprogress — skipping move to 3_failed")

        results.append(_commit_or_revert(
            step_name, f"dream: task {f.stem}", git, logger, dry_run,
            timed_out, all_ok, elapsed, on_done=on_done, on_fail=on_fail
        ))

    return results


def _area_from_head(git: GitHelper) -> str | None:
    """I8: derive the dominant module "area" touched by the last commit.

    Uses the majority file stem from `git log -1 --name-only`; returns None
    if the commit touched no readable files.
    """
    try:
        r = git._run(["git", "log", "-1", "--name-only", "--pretty=format:"])
        files = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        return None
    if not files:
        return None
    stems = [Path(f).stem for f in files]
    return max(dict.fromkeys(stems), key=stems.count)


def step_rearch(logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path, n: int = 3, state: DreamState = None, cycle_num: int = 0) -> List[StepResult]:
    """Run re-architecture n times."""
    results = []
    for i in range(n):
        if shutdown.check():
            break

        step_name = f"rearch:{i+1}/{n}"
        t0 = time.time()
        logger.header(f"Step: {step_name}")

        timed_out, elapsed, all_ok = _run_tau_and_test(
            step_name, "rearch", llm_group, logger, dry_run, agent_bin, PROMPT_REARCH, t0
        )
        result = _commit_or_revert(
            step_name, f"dream: rearch {i+1}/{n}", git, logger, dry_run,
            timed_out, all_ok, elapsed, on_done=None, on_fail=None
        )
        # I8: record which area was rearched so convergence detection works.
        if state is not None and result.success and result.detail == "committed":
            area = _area_from_head(git)
            if area:
                state.record_rearch_area(area, cycle_num)
        results.append(result)

    return results


def step_single(logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path, command: str, step_name: str, prompt: str | None = None) -> StepResult:
    """Generic single-step: run tau command, test, commit/revert."""
    t0 = time.time()
    logger.header(f"Step: {step_name}")

    timed_out, elapsed, all_ok = _run_tau_and_test(
        step_name, command, llm_group, logger, dry_run, agent_bin, prompt, t0
    )
    return _commit_or_revert(step_name, f"dream: {step_name}", git, logger, dry_run, timed_out, all_ok, elapsed, on_done=None, on_fail=None)


def _run_tau_and_test(
    step_name: str, command: str, llm_group: str, logger: Logger,
    dry_run: bool, agent_bin: Path, prompt: str | None, t0: float,
    skip_sanity: bool = False, task_file: "Path | None" = None
) -> tuple:
    """Run tau, then tests. Returns (timed_out, elapsed, all_ok)."""
    tau_ok = False
    timed_out = False
    try:
        proc = run_tau(command, llm_group, logger, dry_run, agent_bin, prompt=prompt)
        tau_ok = (proc.returncode == 0)
    except subprocess.TimeoutExpired:
        logger.log("[timeout]", f"{step_name}: tau timed out after {TIMEOUT_SECONDS}s")
        timed_out = True
        _append_failure_note(
            task_file, f"tau.py TIMEOUT (>TIMEOUT_SECONDS={TIMEOUT_SECONDS}s)",
            "tau.py was killed on timeout; all src/ changes were reverted."
        )

    # Test (skip if tau timed out — tests would be meaningless)
    if timed_out:
        logger.log("[skip]", f"{step_name}: skipping tests after timeout")
        all_ok = False
    elif not dry_run:
        pytest_ok, sanity_ok = run_tests(logger, dry_run, skip_sanity, task_file=task_file)
        all_ok = tau_ok and pytest_ok and sanity_ok
    else:
        all_ok = True

    elapsed = time.time() - t0
    return timed_out, elapsed, all_ok


def _commit_or_revert(
    step_name: str, commit_msg: str, git: GitHelper, logger: Logger,
    dry_run: bool, timed_out: bool, all_ok: bool, elapsed: float,
    on_done, on_fail
) -> StepResult:
    """Commit on success, revert on failure. Log result. Returns StepResult."""
    if dry_run:
        logger.log("[DRY-RUN]", f"would commit: {commit_msg}")
        result = StepResult(step_name, True, elapsed=elapsed, detail="dry-run")
    elif all_ok:
        if git.commit(commit_msg):
            if on_done:
                on_done()
            result = StepResult(step_name, True, elapsed=elapsed, detail="committed")
        else:
            result = StepResult(step_name, False, elapsed=elapsed, detail="git_commit_failed")
    else:
        if not git.revert():
            logger.log("[git-ERROR]", f"{step_name}: revert FAILED — manual intervention needed")
        if on_fail:
            on_fail()
        status = "timeout" if timed_out else "test_fail"
        result = StepResult(step_name, False, elapsed=elapsed, detail=status)

    logger.step_result(step_name, "PASS" if all_ok else ("TIMEOUT" if timed_out else "FAIL"), elapsed)
    return result


# ─── Log Rotation (Deterministic — No LLM) ────────────────────────────────────

def step_log_rotate(logger: Logger, dry_run: bool) -> StepResult:
    """Archive old session files, create symlinks, update registry.

    Deterministic operation — no LLM needed. Preserves --continue compatibility
    by creating symlinks in original locations.
    """
    t0 = time.time()
    step_name = "log_rotate"
    logger.header(f"Step: {step_name}")

    if dry_run:
        logger.log("[DRY-RUN]", "would run log rotation")
        return StepResult(step_name, True, elapsed=time.time() - t0, detail="dry-run")

    try:
        # Import here — dream.py runs from project root, src/ is on path
        sys.path.insert(0, str(SRC_DIR))
        from agent_session_registry import get_registry, LOG_DIR  # type: ignore
        import shutil

        registry = get_registry()

        # Read retention config from tau.json
        max_age_days = 30
        max_size_mb = 500
        archive_dir = LOG_DIR / "archive"

        try:
            tau_json = SRC_DIR / "tau.json"
            if tau_json.exists():
                config = json.loads(tau_json.read_text(encoding="utf-8"))
                retention = config.get("log_retention", {})
                max_age_days = retention.get("max_age_days", max_age_days)
                max_size_mb = retention.get("max_size_mb", max_size_mb)
                archive_path_str = retention.get("archive_dir", str(archive_dir))
                archive_dir = Path(archive_path_str)
                if not archive_dir.is_absolute():
                    archive_dir = Path.home() / archive_dir.expanduser()
        except Exception as e:
            logger.log("[warn]", f"Failed to read retention config: {e}")
        # Find sessions to archive (older than max_age_days)
        cutoff = time.time() - (max_age_days * 86400)
        active_cutoff = time.time() - 300  # 5 minutes — likely active

        sessions = registry.list_sessions(status="active")
        to_archive = []

        for s in sessions:
            ctx_path_str = s.get("context")
            if not ctx_path_str:
                continue
            try:
                mtime = Path(ctx_path_str).stat().st_mtime
                # Skip active sessions (modified in last 5 minutes)
                if mtime > active_cutoff:
                    continue
                if mtime < cutoff:
                    to_archive.append(s)
            except OSError:
                continue

        # Also check total log directory size
        if not to_archive:
            try:
                total_size = sum(
                    f.stat().st_size
                    for f in LOG_DIR.iterdir()
                    if f.is_file() and not f.name.endswith(".tmp")
                )
                if total_size > max_size_mb * 1024 * 1024:
                    # Archive oldest 20 INACTIVE sessions (same active_cutoff filter)
                    inactive = []
                    for s in sessions:
                        ctx_path_str = s.get("context")
                        if not ctx_path_str:
                            continue
                        try:
                            mtime = Path(ctx_path_str).stat().st_mtime
                            if mtime <= active_cutoff:
                                inactive.append((mtime, s))
                        except OSError:
                            continue
                    inactive.sort(key=lambda x: x[0])  # oldest first
                    to_archive = [s for _, s in inactive[:20]]
            except OSError:
                pass

        if not to_archive:
            logger.log(
                "[info]",
                f"No sessions to archive (age>{max_age_days}d, size<{max_size_mb}MB)",
            )
            return StepResult(
                step_name, True, elapsed=time.time() - t0, detail="nothing to archive"
            )

        logger.log("[info]", f"Archiving {len(to_archive)} session(s)...")

        archived = 0
        errors = 0

        for s in to_archive:
            prefix = s["prefix"]
            ctx_path = Path(s.get("context", ""))
            audit_path = Path(s.get("audit", "")) if s.get("audit") else None
            plan_path = Path(s.get("plan", "")) if s.get("plan") else None

            # Extract date from prefix ({ppid}_{YYYYMMDDHHMMSS}_{N})
            archive_date = "unknown"
            try:
                parts = prefix.split("_")
                if len(parts) >= 2 and len(parts[1]) >= 8:
                    ts = parts[1]
                    archive_date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            except Exception:
                pass

            dest_dir = archive_dir / archive_date
            dest_dir.mkdir(parents=True, exist_ok=True)

            files_to_move = [
                ("context", ctx_path),
                ("audit", audit_path),
                ("plan", plan_path),
            ]

            new_paths: dict[str, str] = {}
            for label, fpath in files_to_move:
                if not fpath or not fpath.exists():
                    continue

                dest = dest_dir / fpath.name
                try:
                    shutil.move(str(fpath), str(dest))
                    fpath.symlink_to(dest)
                    new_paths[label] = str(dest)
                    logger.log("[archive]", f"{prefix}/{fpath.name} -> archive/{archive_date}/")
                except Exception as e:
                    logger.log("[error]", f"Failed to archive {fpath}: {e}")
                    errors += 1
                    # Restore from dest if symlink failed
                    if dest.exists() and not fpath.exists():
                        try:
                            shutil.move(str(dest), str(fpath))
                        except Exception:
                            pass

            # Update registry
            if new_paths:
                registry.archive_session(prefix, new_paths)
                archived += 1

        logger.log("[summary]", f"Archived {archived} session(s), {errors} error(s)")
        logger.log("[summary]", f"Archive dir: {archive_dir}")

        # Clean up orphaned registry entries
        try:
            orphans = registry.cleanup_orphans()
            if orphans:
                logger.log("[cleanup]", f"Removed {orphans} orphaned registry entries")
        except Exception as e:
            logger.log("[warn]", f"Failed to cleanup orphans: {e}")

        return StepResult(
            step_name,
            errors == 0,
            elapsed=time.time() - t0,
            detail=f"archived={archived},errors={errors}",
        )

    except Exception as e:
        logger.log("[error]", f"Log rotation failed: {e}")
        logger.log("[traceback]", traceback.format_exc())
        return StepResult(
            step_name, False, elapsed=time.time() - t0, detail=str(e)
        )


def step_test_sanity(logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path) -> StepResult:
    return step_single(logger, git, llm_group, dry_run, agent_bin, "test_sanity", "test_sanity", prompt=PROMPT_TESTSANITY)


def step_skill_maintenance(logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path) -> StepResult:
    return step_single(logger, git, llm_group, dry_run, agent_bin, "skill_maintenance", "skill_maintenance", prompt=PROMPT_SKILLMAINTENANCE)


def step_doc_sync(logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path) -> StepResult:
    return step_single(logger, git, llm_group, dry_run, agent_bin, "doc_sync", "doc_sync", prompt=PROMPT_DOC)


# ─── Cycle ───────────────────────────────────────────────────────────────────

def run_health_check(logger: Logger, state: DreamState) -> dict:
    """Deterministic post-cycle health score. No LLM needed."""
    checks = {}
    try:
        # 1. pytest pass rate
        r = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-q", "--tb=no"],
            capture_output=True, text=True, timeout=120, cwd=str(SRC_DIR)
        )
        checks["tests_pass"] = (r.returncode == 0)

        # 2. TODO/FIXME count (should trend down)
        todo_count = 0
        for f in SRC_DIR.rglob("*.py"):
            try:
                text = f.read_text()
                todo_count += text.count("TODO") + text.count("FIXME")
            except OSError:
                pass
        checks["todo_count"] = todo_count

        # 3. Largest file in src/
        largest = 0
        for f in SRC_DIR.glob("*.py"):
            try:
                largest = max(largest, f.stat().st_size)
            except OSError:
                pass
        checks["largest_file_kb"] = largest // 1024

        # 4. Git status clean?
        r2 = subprocess.run(
            ["git", "status", "--porcelain", "."],
            capture_output=True, text=True, timeout=10, cwd=str(SRC_DIR)
        )
        checks["git_clean"] = (r2.stdout.strip() == "")

    except Exception as e:
        logger.log("[health]", f"error: {e}")

    # Score: count passing checks
    _thresholds = {"todo_count": 50, "largest_file_kb": 200}
    passed = sum(1 for k, v in checks.items() if v is True or (isinstance(v, int) and v < _thresholds.get(k, 50)))
    total = len(checks)
    score = f"{passed}/{total}"

    prev = state.data.get("last_health_score")
    state.data["last_health_score"] = score
    trend = ""
    if prev and prev != score:
        trend = f" (was {prev})"

    logger.log("[health]", f"Health: {score}{trend} | {checks}")
    return checks


def run_cycle(cycle_num: int, logger: Logger, git: GitHelper, llm_group: str, dry_run: bool, agent_bin: Path, state: DreamState = None, max_cycle_minutes: int = 0) -> List[StepResult]:
    """Run one complete cycle of all 7 steps."""
    t0 = time.time()
    logger.header(f"━━━ Cycle {cycle_num} ━━━ (prompt: {PROMPT_VERSION})")
    all_results = []

    def _budget_ok(priority: str = "normal") -> bool:
        """Check if we have budget for this step. 'critical' always runs."""
        if priority == "critical":
            return True
        if state is None or max_cycle_minutes <= 0:
            return True
        remaining = state.budget_remaining(max_cycle_minutes, time.time() - t0)
        if remaining < 0.2 and priority == "low":
            logger.log("[budget]", f"skipping low-priority step (budget {remaining:.0%} remaining)")
            return False
        return True

    # 1. Process tasks (critical — always runs)
    all_results.extend(step_process_tasks(logger, git, llm_group, dry_run, agent_bin))
    if shutdown.check():
        return all_results

    # 1.5 Log rotation (deterministic, no LLM)
    all_results.append(step_log_rotate(logger, dry_run))
    if shutdown.check():
        return all_results

    # 2. Re-architecture (x3) — skip if convergence detected
    if state and state.should_skip_rearch():
        logger.log("[rearch]", "skipping: convergence detected (same area 3+ times in last 5 cycles)")
        all_results.append(StepResult("rearch:skip", True, elapsed=0, detail="convergence"))
    else:
        all_results.extend(step_rearch(logger, git, llm_group, dry_run, agent_bin, n=3, state=state, cycle_num=cycle_num))
    if shutdown.check():
        return all_results

    # 3. Test sanity (normal priority)
    if _budget_ok("normal"):
        all_results.append(step_test_sanity(logger, git, llm_group, dry_run, agent_bin))
    else:
        all_results.append(StepResult("test_sanity", True, elapsed=0, detail="budget_skip"))
    if shutdown.check():
        return all_results

    # 4. Skill maintenance (low priority)
    if _budget_ok("low"):
        all_results.append(step_skill_maintenance(logger, git, llm_group, dry_run, agent_bin))
    else:
        all_results.append(StepResult("skill_maintenance", True, elapsed=0, detail="budget_skip"))
    if shutdown.check():
        return all_results

    # 5. Doc sync (low priority)
    if _budget_ok("low"):
        all_results.append(step_doc_sync(logger, git, llm_group, dry_run, agent_bin))
    else:
        all_results.append(StepResult("doc_sync", True, elapsed=0, detail="budget_skip"))
    if shutdown.check():
        return all_results

    # 6. Health check (deterministic, fast)
    if state and not dry_run:
        run_health_check(logger, state)

    elapsed = time.time() - t0
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    passed = sum(1 for r in all_results if r.success)
    total = len(all_results)
    logger.header(f"━━━ Cycle {cycle_num} complete ({h:02d}:{m:02d}:{s:02d}, {passed}/{total} passed) ━━━")

    return all_results
