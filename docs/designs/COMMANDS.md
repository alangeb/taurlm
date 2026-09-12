# Commands — Implementation Guide

## Two Command Types

### Python Commands (`.py`)
Full agent access. Create `commands/my_command.py`:

```python
"""My Command — brief description."""

def run(agent: "TauErgon", args: list[str] = []) -> str:
    """Execute with full agent access."""
    ...
```

> **Note:** Signatures vary by command. Some use `run(agent, args: list[str]) -> str`, others use `run(args: str = "") -> str` (e.g., `/goal`, `/refine`, `/autonomous`).

Registered in `commands/__init__.py` via `COMMANDS` dict.

### Markdown Commands (`.md`)
Prompt templates that expand into simulated user input. Create `commands/my_command.md`:

```markdown
---
description: What this command does
---

Command content with placeholder substitution.
Each --- separated segment becomes a full LLM turn.

---

Second phase here.

---

/someothercommand arg1 arg2
```

**Placeholder substitution**: `$1`, `$2` (positional), `$*` (all args), `$1+` (from first onward), `${time}`, `${date}`, `${datetime}` (dynamic).

**Recursion**: Segments starting with `/` dispatch to other commands (depth limit: 5).

**Discovery**: Auto-discovered from `commands/*.md` into `MD_COMMANDS` dict.

## Key Rules

1. Python commands have **full `TauErgon` access** — manage their own context, spawn child agents.
2. Markdown commands flow through the **input pipeline** as simulated user input — indistinguishable from real prompts.
3. **Static registry** — `COMMANDS` (.py) in `commands/__init__.py`. `MD_COMMANDS` is auto-discovered from `commands/*.md` and wired into dispatch.
4. **Multiprompting** — `.md` files split on `---` delimiters; each segment = full LLM turn with context accumulation.

## Command Implementation Rules

| Rule | Details |
|------|---------|
| Python commands | `run(agent, args) -> str` or `run(args: str) -> str` (varies by command) |
| Markdown commands | YAML frontmatter (`description:`), placeholder substitution, `---` multiprompting |
| Dispatch order | Help aliases → `COMMANDS` (.py) → `MD_COMMANDS` (.md) → unknown command error |
| Location | `commands/` directory |
| Loading | Static import via `commands/__init__.py` |
| Recursion guard | `MAX_MD_RECURSION = 5` — enforced via `_cmd_dispatch_depth` counter in `agent_command_handlers.py` |

## Why This Design?

Python commands get full agent access for complex logic. Markdown commands expand into segments that flow through `_process_input()` → `rlm_loop()` → full LLM turn, making them indistinguishable from real user prompts. Both types are fully wired into dispatch: `handle_command()` checks `COMMANDS` (.py) first, then `MD_COMMANDS` (.md). Both types coexist in the `commands/` directory.
