# SPAWN Implementation Plan - Detailed Diff

Companion to SPAWN.md. Shows exact changes per file.

---

## 1. src/rlm/kernel.py

### REMOVE: lines 251-253 (subagent/fork injection)
```diff
         # Initialize skills functions
         namespace.update(self._make_skills_namespace())
 
-        # Initialize delegation functions
-        namespace["subagent"] = self._make_subagent_callable()
-        namespace["fork"] = self._make_fork_callable()
+        # Initialize spawn management functions
+        namespace["list_spawns"] = self._make_list_spawns()
+        namespace["get_spawn"] = self._make_get_spawn()
 
         # Set __name__ for module detection
         namespace["__name__"] = "__main__"
```

### REMOVE: lines 426-528 (_make_subagent_callable + _make_fork_callable)
Delete both entire methods.

### MODIFY: _make_spawn_callable (lines 260-308)
- Remove `persistent` param
- Add `budget: float = 0.70` param
- Update docstring: "Always returns SpawnHandle"
- Remove "If persistent=False/True" branching in docstring
- Change _spawn_func call: remove persistent=, add budget=
- Update stub signature to match

### ADD: _make_list_spawns + _make_get_spawn (after _make_spawn_callable)
```python
def _make_list_spawns(self) -> Any:
    from rlm.spawn import SpawnRegistry
    def list_spawns():
        return SpawnRegistry().list_active()
    return list_spawns

def _make_get_spawn(self) -> Any:
    from rlm.spawn import SpawnRegistry
    def get_spawn(name_or_id: str):
        return SpawnRegistry().get_by_name_or_id(name_or_id)
    return get_spawn
```

---

## 2. src/rlm/spawn.py - FULL REWRITE (~800 lines)

Replace entire file. Structure:

```
- Module docstring
- __all__ = ["SpawnHandle", "SpawnRegistry", "spawn", "SpawnError", ...]
- Constants: MAX_SPAWNS=5, MAX_NESTING_DEPTH=3, BUDGET_WARN_THRESHOLD=0.10
- Exception classes: SpawnError, SpawnLimitError, NestingLimitError, SpawnClosedError
- _with_real_stdout(func, *args, **kwargs) helper
- SpawnRegistry (singleton): register, unregister, list_active, get_by_name_or_id, count_active
- SpawnHandle (dataclass):
    - Fields: spawn_id, name, status, last_result, turns, budget, _agent
    - Properties: context_pct, min_B
    - Methods: send, resume, summarize, inspect, extend_budget, close, status_dict
    - Dunder: __str__ (returns last_result), __repr__
- spawn(task, *, inherit_context, name, budget, parent_agent) -> SpawnHandle
    1. Check nesting depth (parent_agent.nesting_count < MAX_NESTING_DEPTH)
    2. Check registry limit (count_active < MAX_SPAWNS)
    3. Create TauErgon child (inherit context or fresh)
    4. Set child.spawn_B = budget - C_start
    5. Set child.spawn_C_last = C_start
    6. Create SpawnHandle
    7. _with_real_stdout: child.invoke(preamble + task)
    8. Set handle.last_result, handle.status (completed/yielded/budget_exhausted/error)
    9. Registry.register(handle)
    10. Return handle
```

Key method implementations:
- send(prompt): _with_real_stdout(self._agent.invoke, prompt). Update last_result/status/turns.
- resume(hint=None): prompt = "Continue your current work." + (f" Direction: {hint}" if hint else ""). Same as send.
- summarize(): Set agent._force_max_turns=1. invoke with SUMMARIZE_PROMPT. Clear flag.
- inspect(mode, **kwargs): Read agent.context._messages directly. No LLM. No stdout swap.
- extend_budget(amount): agent.spawn_B += amount. Reset _budget_warned if B > 0.10.
- close(): status='closed'. Flush audit. Save context. Unregister. self._agent=None.

