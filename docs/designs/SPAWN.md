# SPAWN — Unified Persistent Agent Delegation

## Status: DRAFT v2 — 2026-09-12

## 1. Overview

`spawn()` is the ONLY delegation API. It replaces `subagent()`, `fork()`, and the old
one-shot `spawn()`. Every spawn is persistent. Every spawn returns a `SpawnHandle`.

The parent (LLM in REPL) creates children via `spawn()`, steers them via handle methods,
and inspects them as needed. Children are bounded by a context-percentage budget.

**Design principles:**
- Synchronous. No async, no threads, no callbacks.
- Parent sees only `last_result` by default. Context-lean.
- Child is self-aware: sees its budget, can voluntarily stop and request extension.
- Live output: child turns stream to console as they happen.
- Max 5 concurrent spawns. Max nesting depth 3.

## 2. API (what the LLM sees in the REPL namespace)

### 2.1 `spawn(task, *, inherit_context=False, name=None, budget=0.70)`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `task` | `str` | required | Initial task for the child |
| `inherit_context` | `bool` | `False` | `True` = child inherits parent conversation (was "fork"). `False` = blank slate (was "subagent") |
| `name` | `str` | `None` | Label for tracking. Shown in `# ctx:` line and `list_spawns()` |
| `budget` | `float` | `0.70` | Max context fraction before forced stop. 0.70 = 70% of child's context window |

**Returns:** `SpawnHandle` (always).

**Behavior:**
- Blocks until the child completes its initial task (sets `answer['ready']=True` or hits budget).
- On return, `handle.last_result` contains the child's final answer string.
- `handle.status` is `"completed"` (normal finish) or `"error"` (failure).
- If max spawns (5) already active, raises `SpawnLimitError("Max 5 active spawns")`.
- If nesting depth would exceed 3, raises `NestingLimitError`.

**Example:**
```python
# Isolated child (was subagent):
h = spawn("Count lines in agent_core.py", name="counter")
print(h.last_result)  # "agent_core.py has 1234 lines"
h.close()

# Context-inherited child (was fork):
h = spawn("Now fix the top 3 issues we found", inherit_context=True, name="fixer")
print(h.last_result)
h.close()

# With custom budget:
h = spawn("Refactor the entire auth module", name="refactorer", budget=0.85)
```

### 2.2 `SpawnHandle` — full method reference

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `.spawn_id` | `str` | Unique 12-char ID (e.g., `"a3f2b1c9d4e5"`) |
| `.name` | `str` | The name given at spawn (or `None`) |
| `.status` | `str` | `"running"` \| `"completed"` \| `"yielded"` \| `"budget_exhausted"` \| `"error"` \| `"closed"` |
| `.last_result` | `str` | Child's most recent `answer['content']` |
| `.turns` | `int` | Total turns executed by child (monotonic, never decreases) |
| `.context_pct` | `float` | Current context usage (0.0-1.0). Can decrease after compression. |
| `.min_B` | `float` | Lowest B value reached (work spent). Useful for monitoring. |

| `.budget` | `float` | Current budget fraction |


#### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `.send(prompt)` | `(str) -> str` | Send a new user message. Blocks until child responds. Returns child's `answer['content']`. |
| `.resume(hint=None)` | `(str?) -> str` | Resume child's current work. Optional direction hint. Returns child's response. |
| `.summarize()` | `() -> str` | 1-turn LLM summary of child's work. Cache-friendly. Returns summary string. |
| `.inspect(mode, **kw)` | `(str, **kw) -> str` | Read child's context. Free (no LLM call, no turn). See §2.3. |
| `.extend_budget(amount)` | `(float) -> None` | Grant additional work budget. Amount is added to current B. |
| `.close()` | `() -> None` | Terminate. Flush audit. Free resources. Handle becomes `"closed"`. |
| `.status_dict()` | `() -> dict` | Full status as dict (for `list_spawns()` and debugging). |

#### `__str__` / `__repr__`

```python
def __str__(self) -> str:
    return self.last_result or ""

def __repr__(self) -> str:
    name = self.name or self.spawn_id[:8]
    return f"SpawnHandle({name!r}, status={self.status!r}, turns={self.turns}, B={self.min_B:.3f})"
```

This means `print(handle)` shows the last result. `f"{handle}"` works. Old code that
did `result = subagent("...")` and then `print(result)` will still work if adapted to
`h = spawn("..."); print(h)`.

### 2.3 `.inspect(mode, **kwargs)` — detailed modes

All inspect calls are **free**: no LLM call, no turn consumed, no budget impact.
They read the child's `context._messages` list directly.

| Mode | Params | Returns | Use case |
|------|--------|---------|----------|
| `"overview"` | — | Message count, role breakdown, first/last previews | "What's the shape of this conversation?" |
| `"last_user"` | `chars=80` | Last user message, truncated | "What did I last tell it?" |
| `"last_assistant"` | `chars=80, include_reason=False` | Last assistant message, truncated | "What did it last say/do?" |
| `"last_n"` | `n=5, chars=80, include_reason=False` | Last N messages, each truncated to chars | "What has it been doing recently?" |
| `"first_n"` | `n=5, chars=80, include_reason=False` | First N messages, each truncated to chars | "How did it start?" |

**Return format for `"overview"`:**
```
Messages: 5 (system:1, user:2, assistant:2)
First: You are TauRLM, a helpful AI coding agent...
Last: answer['content'] = "agent_core.py has 1234 lines"...
```

**Return format for `"last_n"`:**
```
[assistant] print("Reading file...")
[assistant] content = Path("agent_core.py").read_text()
[assistant] lines = content.count("\n") + 1
[assistant] answer['content'] = f"Lines: {lines}"
[assistant] answer['ready'] = True
```

**`include_reason=True`**: If the LLM returns reasoning/thinking tokens (model-dependent),
include them. Default `False` to keep output clean.

**Error handling:** If handle is `"closed"`, raises `RuntimeError("Cannot inspect closed handle")`.

### 2.4 `.summarize()` — detailed behavior

**Purpose:** Get a 2-4 sentence summary of what the child has done. Uses 1 LLM call.

