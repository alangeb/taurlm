---
name: session-lifecycle
description: 'How TauRLM sessions acquire identity + write state: {ppid}_{ts}_{n} prefix, atomic O_CREAT|O_EXCL claim vs peek, SESSION_PREFIX global, init-order invariant, two turn-summary sinks (.context metadata vs .audit typed record), AuditWriter-vs-Path silent-no-op footgun, exit paths (/exit force-save vs double-Ctrl-C unsaved), empty .context files. Use for debugging session files, missing/duplicate prefixes, lost context, verifying a TURN_SUMMARY landed. Keywords: session, prefix, SESSION_PREFIX, claim, peek, O_EXCL, audit, .audit, .context, TURN_SUMMARY, turn summary, exit, Ctrl+C, SystemExit, empty context, orphan, ppid, launcher pid.'
category: rlm
keywords: 'session, prefix, SESSION_PREFIX, claim, peek, O_EXCL, audit, .audit, .context, TURN_SUMMARY, turn summary, exit, Ctrl+C, SystemExit, empty context, orphan, ppid, launcher pid'
---

# Session Lifecycle (TauRLM)

How a session gets its files, what it writes, how it dies. Sources:
`agent_session.py`, `agent_subsystems.py`, `agent_core.py`, `rlm/turn_summary.py`,
`agent_audit_writer.py`, `agent_input.py`, `tau.py`.

## Prefix shape + claim vs peek
- Prefix = `{ppid}_{YYYYMMDDHHMMSS}_{n}`. `ppid = os.getppid()` = the LAUNCHER pid,
  SHARED by all sessions one launcher spawns (`agent_session.py:64,79`). NOT per-session.
- Files: `<prefix>.context`, `<prefix>.audit` (+ `.plan`, `.toolout.NNN`,
  `.failed_request.json`) under `LOG_DIR` (~/.local/taurlm/log, `TAU_LOG_DIR` override;
  `agent_session.py:42-44,235-236`).
- `_get_log_filename_prefix()` CLAIMS atomically: creates a 0-byte `<prefix>.context`
  via `os.open(... O_CREAT|O_EXCL ...)` (M-S1 guard; `agent_session.py:77-92`, claim `:88`).
  This is the ONLY prefix-creating function and the only production call is
  `AgentSessionManager.__init__` on `setup_files=True` (`:229`).
- `_peek_log_filename_prefix()` is STAT-ONLY, never creates — display/interpolation
  only (`agent_session.py:54-74`). Use peek when you only want a name.

## SESSION_PREFIX global
Module global, set ONCE by the root session (`agent_session.py:48,220-232`); children
reuse it so one process tree shares one prefix. Tests reset it (`conftest.py:251`).

## Init-order invariant
`agent_core._init_subsystems` calls `init_subsystems()` (claims prefix, sets
`SESSION_PREFIX`) BEFORE `read_system_prompt()` (`agent_core.py:220-234`).
`read_system_prompt` reads the CURRENT `SESSION_PREFIX` (function-local import,
`agent_subsystems.py:137`), peeks only if still None (`:145-147`), and interpolates
`{audit_file}`/`{context_file}` (`:156-161`). AGENT_RLM.md has ZERO such placeholders
today — ordering is the guard that keeps it correct if one is added; claiming a
throwaway prefix first would leak a stray 0-byte context AND interpolate wrong paths.

## Two turn-summary sinks (different files!)
`generate_turn_summary` (root-only, substantive, gated by
`config.rlm.auto_summary.enabled`; `rlm/turn_summary.py:32,52-62`) writes TWO sinks
on one success path (`turn_summary.py:126` then `:129`):
1. `.context` metadata: `msg["summary"]` on last assistant msg (`:243-250`) → `/ctx sum`.
2. `.audit` typed record: `audit_writer.turn_summary(...)` → greppable TURN_SUMMARY
   (`:300-313`; format `[ts] TURN_SUMMARY stack=SS` via `agent_audit_writer.py:127-136,254-256`).

### AuditWriter-vs-Path footgun
Sink 2 target must be `agent._session.audit_writer` (an `AuditWriter` with
`turn_summary()`/`turn_summary_status()`, `agent_session.py:328-333`). Passing
`agent._session.audit_file` (a Path, no such method) makes the write a SILENT NO-OP —
sink 1 still populates, so it looks fine. The code resolves the writer and warns
if it lacks `turn_summary` (`turn_summary.py:71-77`).

### Verify a typed record landed
Match the FULL event line form, not the bare token:
```bash
grep -aF "] TURN_SUMMARY stack=" "$HOME/.local/taurlm/log/<prefix>.audit"
```
One record per produced/skipped substantive turn (`status=skipped reason=...`
variant at `agent_audit_writer.py:258-267`). A bare `TURN_SUMMARY` grep also
matches your own prior grep commands logged as message CONTENT — false positives.

## Exit paths (save guarantees differ)
- `/exit` → `_exit`: `close_turn` + `save_to_file(force=True)` + `audit_writer.flush`
  + `sys.exit(0)` (`agent_input.py:569-585`). Context force-saved.
- Ctrl+C → two-stage `_signal_handler` (`agent_input.py:163-175`): 1st sets
  interrupted; 2nd raises `SystemExit(0)` WITHOUT running `_exit` → `.context` can
  be left unsaved/empty (only non-force per-turn save at `:512`).
- `tau.py` wraps `run()` with KeyboardInterrupt/SystemExit/BaseException + `finally`
  stops A2A (`tau.py:489-515`). SIGKILL/OOM run no handler — unrecoverable in-process.

## Empty `.context`
Live sessions are 0-byte only at the claim instant, then `tau.py` force-saves content
(`tau.py:476`). A persisting empty `.context` = a claim from a process that died
before its first save. Cleanup is guarded/opt-in: `SessionRegistry.cleanup_orphans()`
(`agent_session_registry.py:317-339`) drops REGISTRY ENTRIES for vanished files — it
does NOT delete the files and no auto-reaper runs.

## pid-liveness trap
The prefix pid is the launcher, not the session. Two sessions from one launcher share
the pid; the `{ts}_{n}` tail distinguishes them. Don't treat the pid as a liveness /
uniqueness key for a single session.

## Related Skills
- `wiki` — persistent knowledge store for must-keep facts across sessions.
- `testing` — gates to verify behavior; never trust a claim (see **verification-discipline**).
- `debug` — REPL error recovery, stale-module rule, reading live `.audit`.
- `context` — `/ctx sum`, `.context` format, compression (the .context sink).
