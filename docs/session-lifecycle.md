# Session Lifecycle

Status-quo description of how a TauRLM session acquires its identity, what it
writes, and how it dies. Source: `src/agent_session.py`, `src/agent_subsystems.py`,
`src/agent_core.py`, `src/rlm/turn_summary.py`, `src/agent_audit_writer.py`,
`src/agent_input.py`, `src/tau.py`.

## The prefix

Every session writes files named by a prefix:

```
{ppid}_{YYYYMMDDHHMMSS}_{n}
```

`ppid = os.getppid()` — the **launcher** pid, shared by every session a launcher
spawns. So the pid in a filename is NOT a per-session pid; it identifies the
launcher process tree, not the individual session. `n` is a per-`{ppid}_{ts}`
counter incremented to avoid collisions (`agent_session.py:64-66,79-81`).

Files derived from a prefix (`agent_session.py:235-236`):

| File | Role |
|------|------|
| `<prefix>.context` | conversation state (`ContextManager.save_to_file`) |
| `<prefix>.audit` | typed audit records (`AuditWriter`) |
| `<prefix>.plan`, `<prefix>.toolout.NNN`, `<prefix>.failed_request.json` | derived artifacts |

`LOG_DIR` defaults to `~/.local/taurlm/log`, overridden by `TAU_LOG_DIR`
(`agent_session.py:42-44`).

## Claim vs peek (M-S1)

Two prefix functions, one side-effecting and one not:

- **`_get_log_filename_prefix()`** — CLAIMS a prefix. It atomically creates a
  0-byte `<prefix>.context` via `os.open(..., os.O_CREAT | os.O_EXCL | ...)`
  (`agent_session.py:77-92`, claim at `:88`). On `FileExistsError` it bumps `n`
  and retries. `O_CREAT|O_EXCL` is the guard that stops two processes grabbing
  the same prefix — only one `open` wins.
- **`_peek_log_filename_prefix()`** — NON-creating. Same shape, but only `stat`s
  to skip taken numbers and never opens with `O_CREAT` (`agent_session.py:54-74`).
  For display/template interpolation only.

The only production caller of the claiming variant is `AgentSessionManager.__init__`
on its `setup_files=True` path (`agent_session.py:229`); every other use is peek
or test (`test_session_prefix_leak.py`).

## SESSION_PREFIX global

`SESSION_PREFIX` is a module global in `agent_session.py` (`:48`), `None` at
import. `AgentSessionManager.__init__` sets it ONCE on the `setup_files=True`
path: if `SESSION_PREFIX is None` it claims a prefix and assigns the global,
otherwise it reuses the existing value (`agent_session.py:220-232`). So the root
session claims; children (forks/subagents, which also construct a manager) reuse
it — all session files in one tree share one prefix. Tests reset it to `None`
between cases (`conftest.py:251`).

## Init-order invariant

`agent_core._init_subsystems` runs `init_subsystems()` BEFORE `read_system_prompt()`
(`agent_core.py:220-234`):

1. `init_subsystems(self, init)` → constructs the session manager → claims the
   prefix → sets `SESSION_PREFIX` (`agent_core.py:228`).
2. `read_system_prompt(...)` then reads the CURRENT `SESSION_PREFIX` via a
   function-local import (`agent_subsystems.py:137`) and, only if it is still
   `None`, falls back to a peek prefix that creates nothing
   (`agent_subsystems.py:141-149`).

`read_system_prompt` interpolates `{audit_file}`/`{context_file}` from that prefix
(`agent_subsystems.py:156-161`). `AGENT_RLM.md` currently contains ZERO such
placeholders, so the interpolation is a no-op today — but the ordering is what
keeps it correct if a placeholder is ever added: claiming a throwaway prefix first
would both leak a stray 0-byte `<prefix>_1.context` and interpolate the wrong
paths (`agent_core.py:221-227`).

## Two turn-summary sinks

`generate_turn_summary(agent)` (`rlm/turn_summary.py:32`) runs from
`agent_input.py:434-440` — after the answer is displayed, root agent only
(`nesting_stack` empty, guard `turn_summary.py:57-58`), substantive turns only
(`:61-62`), and only when `config.rlm.auto_summary.enabled` (`:52-54`).

A produced summary writes to TWO sinks on the SAME success path
(`turn_summary.py:126` then `:129`):

1. **`.context` metadata** — `_attach_summary_metadata` attaches a `summary` key
   to the last assistant message dict in place (`turn_summary.py:243-250`). It
   persists into `<prefix>.context` on the next save; `/ctx sum` reads this sink.
2. **`.audit` typed record** — `_write_summary_audit` calls
   `audit_writer.turn_summary(summary)` (`turn_summary.py:300-313`), which emits a
   greppable `TURN_SUMMARY` record (`agent_audit_writer.py:254-256`).

