# Fence Pipeline v2 — Design Decisions

Status: IMPLEMENTED. This document now describes the shipped code, not the
pre-fix intent — where the two diverged, the code won and the text was changed.
Source: session 81891(64437) successor, 2026-09-12; hardening pass 2026-09-12.
Supersedes the ad-hoc if-ladder in `agent_fence_repair.py` and the fallback mess
in `agent_repl_parse.py`.

Files: `agent_repl_parse.py` (counter + strict extractor), `agent_fence_repair.py`
(3-stage ladder), `agent_pipeline.py` (`validate_eot`), `agent_loop.py` (no-code
streak). Tests: `src/tests/test_fence_repair.py`, `src/tests/rlm/test_repl_parse.py`,
`src/tests/test_pipeline_eot.py`, `src/tests/rlm/test_no_code_streak.py`.

## Core contract

- repair normalizes the assistant text; the extractor then only accepts perfectly-formed active-style blocks.
- repair runs on the ASSISTANT MESSAGE ONLY. Not on reasoning_content (dropped; never executed).
- repair runs BEFORE `append_assistant` so the model never re-sees its own bad pattern.
- Only the WINNING stage may modify the text. Each stage runs against a snapshot; commit only the winner.
- Invariant (unit-tested AND fuzz-tested): `repair(repair(x))[0] == repair(x)[0]` for the
  TEXT. The REPORT is not idempotent by design — run 2 emits no warning. Repair runs once per
  fresh assistant message, so run 2 never happens in production; the invariant is still
  enforced because it is the only cheap way to prove the ladder has no oscillation.
  `test_fuzz_repair_never_raises_never_drops_is_idempotent` asserts, over 1000 seeded inputs
  x 4 styles: repair never raises, a reported step always leaves extractable code, and the
  text is a fixed point after one pass.
- `_finalize` (see below) is what makes that invariant true. Without it a converting stage
  leaves ACTIVE-style orphan closers in its own output, which the next pass would strip.

## The counter (shared by repair and the extractor)

Single linear line-by-line pass. `depth` +1 on an opener, -1 on a closer. Body = lines at depth > 0.
A block is emitted when depth returns to 0.

- Opener: token ALONE on the line, strictly at COLUMN 0. NO leading whitespace accepted (decided).
  TRAILING spaces/tabs ARE tolerated (`@PY  ` opens) — models pad fence lines constantly and a
  pad is not ambiguity. Trailing non-blank text is still not an opener.
- Closer: token starts at column 0; what follows may be whitespace, other content, or nothing.
  Must be a token-boundary match, never a substring match (`@/PYX` must NOT close).
- CRLF: normalized to LF at the top of `repair_response` ONLY. The extractor deliberately does
  NOT normalize (`\r` is not a trailing blank, so a CRLF opener simply does not match there).
  One owner for the rule, in both mirrored copies of the counter.
- `open open close` -> depth 1,2,1 -> EOF depth 1 -> ONE block, inner opener kept as literal code.
- `open close close` -> depth 1,0 -> block emitted; the second closer is an ORPHAN -> dropped.
- Only the LAST block may extend to end-of-text (auto-close at EOF). Nowhere else. This falls out
  of the counter automatically: depth > 0 at EOF can only be the in-progress last block.
  FENCE openers only — protocol tags never auto-close (see "Step 3 closer rule").
- Empty / whitespace-only body = clearly wrong -> REJECT the block.
- No syntax checking in repair (decided). Accept the block and let the REPL error teach the model.
  Consequence: repair and the extractor can never disagree, so no fence-neutralization is needed.

## The ladder (3 stages, first stage with >=1 ACCEPTED block wins)

'Succeeded' means >=1 block ACCEPTED, not merely found. A stage whose candidates are all rejected
must fall through, otherwise it locks out a good block sitting further down the reply.

