# Testing — Guide

## Manual Testing

```bash
# Quick test
./tau.py "hello"

# Chained inputs
./tau.py "X=1" "What is X?"

# With specific LLM group
./tau.py --llm cuda "test prompt"
```

## Unit Tests

```bash
cd src && pytest          # Full suite
cd src && pytest test_agent_context_validation.py  # Specific module
```

26 test files in `tests/` (~24 test modules) covering: context, LLM pipeline, A2A, config, console, loop detection, models, compression, vision recovery, loop escalation, heartbeat, input protocol, session registry, and more. An additional 26 legacy test files exist in `tests_legacy/` (pre-RLM transformation).

## End-to-End Tests: sanity.sh (GOLD STANDARD)

```bash
bash sanity.sh            # Full e2e suite (~100 seconds, requires LLM endpoint)
```

### sanity.sh — Absolute Prerequisite for Everything

`sanity.sh` is the **gold standard** and the **only gate** between broken code and production. It tests CLI, positional args, file I/O, /help, unknown commands, /agent, .md multiprompt, and /ctx (9 tests total).

**sanity.sh MUST pass 100% — zero failures, zero exceptions.**

### Absolute Rules

| Rule | Details |
|------|---------|
| **100% pass rate required** | Zero failures. Zero exceptions. No partial passes. |
| **No "pre-existing" excuses** | "Pre-existing error" or "not caused by current edits" is **NOT valid**. If it fails, fix it. |
| **Stop everything on failure** | If sanity.sh fails, STOP ALL OTHER WORK and fix the root cause. |
| **Never assume model failure** | Always investigate the code. Model failures are symptoms, not root causes. |
| **Never modify tests** | Tests, prompts, and expectations in sanity.sh are **immutable**. Fix the code, not the tests. |
| **Cannot proceed without 100%** | You cannot move forward on ANY task until sanity.sh passes 100%. |

### Why This Matters

sanity.sh is the only automated verification that the agent works end-to-end. A single failure means something fundamental is broken — whether it's a code regression, a configuration issue, or an environmental problem. Patching around failures or blaming the model compounds technical debt and erodes confidence in the codebase.

The correct response to **ANY** sanity.sh failure is:
1. Investigate the failure
2. Find the root cause in the code
3. Fix the code
4. Re-run sanity.sh until it passes 100%

### When sanity.sh Fails

```bash
# 1. Read the full log
cat $SANITY_LOG

# 2. Check agent log
cat $SANITY_AGENT_LOG

# 3. Reproduce the failing test manually
./tau.py "<failing prompt>"

# 4. Investigate the code path that caused the failure
# 5. Fix the root cause — never change the test
# 6. Re-run sanity.sh until it passes 100%
```

### Test Structure

- Unit tests organized by module in `src/tests/` (26 files)
- Legacy tests in `src/tests_legacy/` (26 files, pre-RLM)
- sanity.sh: 9 numbered tests (Test 1–Test 9) covering stdin pipe, positional args, file I/O, /help, unknown command, /agent, .md multiprompt, /ctx
- Helper functions: `expect`, `expect_not`, `pass`, `fail`, `log_test`, `result_since_test`, `has_exceptions`, `show_failure_output`, `cleanup_sanity`, `cleanup_files`

## Testing Rules

| Rule | Details |
|------|---------|
| Gold standard | `sanity.sh` — end-to-end tests requiring LLM endpoint (~100 sec) |
| Unit tests | `cd src && pytest` — 26 test files in `tests/` + 26 in `tests_legacy/` |
| Naming | Numbered tests in `sanity.sh` (e.g., `Test 1: Math via stdin pipe`) |
| Structure | SETUP → EXECUTE → VALIDATE → CLEANUP |
| Helpers | `expect_*()` functions — print PASS/FAIL, return 0/1 — **DO NOT INVERT** |
| Prompts | **NEVER modify `sanity.sh` prompts** — deliberately crafted |
