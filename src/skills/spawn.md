---
name: spawn
description: 'Delegation via spawn(): MANAGER pattern - delegate all well-defined work, keep own context lean. Lifecycle spawn/send/resume/summarize/close, budget B, yield protocol, inspect, inherit_context tradeoff, verification. Use for parallel work, context offloading, subtask isolation. Keywords: spawn, delegation, subagent, manager, worker, worker template, role preset, implementer, reviewer, budget, yield, parallel.'
category: rlm
keywords: 'spawn, delegation, subagent, manager, worker, worker template, role preset, implementer, reviewer, budget, yield, parallel'
---

# Spawn & Delegation — The Manager Pattern

**CORE LESSON: you are a MANAGER with WORKERS, not a doer.** The main agent should do as LITTLE work itself as possible. Every well-defined, bounded subtask belongs in a child. The manager: (1) decomposes the job into crisp task briefs, (2) spawns/reuses workers, (3) orchestrates — reads results, not raw outputs, (4) INDEPENDENTLY VERIFIES worker claims, (5) keeps its OWN context lean by pushing heavy lifting into children. A manager that reads 2000-line files and runs verbose commands inline is failing at the job.

## Delegate vs do-inline
- DELEGATE: file analysis, test runs with long output, large diffs, research, anything whose OUTPUT you don't need verbatim in your context — child context absorbs it, you get the summary.
- INLINE: single-line checks, decisions requiring your full history, trivial edits (spawn overhead > task).
- At 30%+ own context: start delegating. 50%+: delegate remaining work. 70%+: stop non-critical work.
- Re-use ONE worker per role across a multi-turn task (`send()` batches) — fresh spawns lose accumulated worker knowledge.

## Lifecycle (all calls BLOCK until child responds)
1. `h = spawn(task_brief, name="worker", budget=0.70)` — blocks until initial task done; ALWAYS store the handle.
2. `h.send("next instruction")` — new work; worker keeps its accumulated context.
3. `h.summarize()` — 1 LLM call, appends summary to child context; then `h.resume("finish X")` to continue compacted.
4. `h.close()` — REQUIRED when done. Idempotent; further ops raise SpawnClosedError; GC without close() logs a resource-leak warning (`__del__`, spawn.py:457-478).

Don't tell children how to use answer['content']/ready — they inherit the full system prompt. Give natural task briefs.

## Budget B (work budget, separate from context C)
- `B_start = budget - child_context_at_spawn` (spawn.py:643-648). B then falls by the child's CONTEXT GROWTH each turn — not a fixed per-turn cost. A child that prints huge outputs burns budget fast.
- B <= 0 → hard stop, status `budget_exhausted` (spawn.py:563), answer "[Stopped: work budget exhausted]".
- B < 10% → child gets a nudge to yield (`BUDGET_WARN_THRESHOLD`, spawn.py:39, checked spawn.py:423).
- `h.extend_budget(0.15)` is the ONLY way B rises (spawn.py:411-422; the 1.0 cap is the `min()` at spawn.py:422). Only useful once budget is actually spent.
- Child auto-compresses at C >= 70% to ~28% (`AUTO_COMPRESS_THRESHOLD`/`_TARGET`, spawn.py:40-41, `_maybe_compress` spawn.py:496) which can RECOVER B (the `spawn_B = max(...)` recompute, spawn.py:533-536).
- `spawn(..., budget=0.0)` or inherited context that fills budget → child gets exactly ONE LLM turn (clamp `max(0.0, budget - C_start)` spawn.py:648, 1-turn detection/log spawn.py:651-655).

