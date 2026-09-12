# RLM Main Loop Design

## Overview

The RLM (Recursive Language Model) main loop replaces the tool-calling loop with a Python REPL-based loop. The model generates Python code, which is executed in a persistent kernel, and the answer is delivered via `answer["content"]` + `answer["ready"]`.

## Previous Architecture (Tool-Calling Loop — Removed)

```
User Input → Context → LLM (with tools) → Tool Calls → Execute Tools → Context → Loop → end_turn
```

## RLM Architecture

```
User Input → Context → LLM (NO tools) → Python Code → REPL Kernel → Check answer["ready"]
                                                          ↓
                                              If ready: Extract answer, end turn
                                              If not: Append output to context, loop back
```

## Message Flow

### 1. Entry Point
- `invoke(user_input)` appends user message to context
- Calls `invoke_loop()` which dispatches to `run_rlm_loop()`

### 2. RLM Loop Iteration
```
a. Call LLM (no tool params in kwargs)
b. LLM returns response text (may contain Python code blocks)
c. Extract code blocks using extract_code_blocks()
d. If code found:
   - Execute in REPL kernel
   - Capture output (limited to max_output_chars)
   - Collect all feedback (output/errors/warnings) into ONE synthetic user message
   - Check answer["ready"]
   - If ready: extract answer["content"], end turn
   - If not: loop back to step a
e. If no code found:
   - Append response to context as assistant message (always done, before code extraction)
   - No-code warning added to feedback, counts as consecutive error
   - Synthetic user message created with warning
   - Loop back to step a (model may generate code next turn)
```

### 3. Exit Conditions
- `answer["ready"] = True` — normal exit
- Max turns reached (default 1000; `agent_loop.py` uses a fallback of 500 when config is missing) — force exit with current answer["content"]
- Exit requested (Ctrl+C) — interrupted exit
- N consecutive errors (5) — force exit with error message
- Loop escalation level 4+ (12+ warnings) — force exit via `LoopEscalationManager` (levels 2-3 inject reflection prompts but do not force exit)
- Work budget exhausted (spawn children only, B ≤ 0) — force exit

### 4. EOT Rejection (answer["ready"] reset to False)
- **Multi-block**: Response contains >1 code block opening (```python/```bash) — EOT rejected, model must consolidate
- **Empty content**: `answer["content"]` is empty/whitespace — EOT rejected
- **Oversized content**: `answer["content"]` > 100,000 chars — truncated (not rejected)

## Context Changes

### Message Types
- `repl_feedback` — ONE synthetic user message per turn containing ALL feedback (output, errors, warnings, escalation)
- `repl_output` — Context method for standalone REPL output (type="repl", survives cleanup)
- `repl_error` — Context method for standalone REPL errors (type="system", removed by cleanup)

### Message Format
The loop appends ONE synthetic user message per turn via `append_synthetic_user("repl_feedback", ...)`:
```python
# Main loop feedback (type="repl" — survives cleanup_synthetic)
{"role": "user", "content": "[U:repl | N:0 | M:5 | C:12] <all feedback: output, errors, warnings, escalation>"}
```

**Key distinction:** The main loop's `repl_feedback` message is type="repl" and **survives** `cleanup_synthetic()`. 
This means ALL turn feedback (including errors) stays on the context stack. The standalone 
`append_repl_error()` method (type="system", removed by cleanup) is only used for LLM call exceptions 
in the loop's error handler, not for normal REPL execution feedback.
## LLM Call Changes

### No Tool Params
- No `tools` or `tool_choice` in call kwargs — no tool schemas sent to LLM
- Response contains natural language + Python code blocks

### LLM Call (No Tools)
The LLM is called via `_invoke_llm_with_retry()`. No `tools` or `tool_choice` params are included in the call kwargs.

## Error Handling

### REPL Execution Errors
- Caught and included in the `repl_feedback` synthetic message (with turn number and error text)
- Loop continues (model may fix the error)
- After 5 consecutive errors, force exit with error message

### LLM Call Errors
- Same retry logic as existing `_invoke_llm_with_retry`
- Context overflow handling preserved
- Vision error handling preserved

### Timeout Handling
- LLM call timeouts: retried by `_invoke_llm_with_retry` (up to max_retries)
- REPL execution timeout: error included in feedback, counts toward consecutive_errors

## Max Turns Protection

- Counter incremented on each loop iteration
- Max turns from config (default: 1000)
- On max turns: force exit with current answer["content"] or error message

## Note: RLM is the Only Mode

The tool-calling loop has been fully removed. RLM mode is the only execution path.
`invoke_loop()` always dispatches to `run_rlm_loop()`. There is no `rlm.enabled` toggle.

## Implementation (Complete)

- `agent_loop.py` — `run_rlm_loop()` function
- `agent_core.py` — `invoke_loop()` dispatches to `run_rlm_loop()`
- `agent_llm_invoke.py` — LLM called without tool params
- `agent_context.py` — `append_repl_output()` and `append_repl_error()` methods
- `tests/rlm/test_rlm_loop.py` — Comprehensive tests

## Testing Strategy

### Unit Tests
- Test `run_rlm_loop` with mock LLM
- Test answer ready detection
- Test max turns protection
- Test error recovery
- Test REPL output in context
- Test LLM call without tool params
- Test `append_repl_output` and `append_repl_error`

### Integration Tests
- Full RLM loop with real LLM (if available)
- RLM loop is the only execution mode (no toggle)
- sanity.sh must still pass

## Rollback

See `docs/rollback-procedure.md` for the rollback procedure.
