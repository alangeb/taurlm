---
name: delegation
description: 'Delegation mechanics reference: spawn constants, statuses, exceptions, registry (list_spawns/get_spawn). Use when reading spawn statuses/registry; pattern authority is the spawn skill. Keywords: spawn, delegate, child agent, subagent, fork, context, budget, inspect, resume, close, parallel, background.'
category: rlm
keywords: 'spawn, delegate, child agent, subagent, fork, context, budget, inspect, resume, close, parallel, background'
---

# Delegation — Mechanics Only

**The manager pattern, lifecycle, budget/yield, worker templates, and usage guidance live in the `spawn` skill — that is the authority.** This file is the constants/statuses/exceptions lookup table.

| Item | Value | Source |
|------|-------|--------|
| Max active spawns | 5 (`MAX_SPAWNS`; `SpawnLimitError`) | spawn.py:37, raised spawn.py:167/618 |
| Max nesting depth | 3 (`MAX_NESTING_DEPTH`; `NestingLimitError`) | spawn.py:38, raised spawn.py:610 |
| Budget warn | B < 10% → child nudged to yield | spawn.py:39 (`BUDGET_WARN_THRESHOLD`), checked spawn.py:423 |
| Child auto-compress | C >= 70% → compress to ~28%, may recover B | spawn.py:40-41 (`AUTO_COMPRESS_*`), `_maybe_compress` spawn.py:496, B-recover spawn.py:533-536 |
| Statuses | running / completed / suspect / yielded / budget_exhausted / error / closed | spawn.py:217; suspect spawn.py:559 |
| Exceptions | SpawnError, SpawnLimitError, NestingLimitError, SpawnClosedError (op on closed handle) | spawn.py:44-56; extend_budget cap `min(…,1.0)` spawn.py:422 |
| Registry | `list_spawns()` (mine) / `list_spawns(all=True)` (tree) / `get_spawn(name_or_id)` | namespace.py:137,152 |
| Failed spawn | handle returned with status='error', NOT registered | spawn.py:680, skipped register spawn.py:690 |
| Nesting letters | F = inherit_context, S = fresh | spawn.py:632 |

## Related Skills
- `spawn` — THE authority (manager pattern, lifecycle, budget/yield)
- `spawn` §Worker Templates — ready-made role briefs (merged)
- `verification-discipline` — independent re-verify of worker results before trusting
