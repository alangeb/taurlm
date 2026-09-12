# Context Management

## Common Patterns

```python
# Append messages (maintains alternation)
# All user messages are auto-prefixed with [U:TYPE | N:stack | M:msgs | C:pct]
agent.context.append_user("User message")  # → [U:real | N:0 | M:5 | C:12%] User message
agent.context.append_user("Fork task", user_type="fork")  # → [U:fork | N:F | M:1 | C:5%] Fork task
agent.context.append_assistant("Assistant response")

# Synthetic user message (use bridge helper when context ends with user)
ctx.append_synthetic_user_with_bridge("category", "content")

# Synthetic user message AFTER assistant (no bridge needed)
ctx.append_synthetic_user("category", "content")

agent.context.compress(0.30, agent)  # Target 30% reduction
```

> **Note:** `cleanup_synthetic()`, `merge_consecutive_assistants()`, and `close_turn()` are defined in `agent_context_turn.py` and mixed into `TauContext` at runtime — they are not in `agent_context.py` itself.


## User Message Prefix Protocol

All user messages are prefixed with `[U:TYPE | N:stack | M:msgs | C:pct]` to indicate source, nesting, message count, and context usage. Spawned children additionally get `| B:budget%`:

| Type | Meaning | Synthetic? |
|------|---------|-----------|
| `real` | Actual user input (CLI/stdin) | No |
| `meta` | System metadata (bridges, turn markers) | Yes |
| `confirm` | End-of-turn confirmation requests | Yes |
| `inject` | Parent injection via control queue extension point | Yes |
| `system` | System-injected (escalation, recovery) | Yes |
| `fork` | Fork task (spawn() child with inherit_context) | No |
| `subagent` | Subagent task (spawn() child agent) | No |
| `redirect` | Redirect command (clear + new task) | No |
| `repl` | REPL output/feedback (successful turns) | No (survives cleanup) |

> **Note:** `repl_error` messages are mapped to type `system` (not `repl`), so they ARE removed by `cleanup_synthetic()`. Only `repl_output` and `repl_feedback` survive.

**Nesting stack:** `0` (root), `F` (fork), `S` (subagent), `SF` (fork in subagent), etc.

**Key distinction:** `meta`, `confirm`, `inject`, `system` are synthetic (removed by `cleanup_synthetic()`). `real`, `fork`, `subagent`, `redirect`, `repl` are NOT synthetic (preserved across turns).

```python
# Synthetic user messages (auto-prefixed via category mapping)
ctx.append_synthetic_user("continuation", "Continue...")  # → [U:meta | N:0 | M:8 | C:15%] Continue...
ctx.append_synthetic_user("escalation", "You are looping...")  # → [U:system | N:0 | M:9 | C:16%] You are looping...

# Non-synthetic user messages (explicit type)
ctx.append_user("Task", user_type="fork")      # → [U:fork | N:F | M:1 | C:5%] Task
ctx.append_user("Task", user_type="subagent")  # → [U:subagent | N:S | M:1 | C:5%] Task
ctx.append_user("Task", user_type="redirect")  # → [U:redirect | N:0 | M:1 | C:2%] Task
```

## Synthetic Bridge Cleanup & Explicit Merge

After synthetic bridges are removed, consecutive assistant messages may appear.
The merge is **explicit** — the caller decides when to merge. See **DECISIONS.md §18.7** for rationale.

```python
# Remove synthetic bridges only (no automatic merge)
ctx.cleanup_synthetic()

# Explicitly merge consecutive assistant messages (optional, caller decides)
ctx.merge_consecutive_assistants()
```

**RLM mode note:** RLM bypasses continuation bridges entirely. The loop injects exactly one `repl_feedback` message per turn, `cleanup_synthetic()` and `merge_consecutive_assistants()` ARE invoked via `close_turn()` at turn end to remove synthetic bridges and consolidate consecutive assistant messages.

