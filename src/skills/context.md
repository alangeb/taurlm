---
name: context
description: 'TauRLM context internals: /ctx inspection subcommands, .context file format + LOG_DIR paths, ContextManager clear/undo/pop, compression pipeline signatures, auto-compress thresholds. Use for context overflow, session replay, reading raw .context files. Keywords: context, compress, clear, undo, load, overflow, /ctx, .context, replay, token, stack, checkpoint.'
category: infrastructure
keywords: 'context, compress, clear, undo, load, overflow, /ctx, .context, replay, token, stack, checkpoint'
---

# Context Management & Compression (TauRLM)

## Inspecting context (what you can actually run)
- `/ctx` summary (default); display-only: `full|summary|short|sum|last|last sum|last user|user|last assistant|assistant` (commands/ctx.py:1-25).
- Mutating subcommands DO exist: `/ctx clear` (ctx.py:129), `push`/`pop` (ctx.py:133,137), `undo [n]` (ctx.py:141), `compress [n]` (ctx.py:153, default 30), `fix [check]` (ctx.py:165). The Python APIs below are the same operations without the CLI.
- Session files: `~/.local/taurlm/log/<prefix>.context` (JSON: `{'metadata': {...}, 'messages': [{role, content}, ...]}`), plus `.audit` (full prompt/response). `TAU_LOG_DIR` overrides (agent_session.py:41-42). Replay: `./tau.py -c` or `--continue-from FILE` — NOT for validating new code (stale modules).

## Internal APIs (need a live TauErgon; `agent._context_manager`, agent_core.py:269)
```python
from agent_context_manager import ContextManager   # ContextManager(agent)
cm = ContextManager(agent)
cm.clear()                 # drop all but system prompt, reset token counters
cm.undo()                  # remove last turn (from last user msg onward)
cm.load_context_by_id(3)   # load by ID from context list
cm.pop()                   # pop context stack
```
Compression (signatures verified — easy to get wrong):
```python
from agent_context_compress.compress_api import compress_to_target
compress_to_target(context, agent, target_pct)   # target is a PERCENT (50 = 50% full), not tokens; returns bool  # compress_api.py:60
from agent_context_compress import compress_context
compress_context(context, client, model_name, compression_factor, ...)  # low-level pipeline  # __init__.py:117
```
- Auto-compress: spawn children compress at C >= 70% down to ~28% (rlm/spawn.py:39-40,414-437); the main loop compresses via `validate_and_compress` (agent_pipeline.py).
- Compression is LOSSY (summaries replace verbatim) — persist must-keep facts to the **wiki** first.
- RestartManager (agent_context_manager.py:237) handles full-agent restart (filter args, clear bytecode cache, exec). Plan/audit copying lives on ContextManager: `copy_plan_file`/`copy_audit_file` (agent_context_manager.py:119,139).

## Related Skills
- `spawn` — delegate to avoid context bloat (auto-compress recovers child budget)
- `wiki` — persist facts before compression eats them
- `session-lifecycle` — `.context`/`.audit` paths, SESSION_PREFIX, save/exit paths
