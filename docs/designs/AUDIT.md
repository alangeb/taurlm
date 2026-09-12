# Audit System Design

## Overview

The audit system provides 100% traceability of all user-visible console output. Every message displayed to the user is logged to an audit file with timestamps, record types, and nesting levels.

## Key Design Decisions

### No Rotation — Immutable, Perpetual Audit Logs

**Decision**: Audit files are NEVER rotated, truncated, or deleted by the system.

**Rationale**:
- Audit logs are for forensic analysis — they must be immutable
- External tools (logrotate, backup systems, archival) handle lifecycle management
- The system's job is to write, not to manage retention
- Any automatic rotation could destroy evidence of system behavior

**Implementation**:
- `AuditWriter` appends to files with `open(file, "a")`
- No size limits, no age limits, no rotation logic
- Buffering is used for performance, but flushes at turn boundaries
- On write failure: buffer is retained for retry (never dropped)

## Audit File Location

All audit files are stored in `LOG_DIR`:
- **Default**: `~/.local/taurlm/log/`
- **Override**: `TAU_LOG_DIR` environment variable

File naming: `{ppid}_{timestamp}_{sequence}.{ext}`
- Example: `12345_20260813120000_1.audit`

## Record Format

```
[TIMESTAMP] RECORD_TYPE stack=SS field1=value1 field2=value2
  | continuation_line
  | continuation_line
```

### Record Types

| Record Type | Source | Description |
|-------------|--------|-------------|
| `SESSION_START` | `agent_audit_writer.py` | Agent initialization (version, model, cwd) |
| `USER` | `agent_audit_writer.py` | User input message |
| `FORK_START` | ~~`rlm/spawn.py`~~ | Spawn child started (**not currently emitted**) |
| `FORK_END` | ~~`rlm/spawn.py`~~ | Spawn child completed (**not currently emitted**) |
| `SUBAGENT_START` | ~~`rlm/spawn.py`~~ | Spawn child started (**not currently emitted**) |
| `SUBAGENT_END` | ~~`rlm/spawn.py`~~ | Spawn child completed (**not currently emitted**) |
| `CONTEXT_ADD` | `agent_audit_bridge.py` | Context messages added (count, total, bytes) |
| `CONTEXT_REMOVE` | `agent_audit_bridge.py` | Context messages removed |
| `CONTEXT_MERGE` | `agent_audit_bridge.py` | Context messages merged |
| `CONTEXT_SNAPSHOT` | `agent_audit_writer.py` | Context state snapshot |
| `COMPRESS_START` | `agent_audit_writer.py` | Compression started |
| `COMPRESS_STEP` | `agent_audit_writer.py` | Compression step progress |
| `COMPRESS_ACTION` | `agent_audit_writer.py` | Compression action taken |
| `COMPRESS_END` | `agent_audit_writer.py` | Compression completed |
| `LLM_CALL` | `agent_llm_client.py` | LLM invocation (emitted directly, not via AuditWriter) |
| `CONSOLE_INFO` | `agent_audit_bridge.py` | Informational console output |
| `CONSOLE_WARNING` | `agent_audit_bridge.py` | Warning console output |
| `CONSOLE_ERROR` | `agent_audit_bridge.py` | Error console output |
| `CONSOLE_SUCCESS` | `agent_audit_bridge.py` | Success console output |

### Nesting Stack

The `stack=SS` field identifies which agent wrote the record:
- `stack=.`: Root agent
- `stack=S`: First-level subagent
- `stack=SS`: Second-level subagent (subagent of subagent)
- `stack=F`: Fork
- `stack=SF`: Fork in subagent
- etc.

The stack string encodes both depth AND agent type, making every record
human-readable and attributable to a specific agent.

This enables filtering audit output by agent level.

## Console-to-Audit Bridging

All user-visible console output is logged to audit:

### Direct Display Functions
- `display_info()`, `display_error()`, `display_warning()`, `display_success()`
- `echo()`, `echo_no_newline()`, `status()`, `reasoning()`, `verbose()`
- All map to `console_info()`, `console_error()`, etc. in `agent_audit_bridge`

### Templates
- All `_ConsoleMessage` templates have `audit=True`
- Includes: `user_echo`, `assistant_message_display`, `repl_output`, `repl_feedback`, `repl_error`, etc.

### Command Output
- `/ctx`, `/help`, `/goal`, etc. all log via `display_info()`
- `.md` command segments log through input pipeline

### Sub-agent Identification
- `FORK_START`/`FORK_END` and `SUBAGENT_START`/`SUBAGENT_END` records
- All console output between these markers has `stack=S` or deeper
- Enables filtering: `grep "stack=\." audit.log` for root agent only

## Architecture

```
Console Output Path:
  display_info("message")
    → _cw(color, text)          # Write to stdout
    → console_info(text)         # Log to audit (bridge)
      → _safe_writer_call()      # Exception-safe
        → writer._console_info() # Emit CONSOLE_INFO record
          → _emit()              # Format + enqueue
            → _flush()           # Write to file (buffered)
```

## Analysis

### Filtering by Record Type
```bash
grep "CONSOLE_ERROR" audit.log          # All errors
grep "FORK_START" audit.log             # All forks
grep "LLM_CALL" audit.log               # All LLM calls
```

### Filtering by Nesting Stack
```bash
grep "stack=\." audit.log                # Root agent only
grep "stack=S " audit.log                # First-level sub-agents
grep "stack=SS" audit.log                # Second-level sub-agents
```

### Filtering by Time Range
```bash
grep "\[2026-08-13T12:" audit.log       # All records from 12:00
```

## Files

| File | Purpose |
|------|---------|
| `agent_audit_writer.py` | `AuditWriter` class — buffered file writer |
| `agent_audit_bridge.py` | Single entry point for all audit writes |
| `agent_console/audit.py` | `_log_audit()` — console-to-audit bridging |
| `agent_console/primitives.py` | Display functions with audit logging |
| `agent_console/templates.py` | Message templates with `audit=True` |
| `agent_session.py` | `LOG_DIR`, session file paths |
| `agent_llm_client.py` | LLM call audit logging |