**Merge behavior:** `merge_consecutive_assistants()` merges assistant and user messages.
Consecutive assistant messages are merged (content, reasoning, refusal — newline-separated).
Consecutive user messages are merged gracefully (content concatenated) with a warning logged.
No tool messages exist in RLM mode — only user and assistant roles.

- **assistant**: Merge `content`, `reasoning`, `refusal` (newline-separated)
- **user**: Merge `content` (concatenated with newline), log warning
- No tool role in RLM mode

**Used in `close_turn()`:**
```python
def close_turn(self, reason, skip_cleanup=False):
    if not skip_cleanup:
        self.cleanup_synthetic()           # remove bridges
    self.merge_consecutive_assistants()  # explicit merge
    # ... repair, validate, idempotent terminal-state check
```

**Note:** The RLM loop calls `close_turn(reason, skip_cleanup=True)` to defer cleanup until after the next LLM call, letting compression see the full context (including REPL errors) for a richer summary.

## Synthetic Bridge Requirement

**MANDATORY:** When adding a synthetic user message and the context ends with a user message, a synthetic assistant bridge MUST be added first. This maintains OpenAI alternation compliance (see **DECISIONS.md §18.6**).

```python
# WRONG - violates alternation (user → user without assistant)
ctx.append_synthetic_user("category", "content")

# RIGHT - full bridge (user → assistant → user)
ctx.append_assistant("[Processing...]", synthetic=True)
ctx.append_synthetic_user("category", "content")

# BEST - use the bridge helper (atomic, always correct)
ctx.append_synthetic_user_with_bridge("category", "content")
```

**Bridge patterns by context state:**
- **After user message:** assistant bridge → synthetic user (BRIDGE REQUIRED)
- **After assistant message:** synthetic user directly (no bridge needed)
- **After system message:** synthetic user directly (no bridge needed)

**When to use the bridge helper:**
- Control queue inject (`_process_control_queue()`)
- Loop escalation injection
- Any code path that injects synthetic user messages when context ends with user

**When the bridge is NOT needed:**
- After assistant message (REPL feedback flow)
- After context clear (redirect flow)
- At the start of a turn (normal user input)

## Spawn (Child Agent Delegation)

```python
# Spawn — blank slate (isolated context)
handle = spawn("Review the changes", name="reviewer")
print(handle.last_result)

# Spawn with inherited context (was "fork")
handle = spawn("Based on our analysis, fix the issues", inherit_context=True)

# Multi-turn interaction
handle.send("Now add tests for the fix.")
handle.close()  # Required when done
```

**Nesting stack:** Tracks delegation depth via string concatenation (e.g., `"F"` = fork, `"S"` = subagent, `"SF"` = subagent → fork). Depth is `len(nesting_stack)`. Max nesting depth is 3. Max concurrent spawns is 5.

## End-Turn Recovery

The agent uses a recovery mechanism to handle cases where the model returns
plain text without setting `answer['ready'] = True`. See **DECISIONS.md §18.8** for full rationale.

**Normal flow:**
- Model returns Python code → executed in REPL, loop continues
- Model sets `answer['ready'] = True` → turn ends immediately
- Model returns code without `answer['ready']` → loop continues (REPL output fed back)

**End-of-turn:**
- Model sets `answer['ready'] = True` in Python code
- The `answer['content']` is used as final response
- Turn ends immediately — no confirmation round needed
- Audit event `TURN_COMPLETED` is emitted

**Recovery flow (when answer["ready"] is not set):**
- REPL output/error is fed back as synthetic user message (loop continues)
- Model gets another chance to set `answer['ready'] = True`
- EOT rejection (multi-block, empty content) resets `answer['ready']` to False with a system warning
- Loop terminates on: max_turns (500), max_consecutive_errors (5), or loop escalation level 4+

**Key invariants:**
- Turn state resets at start of each `run_rlm_loop()`
- `answer` dict is fresh each turn (content="", ready=False)
- Consecutive error counter resets on successful code execution
- Loop escalation warnings accumulate monotonically within a turn
- Max nesting depth is 3, max concurrent spawns is 5