These are different sinks: `/ctx sum` reflects sink 1, grepping the `.audit` file
reflects sink 2. A summary visible in one is not proof of the other.

### AuditWriter-vs-Path footgun

The audit sink target must be the **writer**, not the path. `agent._session.audit_writer`
is an `AuditWriter` exposing `turn_summary()`/`turn_summary_status()`
(`agent_session.py:328-333`); `agent._session.audit_file` is a `Path` with no such
method. `generate_turn_summary` deliberately resolves `audit_writer`
(`turn_summary.py:71-77`) and warns (then degrades to a quiet no-op) if the
resolved target lacks `turn_summary`. Handing the Path to the sink instead makes
the audit write a **silent no-op** — sink 1 still works, so the failure is invisible
unless you check the `.audit`.

### Verifying a typed record landed

`_emit` formats each record as `[<ts>] RECORD_TYPE stack=<SS> [fields...]`
(`agent_audit_writer.py:127-136`); stack `.` = root, `S`/`SS`/`SF` = nested. The
greppable form is the whole `] TURN_SUMMARY stack=` token sequence, one per
produced or skipped substantive turn (`turn_summary` success; `turn_summary_status`
emits `status=skipped reason=...`, `agent_audit_writer.py:258-267`):

```bash
grep -aF "] TURN_SUMMARY stack=" "$HOME/.local/taurlm/log/<prefix>.audit"
```

Match the full `] EVENT stack=` form, not the bare token (see gotchas below).

## Exit paths

Three ways a session ends, with different save guarantees:

- **`/exit`** → `InputHandler._exit` (`agent_input.py:569-585`): `close_turn("[Session ended]")`
  (`:571`), `context.save_to_file(..., force=True)` (`:575`), `audit_writer.flush()`
  (`:584`), `sys.exit(0)` (`:585`). Context is force-saved. The main loop's normal
  exit also delegates here (`:462-463`).
- **Ctrl+C** → `_signal_handler`, a two-stage SIGINT handler (`agent_input.py:163-175`).
  1st press sets `interrupted` and prints a notice (`:173-175`); 2nd press raises
  `SystemExit(0)` directly (`:168-172`) and does NOT run `_exit`, so the
  `.context` may be left unsaved/empty (only the per-turn `save_to_file` at `:512`
  is non-force and only runs on a completed turn). Double-Ctrl-C is the force path.
- **`tau.py` wrapper** (`tau.py:489-515`): `run()` wrapped by `KeyboardInterrupt`
  (`:496`), specific error types (`:499`), `SystemExit` re-raise (`:502-507`),
  `BaseException` log-and-re-raise (`:508-513`), and a `finally` that stops the
  A2A server (`:514-515`). SIGKILL/OOM/power-loss run NO Python — no handler fires;
  the `.context` reflects only what was saved before the kill.

## Empty `.context` files

A live session writes a **0-byte** `<prefix>.context` the instant it claims the
prefix (`agent_session.py:87-89`), then `tau.py` startup force-saves real content
into it (`tau.py:476`) so a healthy session's `.context` is non-empty quickly. An
**empty `.context` that persists** therefore marks a prefix claimed by a process
that died before its first save (early crash, or the double-Ctrl-C / SIGKILL paths
above).

Cleanup is guarded and opt-in, not automatic. `SessionRegistry.cleanup_orphans()`
(`agent_session_registry.py:317-339`) drops **registry entries** whose files have
vanished from disk — it does NOT delete `.context`/`.audit` files and is not
invoked by a background reaper. There is no in-process auto-reaper; removing the
empty files themselves is a manual, opt-in action.

## Observability gotchas

- **Grep false-positives.** A bare token like `TURN_SUMMARY` (or any event type)
  also matches your own prior grep/audit-probe commands recorded as message
  *content*. Match the exact typed line form `] TURN_SUMMARY stack=` (the leading
  `] ` and trailing `stack=`), not the bare word.
- **Different sinks.** `/ctx sum` reads `summary` metadata in `.context`; the
  typed `TURN_SUMMARY` records live in `.audit`. They are separate sinks and can
  disagree (Path-vs-writer footgun). Verify the sink you mean.
- **pid ≠ session.** The prefix pid is the launcher pid shared across a tree; two
  sessions from one launcher share it. Distinguish sessions by the `{ts}_{n}` tail,
  not the pid.
- **Stale modules.** A running kernel keeps modules imported at startup; code
  changes need a fresh process — see **debug** and **spawn**.

## Related docs

- [INDEX.md](INDEX.md) — documentation index
- [STRUCTURE.md](STRUCTURE.md) — project-structure rules
- [CONVENTIONS.md](CONVENTIONS.md) — code conventions
- [../src/TAU.md](../src/TAU.md) — developer guide