Status determination after invoke:
- If answer has yield=True -> "yielded"
- If spawn_B <= 0 -> "budget_exhausted"
- If exception -> "error"
- Else -> "completed"

### Status semantics + slot hygiene
- **`suspect`**: an empty-content + `ready=True` exit maps to `suspect` (not
  `completed`) — a child that claims done but produced no substantive result.
  `record_spawn` captures the suspect metric for post-mortem.
- **`incomplete`**: a non-ready exit (max_turns/force_end) reports `incomplete`,
  never `completed` (reserved for an actual `ready`).
- **Slot hygiene**: the registry holds weak references, so a GC'd unclosed handle
  unregisters itself and never permanently consumes a `MAX_SPAWNS` slot.

---

## 3. src/agent_loop.py

### ADD: B: budget check (after max_turns check, ~line 150)
```python
# --- B: WORK BUDGET CHECK ---
if getattr(agent, 'spawn_B', 0) > 0:
    _, C_now, _, _ = agent.context.get_usage_stats(agent.max_context_tokens)
    delta = C_now - agent.spawn_C_last
    if delta > 0:
        agent.spawn_B = max(0.0, agent.spawn_B - delta)
    agent.spawn_C_last = C_now
    if hasattr(agent, 'spawn_min_B'):
        agent.spawn_min_B = min(agent.spawn_min_B, agent.spawn_B)

    if agent.spawn_B <= 0:
        agent._cleanup_pending = True
        answer = agent.get_answer()
        answer_str = _answer_to_str(answer) if answer and answer.content else ''
        if not answer_str:
            answer_str = '[Stopped: work budget exhausted]'
        agent.context.close_turn(answer_str, skip_cleanup=True)
        return answer_str

    if agent.spawn_B < BUDGET_WARN_THRESHOLD and not getattr(agent, '_budget_warned', False):
        agent._budget_warned = True
        agent._budget_warning_text = (
            f"[BUDGET] B:{agent.spawn_B*100:.1f}% remaining. "
            f"Consider yielding: set answer['yield']=True with status summary."
        )
```

### MODIFY: max_turns (line ~97)
```diff
-    max_turns = getattr(rlm_config, "max_turns", 1000) if rlm_config else 1000
+    max_turns = getattr(agent, '_force_max_turns', None) or (
+        getattr(rlm_config, 'max_turns', 1000) if rlm_config else 1000
+    )
```

### ADD: budget_warning to parts (where parts list is assembled, ~line 390)
```python
if getattr(agent, '_budget_warning_text', None):
    parts.append(agent._budget_warning_text)
    agent._budget_warning_text = None
```

---

## 4. src/agent_models.py

### ADD to AgentStatus (after line 136, turn_active):
```python
    # --- Spawn/budget ---
    spawn_B: float = 0.0
    spawns_count: int = 0
```

---

## 5. src/agent_core.py

### ADD in __init__ (after self._init_repl_kernel(), line ~302):
```python
        self.spawn_B: float = 0.0
        self.spawn_C_last: float = 0.0
        self.spawn_min_B: float = 1.0
        self._budget_warned: bool = False
        self._budget_warning_text: str | None = None
        self._force_max_turns: int | None = None
```

### MODIFY get_status(): add to AgentStatus constructor call:
```python
            spawn_B=getattr(self, 'spawn_B', 0.0),
            spawns_count=SpawnRegistry().count_active() if not self.nesting_stack else 0,
```

---

## 6. src/agent_console/display_status.py

### MODIFY lines 144-149:
```diff
     if status.nesting_stack:
         base_content += f' | nest: {status.nesting_stack}'
+        if status.spawn_B > 0:
+            base_content += f' | B:{status.spawn_B*100:.1f}%'
         color = Colors.INVERT_BLUE
     else:
         base_content += ' | nest: .'
+        if status.spawns_count > 0:
+            base_content += f' | spawns: {status.spawns_count}'
         color = Colors.INVERT_CYAN
```

