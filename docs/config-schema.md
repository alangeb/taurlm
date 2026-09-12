# RLM Configuration Schema Design

**Phase:** 0.5  
**Status:** Design  
**Depends on:** Phase 0.1 (Codebase Analysis), Phase 0.4 (Directory Structure)

---

## Overview

This document defines the RLM-specific configuration additions to `tau.json` and `agent_config.py`. The RLM config introduces a new top-level `rlm` section with nested configuration for the REPL kernel, sub-LLM spawning, compaction, and security.

---

## Proposed JSON Schema

### Complete RLM Config Section

```json
{
  "rlm": {
    "max_turns": 1000,
    "max_tokens": 100000,
    "repl": {
      "max_output_chars": 8192,
      "max_state_size_mb": 100,
      "allowed_modules": ["stdlib"],
      "blocked_modules": [],
      "bash_timeout_seconds": 180,
      "python_timeout_seconds": 180
    },
    "sub_llm": {
      "model": null,
      "max_children": 10,
      "max_recursion_depth": 1,
      "tools": [
        "file_read", "file_write", "file_edit", "bash",
        "search", "fetch", "grep", "glob", "ls", "pyscan", "end_turn"
      ]
    },
    "auto_summary": {
      "enabled": true,
      "model": null,
      "max_output_tokens": 512
    }
  }
}
```

---

## Field Descriptions

### Top-Level `rlm` Section

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_turns` | int | `1000` | Maximum LLM turns before forced end-of-turn |
| `max_tokens` | int | `100000` | Maximum tokens to use before triggering compression |

### `repl` Section — Python REPL Kernel

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `python_timeout_seconds` | float | `180.0` | SIGALRM timeout for Python block execution |
| `max_output_chars` | int | `8192` | Maximum characters of REPL output to capture and append to context |
| `max_state_size_mb` | float | `100.0` | Maximum kernel state size (variables, imports) in MB before warning |
| `allowed_modules` | list[str] | `["stdlib"]` | List of allowed Python modules. `"stdlib"` means all standard library modules |
| `blocked_modules` | list[str] | `[]` | List of explicitly blocked modules (overrides allowed_modules) |
| `bash_timeout_seconds` | float | `180.0` | Maximum seconds for a bash command |

### `sub_llm` Section — Sub-LLM Spawning

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | str \| null | `null` | LLM group name for sub-LLMs. `null` = inherit from parent |
| `max_children` | int | `10` | Maximum concurrent child agents **[NOT WIRED — actual limit is `MAX_SPAWNS=5` hardcoded in `rlm/spawn.py`]** |
| `max_recursion_depth` | int | `1` | Maximum recursion depth for spawn() calls (0 = no recursion, 1 = root can spawn children) **[NOT WIRED — actual limit is `MAX_NESTING_DEPTH=3` hardcoded in `rlm/spawn.py`]** |
| `tools` | list[str] | 11 tool names | Legacy field (ignored in RLM mode — no tools) |

## Environment Variable Overrides

Following the existing pattern, RLM config supports environment variable overrides with the `TAU_RLM_` prefix:

```python
```python
# RLM env vars are part of the main _ENV_OVERRIDES tuple in Config:
    ("TAU_RLM_ENABLED", "rlm", "enabled", str.lower),
    ("TAU_RLM_MAX_TURNS", "rlm", "max_turns", int),
    ("TAU_RLM_REPL_MAX_OUTPUT", "rlm", "repl_max_output_chars", int),
```
```

---

## agent_config.py Dataclasses (Implemented)

The following dataclasses are implemented in `agent_config.py`:

- `REPLConfig` — max_output_chars, max_state_size_mb, allowed_modules, blocked_modules, bash_timeout_seconds, python_timeout_seconds
- `SubLLMConfig` — model, max_children, max_recursion_depth, tools
- `RLMConfig` — repl, sub_llm, max_turns, max_tokens, auto_summary
- `AutoSummaryConfig` — enabled, model, max_output_tokens
- `LoopDetectionConfig` — window_size, repeat_threshold
- `Config` — top-level config with env var overrides

## Migration (Complete)

All migration phases are complete. RLM config is fully implemented and active.

---

---

## Validation Rules (NOT IMPLEMENTED — no validation code in agent_config.py)

| Field | Validation | Error if Invalid |
|-------|------------|------------------|
| `repl.max_output_chars` | `> 0` and `<= 100000` | "REPL max output must be between 1 and 100000 characters" |
| `repl.max_state_size_mb` | `> 0` and `<= 1024` | "REPL max state size must be between 1 and 1024 MB" |
| `sub_llm.max_children` | `> 0` and `<= 50` | "Max children must be between 1 and 50" |
| `sub_llm.max_recursion_depth` | `>= 0` | "Max recursion depth must be >= 0" |
| `max_turns` | `> 0` and `no upper bound` | "Max turns must be > 0" |
| `max_tokens` | `> 0` and `<= 1000000` | "Max tokens must be between 1 and 1000000" |

---

## Example tau.json (Actual)

```json
{
  "timeout": 180,
  "agent_name": "default",
  "debug": false,
  "loop_detection": {
    "window_size": 30,
    "repeat_threshold": 3
  },
  "rlm": {
    "max_turns": 1000,
    "max_tokens": 100000,
    "repl": {
      "max_output_chars": 8192,
      "max_state_size_mb": 100,
      "bash_timeout_seconds": 60
    },
    "sub_llm": {
      "model": null,
      "max_children": 10,
      "max_recursion_depth": 1,
      "tools": [
        "file_read",
        "file_write",
        "file_edit",
        "bash",
        "search",
        "fetch",
        "grep",
        "glob",
        "ls",
        "pyscan",
        "end_turn"
      ]
    }
  }
}
```

> See `src/tau.json` for the complete configuration including `llm_groups` and `malformed`.

## Testing Checklist

- [ ] Config loads successfully with `rlm` section
- [ ] Config loads successfully without `rlm` section (defaults)
- [ ] Environment variable overrides work for all RLM fields
- [ ] Nested config dataclasses are properly instantiated
- [ ] Validation rules catch invalid values
- [ ] RLM config loads correctly (no `enabled` field — always active)
- [ ] `get_config().rlm.repl.bash_timeout_seconds` returns `180` by default
- [ ] All RLM config fields are accessible via `get_config().rlm.*`

---

## Rollback

To rollback this phase:
```bash
git checkout phase-0.5-start -- src/agent_config.py docs/config-schema.md
```

---

## Next Steps

After Phase 0.5 is complete:
1. Phase 0.6: Package Inventory (can run in parallel)
2. Phase 0.7: Test Framework Setup
3. Phase 1: REPL Kernel Implementation (depends on 0.5, 0.6, 0.7)
