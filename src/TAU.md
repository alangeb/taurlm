# TAU.md — TauRLM Developer Index

**This file is the developer index.** Read this before working on TauRLM code.

## Before You Start

1. **ALWAYS run the AST analyzers** (`from rlm.pyscan_core import scan_project` and `from rlm.pyanalyze_core import analyze_project`) on the target file/directory to understand the current structure.
2. Read relevant design documents in `../docs/designs/` for rationale behind existing patterns.

## Quick Reference

| Task | Where to Look |
|------|---------------|
| Design decisions | `../docs/designs/DECISIONS.md` |
| Context management | `../docs/designs/CONTEXT.md` |
| A2A protocol | `../docs/designs/A2A_PROTOCOL.md` |
| Audit log | `../docs/designs/AUDIT.md` |
| Commands | `../docs/designs/COMMANDS.md` |
| Compression | `../docs/designs/COMPRESSION.md` |
| Input protocol | `../docs/designs/INPUT_PROTOCOL.md` |
| Skills | `../docs/designs/SKILLS.md` |
| Spawn system | `../docs/designs/SPAWN.md` |
| Spawn implementation | `../docs/designs/SPAWN_IMPLEMENTATION.md` |
| Testing | `../docs/designs/TESTING.md` |

## Making Changes

### Code Style

- `from __future__ import annotations` for forward references
- Dataclasses for all structured data (models, config)
- `__all__` exports declared in every module
- Type hints for public functions
- Local imports to avoid circular dependencies

### Testing Changes

```bash
# Quick manual test
./tau.py "test prompt"

# Unit tests (requires pytest)
cd src && pytest

# RLM sanity tests (requires LLM endpoint)
cd src && bash sanity.sh
```

## Debugging

### Quick Checks

```bash
./tau.py --debug "test prompt"
```

### Log Files

All session artifacts live in `LOG_DIR` (default: `~/.local/taurlm/log`):

| File | Purpose |
|------|---------|
| `{prefix}.log` | Agent log (stdout/stderr) |
| `{prefix}.audit` | Structured audit log |
| `{prefix}.context` | Conversation context (JSON) |

### In-Agent Commands

| Command | Purpose |
|---------|---------|
| `/goal` | Show/set current goal |
| `/agent` | List/manage active spawns |
| `/heartbeat` | Show/configure heartbeat |
| `/autonomous` | Show/toggle autonomous mode |
| `/refine <text>` | Refine current answer |
| `/continue` | Continue from saved context |
| `/ctx` | Display/compress context |
| `/wiki` | Knowledge store operations |
| `/llm` | Show/switch LLM group |
| `/tweak` | Set/delete runtime gen param overrides |
| `/status` | Show agent status |
| `/skills` | List available skills |

### Markdown Commands (`commands/*.md`)

Additional commands are defined as markdown files in `commands/*.md` and discovered at
startup (files prefixed with `_` are internal helpers invoked by other commands):

| Command | File |
|---------|------|
| `/delegate` | `_delegate.md` |
| `/improve` | `_improve.md` |
| `/update` | `_update.md` |
| `/heartbeat` | `heartbeat.md` (shadowed — see precedence) |
| `/sanitytest1`, `/sanitytest2` | `sanitytest1.md`, `sanitytest2.md` |

**Precedence:** Slash-command dispatch checks `.py` modules (`commands.COMMANDS`)
**before** markdown files (`commands.MD_COMMANDS`). When both exist for the same name
(e.g. `heartbeat`), the `.py` handler wins and the `.md` file is unreachable.

## Rules

### Git Workflow

| Rule | Details |
|------|---------|
| Atomic | Every task result = git commit (PASS or FAIL) |
| No partials | No partial states, no checkpoints to restore |
| Truth | Git commit = final truth |

### Security

| Rule | Details |
|------|---------|
| Environment | Run in sandboxed environment (Docker/VM) |
| Exposure | Do NOT expose to public internet |
| Credentials | Do NOT provide production credentials |
| Access | Restrict filesystem and network access |
| Code | Assume ALL generated code may be unsafe |

### File Naming Conventions

| Pattern | Purpose | Example |
|---------|---------|---------|
| `agent_*.py` | Core agent modules | `agent_core.py`, `agent_llm_models.py` |
| `rlm/*.py` | RLM modules (lives under `src/rlm/`; resolves from cwd `src/`) | `kernel.py`, `answer.py`, `spawn.py` |
| `commands/*.py` | Command files | `goal.py`, `agent.py` |

Note: REPL-exposed helpers such as `wiki` and `spawn` are wired into the namespace in `rlm/kernel_wiring.py` rather than living in standalone modules.

### Path Convention

**CRITICAL:** The agent runs from `src/` (cwd: `~/taurlm/src`). Task files and other project-level files must use **absolute paths** to avoid being created in the wrong location.

| Context | Correct Path | Wrong Path | Why |
|---------|-------------|------------|-----|
| Task files | `<repo>/tasks/1_todo/` | `tasks/1_todo/` | Relative `tasks/` resolves to `src/tasks/` (wrong) |

**Rule:** Always use absolute paths for ALL task file operations.

### Agent Behavior (from AGENT_RLM.md)

| Rule | Details |
|------|---------|
| Python REPL | Main interface — no tools, pure Python |
| Answer | `answer["content"]` + `answer["ready"] = True` |
| Planning | Use `wiki` for persistent notes, `host_request('goal')` for task management |
| Changes | No code changes until user explicitly asks |
| Analysis | Use `pyscan` + `pyanalyze` before modifying Python code |
| Testing | Always test after changes |
| Process | **NEVER kill `tau.py` process** |
| Verification | Run `bash sanity.sh` (from src/) |

## Links

- [Root README (../README.md)](../README.md) — User-facing documentation
- [Design Docs (../docs/designs/)](../docs/designs/) — Architecture, decisions, implementation guides
- [Project Structure (../docs/STRUCTURE.md)](../docs/STRUCTURE.md) — Project layout and cleanup principles
- [Package Inventory (../docs/package-inventory.md)](../docs/package-inventory.md) — Module/package inventory
