---
name: debug
description: 'Debugging in TauRLM: REPL error recovery (_code/_output vars), timeout partial results, live-session audit logs, reproduce-isolate-fix-verify discipline. Use for error diagnosis, traceback analysis, root-cause investigation. Keywords: debug, error, traceback, exception, root cause, stack trace, bisect, reproduce, diagnose, fix bug, _code, _output.'
category: development
keywords: 'debug, error, traceback, exception, root cause, stack trace, bisect, reproduce, diagnose, fix bug, _code, _output'
---

# Debugging

Discipline: reproduce (exact error+input) -> isolate (smallest failing unit; bisect) -> diagnose -> minimal root-cause fix -> verify with a regression test (see **testing**). Everything below is the repo-specific part.

## REPL error recovery (this harness)
- Every block is stored: `_code{N}` = source lines, `_output{N}` = output. Error "line N" maps to `_code{N}[N-1]` (0-based).
- Lines BEFORE the failing line already executed and their side-effects persist — fix the list in place and exec ONLY the tail; re-running from the top can double side-effects.
- A block killed by the per-block timeout still returns partial output; `except BaseException` catches the timeout (`except Exception` does NOT) so you can salvage partial results.

## Hanging vs slow
- A test run over ~120s is hanging, not slow: kill it and bisect. No pytest-timeout plugin here (see **test-runner**).
- Blocking subprocess without `stdin=subprocess.DEVNULL` deadlocks the REPL until timeout — child inherits the terminal and waits on input.

## Live-session evidence
- Every tau.py session writes `~/.local/taurlm/log/<ppid>_<YYYYMMDDHHMMSS>_<counter>.{audit,context}` (agent_session.py:53-62) — `TAU_LOG_DIR` overrides (agent_session.py:42). Read the audit to see exactly what the model was sent — don't guess from scrollback.
- Code changes need a fresh process: a running kernel keeps old modules in memory; never validate new code in a resumed session.

## Related Skills
- `testing` — regression-test recipe, 3-gate verification
- `rlm-analysis` — `build_graph` -> `callers`/`impact` to find who corrupts state (AST, not grep)
- `code-analysis` — quick per-file structure
- `sanity` — the live-LLM gate that catches what unit tests can't
- `session-lifecycle` — session prefix/claim, turn-summary sinks, exit/save paths, empty `.context`
- `verification-discipline` — independent re-verify; do not trust claims