## Statuses & yield
`running | completed | suspect | yielded | budget_exhausted | error | closed` (spawn.py:217, suspect spawn.py:559).
- `yielded` (spawn.py:549): child set answer['yield']=True → read `h.last_result` for done-vs-pending, then `h.extend_budget(0.15)` + `h.resume('finish')`, or `h.send('new task')`.
- `suspect` (spawn.py:559): child set ready=True but returned EMPTY content — the signature of a worker that ended its turn without producing an answer. Do NOT trust 'completed'; treat it as unverified and re-run / re-send before building on it.
- `error`: spawn() itself raised — handle is returned unregistered with `h.last_result` = error text (spawn.py:680, not registered spawn.py:690); re-spawn with a clearer brief.
- `h.inspect('overview'|'last_n'|'first_n'|'last_user'|'last_assistant', n=5, chars=80)` — FREE, no LLM call. Use to check a worker isn't going in circles before spending a send().

## inherit_context tradeoff
`inherit_context=True` deep-copies your whole conversation into the child (spawn.py:636-639). The history counts against the child's budget: if your context >= budget, the child starts at B=0 (one turn). Use only when the subtask genuinely needs your history; otherwise write a self-contained task brief (better: it forces you to specify the task). Nesting letter: F = inherited, S = fresh.

## Verify, don't trust
A worker's report is a CLAIM. Before building on it: `git status`/`git diff` for file changes, re-run the tests it says pass, spot-check one or two outputs. Fixing a wrong worker result after trusting it costs more than verification. A child runs the code loaded at ITS startup — a fix you saved mid-session is not exercised by an already-running child (or by you); verify in a fresh process (see **debug**).

## Limits
- Max 5 active spawns (`MAX_SPAWNS`, spawn.py:37; `SpawnLimitError` raised spawn.py:167/618) — close before spawning more; `list_spawns()` (namespace.py:137) / `get_spawn(name)` (namespace.py:152).
- Max nesting depth 3 (`MAX_NESTING_DEPTH`, spawn.py:38; `NestingLimitError` raised spawn.py:610): top → child → grandchild; depth letters stack (S, SF, ...) (spawn.py:632).
- Children share the parent's config AND its **LIVE** LLM group — `current_group_name` (reflects runtime `/llm` switches), not the config default (spawn.py:627-631). A parent that `/llm`-switched mid-session spawns children on the switched-to group.

## Worker Templates (spawn role briefs)

Role-prompt presets for `spawn()` workers. Use these as the brief opener; spawn is the authority for lifecycle/budget.

### Templates

| Template | Role prompt opener | Use for |
|----------|-------------|--------|
| `test_runner` | "You are a test runner and debugger. Work in the current directory." | Running tests, fixing failures |
| `code_reviewer` | "You are a ruthless code reviewer. In the current directory, run analysis and report issues." | Bugs, style, security |
| `architect` | "You are an architecture reviewer and implementer. Work in the current directory." | Design analysis, refactoring |
| `implementer` | "You are a focused implementer. Work in the current directory. Implement exactly what is asked." | Features, bug fixes |

### Pattern: one worker per role, reused, verified

```python
# Brief = role prompt + concrete task + expected output shape
worker = spawn(
    'You are a test runner and debugger. Work in the current directory. '
    'Run the test suite, fix failures, report: pass/fail counts + files changed.',
    name='test_runner', budget=0.70,
)
worker.send('Now rerun only the previously failing tests with --lf and confirm.')
print(worker.last_result)
worker.close()
```

- Reuse via `send()` — the worker accumulates repo knowledge; a fresh spawn re-pays the discovery cost.
- Ask for a SUMMARY shape in the brief (counts, paths, verdict) — that's the whole point of delegating: raw output stays in the child's context, not yours.
- VERIFY before trusting: rerun the tests it claims pass, `git diff` the files it claims to have changed.
- Multiple roles: spawn reviewer + tester, but every spawn()/send() BLOCKS (no true parallelism) — 'concurrent' means many live handles, max 5 active total; close before spawning more.
- Budget: 0.70 default; 0.85 only for long multi-turn workers (B falls with the child's context growth — a bigger budget just delays exhaustion).

## Related Skills
- **Worker Templates** section above — role-prompt presets for common workers
- **delegation** — mechanics-only dir skill (companion, don't duplicate)
- **test-runner / code-review** — classic delegate-and-verify tasks
- `verification-discipline` — re-run worker test claims yourself before trusting
