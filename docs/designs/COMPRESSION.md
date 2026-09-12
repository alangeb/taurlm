# Context Compression Pipeline

## When Compression is Called

| Trigger | Context Top | In-progress turn? |
|---------|-------------|-------------------|
| Proactive (85%) | USER (new message) | YES — no assistant yet |
| Reactive (overflow) | USER (new message) | YES — no assistant yet |
| Manual (/ctx compress) | USER or ASSISTANT | NO — turn complete |

## RLM Message Pattern

```
[user] → [assistant: code] → [user: REPL output] → [assistant: code] → [user: REPL output] → [assistant: final + summary]
```

## Pipeline Steps (10)

| # | Step | What it does | LLM? |
|---|------|-------------|------|
| 1 | PRUNE_IMAGES | Replace image blocks with text (no-op for RLM) | No |
| 2 | OVERSIZE_USER_PRUNE | Remove oversized user messages | No |
| 3 | DROP_REASONING | Strip reasoning fields from assistant msgs | No |
| 4 | COLLAPSE_TURNS_50 | Replace summarized turns in first 50% with [user]+[summary] | No |
| 5 | REDACT_BLOCKS_50 | Strip intermediates from completed blocks (first 50%) | No |
| 6 | COLLAPSE_TURNS_FULL | Same as 3, entire context | No |
| 7 | REDACT_BLOCKS_FULL | Same as 4, entire context | No |
| 8 | FULL_RESET | LLM rebuild (summary + plan) | Yes (2 calls) |
| 9 | CONVERSATION_SUMMARY | Deterministic restructure | No |
| 10 | BLIND_TRUNCATE | Truncate from beginning | No |

Stops early when `size <= target_size`.

## Step-by-Step: Context State Handling

### COLLAPSE_TURNS_50 / FULL
- Finds: `[user(real)] → ... → [assistant with "summary"]`
- Replaces with: `[user] + [assistant: summary]`
- **In-progress turn**: Correctly skipped (no assistant with summary after top user)
- **Top is USER (synthetic/repl)**: REPL output is between user and summarized assistant — correctly collapsed
- **Top is ASSISTANT (no summary yet)**: Not collapsed

### REDACT_BLOCKS_50 / FULL
- Finds: `[user(real)] → ... → [LAST assistant before next user]`
- Keeps: user + last assistant
- Removes: all intermediates (synthetic user messages, intermediate assistants)
- **CRITICAL**: Must find the LAST assistant, not the first. In RLM, the first assistant is always right after the user (0 intermediates). The block extends to the last assistant.
- **In-progress turn**: Correctly skipped (no assistant after top user)
- **Top is USER (synthetic)**: Synthetic REPL message is part of the block, correctly removed

### CONVERSATION_SUMMARY
- Rebuilds entire context into structured format
- Groups by user messages: each "interaction" = user → assistant (+ synthetic user)
- **In-progress turn**: Included as "current task" section
- **Top is USER (synthetic)**: Attached to previous assistant's interaction

### BLIND_TRUNCATE
- Truncates from beginning, preserves "CURRENT TASK" section
- **Safe for all cases**: Most recent messages always preserved

### FULL_RESET
- LLM generates summary + plan from entire context
- **In-progress turn**: LLM sees it and includes in summary

## Key Invariants

1. **OpenAI context compliance**: User/Assistant must alternate. No two Users adjacent, no two Assistants adjacent.
2. **In-progress turns are NEVER collapsed/redacted**: No final assistant (or no summary).
3. **System message is always preserved**: Framework restores it if a step loses it.
4. **Synthetic user messages are intermediates**: Can be removed. NOT part of OpenAI alternation rule.

## OpenAI Alternation Rule in RLM

Two roles: user, assistant. (No tool role in RLM mode.)
- Alternation rule applies to USER and ASSISTANT only.
- Synthetic user messages (REPL output, bridges) appear between user and assistant.
- After compression: no two USER adjacent, no two ASSISTANT adjacent.

**Why it's safe**: Turns always start with USER.
```
Before: [user1] → [asst1] → [user2] → [asst2]
After collapse: [user1] → [asst1: summary] → [user2] → [asst2]  ← valid
```

### User-End Invariant (framework choke point)

After compression the context MUST end on a `user` message. Compression is
triggered by an overflow error mid-invoke and the compressed list is re-sent
as-is to `_invoke_llm_streaming` — it is the live prompt, so it must end on the
user's pending request for the model to answer it. A trailing assistant message
would make the model prefill instead of answering. `framework.py` and
`full_reset.py` therefore end the rebuilt context on `user`; no synthetic
assistant is appended.

## Logging

### Console (non-verbose) — 3 lines:
```
[COMPRESS] Trigger: proactive (context at 94% >= 85%)
[COMPRESS] 45,230B / 12 msgs → target 22,615B (50% reduction)
[COMPRESS] DONE: 25,400B / 8 msgs (43.8%) via DROP_REASONING+REDACT_BLOCKS_50 [2.1s]
```

### Console (verbose) — adds per-step (only steps that did work):
```
  DROP_REASONING: 45,230 → 38,100B (saved 7,130B) [REDUCED]
  REDACT_BLOCKS_50: 38,100 → 25,400B (saved 12,700B) [ACHIEVED]
```

### Audit file:
```
COMPRESS_START original=45230 target=22615 factor=0.50 tokens=85000
COMPRESS_STEP step=DROP_REASONING before=45230 after=38100 msgs=12→12 status=REDUCED actions="dropped reasoning at msg 3"
COMPRESS_STEP step=REDACT_BLOCKS_50 before=38100 after=25400 msgs=12→8 status=ACHIEVED actions="redacted block [2:5]"
COMPRESS_END final=25400 saved=19830 algorithms=DROP_REASONING,REDACT_BLOCKS_50 duration=2.1s
```

## Lessons Learned

1. **REDACT_BLOCKS must find the LAST assistant**, not the first. (Bug fixed 2025-01)
2. **COLLAPSE_TURNS is safe** because it anchors on the summary (only on last assistant).
3. **In-progress turns are naturally protected** by absence of final assistant/summary.
4. **OpenAI alternation is maintained** because turns always start with USER.
5. **Trigger is logged at caller level**, not inside the pipeline. Pipeline is self-contained.