---

## 7. src/AGENT_RLM.md

### REMOVE:
- Lines 314-327 (subagent/fork sections + old decision matrix)
- Lines 329-346 (old persistent agents section)
- Lines 61-69 (duplicate context management)
- Line 32: "PREFER subagent" -> "PREFER spawn"
- Line 83: same fix

### REPLACE lines 293-348 with:
```markdown
## DELEGATION

spawn(task, *, inherit_context=False, name=None, budget=0.70) -> SpawnHandle

Always returns a handle. Blocks until child finishes initial task.
ALWAYS store the handle: myworker = spawn('do something')

### Handle methods
- h.send('msg')         # New instruction (blocks, returns result)
- h.resume('hint')    # Resume with optional direction
- h.summarize()         # 1-turn summary (cache-friendly)
- h.inspect('last_n', n=5, chars=80)  # See recent turns (free)
- h.extend_budget(0.15) # Grant more work budget
- h.close()             # Free resources (REQUIRED when done)
- h.last_result         # Last answer string
- h.status              # running|completed|yielded|budget_exhausted|error|closed

### Management
- list_spawns()         # All active handles
- get_spawn('name')     # Get handle by name or ID

### Rules
- Max 5 active. ALWAYS close handles when done.
- inherit_context=True for tasks needing your conversation.
- If child yields (status='yielded'), read h.last_result for summary.
  Then: h.extend_budget(0.15) + h.resume('finish') or h.send('new task').
- Budget (B:) is work budget. Child sees it in ctx line. Only decreases.

### Yield protocol (for spawned agents)
If your # ctx: line shows B: (you are a spawned agent):
- B is your work budget. It decreases each turn.
- When B is low and you cannot finish, set answer['yield']=True
  with a status summary. Your parent may extend your budget.
```

---

## 8. docs/designs/DECISIONS.md

Mark section 6 (Subagent & Fork, 6.1-6.16) as [SUPERSEDED by SPAWN.md].
Add new decisions:
- 6.20: Single spawn() API. No subagent/fork. Always persistent.
- 6.21: B: work budget. Only decreases. Compression does not restore.
- 6.22: 5 exit statuses. answer['yield'] for voluntary pause.
- 6.23: Max 5 global spawns. Max nesting depth 3.
- 6.24: list_spawns() returns handles. get_spawn() for direct access.
- 6.25: No timeout. Budget is the bound.
- 6.26: Live output via _with_real_stdout.

---

## 9. Tests

### Update: src/tests/rlm/test_spawn.py
- All tests expect SpawnHandle return (not str)
- Remove persistent=True param
- Add .close() after each spawn

### New: src/tests/rlm/test_spawn_v2.py (~300 lines, 24 tests)
- test_spawn_returns_handle
- test_spawn_isolated_context
- test_spawn_inherited_context
- test_handle_last_result
- test_handle_send
- test_handle_continue
- test_handle_summarize
- test_handle_inspect_overview
- test_handle_inspect_last_n
- test_handle_extend_budget
- test_handle_close
- test_handle_str_compat
- test_budget_decreases_each_turn
- test_budget_compression_no_restore
- test_budget_warning_at_10pct
- test_budget_force_stop_at_0
- test_budget_inherited_context
- test_max_spawns_limit
- test_nesting_depth_limit
- test_list_spawns_returns_handles
- test_get_spawn_by_name
- test_ctx_line_shows_B
- test_ctx_line_shows_spawns
- test_yield_status
- test_live_output

---

## 10. Phases

Phase 1: kernel.py + spawn.py rewrite + update test_spawn.py
Phase 2: agent_loop.py + agent_core.py + agent_models.py + display_status.py (B: budget)
Phase 3: spawn.py handle methods (summarize/inspect/extend) + AGENT_RLM.md
Phase 4: DECISIONS.md + wiki + test_spawn_v2.py + polish
