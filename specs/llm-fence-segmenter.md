---
id: llm-fence-segmenter
version: 0.6
status: draft
weight: full
source: [ src/agent_fence_segmenter.py, src/agent_fence_repair.py, src/agent_loop.py ]
tests: [src/tests/test_fence_segmenter.py]
depends: [ fence-pipeline-v2 ]
created: 2026-09-12
updated: 2026-09-12
---
# LLM Fence Segmenter (Tier-2 Fence Repair)

## Purpose
When the deterministic fence ladder (fence-pipeline-v2) cannot repair a reply with
certainty, classify its lines with a context-free LLM call into prose/code/drop ranges
and rebuild the reply from the ORIGINAL bytes plus active fence tokens (content-
free inserted blank lines excepted — REQ-LFS-012), so the model
never sees its own broken fencing and never loses code it actually wrote. The LLM
emits index ranges only; it never re-types code.

## Requirements

### REQ-LFS-001: Verdict on RepairReport
`RepairReport` gains a `verdict` attribute with exactly one of `"clean"`, `"ok"`,
`"ambiguous"`; default `"ok"`. `repair_response` keeps its exact signature and return shape
`(text, active_style) -> (str, RepairReport)`; verdict is additive, so existing
callers and tests are untouched.

### REQ-LFS-002: Verdict computation rules
Verdict is computed INSIDE `_repair_core` (where scan tuples carry their flags and the
accepted-block list is in hand) and stored on the returned `RepairReport` at EVERY
return path — including the early-return already-valid path (which otherwise yields an
empty report; there the verdict is `"clean"` or `"ok"` per the rules below, never left
at default). Two propagation duties: (a) the `unbalanced` list is consulted IN
FLIGHT while `_repair_core` still holds it — a stage that later wins discards those
notes from the report, so verdict must NOT be re-derived from the final report alone;
(b) when `repair_response` re-wraps the report in the glue-branch (it prepends glue
entries into a NEW RepairReport), the verdict computed by `_repair_core` MUST be
carried onto that new report — a glued turn must not silently default back to `"ok"`.
`repair_response` passes the verdict through; the glue-split and
envelope-strip passes never alter it (both are proven re-scan transforms under
fence-pipeline-v2). Rules
(`fence_like` = REQ-LFS-003):
- `"clean"` when `fence_like(text)` is False (empty input and pure prose).
- `"ambiguous"` when ANY of: a refusal step (`unbalanced-block` /
  `closer-inside-block-suspect`) is present; OR any accepted block of the winning stage
  has its SCAN TUPLE's `auto_closed` flag set AND its body is NOT provably
  self-contained: for lang python, `ast.parse(body)` must FAIL to count as ambiguous —
  an auto-closed block whose body parses cleanly lost nothing (nothing follows at EOF),
  so it stays ok and pays no fixer call (B1: `scan_style` sets `auto_closed` on every
  EOF block, and a closerless-but-clean final block is a common success shape);
  auto-closed BASH blocks have no parser and count as ambiguous; OR the auto-close came
  from the protocol stage (H1b) or a converting stage. `Entry` carries no auto_closed
  field and H1b reuses step `protocol-tags`, so this is computed where the scan tuple
  (index 4) is in hand, never from the final report; OR zero accepted blocks exist AND `fence_like(text)`
  is True.
