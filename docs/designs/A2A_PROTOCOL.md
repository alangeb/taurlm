# A2A Protocol v1.0 — Agent-to-Agent Communication

> **External Interface Contract**: This document defines the stable v1.0 contract for
> inter-agent communication. External projects (e.g., TauWeb, custom tooling)
> should implement against this protocol to interact with TauErgon agents.
> All extension fields are optional; old clients and agents remain compatible.

**Version**: 1.0
**Status**: Active
**Source**: `src/agent_a2a.py`, `src/agent_core.py`
**See also**: `docs/designs/INDEX.md`

---

## 1. Overview

A2A (Agent-to-Agent) enables inter-agent communication via Unix domain sockets.
A parent agent can connect to a child agent's socket to query it and
inspect its status.

**Transport**: Unix domain socket (`AF_UNIX`, `SOCK_STREAM`)
**Socket path**: `/tmp/taua2a-{PID}.sock`
**Message format**: JSON, newline-delimited (`\n`)
**JSON parsing**: `json.JSONDecoder.raw_decode()` — reads one complete JSON object at a time

### Design principles

- **No wall-clock timeout** — client times out only if no data (response or heartbeat)
  arrives for `HEARTBEAT_IDLE_TIMEOUT` seconds. Slow agents are fine.
- **Heartbeat-based liveness** — server sends heartbeat every `HEARTBEAT_INTERVAL` seconds
  during query processing. Client resets idle timer on any data.
- **Thread-per-connection** — server spawns a daemon thread per client connection.
- **Backward compatible** — all extension fields use `getattr` defaults; old clients
  and minimal duck-typed agents keep working.

---

## 2. Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_CONNECT_TIMEOUT` | `5` | Default timeout for `connect_to_agent()` |
| `DEFAULT_ACK_TIMEOUT` | `5` | Timeout for acknowledgment responses |
| `DEFAULT_POLL_INTERVAL` | `0.1` | Server polling interval for pending responses |
| `SOCKET_BUFFER` | `4096` | recv() buffer size |
| `HEARTBEAT_INTERVAL` | `5.0` | Server sends heartbeat every N seconds |
| `HEARTBEAT_IDLE_TIMEOUT` | `30.0` | Client gives up if no heartbeat for N seconds |

### Socket naming

```
/tmp/taua2a-{PID}.sock
```

- `{PID}` is the agent process PID
- Socket file is created on server start, removed on server stop
- Stale socket files (from crashed processes) are unlinked on startup

### Context file naming

```
{ppid}_{timestamp}_{counter}.context
```

- `{ppid}`: parent PID
- `{timestamp}`: Unix timestamp
- `{counter}`: incrementing counter
- Regex: `^(\d+)_\d+_\d+\.context$`

---

## 3. Message Types

### 3.1 agent_card (sync)

**Request** (client → server):
```json
{"type": "agent_card"}
```

**Response** (server → client):
```json
{
  "type": "agent_card",
  "name": "my-agent",
  "model": "gpt-4o-mini",
  "mode": "rlm",
  "working_dir": "/home/user/project",
  "context_length": 42,
  "uptime": 120,
  "sock_path": "/tmp/taua2a-12345.sock",
  "file": "tau.py",
  "original_task": "Build a web server",
  "start_time": 1700000000.0,
  "parent_pid": 12344,
  "llm_group": "default",
  "max_context_tokens": 128000,
  "session_id": "12344_1700000000_0",
  "turn_active": false
}
```

**Fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | str | yes | Always `"agent_card"` |
| `name` | str | yes | Agent name (`agent_name`) |
| `model` | str | yes | Model name (`model_name`) |
| `mode` | str | yes | Agent mode (always `"rlm"`) |
| `original_task` | str | no | Original task prompt (if any) |
| `start_time` | float | no | Unix timestamp of agent start |
| `llm_group` | str | no | Current LLM group name |
| `max_context_tokens` | int | no | Max context tokens |
| `session_id` | str | no | Session prefix ID |
| `turn_active` | bool | no | Whether a turn is currently active |
| `working_dir` | str | yes | Agent's original working directory |
| `context_length` | int | yes | Number of messages in context |
| `uptime` | int | yes | Seconds since agent start (0 if unknown) |
| `sock_path` | str | yes | Server socket path |
| `file` | str | yes | Agent script filename (default `"tau.py"`) |
| `original_task` | str? | no | First user message (extension-004) |
| `start_time` | float? | no | Unix timestamp of agent start (extension-004) |
| `parent_pid` | int | yes | Parent process PID |
| `llm_group` | str? | no | Current LLM group name (extension-004) |
| `max_context_tokens` | int? | no | Max context tokens (extension-004) |
| `session_id` | str? | no | Session prefix from `_session.prefix` (extension-004) |
| `turn_active` | bool | yes | Whether agent is currently processing a turn |

---

### 3.2 status (sync)

**Request** (client → server):
```json
{"type": "status"}
```

**Response** (server → client):
```json
{
  "type": "status_response",
  "pid": 12345,
  "turn_active": false,
  "context_length": 42,
  "uptime": 120,
  "nesting_count": 0,
  "last_audit_mtime": 1700000120.0
}
```

**Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"status_response"` |
| `pid` | int | Server process PID |
| `turn_active` | bool | Whether agent is processing a turn |
| `context_length` | int | Number of messages in context |
| `uptime` | int | Seconds since agent start |
| `nesting_count` | int | Current fork/subagent nesting depth |
| `last_audit_mtime` | float? | Modification time of audit file (null if unavailable) |

**Purpose**: Lightweight status check — does not trigger LLM processing.
Used by TauWeb to determine `running` vs `idle` state.

---

### 3.3 query (async)

**Request** (client → server):
```json
{
  "type": "query",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "query": "What is the capital of France?"
}
```

**Acknowledgment** (server → client):
```json
{
  "type": "queued",
  "id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Streaming chunks** (server → client, zero or more):
```json
{
  "protocol_version": "1.0",
  "type": "stream_chunk",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "chunk": {
    "type": "tool_call",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "tool": "read",
    "args": {"path": "/tmp/file.txt"}
  }
}
```

**Heartbeat** (server → client, during processing):
```json
{
  "type": "heartbeat",
  "id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Final response** (server → client):
```json
{
  "type": "response",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "response": "The capital of France is Paris."
}
```

**Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | `"agent_card"`, `"status"`, `"status_response"`, `"query"`, `"queued"`, `"stream_chunk"`, `"heartbeat"`, `"response"`, `"error"` |
| `id` | str | UUID matching the original query request |
| `query` | str | The prompt to process |
| `chunk` | dict | Streaming chunk data (see §3.4) |
| `response` | str | Final LLM response |

**Flow**:
1. Client sends `query` with unique `id`
2. Server responds with `queued` acknowledgment
3. Server processes query (RLM loop — no stream_chunks emitted in RLM mode)
4. Server sends `heartbeat` every `HEARTBEAT_INTERVAL` seconds
5. Server sends final `response` with the result
6. Client matches `response.id` to the original `query.id`

**Client behavior**:
- Accepts `heartbeat` messages (resets idle timer, does not return them)
- Times out if no data (response or heartbeat) for `HEARTBEAT_IDLE_TIMEOUT` seconds
- No wall-clock timeout — slow agents are fine as long as heartbeats flow

---

### 3.4 stream_chunk types

> **[RLM: NOT USED]** In RLM mode, no stream_chunks are emitted. The agent has NO tools —
> it communicates entirely through Python code blocks. The `_pending_a2a_chunks` mechanism
> exists in the code but is never populated. Query processing goes directly from
> `queued` ack → heartbeats → final `response`. The chunk types below are documented
> for protocol completeness and potential future use.

#### assistant

```json
{
  "type": "assistant",
  "id": "<request_id>",
  "content": "The answer is..."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"assistant"` |
| `id` | str | Request ID |
| `content` | str | Assistant message content |

#### tool_call

```json
{
  "type": "tool_call",
  "id": "<request_id>",
  "tool": "read",
  "args": {"path": "/tmp/file.txt"}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"tool_call"` |
| `id` | str | Request ID |
| `tool` | str | Tool name |
| `args` | dict | Tool arguments |

#### tool_result

```json
{
  "type": "tool_result",
  "id": "<request_id>",
  "tool": "read",
  "output": "file contents here...",
  "status": "success"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"tool_result"` |
| `id` | str | Request ID |
| `tool` | str | Tool name |
| `output` | str | Tool output (truncated to 1000 chars) |
| `status` | str | Always `"success"` |

#### tool_error

```json
{
  "type": "tool_error",
  "id": "<request_id>",
  "tool": "read",
  "error_message": "Error invoking tool: file not found"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"tool_error"` |
| `id` | str | Request ID |
| `tool` | str | Tool name |
| `error_message` | str | Error description |

#### fork_start

```json
{
  "type": "fork_start",
  "id": "<request_id>",
  "task": "Subtask description"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"fork_start"` |
| `id` | str | Request ID |
| `task` | str | Fork/subagent task description |

#### fork_end

```json
{
  "type": "fork_end",
  "id": "<request_id>",
  "duration_s": 2.5
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Always `"fork_end"` |
| `id` | str | Request ID |
| `duration_s` | float | Fork duration in seconds |

**Chunk emission**:
- `tool_call`: emitted before tool execution
- `tool_result`: emitted after successful tool execution (output truncated to 1000 chars)
- `tool_error`: emitted when tool result matches error patterns (`"Error invoking tool"`, `"Tool '...' not found"`, `"is not available"`)
- `assistant`: emitted for assistant message content
- `fork_start`/`fork_end`: **planned** — not yet emitted by the current implementation

> **Implementation status**: The `_emit_a2a_chunk()` function was removed in RLM mode.
> Chunk delivery infrastructure remains (`stream_chunk` messages are sent if
> `_pending_a2a_chunks` is populated), but no code currently emits chunks.
> A2A queries effectively return the complete response as a single `response` message.

**No-op**: chunks are only sent if `_pending_a2a_chunks` has entries for the request ID.

---

### 3.5 error

**Server → client** (any request):
```json
{
  "type": "error",
  "message": "Human-readable error description"
}
```

---

## 4. Client Utilities

### 4.1 connect_to_agent

```python
def connect_to_agent(pid: int, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> socket.socket
```

- Constructs socket path: `/tmp/taua2a-{pid}.sock`
- Verifies socket file exists and accepts connections
- Raises `FileNotFoundError` if socket file missing
- Raises `ConnectionError` if socket not responding (stale)
- Returns connected `socket.socket`

### 4.2 get_agent_card

```python
def get_agent_card(client: socket.socket) -> dict
```

- Sends `{"type": "agent_card"}`
- Reads response with `DEFAULT_ACK_TIMEOUT`
- Returns full agent card dict
- Raises `RuntimeError` if response type is not `agent_card`

### 4.3 query_agent

```python
def query_agent(client: socket.socket, prompt: str, idle_timeout: float = HEARTBEAT_IDLE_TIMEOUT) -> dict
```

- Generates UUID for `id`
- Sends `{"type": "query", "id": <uuid>, "query": <prompt>}`
- Reads `queued` acknowledgment
- Calls `_wait_for_response()` which:
  - Polls for response matching `request_id`
  - Accepts heartbeats (resets idle timer)
  - Times out if no data for `idle_timeout` seconds
- Returns the final `response` dict

### 4.4 list_agents

```python
def list_agents() -> list[dict]
```

- Globs `/tmp/taua2a-*.sock`
- For each socket, calls `_get_agent_status()`
- Returns list of **agent status dicts** (not the full session info dict from §5.3)

**Agent status dict fields** (returned by `_get_agent_status()`):

| Field | Type | Description |
|-------|------|-------------|
| `pid` | int | Agent process PID |
| `sock_path` | str | Socket path |
| `name` | str | Agent name (from agent card) |
| `tools_count` | int | Number of available tools (always 0 in RLM mode — no tools) |
| `working_dir` | str | Agent's working directory |
| `model` | str | Model name |
| `file` | str | Agent script filename |
| `status` | str | `"active"`, `"stale"`, or `"unreachable"` |

> **Note**: `list_agents()` returns a lightweight status dict, **not** the full
> session info dict from §5.3. For the full session info (including
> `context_length`, `start_time`, `uptime`, `parent_pid`, `llm_group`,
> `max_context_tokens`, `context_file`, `audit_file`, `is_sanity`), use
> `--list-sessions` (§8.1) which calls `_scan_sessions()` → `_build_session_info()`.

### 4.5 _is_sanity_session

```python
def _is_sanity_session(log_dir: Path, agent_name: str | None = None) -> bool
```

Returns `True` if:
- `log_dir` path contains `"logtest"` (case-insensitive), OR
- `agent_name` starts with `"a2aname-"`

---

## 5. Session Discovery

### 5.1 Socket-based discovery

```python
Path("/tmp").glob("taua2a-*.sock")
```

For each socket file:
1. Extract PID from filename: `taua2a-{PID}.sock` → `PID`
2. Check socket exists and accepts connections
3. If reachable, connect and call `get_agent_card()`
4. Build session info dict (see §5.3)

### 5.2 Context file-based discovery

```python
LOG_DIR.glob("*.context")
```

- Only files matching `^(\d+)_\d+_\d+\.context$` are considered
- `{ppid}_{timestamp}_{counter}` convention
- Extracts PID from first group

### 5.3 Session info dict

```python
{
    "id": prefix,           # "ppid_timestamp_counter"
    "pid": pid,             # int
    "status": "active" | "stale" | "unreachable",
    "agent_name": str,      # from agent card or context metadata
    "model": str,           # from agent card or context metadata
    "working_dir": str,     # from agent card or context metadata
    "context_length": int,  # message count
    "message_count": int,   # same as context_length
    "start_time": float,    # from agent card or context metadata
    "uptime": int,          # calculated from start_time
    "parent_pid": int,      # from agent card or context metadata
    "llm_group": str,       # from agent card or context metadata
    "max_context_tokens": int,  # from agent card only (None if unreachable)
    "socket_path": str,     # /tmp/taua2a-{pid}.sock
    "context_file": str,    # full path to .context file
    "audit_file": str,      # full path to .audit file
    "is_sanity": bool,      # True if sanity session
}
```

**Priority**: agent card fields take priority over context file metadata.
Non-reachable sessions fall back to context file metadata (written by TAU_005).

### 5.4 Context file metadata

Context files may be in two formats:

**Legacy (bare array)**:
```json
[{...messages...}]
```

**Metadata-wrapped (TAU_005)**:
```json
{
  "metadata": {
    "name": "agent-name",
    "model": "gpt-4o-mini",
    "working_dir": "/home/user/project",
    "start_time": 1700000000.0,
    "parent_pid": 12344,
    "llm_group": "default",
    "agent_name": "agent-name"
  },
  "messages": [{...messages...}]
}
```

`read_context_metadata_for_a2a()` handles both formats and returns
`(metadata_dict, message_count)`.

---

## 6. Server Lifecycle

### 6.1 Start

```python
server = A2AServer(agent, sock_path=None)
server.start()
```

1. `running = True`
2. Spawn daemon thread for `_accept_loop`
3. In `_accept_loop`:
   - Unlink stale socket file
   - Create `AF_UNIX` socket
   - Set `SO_REUSEADDR`
   - Bind to socket path
   - Listen with backlog of 5
   - Set ready event
   - Set 1s timeout for accept loop
4. Block until ready event or 5s timeout

### 6.2 Stop

```python
server.stop()
```

1. `running = False`
2. Close socket
3. Unlink socket file
4. Join thread with 2s timeout

### 6.3 Per-connection handling

```python
# In _accept_loop:
client_sock, _ = self.sock.accept()
threading.Thread(target=self._track_handler, args=(client_sock,), daemon=True).start()
```

1. `_recv_request`: Read chunks until `json.JSONDecoder.raw_decode()` succeeds (complete JSON object detected)
2. Parse JSON
3. Dispatch based on `type`:
   - `agent_card` → `_send_agent_card` (sync, close)
   - `status` → `_handle_status` (sync, close)
   - else → `_handle_query` (async, close)
4. On error: send `{"type": "error", "message": "..."}`
5. In `finally`: close socket

---

## 7. Audit Record Types

A2A protocol is independent of the audit log format, but the audit log
provides the source of truth for session state. Audit records are written
to `{LOG_DIR}/{prefix}.audit`:

```
[TIMESTAMP] RECORD_TYPE nesting=N field1=value1 field2=value2
  | continuation_line_1
  | continuation_line_2
```

### Record types

| Record type | Description |
|-------------|-------------|
| Record type | Description |
|-------------|-------------|
| `SESSION_START` | Session initialization (version, pid, model, tools, cwd) |
| `USER` | User message |
| `CONTEXT_ADD` | Context message added |
| `CONTEXT_REMOVE` | Context message removed |
| `CONTEXT_MERGE` | Context merge |
| `CONTEXT_SNAPSHOT` | Context snapshot |
| `COMPRESS_START` | Compression pipeline start |
| `COMPRESS_STEP` | Compression step |
| `COMPRESS_ACTION` | Compression action |
| `COMPRESS_END` | Compression pipeline end |
| `CONSOLE_ERROR` | Console error output |
| `CONSOLE_WARNING` | Console warning output |
| `CONSOLE_INFO` | Console info output |
| `CONSOLE_SUCCESS` | Console success output |

> **Note:** The following record types were listed in earlier versions but do NOT exist in the current code:
> `ASSISTANT`, `TOOL_CALL`, `TOOL_RESULT`, `TOOL_ERROR`, `TOOL_BLOCKED`, `FORK_START`, `FORK_END`, `SUBAGENT_START`, `SUBAGENT_END`, `TOOL_TRUNCATED`

---

## 8. CLI Interface

### 8.1 A2A CLI mode

```python
def a2a_cli_mode(args) -> None
```

**List modes** (short-circuit, exit):
- `--list` / `--list-all` / `--listjson` / `--listjson-all`
- `--list-sessions` (scan LOG_DIR for all sessions)

**Target resolution**:
- `--pid PID` — direct PID
- `--name NAME` — resolve from `list_agents()`

**Actions**:
- `--card` — fetch and display agent card (requires `--pid` or `--name`)
- Query — if `args.inputs[0]` is provided, send query and display response

### 8.2 CLI flags

| Flag | Description |
|------|-------------|
| `--list` | List active agents (table) |
| `--list-all` | List all agents including stale/unreachable |
| `--listjson` | List active agents (JSON) |
| `--listjson-all` | List all agents (JSON) |
| `--list-sessions` | Scan LOG_DIR, print all sessions as JSON |
| `--card` | Fetch agent card |
| `--pid PID` | Target agent by PID |
| `--name NAME` | Target agent by name |
| `--timeout SECONDS` | Query idle timeout (default: `HEARTBEAT_IDLE_TIMEOUT`) |
| `--inputs[0]` | Query string |

---

## 9. Error Handling

### 9.1 Client-side errors

| Exception | Cause |
|-----------|-------|
| `FileNotFoundError` | Socket file does not exist |
| `ConnectionError` | Socket exists but not responding (stale) |
| `TimeoutError` | No response within timeout |
| `RuntimeError` | Unexpected response type |

### 9.2 Server-side errors

- JSON decode errors → `{"type": "error", "message": "..."}`
- OSError during send → silently dropped
- Unknown request type → treated as `query` (default)

### 9.3 Sanity filtering

```python
def _is_sanity_session(log_dir: Path, agent_name: str | None = None) -> bool:
    if "logtest" in str(log_dir).lower():
        return True
    if agent_name and agent_name.startswith("a2aname-"):
        return True
    return False
```

Sessions matching either condition are marked `is_sanity: True` in the
session info dict. TauWeb uses this to exclude sanity sessions from display.

---

## 10. Protocol Version

```
protocol_version = "1.0"
```

- Included in `stream_chunk` envelope
- All extension fields are optional with `getattr` defaults
- Old clients ignore unknown fields
- Old agents work with new clients (missing fields are `None`)
