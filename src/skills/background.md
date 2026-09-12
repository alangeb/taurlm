---
name: background
description: 'Run long-running commands (tests, builds, training, servers) in detached tmux sessions so they survive the REPL block timeout and process-group kill. Raw tmux (no wrapper tools exist here). Use when a command may exceed the block timeout, must persist across turns, or must not be reaped when its block finishes. Keywords: background, tmux, detached, async, long-running, long running, capture-pane, new-session, poll, wait, build, server, training, orphan process, process group, kill-session.'
category: operations
keywords: 'background, tmux, detached, async, long-running, long running, capture-pane, new-session, poll, wait, build, server, training, orphan process, process group, kill-session'
---

# background

Run anything that outlives a single REPL block in a **detached tmux session**. This is the ONLY reliable persistence in taurlm.

## Why tmux here (the non-obvious part)
- Bash blocks run in their **own process group**; on **timeout** the whole group is killed (kernel.py:31, AGENT_RLM.md:169). A block that finishes *normally* does **NOT** reap background children it spawned — so `cmd &` / `nohup cmd &` children are **unreliable across turns** and may be killed or leak as orphans.
- Block timeouts are **config-driven** (`rlm.repl.bash_timeout_seconds` / `python_timeout_seconds`, default 180s each, may differ per block type). Anything that can exceed that must go to tmux.
- Real cautionary bug: `src/tests/rlm/test_repl_timeout.py::TestOrphanCleanup` — a stray `sleep` surviving a process-group kill. tmux + explicit `kill-session` is the discipline that prevents exactly this.

## Decision: `%timeout` vs tmux
- Moderate wait you can bound (< a few min): just raise the block budget — put `%timeout N` (1-3600) as the **first line** of the block. Reverts to default next block.
- Long / unbounded / must-persist / server / build / training: **tmux**.

## Launch (detached) — the file-redirect pattern (verified)
```bash
tmux new-session -d -s mytask 'cd /abs/path && long_command > /tmp/mytask.log 2>&1; echo "EXIT:$?" >> /tmp/mytask.log'
```
- ALWAYS `cd /abs/path` — the session has an **independent cwd**.
- ALWAYS redirect to a **file** and append `; echo "EXIT:$?"`. Reason (verified): a tmux session running a *single* command **auto-closes the instant it finishes**, so `capture-pane` after completion returns nothing — the session is already gone. The log file survives the session and is the reliable source of truth for output + exit code.
- Don't chain `&&` across *separate* launches; keep one logical job per session.

## Poll (from a Python block — we are in a Python REPL)
Poll the **log file**, not the pane (the pane dies with the session):
```python
import subprocess, time, pathlib
def bg_poll(log, done=("EXIT:",), fail=("Traceback","FAILED","Error"),
            max_s=600, idle_s=30):
    p = pathlib.Path(log); last, last_change = None, time.time()
    for _ in range(max_s):
        txt = p.read_text() if p.exists() else ""
        if "EXIT:" in txt: return ("done", txt)      # job finished (exit code in file)
        if any(k in txt for k in fail): return ("fail", txt)
        if txt != last: last, last_change = txt, time.time()
        elif txt and time.time()-last_change > idle_s: return ("idle", txt)
        time.sleep(1)
    return ("timeout", last or "")
```
- Watch **multiple** failure keywords, not one — unexpected output is the norm.
- `stdin=subprocess.DEVNULL` + `timeout=` on every `subprocess.run` (child inherits the terminal otherwise → deadlock).
- Want to **watch live output** instead? keep the session alive with a trailing `sleep` so the pane persists: `tmux new-session -d -s t 'cmd; echo "EXIT:$?"; sleep 60'` then `tmux capture-pane -t t -p` (verified). Kill it yourself when done.

## Cleanup (do NOT skip — this is the orphan-killer)
```bash
tmux kill-session -t mytask        # explicit teardown
tmux ls                             # list; kill leftovers
```
Leaving sessions around is what produces orphan processes and the flaky-test failure above.

## Gotchas
- **A single-command session auto-closes on completion** (verified) → the pane is empty afterward. Use the file-redirect pattern; never assume `capture-pane` will show a finished job.
- **Module caching**: a running agent keeps old modules in memory; never validate new code inside a resumed/backgrounded session — use a fresh process.
- **Interactive input**: `tmux send-keys -t name 'text' Enter` (add `Enter` to execute; omit for raw keys).

## Related Skills
- `shell` — bash-block process-group semantics this skill works around
- `testing` / `test-runner` / `sanity` — the concrete "run the suite/build detached" case
- `spawn` (§Worker Templates) — delegation is a *different* mechanism (sub-agent), not backgrounding
- `debug` — trace a crashed background job via its `.audit`/log