- `"ambiguous"` ALSO when the winning stage is active-enclosed (the early-return fast
  path with an empty report) AND any accepted block's body contains a line that is a
  fence opener or closer of ANOTHER style at column 0 AND `ast.parse` of that block's
  body fails. That combo is the mixed-style mega-merge (e.g. @PY opened, ```-shaped
  lines inside, @/PY at the end): P2 std-gating makes those lines inert prose but they
  are EXECUTED inside the active block, so the whole merged block dies in the REPL and
  the ladder's 'certainty' is false. Deliberately narrow: string content that merely
  CONTAINS foreign fence characters parses fine and must stay ok (README-writing turns
  must not pay a tier-2 call); bash bodies have no parse check and keep today's
  accept-and-REPL-teach behavior.
- `"ok"` otherwise — ladder committed a proven non-auto-close rewrite, or the text was
  already valid (glue-split and envelope-strip are proven, so they stay ok).

### REQ-LFS-003: fence_like predicate
New public `fence_like(text: str) -> bool` in `agent_fence_repair` (style-agnostic by
intent — it probes ALL four styles). True iff some line matches `_opener_of` or
`_closer_of` under any style in `FENCE_STYLES` (per-style `_tokens`), or
`_proto_kind(...) is not None`, or `_env_open`/`_env_close` — but for the envelope
predicates ONLY when the line has NO leading whitespace (`_env_open`/`_env_close` match
`^\s*`; an indented quoted tag is legitimate prose per REQ-LFS-011 and must not make a
prose turn fence_like). Do NOT reuse
`_ANY_FENCE_RE`: it is a DOTALL block-level opener..closer pattern and never matches a
lone opener or orphan closer. Pure, no I/O.

### REQ-LFS-004: Tier-2 trigger site (main path only)
In the main path of the agent loop, right after `repair_response` and BEFORE
`append_assistant`: when `report.verdict == "ambiguous"`, call `segment_and_repair`
(REQ-LFS-014) and, if it returns verdict "ok", use its assembled text as the new
assistant text. The `StreamAbortedError` path must NOT trigger tier-2 (truncated
replies are half-open by construction; tier-2 would misfire on every token cutoff).

### REQ-LFS-005: Tier-2 never adds a failure mode
On ANY error — transport, timeout, circuit open, malformed answer past retries —
`segment_and_repair` returns the input text unchanged plus a `llm-repair-failed`
entry. It must never raise into the loop and never return text the gates rejected.
"Status quo" means the TEXT path is untouched; carrying the `llm-repair-failed`
lesson entry (which suppresses raw code echo via the truthy-report gate, by design)
is intentional — a lesson is not a failure mode. The gate's honest wording in the
Implementation section: echo is suppressed only when an entry exists.
The verdict on ALL such returns is `"ambiguous"` (status quo, still-unrepaired text);
telemetry `outcome` distinguishes the cause. Verdict-to-outcome mapping is fixed:
"ok"->applied, "clean"->all-prose, "ambiguous"->give-up|call-error|too-large.

### REQ-LFS-006: Segmenter call is fully context-free
`segment_and_repair` issues its own LLM request containing exactly two messages:
the static system prompt (REQ-LFS-008) and one user message (REQ-LFS-009). It must
not read `agent.context`, must not append to it, and must use no tools. Request
params: the session main model; `temperature=0`; `chat_template_kwargs` set exactly
to `{"enable_thinking": False}` — never inherited from group kwargs, which set
enable_thinking=true (tau.json, all groups) — and `max_tokens` from `DEFAULT_MAX_TOKENS`.
Precedent for this kwarg: agent_llm_invoke flips enable_thinking per attempt under
retry pressure; the client (agent_llm_client) forwards chat_template_kwargs verbatim.
Transport kwargs/timeout/retry semantics follow REQ-LFS-021 (timeout is
client-level in this stack — there is NO per-call 30s knob; the fixer inherits the
group timeout and a stalled call is a transport failure -> `call-error` give-up).

### REQ-LFS-007: Retry policy with changed input
At most 4 fixer calls per invocation: 1 initial + 3 retries (`DEFAULT_MAX_RETRIES=3`).
A retry follows only a gate failure (G1/G2/G3 or malformed answer), and its user
message appends `Previous attempt failed: <gate message>. Retry.` — the input change
is what makes the next T=0 attempt differ; blind retries are forbidden. Transport
errors are not retried: they go straight to REQ-LFS-005 give-up. Recorded cost
bound, stated honestly (a chronic pathologizing model with no session cap): up to 4
small calls per ambiguous turn for the whole session — transport-failure turns cost
1 call up to the GROUP timeout (tau.json groups: 600-1800s; client-level, see
REQ-LFS-021), gate-failure turns up to 4 calls x DEFAULT_MAX_TOKENS. No streak or loop
detector bounds this by construction: give-up text usually still contains executable
code, so no-code streak never fires and loop_detector needs verbatim repetition.
This is accepted per REQ-LFS-019 (no session cap); telemetry makes it visible.

### REQ-LFS-008: Static system prompt
Module-level template built per style (cached). Contents: task statement (classify
lines into non-overlapping ascending half-open ranges typed prose|code|drop that tile
the input exactly); the ACTIVE fence tokens rendered as quoted reprs from
`fence_tokens(style)`; rules — code ranges need `lang`; lang=bash only when a
dropped fence line in that range is SH-style or the original protocol tag named a
bash tool, else python; drop ranges ONLY for fence/tag-corrupted lines; keep prose
content; output ONLY the JSON object, no prose, no fences. No active fence token may
appear at column 0 in the prompt itself (tokens only inside quoted reprs). The
template carries a `PROMPT_VERSION: str` constant; telemetry records it.

### REQ-LFS-009: Payload schema and injection containment
User message wraps the raw text between per-call random delimiters:
`<<<SEGMENT-BEGIN-<hex8>>>` and `<<<SEGMENT-END-<hex8>>>`; content lines are
`text.split(chr(10))` after CRLF normalization (same normalization as `_repair_core`),
INCLUDING a trailing empty line if present, so index counting matches `_repair_core`
exactly and EOF off-by-one is impossible (0-based; content line 0 = line right
after BEGIN). Expected
answer: `{"segments": [{"type": "prose|code|drop", "from": <int>, "to": <int>, "lang": "python|bash"}]}`
with `lang` present iff type==code, ranges half-open and ascending. If the payload
contains a line identical to either delimiter, regenerate the nonce and re-wrap;
regeneration is bounded at 5 attempts, then give up as `call-error` (collision rule). Malformed JSON,
unknown keys, or missing lang -> malformed-answer gate failure with the reason.

### REQ-LFS-010: Gate G1 — exact partition
`gate_partition(segments, n) -> (ok, msg)`: every segment has `0 <= from < to <= n`;
segments sorted ascending by from; no overlap; union covers every index in [0,n).
Failure message names the first offending index and kind (`gap at line k`,
`overlap at line k`, `range out of bounds ...`).

### REQ-LFS-011: Gate G2 — drop legality
Every index in a drop range must be fence-like per REQ-LFS-003 predicates OR blank.
First illegal index -> failure (`illegal drop at line k: <preview>`). Asymmetric
CODE-INTERNAL DROPS (accepted, made loud): a drop segment BETWEEN two code segments
(both neighbours code) deletes a col-0 fence token from what may have been one broken
mega-block — the same ambiguous pattern the ladder refuses (unbalanced-block). Tier-2
may make that call, but never silently: count it as `dropped_inside_code` in telemetry
and set `boundary_uncertain` in the banner when it happens. This keeps the Purpose
promise honest: no silent loss, every deletion explainable by line index. Companion
rule, ASYMMETRIC ON PURPOSE: a line that is fence-like AT COLUMN 0
(predicates satisfied with no leading whitespace, matching `_opener_of` strictness)
is ILLEGAL inside a prose range — col-0 lone fence/protocol-tag lines MUST be typed
drop. INDENTED tag-shaped lines are NOT forced to drop: `_env_open`/`_env_close`
accept leading whitespace while openers do not, so an indented tag-shaped line is
legitimate prose (e.g. a docstring example) and forcing it to drop would be silent
content loss; such lines may stay prose or drop at the fixer's choice. This forces tier-1/tier-2 convergence on residual envelope tags. Zero code
ranges is not a G2 failure (REQ-LFS-016 defines its handling).

### REQ-LFS-012: Assembly and Gate G3 — re-scan proof
`assemble(segments, lines, style)`: prose -> original lines verbatim; drop ->
omitted; code -> active open token alone on a line, original lines verbatim, active
close token alone on a line; a blank line separates a block from adjacent prose or a
neighboring block (inserted blank lines are the ONLY deviation from original-bytes-only
assembly and are content-free). Gate G3 receives the assembled text and runs
`_repair_core(assembled, style)` — the PURE ladder,
never `repair_response` (recursion ban, REQ-LFS-019) — and requires verdict != 
Before the proof, run `strip_envelopes(assembled, style)` (the module's existing
proven envelope pass) on the assembled text — tier-1 and tier-2 then converge on any
residual lone-tag lines, and G3's proof sees the same text the loop would store
(belt-and-suspenders: with REQ-LFS-011's drop obligation the assembler leaves no
adjacent lone tags, so this pass normally finds nothing — kept as a tripwire).
Then: verdict != `"ambiguous"`, AT LEAST ONE accepted block, and accepted block bodies byte-identical
(the proof scans the assembled text, takes the ACCEPTED blocks in order, and compares each block body to the ORIGINAL lines[lo:hi] of the matching code segment (independent of the assembler's own bookkeeping), and compares them
byte-for-byte against the original lines of each code range), in order (with zero code ranges both the body test and
the verdict test would be vacuously satisfied — the block-count test is what makes
REQ-LFS-016's zero-code-with-drops claim true). Failure names the first mismatch. With a correct assembler this fires only
on assembler bugs — it is a tripwire, not a model-error gate.

### REQ-LFS-013: Gate G4 — boundary sanity (flag, never retry)
For each code range with lang python: `ast.parse(chr(10).join(range_lines))`. On
SyntaxError, look at the immediately following prose segment's first lines for
continuation hints (greater indentation than the block's minimum, or first token in
{except, else, finally, elif} or a bare closing bracket) -> append
`boundary uncertain at line k` to the entry and telemetry. NEVER triggers a retry.
Bash ranges: no parse check.
A boundary cut that leaves the remaining code syntactically valid (e.g. a trailing
`answer['ready']` assignment reassigned to prose) passes `ast` cleanly and CANNOT be
flagged by G4; the consequence is only that the turn does not end and the loop
re-teaches. This is accepted — G4 is never upgraded into guessing.

### REQ-LFS-014: Acceptance path
G1+G2+G3 pass -> return `(assembled, entries, "ok", telemetry)` where entries have
step `llm-repair-applied` summarizing code ranges (line spans + lang), drop count,
and any G4 flags. The caller substitutes `assembled` for `response_text` BEFORE
`append_assistant`, so the broken reply never enters context; re-repair of assembled
is a ladder-certified no-op (idempotence).

### REQ-LFS-015: Two-audience reporting
User banner (existing separated yellow _repair_notice channel): AMBIGUOUS REPLY
REPAIRED by segmenter, one line per block (span, lang), dropped-fence-line count,
plus `boundary uncertain` note when set. Model-facing note: one terse sentence
(fencing was ambiguous; repaired mechanically from your own lines; fence tokens must
sit alone at column 0). The range table must not enter model context.
`agent_fence_repair.STEP_MESSAGES` must gain BOTH new keys `llm-repair-applied` and
`llm-repair-failed` with single-line, terse model-facing text, since `Entry.message`
resolves through `STEP_MESSAGES` — without these entries the terse lesson line
silently disappears.

### REQ-LFS-016: All-prose answer terminates immediately
Segments with zero code AND zero drop ranges: accept as-is, return `(text, [],
"clean", {outcome: all-prose})` — no retry, no change (no-code feedback path
handles the turn per fence-pipeline-v2). After all-prose returns verdict `clean`,
the loop proceeds with the unchanged text; no code blocks are extracted, so the
existing no-code feedback path and streak counters (fence-pipeline-v2) apply
unchanged — tier-2 never suppresses no-code feedback. Zero code ranges WITH drops
present fails G3 (proof finds no blocks) and follows the retry path.

### REQ-LFS-017: Telemetry every invocation
Telemetry is written by `segment_and_repair` ITSELF via `telemetry_writer(dict) -> None`
(default: a module-level writer using `agent_session.LOG_DIR` — there is no
`file_paths` module; tests inject a recorder). One JSONL line per call appended to
`fence_segmenter.jsonl` in the audit/log directory: ts, verdict_in, outcome
(`applied|all-prose|give-up|call-error|too-large`), retries_used, gate-failure strings per
attempt, dropped_count, dropped_inside_code count, boundary_uncertain list, prompt_version, model, in/out tokens when the
transport exposes them, plus sha256 of the pre-repair input and first/last 200-char
snippets (the accepted path REMOVES the broken text from context, so telemetry is the
only surviving forensic record of what actually went wrong).
transport exposes them. Recorded on success too — gate-failure histograms of
eventually-successful runs are the feedstock for future deterministic ladder stages.
Telemetry write failures are swallowed (REQ-LFS-005 priority).

### REQ-LFS-018: Operates on post-ladder text
Input is exactly the string the loop would append (after glue split, ladder,
envelope strip). Line array = input after CRLF normalization, split on newline,
matching `_repair_core` normalization so line indices are shared across modules.

### REQ-LFS-019: Hard non-goals
No LLM call inside agent_fence_repair; ladder, std-gating, counter semantics, and
`_renders_extractable` unchanged; `extract_code_blocks` stays strict; all gates
binary (no fuzzy or percentage matching anywhere); no per-session invocation cap;
thinking stays off for the fixer; no semantic fixes — broken semantics stay the
REPL's lesson; no v1 result caching.

### REQ-LFS-020: Size guard on tier-2 input
If the input text exceeds MAX_LINES (default 400) or MAX_BYTES (default 60000),
`segment_and_repair` must NOT call the fixer; it returns the input unchanged with a
`llm-repair-failed` entry and telemetry outcome `"too-large"`. Tier-2 newly introduces
prompt-size/latency blowup risk that the pure-CPU ladder does not have; a huge reply
is better left to status quo + lesson feedback.

### REQ-LFS-021: invoke_fn transport contract (repo-true wiring)
`segment_and_repair` calls `invoke_fn(messages, kwargs)`. Contract: returns
`(text: str, ok: bool)` where `ok=False` covers transport/circuit/timeout/provider
failure. TRUTH (verified): `_invoke_llm_with_retry` RAISES on failure (exhausted
retries raise the last error; no `success=False` return path exists) — the adapter in
`agent_pipeline` wraps it in try/except and converts ANY exception to
`('', False)`; `segment_and_repair` must never see an exception escape `invoke_fn`.
The adapter passes `min_response_bytes=0` in `LLMCallConfig` (default 10 can reject a
terse JSON answer and, with `max_retries=0`, turn it into a false call-error).
Signature verified: `_invoke_llm_with_retry(client, model_name, messages, stream=False,
config=None) -> tuple[LLMResponse, list | None]`. Production wiring (new helper in
`agent_pipeline.py`): `build_segmenter_invoke_fn(client, model_name, extra_kwargs)`
delegating to `agent_llm_invoke._invoke_llm_with_retry(client, model_name, messages,
`stream=False`, `LLMCallConfig(extra_kwargs=kwargs, max_retries=0))` — the
transport's own retry loop is DISABLED (`max_retries=0`); retries are owned by
REQ-LFS-007. `kwargs` is a FRESH dict per call, never merged with group params:
`{temperature: 0, max_tokens: DEFAULT_MAX_TOKENS, chat_template_kwargs:
{enable_thinking: False}}` — kwargs REPLACED wholesale, not updated; dropping
`preserve_thinking` is intentional (a two-message classification call has no
reasoning to preserve). `max_tokens` MUST ride inside `kwargs` (it is an
`OPENAI_BODY_PARAMS` pass-through); `LLMCallConfig.max_output_tokens` is NOT the
fixer's knob. TIMEOUT TRUTH (replaces the timeout sentence in REQ-LFS-006): this
stack bakes timeout at client construction (`agent_llm_client`, `timeout: int = 300`)
— there is no per-call override; the fixer therefore inherits the GROUP timeout
(tau.json: 600-1800s), and REQ-007's cost bound uses that honest number, flagged
in telemetry. Import site: `agent_fence_segmenter` imports ladder helpers lazily
(function-local) to avoid an import cycle.
## Interface
agent_fence_repair (additive only):
- `fence_like(text: str) -> bool`
- `RepairReport.verdict: str` — one of "clean" | "ok" | "ambiguous" (computed by
  `_repair_core` at every return path; no separate public helper needed — REQ-LFS-002
  puts it inside the core where scan tuples are in hand; REQ-LFS-012's G3 gets the
  verdict as a `.verdict` read off the `_repair_core` return report)
- `repair_response(text, active_style)` signature unchanged

agent_fence_segmenter (implemented module: src/agent_fence_segmenter.py):
- `DEFAULT_MAX_TOKENS: int = 4096`
- `DEFAULT_MAX_RETRIES: int = 3`
(no timeout constant — transport timeout is client-level, REQ-LFS-021)
- `MAX_LINES: int = 400`
- `MAX_BYTES: int = 60000`
- `build_system_prompt(active_style: str) -> str`
- `build_user_message(text: str, nonce: str) -> str`
- `parse_fixer_answer(raw: str, n_lines: int) -> tuple[list[dict] | None, str]`
  (segments, reason; `segments=None` on malformed input, reason non-empty — REQ-LFS-007
  embeds it in the retry prompt)
- `gate_partition(segments, n_lines) -> tuple[bool, str]`
- `gate_drop_legality(segments, lines, active_style) -> tuple[bool, str]`
- `gate_bash_witness(segments, lines, active_style) -> tuple[bool, str]`
  (hard REQ-LFS-011 sub-gate: a `lang=bash` code segment is legal only if the INPUT
  contains an active-style SH fence token — the ladder-parity rule that stops a
  garbled reply / injected instruction from fabricating shell execution)
- `assemble(segments, lines, active_style) -> tuple[str, list[tuple[int, int, list]]]`
  (assembled text + per-code-segment spans as (start,end,expected_body); retained for
  telemetry/assembly introspection — the PROOF no longer trusts these, see below)
- `gate_assembly_proof(assembled: str, segments, lines, active_style) -> tuple[bool, str]`
  (caller assembles once via `assemble()`; the gate re-scans `assembled`, takes the
  accepted blocks in order, and compares each body byte-for-byte against the ORIGINAL
  `lines[lo:hi]` of the matching code segment — NOT against `assemble`'s own bookkeeping,
  so an assembler off-by-one that silently drops a body line (e.g. `answer['ready']`)
  fails here; also runs the pure ladder `_repair_core` for a final non-ambiguous verdict)
- `boundary_sanity(segments, lines) -> list[str]`
- `segment_and_repair(text, active_style, *, invoke_fn, model, max_retries=3, telemetry_writer=None) -> tuple[str, list, str, dict]`
  Returns (text, entries, verdict, telemetry). `invoke_fn(messages, kwargs) ->
  tuple[str, bool]` (ok=False on transport/provider failure) is the single injection
  seam. Production: `agent_pipeline.build_segmenter_invoke_fn(client, model_name,
  extra_kwargs)` -> `_invoke_llm_with_retry(..., stream=False, LLMCallConfig(
  extra_kwargs=kwargs, max_retries=0))` (REQ-LFS-021).

agent_loop (main path, ~5 lines): on `report.verdict == "ambiguous"` call
`segment_and_repair(text, style, invoke_fn=..., model=...)`. ALWAYS extend the report
with the returned entries (on "ok" those are `llm-repair-applied`, on give-up
`llm-repair-failed` — the failure lesson must reach the model too); substitute
`response_text` ONLY when verdict is "ok". Both happen BEFORE the single
`_repair_notice` call so exactly one banner renders (no double-banner). Note the
existing code-display gate is keyed on a truthy repair report, so tier-2 entries
suppress raw echo — intended (the banner covers it). `validate_eot`, loop_detector,
and no-code feedback all then act on the substituted text, unchanged code paths.

## Behavior
Normal: ambiguous text -> one call -> gates pass -> assembled text in context,
banner for the user, one terse lesson line for the model. Gate failure on attempt k
retries with that failure text (k<=4); telemetry records every gate message.
Persistent failure: status quo text, `llm-repair-failed` entry, ordinary lesson
feedback proceeds. Edge cases: empty input -> clean, no call; prose-only -> clean;
fence-looking prose reply -> ambiguous -> segmenter says all-prose -> clean ->
NO-CODE path; half-open tail with prose after -> G1-G3 pass, G4 may flag; glued
openers never reach tier-2 (deterministic pre-pass owns them); truncated replies
never reach tier-2.

## Constraints
- Source derives fence tokens at runtime (fence_tokens / chr), no literal tokens at
  line start — same discipline as agent_fence_repair.py (fence-pipeline-v2).
- Zero network in tests: everything above `invoke_fn` is pure.
- Gate/telemetry strings always carry exact 0-based line indices.
- Fixer kwargs dict built fresh per call; never merged with group kwargs.
- Scrub-vs-teach trade-off, recorded deliberately: replacing the reply before context
  means the model never re-sees its own broken fencing (ladder commits already work
  this way; tier-2 extends it). The terse STEP_MESSAGES line is the teaching
  substitute; the trade is accepted because a clean context prevents error-mirroring,
  and telemetry preserves the original for humans.

## References
- specs/fence-pipeline-v2.md — the deterministic ladder this tier plugs into (dep-side
  gap: that older spec predates frontmatter conventions; retrofit tracked separately)
- src/agent_repl_parse.py — shared predicates, scan_style, _ANY_FENCE_RE
- audit forensics 2026-09-12..09 — P1/P1b pattern catalog motivating tier-2

## REQ-LFS-022 — Repair-hardening amendments (2026-09-12, live e2e + fuzz findings)

- **022a** Auto-close certification for active-style python requires a clean parse AND no
  bare-Name/Constant statement line in the body ("after"/"Done" parse as no-op statements;
  they must never execute as auto-closed code). The same foreign-fence veto (`mixed`) that the
  fast path applies MUST also veto `verdict=ok` on every committed repair path.
- **022b** Ladder glue splitting also covers bare glue: `<indent>@PY <code>` on one line is
  split ONLY when the opener is inside an accepted block in the final text (honest-gate).
- **022c** `segment_and_repair` runs `normalize_segments` BEFORE the gates: gaps whose lines
  are all blank/fence-like become implicit drops; an active fence line inside a code segment
  splits it into code/drop/code. Only fence-like lines are ever dropped. Telemetry counts
  `norm_gap_fills` / `norm_fence_splits`. Gate give-up returns must carry a forensic lesson
  (dominant gate failures + offending lines) — a silent give-up is a spec violation.
- **022d** Tier-1 must NOT convert std fences under a non-std style, even when std-only:
  code-vs-prose intent is semantic (fenced examples in prose). It returns `ambiguous` with a
  lesson; tier-2 recovers the genuinely-code case with tolerances.
- Tests: src/tests/test_fence_repair_hardening.py incl. seeded mutation fuzz pinning the two
  universal invariants (never raise; verdict=ok => extracted bodies byte-equal originals).