1. ACTIVE style, counter pass -> all balanced blocks + optional auto-closed last block.
2. Other styles, order `quad -> html -> at -> std` minus the active one (`_OTHER_ORDER`).
   TRIPLE-TICK IS LAST, deliberately:
   triple-tick appears too frequently inside real prose content to be trusted early.
3. Protocol tags (see below).

## Step 3 — protocol tags

Runs ONLY when stages 1-2 found zero accepted blocks. Catches tool-call artifacts from models that
were RL-trained on an Anthropic-style tool dialect and emit it instead of a code fence.

- Extensible registry `PROTOCOL_SPECS: list[ProtocolSpec]` (name, open_res, close_res, lang resolver).
  Shipped: anthropic-tool_call, anthropic-function (lang from tag name), anthropic-parameter.
  Appendable: OpenAI-ish chat markers, DeepSeek full-width markers, etc.
- The opener is a GROUP: a run of consecutive lone-tag lines (`tool_call` + `function=` + `parameter=`)
  counted as ONE opener at depth 1. The group ends at the first non-tag line.
- Do NOT depth-count across a tag family: 3 opens then 3 closes would leave 2 closers inside the
  body as literal code. Rule is: one opener group, FIRST matching closer of the same family closes.
  The leftover closers become orphans and are dropped by the winning stage.
- Allowed closers: the spec's OWN closer(s), the ACTIVE style's closing fence, or the start of
  the next opener group. NO EOF auto-close (changed during hardening — see "Step 3 closer rule").
  Rationale for accepting the active closer: step 3 only runs when stages 1-2 found zero accepted
  blocks, so an active closer present here is closing THIS protocol block, not a real fence.
  (Hardening bug A: the code used to treat that closer as a protocol tag and then index the
  result of `_proto_kind()` — None — raising TypeError.)
- Prose before the group and after the closer is preserved.

## Warnings

- Console: yellow, multi-line. Line 1 = plain-language explanation, then the repaired block shown
  FIRST 2 LINES, then `... +N lines ...`, then LAST 2 LINES, each fence on its own line.
- Step ids carry both a machine id and a human sentence, e.g. `half-open-active` = "Your code block
  was opened but never closed. I assumed it ran to the end and closed it for you."
- No-code is a WARNING (one line), NOT an error. It must NOT increment `consecutive_errors`
  (SHIPPED: the old `consecutive_errors += 1` on the no-code branch is gone; `execute_blocks`
  is never handed a count inflated by prose turns — asserted by
  `test_no_code_does_not_touch_consecutive_errors`).

## The no-code streak (SHIPPED) — separate from `consecutive_errors`

`consecutive_errors` is the REPL failure counter: it force-ends at 5 and is meant to stop a model
that is burning turns on broken code. A plain-text reply is not that. Feeding no-code into it was
the original bug: five prose replies read as five execution failures and killed healthy turns.

So no-code gets its OWN streak on the agent object:
  - `agent._no_code_streak` (created lazily via `getattr(..., 0) + 1`; not an `__init__` attribute)
  - 1st occurrence: the ordinary one-line `[SYSTEM: NO CODE]` warning, no firmness
  - 2nd occurrence onward: the same warning plus an explicit `attempt N of 5` clause naming the
    ACTIVE fence tokens (`ft['py_open']` / `ft['py_close']`), so the model cannot claim it guessed
  - `_NO_CODE_FORCE` (5): force-end the turn with a message that shows the tokens again
  - reset to 0 as soon as a code block executes

Both counters are independent and neither resets the other. The escalation is deliberately a
warning ladder, not an execution error: it must apply pressure without pretending the model's
code failed. Tests: `src/tests/rlm/test_no_code_streak.py` (stub agent driving the real loop).
- The one-line note is appended to the synthetic user feedback AFTER the REPL output/error, because
  it is more visible there than as a header.
- Every repair (including successful auto-close) produces a note. `active-enclosed` never does.
- Repair must land in the AUDIT as well as the console (agent_audit_writer.py:37: audit is truth).
- Suppress `repl_code()` for repaired blocks so the code is not echoed twice.

