# Spawn System Design

> **Note:** This file is misnamed. Despite the filename `rlm-call-design.md`, it contains
> Spawn System documentation. The RLM LLM call design (`_invoke_llm_with_retry`)
> is documented in `docs/loop-design.md` (LLM Call Changes section) and
> `docs/designs/DECISIONS.md`.
> Full spawn documentation: `docs/designs/SPAWN.md`.

## Overview

The `spawn()` function is pre-loaded in the REPL kernel namespace, allowing the main RLM model to spawn child agents with independent Python REPL kernels. The main model has NO tools — only the Python REPL. Children also have NO tools.

## API

```python
# Spawn a child agent (synchronous — blocks until initial task completes)
handle = spawn("Review the authentication flow", name="auth-reviewer")
print(handle.name, handle.status, handle.last_result)

# Spawn with inherited context (was "fork")
handle = spawn("Based on our analysis, fix the top 3 issues", inherit_context=True)

# List all active spawns
spawns = list_spawns()
for s in spawns:
    print(s)

# Get a spawn by name
h = get_spawn("auth-reviewer")

# Multi-turn interaction
h.send("Check the regression test.")
print(h.last_result)

# Inspect child state (free, no LLM call)
h.inspect('overview')
h.inspect('last_n', n=5)

# Extend budget if child yielded
h.extend_budget(0.15)
h.resume('finish')

# Close when done (REQUIRED)
h.close()
```

## SpawnHandle

```python
class SpawnHandle:
    # Properties
    last_result: str      # Last answer string from child
    status: str           # running | completed | yielded | budget_exhausted | error | closed
    turns: int            # Number of completed turns
    min_B: float          # Lowest budget reached
    budget: float         # Initial budget allocation

    # Methods
    send(msg: str) -> str             # New instruction (blocks, returns answer)
    resume(hint: str | None = None) -> str  # Resume with optional direction
    summarize() -> str               # 1-turn summary
    inspect(mode, ...) -> str        # View child context (free)
    extend_budget(amount: float)     # Grant more work budget
    close()                          # Free resources (REQUIRED when done)
```

## Spawn Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `task` | `str` | required | Initial task for the child |
| `inherit_context` | `bool` | `False` | `True` = child inherits parent conversation |
| `name` | `str` | `None` | Label for tracking |
| `budget` | `float` | `0.70` | Max context fraction before forced stop |

## Child Lifecycle

1. **Spawn**: `spawn(task, name, budget)` — creates child agent, blocks until initial task completes
2. **Run**: Child runs its own RLM loop with a decreasing work budget (B:)
3. **Yield**: Child can set `answer['yield']=True` when budget is low
4. **Multi-turn**: Parent calls `handle.send()` for subsequent tasks
5. **Cleanup**: Parent MUST call `handle.close()` when done

## Limits

- Max 5 concurrent spawns
- Max nesting depth 3
- Budget only decreases (work spent is not refunded)
- `inherit_context=True` is expensive (child starts with parent's context)

## Registry

The `SpawnRegistry` tracks all active spawns. Each agent has its own registry.

```python
from rlm.spawn import SpawnRegistry
registry = SpawnRegistry()
registry.count_active()  # Number of active spawns
```

## Design Decisions

- **Synchronous**: No async, no threads, no callbacks. `spawn()` blocks.
- **Persistent**: All spawns are persistent. No one-shot mode.
- **Budget-based**: Children have a work budget (B:) that only decreases.
- **Parent-controlled**: Parent decides when to close children.
- **Context-lean**: Parent sees only `last_result` by default.