**Prompt (appended as user message to child's context):**
```
Summarize what you have done so far. Be concise: what was the task, what did you accomplish,
what remains. Set answer['content'] to the summary. Set answer['ready']=True.
Set answer['content'] to your summary. Set answer['ready']=True.
```

**Implementation:**
1. Save child's current answer state.
2. Call `agent.invoke(prompt)` — this runs the normal RLM loop.
3. **Force 1-turn limit**: Set a flag `agent._force_single_turn = True` before the call.
   In `run_rlm_loop`, after the first turn completes (answer checked), if this flag is set,
   force-exit regardless of `answer['ready']`.
4. Extract `answer['content']` as the summary.
5. Restore answer state.
6. **Counts as a normal turn** toward `handle.turns`.
7. **Updates `min_B`** (the 1 turn may decrease B).

**Cache efficiency:** The prompt is appended AFTER the existing context. The LLM call
uses the full existing context as prefix (KV cache hit). Only the ~50 token prompt is new.
This makes it fast and cheap — essentially just a decode of the summary.

**If child has 0 turns:** Returns `"[No work done yet]"` without an LLM call.

**If child is `"closed"`:** Raises `RuntimeError`.

### 2.5 `.resume(hint=None)` — detailed behavior

**Purpose:** Tell the child to keep working, optionally with a direction change.

**Prompt:**
- Without hint: `"Continue your current work."`
- With hint: `"Continue your current work. Direction: {hint}"`

**Implementation:** Auto-compresses if context >= 70% (to 28%). Then standard `agent.invoke(prompt)`. Counts as a normal turn.

**If child is `"completed"`:** This is how you RESTART a completed child. The status
transitions back to `"running"`. The child's context is preserved — it remembers what
it did before. This is the primary use case for persistent handles over one-shot.

**If child is `"closed"`:** Raises `RuntimeError`.

### 2.6 `.send(prompt)` — detailed behavior

**Purpose:** Send an arbitrary new user message to the child.

**Implementation:** Auto-compresses if context >= 70% (to 28%). Then standard `agent.invoke(prompt)`. The prompt is sent as-is. No wrapper text.

**If child is `"completed"`:** Same as `.resume()` — restarts the child. Status → `"running"`.
**If child is `"error"`:** Raises `RuntimeError("Cannot send to errored agent. Close and respawn.")`.
**If child is `"closed"`:** Raises `RuntimeError`.

### 2.7 `.extend_budget(amount)` — rules

- Can only INCREASE: `new_budget > current_budget`.
- Cannot go below current `context_pct`: `new_budget >= self.context_pct`.
- Max: `0.95` (never allow 100% — always leave room for the system).
- Updates the child's `agent.spawn_budget` attribute.
- Resets `agent._budget_warned = False` (so the warning can fire again at the new level).
- If child is `"closed"`, raises `RuntimeError`.

**Example flow:**
```python
h = spawn("Refactor auth module", name="refactorer", budget=0.70)
# ... child works, hits 65%, voluntarily stops with summary ...
print(h.last_result)  # "I've refactored login and tokens. Session management remains."
h.extend_budget(0.15)  # Grant extension (B += 0.15)
result = h.resume("Yes, finish the session management.")
```

### 2.8 `.close()` — cleanup steps

1. Set `status = "closed"`.
2. Set `handle._agent = None` (allow GC).
3. Remove from `SpawnRegistry`.
4. Idempotent: calling `.close()` on already-closed handle is a no-op.

**Note:** `__del__` warns (via `logging.warning`) if a handle is GC'd without `.close()`.

### 2.9 `list_spawns()` — namespace function

Returns a list of dicts for all active (non-closed) spawn handles:

```python
[
    {
        "name": "reviewer",
        "spawn_id": "a3f2b1c9d4e5",
        "status": "completed",
        "turns": 7,
        "context_pct": 34.2,
        "min_B": 0.2345,
        "budget": 0.70,
                "last_result_preview": "...agent_core.py has 1234 lines",
    },
    {
        "name": "fixer",
        "spawn_id": "b7e4d2a1f6c8",
        "status": "running",
        "turns": 12,
        "context_pct": 58.1,
        "min_B": 0.1234,
        "budget": 0.85,
                "last_result_preview": "...applying fix to line 42...",
    },
]
```

`last_result_preview` = first 80 chars of `last_result`. `min_B` = lowest B reached.

If no active spawns: returns `[]`.

## 3. Budget System

### 3.1 Unit: Context percentage (0.0 to 1.0)

Fraction of the child's `max_context_tokens`. Model-agnostic. Stable across
compression events (we track peak separately).

### 3.2 Enforcement (in `run_rlm_loop`, before each LLM call)

```python
# --- BUDGET CHECK (alongside max_turns check) ---
if hasattr(agent, 'spawn_budget'):
    _, pct, _, _ = agent.context.get_usage_stats(agent.max_context_tokens)
    
    # Update peak
    if pct > getattr(agent, 'spawn_peak_pct', 0.0):
        agent.spawn_peak_pct = pct
    
    # Hard stop
    if pct >= agent.spawn_budget:
        agent._cleanup_pending = True
        answer = agent.get_answer()
        answer_str = _answer_to_str(answer) if answer and answer.content else ""
        if not answer_str:
            answer_str = f"[Stopped: budget {agent.spawn_budget:.0%} exhausted at {pct:.0%} context]"
        agent.context.close_turn(answer_str, skip_cleanup=True)
        return answer_str
    
    # Soft warning (fire once per budget level)
    warn_threshold = agent.spawn_budget - 0.10
    if pct >= warn_threshold and not getattr(agent, '_budget_warned', False):
        agent._budget_warned = True
        warning_text = (
            f"[BUDGET WARNING] You are at {pct:.0%} context usage. "
            f"Your budget is {agent.spawn_budget:.0%}. "
            f"You have limited turns remaining. "
            f"If you cannot finish, set answer['content'] to a summary of progress "
            f"and set answer['ready']=True to request a budget extension from your parent."
        )
        # Inject into the feedback parts (same as other warnings)
```

**Placement in code:** Inside `run_rlm_loop()`, after the `max_turns` check (line ~148),
before the context validation. The warning text is appended to the `parts` list that
becomes the synthetic user message.

### 3.3 Budget extension protocol (voluntary)

The child's system prompt (AGENT_RLM.md) includes:

```markdown
### BUDGET

Your `# ctx:` line shows your budget (e.g., `budget: 70%`). This is the context
percentage at which you will be FORCED to stop.

**If you are approaching your budget and have not finished:**
1. Set `answer['content']` to a brief summary: what you've done + what remains.
2. Set `answer['ready'] = True`.
3. Your parent will see your summary and decide whether to grant a budget extension.

**Do NOT wait until the last moment.** If you're at 50%+ with significant work
remaining, consider stopping early to request an extension. A voluntary stop with
a clear summary is better than a forced stop with no context.
```

**Flow:**
1. Child sees `budget: 70%` in its `# ctx:` line.
2. At ~55-65%, child decides to stop voluntarily.
3. Child sets `answer['content'] = "I've done X, Y. Z remains. Need ~10 more turns."`
4. Child sets `answer['ready'] = True`.
5. `run_rlm_loop` exits normally (answer check passes).
6. `handle.last_result` = the summary string.
7. `handle.status` = `"completed"`.
8. Parent reads `h.last_result`, sees the request.
9. Parent calls `h.extend_budget(0.15)` then `h.resume("Yes, finish Z.")`.
10. Child resumes with more room.

**The parent does NOT need to call `.summarize()`** — the child's voluntary stop
IS the summary. This is the key efficiency gain.

### 3.4 Relationship to existing compression

| Mechanism | Threshold | Action | Scope |
|-----------|-----------|--------|-------|
| Budget warning | `budget - 0.10` | Inject warning text | Spawned agents only |
| Budget hard stop | `budget` | Force end turn | Spawned agents only |
| Proactive compression | 85% | Compress to 50% | ALL agents (safety net) |
| API overflow protection | ~95% | Emergency compress | ALL agents |

The budget (default 70%) fires BEFORE the 85% proactive compression. For spawned
agents, the budget is the primary control. The 85% compression is a backstop for
edge cases (e.g., a single turn that adds 30% context).

### 3.5 What the child sees in `# ctx:`

For spawned agents, the `# ctx:` line gains a `budget:` field:

```
# ctx: ~12500 tk (6.9%) 8 msgs | cache: 35%/34%/33% | pid: 495666(70269) | 260816 110349 | entropy: inf (0) | cwd: ~/taurlm | nest: S | budget: 70% | llmg: cuda | name: reviewer
```

For root agent (no budget):
```
# ctx: ~45000 tk (25.0%) 30 msgs | ... | nest: . | spawns: 2 | llmg: cuda | name: default
```

**`spawns: N`** appears ONLY for the root agent (or any agent that has active children).
It reminds the LLM it has children to manage.

## 4. Nesting

### 4.1 Types (UNCHANGED from current)

| Char | Meaning | Created by |
|------|---------|-----------|
| `F` | Context-inherited (child gets parent conversation) | `spawn(task, inherit_context=True)` |
| `S` | Isolated (blank slate) | `spawn(task)` or `spawn(task, inherit_context=False)` |

### 4.2 Depth

- Max: **3** (hardcoded `MAX_NESTING_DEPTH=3` in `rlm/spawn.py`; `config.nesting.depth_threshold` exists in schema but is NOT wired).
- Root = depth 0 (stack = `""` or `"0"`).
- Child of root = depth 1 (stack = `"S"` or `"F"`).
- Grandchild = depth 2 (stack = `"SS"`, `"SF"`, `"FS"`, `"FF"`).
- Great-grandchild = depth 3 (stack = 3 chars). **MAX.**
- At depth 3, `spawn()` raises `NestingLimitError("Max nesting depth (3) reached")`.

### 4.3 Enforcement

In `spawn()`:
```python
current_depth = len(parent_agent.nesting_stack)
if current_depth >= 3:
    raise NestingLimitError(f"Max nesting depth ({current_depth}) reached. Cannot spawn.")
```

### 4.4 Nesting restriction in system prompt

At depth 2 (one below max), the child's system prompt gets an additional paragraph:
```
You are at nesting depth 2 of 3. You MAY spawn one more level of children,
but they will be at the maximum depth and cannot spawn further.
```

At depth 3:
```
You are at maximum nesting depth (3). You CANNOT spawn child agents.
```

(This is the existing `_nesting_restriction_text` mechanism, unchanged.)

## 5. Live Output

### 5.1 Mechanism

When `spawn()` is called (or `.send()`/`.resume()` is called):
1. Save `sys.stdout` and `sys.stderr` (may be captured by parent's kernel).
2. Restore real fd 1 and fd 2 as `sys.stdout`/`sys.stderr`.
3. Call `agent.invoke(task)` — child's RLM loop runs, all output goes to real console.
4. Restore saved stdout/stderr.
5. Close the real fd handles.

This is the SAME mechanism already used in `_spawn_oneshot()` (lines 365-375 of spawn.py).
We keep it for persistent spawns too.

### 5.2 What the user sees

```
# ctx: ~5550 tk (3.1%) 2 msgs | ... | nest: S | budget: 70% | name: reviewer
[REPL CODE] ────────────────────────────────────────────────────────
print("Analyzing agent_core.py...")
────────────────────────────────────────────────────────────────────
[REPL OUTPUT] ────────────────────────────────────────────────────────
Analyzing agent_core.py...
────────────────────────────────────────────────────────────────────
# ctx: ~6200 tk (3.4%) 4 msgs | ... | nest: S | budget: 70% | name: reviewer
[REPL CODE] ────────────────────────────────────────────────────────
...
```

Each child turn produces its own `# ctx:` line (with `nest: S` or `nest: F`),
code block, and output block. All in real-time. No buffering.

### 5.3 Distinction from parent

The user distinguishes parent vs child output by:
- `nest:` field (`.` for root, `S`/`F`/`SS` etc. for children)
- `name:` field (child's spawn name)
- `budget:` field (only present for children)

## 6. What the Parent Sees (Context-Lean)

### 6.1 Default: `last_result` only

After `spawn()` or `.send()` returns, the parent has a string. That's it.
No intermediate turns, no code, no reasoning. The parent's context grows by:
- The `spawn(...)` call: ~1 line of code
- The result: 1 string (typically <500 chars)

### 6.2 When you need more

| Need | Method | Cost |
|------|--------|------|
| "What's the state?" | `h.status_dict()` | Free (reads attributes) |
| "What did it do?" | `h.inspect("last_n", n=5, chars=80)` | Free (reads context) |
| "Summarize it" | `h.summarize()` | 1 LLM call (cache-friendly) |
| "Show me the start" | `h.inspect("first_n", n=5, chars=100)` | Free (reads context) |

### 6.3 Parent context budget

The parent should treat each spawn handle as ~200-500 tokens of context overhead
(the spawn call + result string). With max 5 spawns, that's ~1000-2500 tokens.
Negligible compared to a 180K context window.

## 7. Registry & Limits

### 7.1 `SpawnRegistry` (replaces `PersistentAgentRegistry`)

```python
class SpawnRegistry:
    """Process-level singleton. Tracks all active spawn handles."""
    MAX_SPAWNS = 5
    
    _instance: SpawnRegistry | None = None
    _lock = threading.Lock()
    
    def __new__(cls):
        # Singleton pattern (same as current PersistentAgentRegistry)
        ...
    
    def register(self, handle: SpawnHandle) -> None:
        if len(self._handles) >= self.MAX_SPAWNS:
            raise SpawnLimitError(f"Max {self.MAX_SPAWNS} active spawns. Close one first.")
        self._handles[handle.spawn_id] = handle
    
    def unregister(self, spawn_id: str) -> None:
        self._handles.pop(spawn_id, None)
    
    def list_active(self) -> list[SpawnHandle]:
        return [h for h in self._handles.values() if h.status != "closed"]
    
    def count_active(self) -> int:
        return len(self.list_active())
```

### 7.2 Max 5 spawns

- Checked at `register()` time.
- "Active" = not `"closed"`. Completed and error handles still count until closed.
- Rationale: Each handle holds a full `TauErgon` instance with context in memory.
  5 agents × ~50K tokens context = ~250K tokens in RAM. Bounded.

### 7.3 Thread safety

All registry operations are under `threading.Lock()`. The synchronous model means
we rarely contend, but the lock is there for safety (e.g., if `.close()` is called
from a signal handler).

## 8. `# ctx:` Line Changes

### 8.1 Current format
```
# ctx: ~5550 tk (3.1%) 2 msgs | cache: 36%/36%/36% | pid: 495666(70269) | 260816 110349 | entropy: inf (0) | cwd: ~/taurlm | nest: S | llmg: cuda | name: default
```

### 8.2 New format (spawned agent)
```
# ctx: ~5550 tk (3.1%) 2 msgs | cache: 36%/36%/36% | pid: 495666(70269) | 260816 110349 | entropy: inf (0) | cwd: ~/taurlm | nest: S | budget: 70% | llmg: cuda | name: reviewer
```

### 8.3 New format (root with active spawns)
```
# ctx: ~45000 tk (25.0%) 30 msgs | cache: 80%/75%/70% | pid: 12345(1) | 260816 110349 | entropy: 1.25 (45) | cwd: ~/taurlm | nest: . | spawns: 2 | llmg: cuda | name: default
```

### 8.4 Implementation

In `display_status.py`, the `print_context_status()` function:
- If `agent.spawn_budget` is set (i.e., this is a spawned agent): add `| budget: {int(budget*100)}%`
- If `SpawnRegistry().count_active() > 0` AND this is the root agent: add `| spawns: {count}`

## 9. AGENT_RLM.md Changes

### 9.1 Remove
- All references to `subagent()` and `fork()` as separate functions.
- The "PREFER subagent" / "Use fork" language.
- The old delegation decision matrix.

### 9.2 Add: Delegation section

```markdown
## DELEGATION

You can delegate work to child agents using `spawn()`.

### Creating a child
spawn(task, *, inherit_context=False, name=None, budget=0.70)

- inherit_context=False: Child starts fresh (isolated). Use for independent tasks.
- inherit_context=True: Child inherits your conversation. Use when context is needed.
- name: Label for tracking (shown in console).
- budget: Max context % before forced stop (default 70%).

Always returns a SpawnHandle. The call blocks until the child finishes.

### Controlling a child
handle.send("New instruction")       # Send a message
handle.resume("hint")              # Continue with optional direction
handle.summarize()                   # 1-turn summary (cache-friendly)
handle.inspect("last_n", n=5)        # See recent turns (free)
handle.extend_budget(0.15)            # Grant budget extension
handle.close()                       # Clean up

### Listing children
list_spawns()                        # All active handles with status

### Rules
- Max 5 active spawns. Close handles when done.
- Max nesting depth 3.
- You only see the child's last_result. Use .inspect() or .summarize() for more.
- If a child stops voluntarily with a summary, consider granting a budget extension.
```

### 9.3 Add: Budget awareness (for children)

```markdown
### BUDGET

Your `# ctx:` line shows `budget: N%`. This is your context limit.

If you are approaching your budget and have not finished:
1. Set `answer['content']` to a summary: what you've done + what remains.
2. Set `answer['ready'] = True`.
3. Your parent will decide whether to extend your budget.

Stop early rather than being forced. A clear summary helps your parent decide.
```

## 10. Implementation Plan

### 10.1 Files to modify

| File | Change | Lines affected (est.) |
|------|--------|----------------------|
| `src/rlm/spawn.py` | **Rewrite.** New `SpawnHandle`, `SpawnRegistry`, `spawn()`. Remove `_spawn_oneshot`. | Full file (532 → ~700) |
| `src/rlm/kernel.py` | Update `_make_spawn_callable()`. Remove `persistent` param. Add `list_spawns` to namespace. Remove `subagent` stub (line 430). | ~50 lines |
| `src/rlm/kernel_wiring.py` | Update `verify_kernel_namespace`. Remove subagent/fork checks. Add `list_spawns`. | ~20 lines |
| `src/agent_loop.py` | Add budget check + warning in `run_rlm_loop()`. Add `_force_single_turn` support for `.summarize()`. | ~30 lines |
| `src/agent_console/display_status.py` | Add `budget:` and `spawns:` to ctx line. | ~15 lines |
| `src/agent_core.py` | Add `spawn_budget`, `spawn_peak_pct`, `_budget_warned`, `_force_single_turn` attributes. Update `get_status()`. | ~20 lines |
| `src/AGENT_RLM.md` | Update delegation section. Add budget section. Remove subagent/fork. | ~100 lines |
| `docs/designs/DECISIONS.md` | Mark 6.1-6.16 superseded. Add new SPAWN decisions. | ~30 lines |
| `src/agent_subagent.py` | Keep `invoke_subagent_sync`/`invoke_fork_sync` as internal. Remove public wrappers if any. | ~10 lines |

### 10.2 New files

| File | Purpose |
|------|---------|
| `docs/designs/SPAWN.md` | This design document (already created) |
| `src/tests/rlm/test_spawn_v2.py` | New test suite for the redesigned spawn |

### 10.3 Phased implementation

**Phase 1: Core refactor (breaking change)**
- [ ] Rewrite `spawn.py`: new `SpawnHandle`, `SpawnRegistry`, `spawn()` always persistent
- [ ] Update `kernel.py`: new `_make_spawn_callable()`, add `list_spawns`, remove subagent stub
- [ ] Update `kernel_wiring.py`: new verification
- [ ] Update `AGENT_RLM.md`: new delegation docs
- [ ] Update all existing tests that use `subagent()`/`fork()`/one-shot `spawn()`
- [ ] Verify: `spawn("task")` returns handle, `.last_result` has answer, `.close()` works

**Phase 2: Budget**
- [ ] Add `budget` param to `spawn()`
- [ ] Add `spawn_budget`, `spawn_peak_pct`, `_budget_warned` to `agent_core.py`
- [ ] Add budget check in `run_rlm_loop()` (hard stop + soft warning)
- [ ] Add `budget:` to `# ctx:` line in `display_status.py`
- [x] Add `.extend_budget()` method
- [ ] Add budget section to `AGENT_RLM.md` (child-facing)
- [ ] Tests: budget warning fires, hard stop works, extend_budget works, peak tracking

**Phase 3: Control interface**
- [ ] Implement `.summarize()` with `_force_single_turn` flag
- [x] Implement `.resume(hint)`
- [x] Implement `.inspect(mode, **kwargs)` — all 5 modes
- [ ] Implement `.status_dict()`
- [ ] Add `list_spawns()` to kernel namespace
- [ ] Add `spawns: N` to root `# ctx:` line
- [ ] Tests: each method, edge cases (closed handle, 0 turns, etc.)

**Phase 4: Polish**
- [ ] Max 5 spawns enforcement + `SpawnLimitError`
- [ ] Nesting depth 3 enforcement + `NestingLimitError`
- [ ] `__str__`/`__repr__` on SpawnHandle
- [ ] Live output verification (child turns visible on console)
- [ ] Update `DECISIONS.md`
- [ ] Update wiki
- [ ] Full test suite green

### 10.4 Testing strategy

| Test | What it verifies |
|------|-----------------|
| `test_spawn_returns_handle` | `spawn()` always returns `SpawnHandle` |
| `test_spawn_isolated` | `inherit_context=False` → fresh context |
| `test_spawn_inherited` | `inherit_context=True` → parent messages present |
| `test_handle_last_result` | After spawn, `.last_result` has the answer |
| `test_handle_send` | `.send()` works, returns new result |
| `test_handle_continue` | `.resume()` restarts completed child |
| `test_handle_summarize` | `.summarize()` returns 2-4 sentence summary |
| `test_handle_inspect_overview` | `.inspect("overview")` shows all messages truncated |
| `test_handle_inspect_last_n` | `.inspect("last_n", n=3)` shows last 3 |
| `test_handle_extend_budget` | Can increase B, amount is added |
| `test_handle_close` | After close, methods raise RuntimeError |
| `test_budget_warning` | At budget-0.10, warning is injected |
| `test_budget_hard_stop` | At budget, loop exits |
| `test_budget_min_B_tracking` | `min_B` only decreases (tracks lowest B reached) |
| `test_max_spawns` | 6th spawn raises SpawnLimitError |
| `test_nesting_depth` | Depth 4 raises NestingLimitError |
| `test_list_spawns` | Returns all active handles |
| `test_ctx_line_budget` | Spawned agent ctx line shows `budget: 70%` |
| `test_ctx_line_spawns` | Root ctx line shows `spawns: N` |
| `test_str_compat` | `str(handle)` returns last_result |
| `test_live_output` | Child output appears on real stdout |

## 11. Error Types

```python
class SpawnError(Exception):
    """Base exception for spawn-related errors."""

class SpawnLimitError(SpawnError):
    """Raised when max active spawns (5) is reached."""

class NestingLimitError(SpawnError):
    """Raised when max nesting depth (3) is reached."""

class SpawnClosedError(SpawnError):
    """Raised when operating on a closed handle."""
```

All handle methods check status and raise `SpawnClosedError` if `"closed"`.
`.send()`/`.resume()` raise `SpawnClosedError` if `"error"` (with message suggesting close+respawn).

## 12. What We're NOT Doing

- **Timeouts** — No timeout on `.send()`, `.resume()`, or `spawn()`. We trust the budget and the system. The budget IS the timeout. We want as few "asynchronous" activities as possible — everything is synchronous and bounded by context %.
- **Async/concurrent spawns** — all synchronous (decision 6.12)
- **Spawn-to-spawn communication** — no direct messaging between siblings
- **Budget inheritance** — each spawn gets its own budget
- **Spawn persistence across process restarts** — spawns die with parent
- **Streaming results** — parent gets full result string, not a stream
- **Child-initiated spawn of parent** — no circular references
- **Dynamic system prompt changes** — child's system prompt is fixed at spawn time

## 13. Migration

| Old code | New code |
|----------|----------|
| `result = subagent("task")` | `h = spawn("task"); result = h.last_result; h.close()` |
| `result = fork("task")` | `h = spawn("task", inherit_context=True); result = h.last_result; h.close()` |
| `result = spawn("task")` (one-shot) | `h = spawn("task"); result = h.last_result; h.close()` |
| `h = spawn("task", persistent=True)` | `h = spawn("task")` (persistent is now default/only) |
| `h = spawn("task", persistent=True, name="x")` | `h = spawn("task", name="x")` |

**Shorthand for simple cases:**
```python
# One-liner for fire-and-forget:
result = spawn("Count lines").last_result  # Handle auto-GC'd if not stored
# But you SHOULD close it:
h = spawn("Count lines"); result = h.last_result; h.close()
```

**Note:** If a handle is not stored (no variable reference), it's still registered
in `SpawnRegistry` and counts toward the max-5 limit. The user MUST call `.close()`.
We could add a `__del__` that auto-closes, but that's unreliable in Python.
**Decision: No auto-close. User must explicitly close.**

## 14. Open Items (post-implementation)

- [ ] Consider: should `.close()` be called automatically when the handle goes out of scope? (Python `__del__` is unreliable. Context manager protocol? `with spawn("task") as h: ...`?)
- [ ] Consider: should `list_spawns()` show closed handles too (with a flag)?
- [ ] Consider: should we add a `.kill()` method (hard stop, no wrap-up) vs `.close()` (graceful)?
- [ ] Consider: should the budget warning be configurable (on/off)?
- [ ] Future: async spawns (explicitly deferred)
- [ ] Future: spawn templates (pre-configured budgets, system prompts)

## 15. Critical Review Refinements (post-planning audit)

### 15.1 AgentStatus changes (`agent_models.py`)

Add two fields to `AgentStatus` dataclass:
```python
# --- Spawn/budget info ---
spawn_budget: float = 0.0    # 0.0 = no budget (root agent)
spawns_count: int = 0        # active spawns (only meaningful for root)
```

### 15.2 `get_status()` changes (`agent_core.py` line ~969)

Add to the `AgentStatus(...)` constructor:
```python
# Spawn/budget
spawn_budget=getattr(self, 'spawn_budget', 0.0),
spawns_count=SpawnRegistry().count_active() if not self.nesting_stack else 0,
```

### 15.3 `display_status.py` insertion point

Current code (line ~145-152):
```python
if status.nesting_stack:
    base_content += f" | nest: {status.nesting_stack}"
    color = Colors.INVERT_BLUE
else:
    base_content += " | nest: ."
    color = Colors.INVERT_CYAN

base_content += f" | llmg: {status.current_group_name}"
base_content += f" | name: {status.agent_name}"
```

New code:
```python
if status.nesting_stack:
    base_content += f" | nest: {status.nesting_stack}"
    if status.spawn_budget > 0:
        base_content += f" | budget: {int(status.spawn_budget*100)}%"
    color = Colors.INVERT_BLUE
else:
    base_content += " | nest: ."
    if status.spawns_count > 0:
        base_content += f" | spawns: {status.spawns_count}"
    color = Colors.INVERT_CYAN

base_content += f" | llmg: {status.current_group_name}"
base_content += f" | name: {status.agent_name}"
```

### 15.4 Budget info delivery to child (NO system prompt modification)

The child learns its budget through THREE channels:
1. **`# ctx:` line** (every turn): Shows `budget: 70%`. Passive, always visible.
2. **Initial task prepending**: The `spawn(task)` call sends the task as the first user message. We prepend:
   ```
   [BUDGET: 70%] Your context budget is 70%. You will be forced to stop at this limit.
   If approaching it with work remaining, set answer['content'] to a progress summary
   and answer['ready']=True to request an extension from your parent.
   
   {task}
   ```
3. **Warning at budget-0.10**: Injected as a synthetic user message by `run_rlm_loop`.

**No system prompt changes needed.** The child's system prompt is the standard AGENT_RLM.md
(same as parent). The budget info is contextual, not structural.

### 15.5 `.summarize()` implementation detail

The `_force_max_turns` mechanism:
```python
# In SpawnHandle.summarize():
agent = self._agent
agent._force_max_turns = 1  # Safety: max 1 turn
result = agent.invoke(SUMMARIZE_PROMPT)
agent._force_max_turns = None  # Clear
return result
```

In `run_rlm_loop()` (line ~97, where max_turns is read):
```python
max_turns = getattr(agent, '_force_max_turns', None) or \
    (getattr(rlm_config, "max_turns", 1000) if rlm_config else 1000)
```

This is cleaner than a boolean flag. The existing `turn_count > max_turns` check handles the rest.

### 15.6 Child's `agent_name`

At spawn time, set `agent.agent_name = name or spawn_id`. This makes the `# ctx:` line
show the spawn name: `| name: reviewer`. Already partially done in current code
(`agent_name` defaults to `"default"`).

### 15.7 What to remove from `kernel.py`

| Line(s) | What | Action |
|---------|------|--------|
| 252 | `namespace["subagent"] = self._make_subagent_callable()` | DELETE |
| 426-476 | `_make_subagent_callable` method | DELETE |
| ~510-530 | Fork stub (`_make_fork_callable` or similar) | DELETE |
| 260-308 | `_make_spawn_callable` | REWRITE (remove `persistent` param) |

### 15.8 `verify_kernel_namespace` — NO CHANGE NEEDED

It checks `["spawn", "host_request", "available_skills", "load_skill", "list_spawns", "get_spawn"]`.
Removing `subagent`/`fork` from the namespace won't break verification.

### 15.9 AGENT_RLM.md — sections to modify

| Lines | Content | Action |
|-------|---------|--------|
| 32-40 | "PREFER subagent" / "Use fork" (1st copy) | REPLACE with spawn language |
| 61-69 | "PREFER subagent" / "Use fork" (2nd copy — duplicate!) | DELETE (it's a duplicate) |
| 83 | "PREFER subagent for independent tasks" | REPLACE |
| 113 | Nesting explanation with F/S | KEEP (F stays) |
| 293-344 | DELEGATION section | REWRITE entirely |
| 396 | "### Spawn child" example | UPDATE |

### 15.10 The `answer` dict in child

The child has its own `AnswerManager` and `answer` dict in its own kernel namespace.
When we call `agent.invoke(task)`:
1. `invoke()` appends user message to child's context
2. `invoke_loop()` → `run_rlm_loop(agent)` runs the child's RLM loop
3. Child executes code in its own kernel, sets `answer['content']`/`answer['ready']`
4. Loop exits when `answer.ready` is True (or budget/max_turns)
5. `invoke()` returns the result string

We extract via `agent.get_answer()` or the return value of `invoke()`.
The child's REPL state (variables, imports) persists between `.send()` calls
because the `PythonKernel` instance is stored on the agent object.

### 15.11 Edge case: child spawns while parent is in `.send()`

This CANNOT happen. `.send()` calls `agent.invoke()` which runs the child's
`run_rlm_loop`. The child's loop is synchronous. If the child calls `spawn()`
in its code, that spawn runs WITHIN the child's turn (nested call). The parent
is blocked in `.send()` the entire time. No race conditions.

### 15.12 Edge case: parent compressed while holding handles

The `SpawnHandle` objects are in the parent's kernel namespace (Python variables).
Compression affects the parent's CONVERSATION CONTEXT (messages), not the kernel
namespace. The handles survive compression. The child agents are separate objects
in memory, completely independent of the parent's context.

### 15.13 Revised file change list

| File | Change | Est. lines |
|------|--------|-----------|
| `src/rlm/spawn.py` | **Rewrite**: SpawnHandle, SpawnRegistry, spawn() | 532 → ~750 |
| `src/rlm/kernel.py` | Remove subagent/fork injection. Rewrite `_make_spawn_callable`. Add `list_spawns`. | -80, +30 |
| `src/rlm/kernel_wiring.py` | No change (verification already correct) | 0 |
| `src/agent_loop.py` | Add budget check + `_force_max_turns` support | +35 |
| `src/agent_models.py` | Add `spawn_budget`, `spawns_count` to AgentStatus | +3 |
| `src/agent_core.py` | Add `spawn_budget`, `spawn_peak_pct`, `_budget_warned`, `_force_max_turns` attrs. Update `get_status()`. | +15 |
| `src/agent_console/display_status.py` | Add budget/spawns to ctx line | +6 |
| `src/AGENT_RLM.md` | Rewrite delegation. Remove duplicate context mgmt. Add budget section. | ~120 changed |
| `docs/designs/DECISIONS.md` | Mark 6.1-6.16 superseded. Add new decisions. | +20 |
| `src/agent_subagent.py` | Keep internal functions. No public API change needed (they're already internal). | 0 |
| `src/tests/rlm/test_spawn.py` | Update existing tests for new API | ~50 changed |
| `src/tests/rlm/test_spawn_v2.py` | **New**: Full test suite for v2 | ~300 new |

## 16. Second Review Refinements (call-flow trace + usability)

### 16.1 CRITICAL: Live output for persistent spawns

**Finding:** `_spawn_persistent` does NOT restore real stdout/stderr. Only `_spawn_oneshot` does.
This means persistent children's output is currently INVISIBLE to the user.

**Fix:** Add the stdout swap to BOTH:
- `spawn()` initial task processing (around `agent.invoke(task)`)
- `SpawnHandle.send()` / `.resume()` (around `agent.invoke(prompt)`)

Code pattern (same as current `_spawn_oneshot` lines 365-375):
```python
import sys, os
real_stdout = os.fdopen(os.dup(1), 'w', buffering=1)
real_stderr = os.fdopen(os.dup(2), 'w', buffering=1)
saved_stdout, saved_stderr = sys.stdout, sys.stderr
sys.stdout, sys.stderr = real_stdout, real_stderr
try:
    result = agent.invoke(task)
finally:
    sys.stdout, sys.stderr = saved_stdout, saved_stderr
    real_stdout.close()
    real_stderr.close()
```

This goes in a helper: `_with_real_stdout(func, *args, **kwargs) -> result`

### 16.2 Child gets its own REPL kernel

**Confirmed:** `TauErgon.__init__` calls `self._init_repl_kernel()` (line 302). Every child
gets its own `PythonKernel` with its own namespace (`answer`, `spawn`, `host_request`, etc.).

**Implications:**
- Child has its own `answer` dict - isolated from parent.
- Child has its own `spawn()` - can spawn grandchildren.
- Child's `spawn()` is wired to the CHILD as parent (correct nesting).
- Child's kernel state persists between `.send()` calls (variables, imports).
- Global `SpawnRegistry` enforces max-5 across ALL agents.

### 16.3 Full call flow: spawn initial task

```
1. LLM writes: h = spawn('task', name='x')
2. Kernel executes in parent's REPL namespace
3. Calls closure from _make_spawn_callable() (kernel.py)
4. Closure calls rlm.spawn.spawn(task='task', name='x', parent_agent=parent)
5. spawn() checks: nesting depth < 3? registry count < 5?
6. Creates TauErgon instance (child) with own kernel
7. Initializes child context (fresh or inherited)
8. Initializes child session
9. Creates SpawnHandle
10. _with_real_stdout: restore real stdout
11. Prepends budget info to task
12. set_nesting_stack(child.nesting_stack)
13. child.invoke(task_with_budget)  # BLOCKS
14. Restore captured stdout
15. set_nesting_stack(parent.nesting_stack)
16. handle.last_result = result
17. handle.status = 'completed'
18. Registry.register(handle)
19. Returns handle to LLM
```

### 16.4 Full call flow: h.send(prompt)

```
1. Check status != closed
2. _with_real_stdout
3. set_nesting_stack(child)
4. child.invoke(prompt)  # BLOCKS
5. Restore stdout, nesting
6. handle.last_result = result
7. handle.status = 'completed'
8. Save child context
9. Return result
```

### 16.5 Full call flow: h.summarize()

```
1. Check status != closed
2. If 0 turns: return '[No work done yet]'
3. child._force_max_turns = 1
4. _with_real_stdout
5. result = child.invoke(SUMMARIZE_PROMPT)
6. child._force_max_turns = None
7. Restore stdout
8. Return result (does NOT update turns/peak)
```

### 16.6 Usability: parent LLM examples

```python
# Simple:
h = spawn('Count lines in agent_core.py', name='counter')
print(h.last_result)
h.close()

# With context:
h = spawn('Fix the 3 bugs we found', inherit_context=True, name='fixer')

# Budget management:
h = spawn('Refactor auth module', name='auth', budget=0.70)
print(h.last_result)  # child stopped voluntarily with summary
h.extend_budget(0.15)
result = h.resume('Yes, finish session management.')
h.close()

# Inspect:
print(h.status, h.turns, h.context_pct)
print(h.inspect('last_n', n=3, chars=80))

# List all:
for s in list_spawns():
    print(s)
```

### 16.7 Usability: what the child sees

Turn 1: `# ctx: ... | nest: S | budget: 70% | name: auth`
First user msg: `[BUDGET: 70%] Your context budget is 70%...` + task
Turn 15: `[BUDGET WARNING] You are at 60%...`
Turn 18: child sets answer with summary + ready=True (voluntary stop)

### 16.8 NO budget section in AGENT_RLM.md

Child learns budget from: (1) ctx line, (2) initial task prepend, (3) warning at 60%.
Three redundant channels. No AGENT_RLM.md budget section needed.

### 16.9 spawns: N only for root

Only root agent (empty nesting_stack) shows spawns count in ctx line.

### 16.10 Global registry, global limit

Max-5 is GLOBAL across all agents. Not per-parent. Prevents memory explosion.

### 16.11 _with_real_stdout helper

Used by: spawn(), .send(), .resume(), .summarize()
NOT used by: .inspect(), .status_dict(), .extend_budget(), .close()

### 16.12 Final file change list

| File | Change | Lines |
|------|--------|-------|
| src/rlm/spawn.py | Rewrite: SpawnHandle, SpawnRegistry, spawn(), _with_real_stdout | 532 to ~800 |
| src/rlm/kernel.py | Remove subagent/fork. Rewrite _make_spawn_callable. Add list_spawns. | -80, +40 |
| src/agent_loop.py | Add budget check + _force_max_turns | +35 |
| src/agent_models.py | Add spawn_budget, spawns_count to AgentStatus | +3 |
| src/agent_core.py | Add spawn attrs. Update get_status() | +15 |
| src/agent_console/display_status.py | Add budget/spawns to ctx line | +6 |
| src/AGENT_RLM.md | Rewrite delegation. Remove duplicates/subagent/fork refs. | ~100 |
| docs/designs/DECISIONS.md | Mark 6.x superseded. Add new. | +20 |
| src/tests/rlm/test_spawn.py | Update for new API | ~50 |
| src/tests/rlm/test_spawn_v2.py | New test suite | ~300 |

Total: ~10 files, ~1400 lines changed/added.

## 17. Resolved Decisions (user confirmation 2026-09-12)

### 17.1 Handle access: list_spawns() + get_spawn()

Both. `list_spawns()` returns actual `SpawnHandle` objects (not dicts).
Plus `get_spawn(name_or_id) -> SpawnHandle` for direct access by name or ID.

AGENT_RLM.md instructs: ALWAYS store the handle.

    myworker = spawn('do something')
    # NOT: spawn('do something')  # handle lost!

### 17.2 Exit statuses (5 states)

| Status | Meaning | How it happens |
|--------|---------|----------------|
| `completed` | Child finished the task. Done. | Child sets answer['ready']=True (no yield flag) |
| `yielded` | Child voluntarily paused. Not done. Awaiting instructions. | Child sets answer['ready']=True AND answer['yield']=True |
| `budget_exhausted` | Forced stop at budget limit. | System detects context >= budget |
| `error` | Abnormal termination. | API failure, max_turns, unrecoverable exception |
| `closed` | Parent terminated the child. | Parent calls .close() |

**The `yield` mechanism:**

The answer dict gains an optional field:

    answer['content'] = 'Done X, Y. Z remains. Can continue or pivot.'
    answer['ready'] = True
    answer['yield'] = True  # signals: I am NOT done, I am pausing

If `answer['yield']` is True (and ready is True):
- Handle status = 'yielded'
- Parent sees: child is paused, content is a status summary
- Parent decides: .resume('keep going') / .send('new task') / .close()

If `answer['yield']` is not set (or False):
- Handle status = 'completed'
- Parent sees: child is done, content is the final result

**Parent's response to a yield:**
- .resume('yes, finish Z') = resume as-is
- .resume('actually, skip Z, do W instead') = resume with tweak
- .send('new entirely different task') = pivot
- .extend_budget(0.15) + .resume('more room, finish') = budget extension
- .close() = done, thanks, no more needed

### 17.3 .send() and .resume() both stay

Semantic distinction confirmed. .resume() = resume. .send() = new direction.
Harness can add slightly different prompt text for each if needed.

### 17.4 Inherited context + budget: DEFERRED

Needs more discussion. See reminder at end of doc.

### 17.5 .summarize() stays on stack

The summarize exchange remains in the child's context. The child can see
its own summary on future turns. This might actually help it maintain
coherence after compression.

### 17.6 spawns: N stays global

No per-parent tracking. Simple global count. No extra complication.

### 17.7 Budget preamble: DEFERRED

Needs more discussion. See reminder at end of doc.

### 17.8 inspect() uses chars, not bytes

All inspect() parameters use `chars=N` not `bytes=N`.
No UTF-8 splitting issues. Simpler mental model.

### REMINDER: Deferred topics to discuss

- [ ] (4) Inherited context + budget interaction: how to handle child starting
      at 40% with a 70% budget? Warn? Auto-adjust? Different default?
- [ ] (7) Budget preamble: how long? Always? Conditional? What exact text?
      Should it be in the first user message? In the system prompt?
      Should it change based on budget level?

## 18. B: Work Budget (REVISED — replaces section 3)

### 18.1 Philosophy

B is a measure of WORK, not SPACE.
It tracks how much context the child has ADDED (cumulative positive deltas).
Compression makes context efficient but does NOT undo work.
B only decreases (except explicit parent grant via extend_budget).

### 18.2 Formula

    B_start = budget - C_start
    Each turn (before LLM call):
        C_now = context.get_usage_stats()[1]  # fraction 0.0-1.0
        delta = C_now - C_last
        B = max(0, B - max(0, delta))
        C_last = C_now

    If B <= 0: force stop (close turn, return).
    If B < 0.10 and not yet warned: inject warning.

### 18.3 Trace example

    Spawn: C_start=3%, budget=70%. B=67%. C_last=3%.
    Turn 1: C=3%, delta=0, B=67%. (No growth yet)
    Turn 2: C=6%, delta=+3%, B=64%. C_last=6%.
    Turn 3: C=10%, delta=+4%, B=60%. C_last=10%.
    ...
    Turn 15: C=58%, delta=+5%, B=10%. C_last=58%.
    Turn 16: C=62%, delta=+4%, B=6%. C_last=62%. WARNING (B<10%).
    Turn 17: C=65%, delta=+3%, B=3%. C_last=65%.
    Turn 18: C=68%, delta=+3%, B=0%. STOP.

    With compression:
    Turn 20: C=85%, proactive compression fires. C=50%.
    Turn 21: C_now=50%, C_last=85%, delta=-35%, B unchanged. C_last=50%.
    Turn 22: C=53%, delta=+3%, B decreases by 3%. C_last=53%.

### 18.4 Inherited context

    Parent at C=40%. Child inherits.
    C_start = 40%. B_start = 70% - 40% = 30%.
    Child has 30% of context GROWTH available. Immediate, clear.

### 18.5 extend_budget

    h.extend_budget(0.15)  # add 15% work budget
    agent.spawn_B += 0.15
    if agent.spawn_B > 0.10: agent._budget_warned = False

Only way B increases. Explicit parent grant.

### 18.6 Display in # ctx: line

    # ctx: ~5500 tk (3.1%) 2 msgs | ... | nest: S | B:66.9% | name: auth

C (in parens) = current context usage. Can drop with compression.
B = remaining work budget. Only drops (except extend_budget).
Only shown for spawned agents (B > 0).

### 18.7 Warning and stop

    B < 10%: Inject once:
    [BUDGET] B:8.3% remaining. Consider yielding with a summary.

    B <= 0: Force stop. Close turn. Return last answer.

### 18.8 Agent attributes

    agent.spawn_B: float = 0.0       # remaining work budget (0 = no budget)
    agent.spawn_C_last: float = 0.0  # C at last calculation
    agent._budget_warned: bool = False

Set at spawn time. Updated each turn in run_rlm_loop.

### 18.9 AgentStatus additions

    spawn_B: float = 0.0  # in AgentStatus dataclass

Populated in get_status(): spawn_B=getattr(self, 'spawn_B', 0.0)

### 18.10 Where in run_rlm_loop

Insert AFTER turn_count check, BEFORE proactive compression:

    # --- B: WORK BUDGET CHECK ---
    if agent.spawn_B > 0:
        _, C_now, _, _ = agent.context.get_usage_stats(agent.max_context_tokens)
        delta = C_now - agent.spawn_C_last
        if delta > 0:
            agent.spawn_B = max(0.0, agent.spawn_B - delta)
        agent.spawn_C_last = C_now

        if agent.spawn_B <= 0:
            # Force stop
            agent._cleanup_pending = True
            answer = agent.get_answer()
            answer_str = _answer_to_str(answer) if answer and answer.content else ''
            if not answer_str:
                answer_str = '[Stopped: work budget exhausted]'
            agent.context.close_turn(answer_str, skip_cleanup=True)
            return answer_str

        if agent.spawn_B < 0.10 and not agent._budget_warned:
            agent._budget_warned = True
            # Add warning to parts list (injected as synthetic msg)

### 18.11 Preamble (short, in first user message)

One line prepended to the initial task:

    [B:70%] Your work budget. When B reaches 0, you stop.
    If running low, set answer['yield']=True with a status summary.

After turn 1, the ctx line shows B: every turn. Preamble not repeated.

### 18.12 What the child needs to know (AGENT_RLM.md addition)

Add to AGENT_RLM.md (2-3 lines, in delegation or new short section):

    If your # ctx: line shows B: (you are a spawned agent):
    - B is your work budget. It decreases each turn.
    - When B is low and you cannot finish, set answer['yield']=True
      with a status summary. Your parent may extend your budget.

### 18.13 Interaction with 85% proactive compression

Independent mechanisms:
- B: work budget. Stops child when work is done (B=0).
- 85% compression: space safety net. Prevents API overflow.

Scenarios:
- B hits 0 at C=50%: child stops. Compression never fires.
- C hits 85% at B=5%: compression fires, C drops, child continues with B=5%.
- Both can happen across different turns.

### 18.14 min_B tracking (replaces peak_context_pct)

    agent.spawn_min_B: float  # lowest B reached (closest to wall)

Updated each turn: agent.spawn_min_B = min(agent.spawn_min_B, agent.spawn_B)
Reported in handle.status_dict() and list_spawns().

### 18.15 .summarize() and B

.summarize() adds context (user msg + assistant response).
Next turn's delta captures this growth. B decreases.
This is correct and accepted (user decision 17.5).
The summary stays on the child's stack (child can see it).


## Implementation Observations (2026-09-12)

### Python Keyword Gotchas
1. **`continue` is a keyword** — cannot be a method name. Renamed to `resume()`.
2. **`yield` is a keyword** — `answer['yield']` works (string key), but:
   - `MagicMock(yield=False)` → SyntaxError
   - `obj.yield = False` → SyntaxError  
   - Must use: `setattr(obj, 'yield', False)`

### Design Decisions Confirmed by Implementation
3. **B: is WORK, not SPACE** — compression reduces C but delta is negative, B unchanged.
   Only `extend_budget()` increases B. This was the key insight that solved the fork issue.
4. **No async** — rlm() was async (thread-based). Removed by design. "As few async activities as possible."
5. **_with_real_stdout for BOTH paths** — the old persistent spawn was missing stdout swap (bug).
   Now both use the same helper.

### Integration Points
6. **TauErgon.__init__ doesn't accept nesting params** — set `child.nesting_count` and
   `child.nesting_stack` as attributes AFTER construction.
7. **verify_kernel_namespace** must be updated whenever namespace items change.
   We removed `agent_message`, added `list_spawns`/`get_spawn`.
8. **Context alternation is safe** — when spawn() is called, parent context ends with
   assistant message (the executing code). So inherit_context copy is safe for invoke().

### Test Considerations
9. **SpawnRegistry is a global singleton** — tests need `@pytest.fixture(autouse=True)`
   to clear `_handles` between tests.
10. **Mocking TauErgon** — patch `agent_core.TauErgon` (not `rlm.spawn.TauErgon` since
    it's a lazy import inside the function).

### Removed Features (Intentional)
11. **agent_message namespace** — was tied to rlm_call.py. Removed. Parent→child via
    handle.send(), child→parent via yield protocol. host_bridge still handles incoming
    agent_message requests (different direction: host→agent).
12. **Per-model selection** — rlm() had `model=` param. Not in spawn() v1. Add later if needed.
13. **Mid-turn child→parent messaging** — AgentMessage allowed this. Not needed for
    budget-bounded workers. Yield protocol covers the use case.

### Additional Observation (post-restart fix)
14. **`nesting_count` is a read-only property** — derived from `len(nesting_stack)`.
    Do NOT set `child.nesting_count = N`. Only set `child.nesting_stack = "..."`.
    The count is automatic.

### Status Logic Fix (2026-09-12, post-testing)
15. **Status determination**: `ready=True` with substantive content → "completed";
    `ready=True` with empty content → "suspect"; a non-ready exit → "incomplete";
    cut off (no ready) AND B<=0 → "budget_exhausted". This prevents false
    "budget_exhausted" when B_start=0 due to inherited context, and avoids
    over-claiming "completed" for empty or non-ready exits.
16. **B_start=0 warning**: If inherited context > budget, log a warning. Child runs
    without budget protection (85% compression still applies).

### B: in Message Prefix (2026-09-12)
17. **B: is in the [U:repl...] prefix** — not just the `# ctx:` terminal line.
    Format: `[U:repl | N:0 | M:3 | C:2% | B:68.0%]`
    This is what the child LLM actually sees. The `# ctx:` line is terminal-only.
    Without this, the child couldn't see its budget and the yield protocol was useless.
18. **context.spawn_B** — the context object carries spawn_B so `_make_user_prefix`
    can include it. Updated in agent_loop.py after each B decrement.

### list_spawns() Scoping (Planned)
19. **Default: direct children only.** list_spawns() shows only the current agent's direct children. list_spawns(all=True) shows the full tree. Rationale: with nested spawns, a global flat list is confusing. Each agent is only responsible for its own children. Status: DECIDED, NOT YET IMPLEMENTED.

### Bug Fixes from Code Review (2026-09-12)
20. **pending_tool_ids removed** — display_status.py accessed non-existent field. Dead code from tools era.
21. **context_file deduplicated** — AgentStatus had it twice (Path|None + str). Removed str.
22. **code="" before try** — agent_loop.py except handler could reference undefined variable.
23. **[context boundary] prefixed** — was unprefixed, treated as real user message. Now [U:system|N:0].
24. **deepcopy for inherit_context** — dict(m) was shallow. Now copy.deepcopy(m).
25. **Dead code removed** — kernel_wiring.py line 63 (no-op getattr).