## Deletions this enabled (SHIPPED)

- `extract_code_blocks` cross-style fallback loop (agent_repl_parse.py:276-295)
- `_FENCE_INCOMPLETE_PATTERNS` (agent_repl_parse.py:104-121) and the `matched_end < len(text)` tail
- `extract_python_code` (dead in prod; tests only)
- `raw_mode` param (never set by prod code or config)
- `_CD_MAGIC_RE` in agent_repl_parse.py:128 (live copy is rlm/kernel.py:131)
- `_VALID_LANGUAGES` (defined, never referenced)
- `_PROTO_SUBSTR` hardcoded substring tuple in agent_fence_repair.py:38 (replaced by the registry)
- `validate_eot` opener-LINE counting (SHIPPED: it now counts `scan_style(...).blocks`)

## Evidence the current error path is too aggressive

During this session the agent received `[SYSTEM: NO CODE]` three times and
`[SYSTEM: MULTI-BLOCK EOT]` once, all triggered by fence-like content inside prose/strings on
otherwise healthy turns. Plain-text replies were repeatedly rejected outright.

## EOT validation (SHIPPED)

`validate_eot` counts BLOCKS with the shared `scan_style` counter, not opener lines, so fence-like
text inside prose or inside a string no longer rejects a healthy EOT. One deliberate wrinkle: it
counts AUTO-CLOSED blocks too, even though the strict extractor refuses them for execution. If it
did not, "two blocks where the second is unclosed" would slip past the one-block EOT rule by
accident — the extractor would run only the first block and the second would be lost silently.
Rejecting is the honest answer there. Tests: `src/tests/test_pipeline_eot.py`.

## Deliberate decisions that must NOT be weakened

- Triple-tick (`std`) is tried LAST among the styles, whatever the active style is.
- Repair runs on the ASSISTANT MESSAGE ONLY — never on reasoning_content.
- No syntax checking in repair.
- No-code is a warning, not an execution error.

## Known trade-off accepted

Because sanitize (orphan-drop) runs only inside the winning stage, a stray `</function>` in a reply
where stage 1 won is never removed and stays in context permanently. This is a deliberate regression
from today's unconditional orphan-drop, accepted for stage-purity and idempotency.

## Step 3 closer rule (SHIPPED — no EOF auto-close)

A step-3 block, opened by a run of consecutive lone protocol-tag lines, may be closed ONLY by:
  (a) a run of consecutive lone closing tags (`</tool_call>` / `</function>` / `</parameter>`,
      possibly several in a row — the whole run is ONE closer), or
  (b) the ACTIVE style's closing fence, or
  (c) the first line of the NEXT opener-tag run.

There is deliberately NO end-of-text auto-close for protocol tags. This INVERTS the original
design, which allowed (c) = EOF for the last block. Reason: a lone protocol tag is far too weak
an opener to justify extending to end-of-text — doing so turns the trailing prose of a reply
into executed code, which is the worst possible failure mode (silent, destructive). Fences still
auto-close (a `@PY` with no closer is unambiguous); tags do not. Consequence: a protocol block
that is never closed is simply not repaired, and the model gets the ordinary NO CODE warning.
Test: `test_protocol_block_without_closer_is_not_auto_closed`.

**One exception (H1b):** a reply that ends with a LONE `<tool_call>` - a single tag
line, no function/parameter envelope, nothing after it - IS auto-closed. That shape is a pure
truncation artifact (the code after the tag was dropped on the floor), whereas an envelope group
really can be followed by prose. Tests: `test_lone_tool_call_at_eof_is_auto_closed`,
`test_lone_tool_call_at_eof_only_for_lone_tag`.

No other style's closers are accepted.

Consequence: step 3 needs NO depth counter. One tag group opens, the next tag group or an allowed
closer shuts it. A language established by an enclosing `<function=...>` is inherited by nested
`<parameter=...>` groups (a bash command must not be rendered as a python block), and is cleared
when the function/tool_call closer is consumed.

## The render gate: `_renders_extractable` (SHIPPED — this is what makes strictness safe)

The whole design rests on "the extractor may be strict because repair guarantees well-formedness".
That is only true if repair cannot commit a rewrite the extractor then discards. So a stage may
commit ONLY if its rendered output, re-read through the SAME `scan_style` counter, yields exactly
the blocks the stage intended (same count, each intended body a prefix of the read-back body, any
extra lines being closers only) and is not left unclosed. If the gate fails, the stage falls
through to the next one, and if no stage passes, the text is returned unchanged with no report.

Why this matters concretely (hardening bug B): `"```python\n@/PY\nprint(1)"` under active style
`at`. Stage 2 sees a std opener, but the embedded ACTIVE closer truncates the body to nothing; the
naive render produced text the strict extractor throws away — i.e. repair SILENTLY DELETED CODE.
With the gate, repair reports NO step and returns the input byte-for-byte. That is the correct
answer: leaving the model's own text intact lets the model see and learn from the failure;
deleting its code teaches it nothing.

`_render` also emits one extra closer per unmatched ACTIVE opener found inside a body (the
`open open close` case, where the inner opener is kept as literal code) so the rendered text
re-scans as an ENCLOSED block rather than an unclosed one.

## `_finalize` and why orphan stripping is needed for idempotence (SHIPPED)

A converting stage (and the protocol stage) copies everything outside its blocks verbatim. Inert
closer lines from another dialect — or ACTIVE closers that were sitting in the input as prose —
therefore survive into the output, where they are ORPHANS under the active style. A second repair
pass would strip them, so `repair(repair(x))[0] == repair(x)[0]` would be false.

`_finalize` runs after `_render` and drops ACTIVE-style orphan closers from the candidate output
before the gate is consulted. It is harmless by construction: an orphan closer carries no content.
Example (hardening bug C): `"@/PY\n@/PY\n```python\n@PY"` -> the std stage converts the fence and
copies the two leading `@/PY` lines; `_finalize` removes them, so pass 2 is a no-op.

Note the asymmetry this preserves: orphan-drop still runs ONLY inside a winning stage. A reply
that contains only stray closers and no opener wins no stage, so those closers stay in context.
That is the accepted trade-off below, not an oversight.

## Self-inflicted evidence (same session)

Seven consecutive replies were rejected with `[SYSTEM: NO CODE]` because the assistant emitted prose
only. Under the shipped design those are a warning line, no `consecutive_errors` bump, a firm
message naming the active fence tokens from the 2nd occurrence, and a force-end at the 5th.


## Post-review hardening (three fixes)

1. **Single owner for the line predicates + counter.** `_tokens`, `_opener_of`,
   `_closer_of`, `Scan` and `scan_style` now live ONLY in `agent_repl_parse.py`;
   `agent_fence_repair.py` imports them. They were previously mirrored copies that
   had already begun to drift in their comments. The whole design rests on repair
   and the strict extractor agreeing about what a block is, so a second copy was a
   latent way for them to disagree silently.

2. **`closer-inside-block-suspect` diagnostic.** A fence token appearing alone on a
   line *inside* code (typical: inside a triple-quoted string) closes the block
   early and strands everything after it. This previously surfaced as the unrelated
   `orphan-close-dropped` note while code was silently lost. We now distinguish the
   two: if non-blank text is stranded between the block's closer and the orphan, we
   emit the louder note telling the model to indent the token or build it from
   parts. A genuinely trailing stray closer keeps the original, accurate note.

3. **Removed dead code.** The `_commit` empty-content guard was unreachable
   (`_render` copies all non-block text verbatim, so output can never blank
   non-empty input) and `_render`'s `orphans` parameter duplicated what
   `_finalize` already does. Orphan-closer removal is now owned solely by
   `_finalize`; `_render` only rebuilds.

Verified: 1054 tests pass, sanity.sh 14/0, ruff clean on the touched module.
