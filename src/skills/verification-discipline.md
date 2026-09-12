---
name: verification-discipline
description: 'Independent verification of work you did not personally execute: never trust a spawned worker self-reported test counts; re-run the suite yourself; prove a failure is pre-existing by reverting your files at HEAD; avoid grep false-positives (event line-form vs your own logged commands); avoid different-sink confusion (.context /ctx sum vs .audit typed records); inspect the real files instead of reasoning to a claim; correct yourself loudly when evidence contradicts. Use before accepting any test count, "done" claim, bug attribution, or grep result, especially after delegation. Keywords: verify, verification, trust, worker claim, test count, re-run, pre-existing, revert, HEAD, false positive, grep, sink, .audit, .context, evidence, self-correct.'
category: development
keywords: 'verify, verification, trust, worker claim, test count, re-run, pre-existing, revert, HEAD, false positive, grep, sink, .audit, .context, evidence, self-correct'
---

# Verification Discipline

A claim is not a result. Verify independently before you build on it. Applies
most when work was delegated or results arrived second-hand.

## Never trust a self-reported test count
A spawned worker reporting "N passed, 0 failed" (or "pre-existing failures,
unrelated to me") is a CLAIM about a run you did not see. Re-run the suite
YOURSELF in your own process and read the actual counts + failing test ids.
Diff your number against the claim; a mismatch is the signal, not the text.

## Prove "pre-existing" by reverting, not by asserting it
To show a failure pre-dates your change, revert YOUR files to HEAD
(`git stash push -- <files>` or `git checkout -- <files>` after you have a copy)
and run the SAME failing test. Red at HEAD = pre-existing. Still red only after
restore = yours. Do NOT attribute by reading the diff and reasoning "this cannot
affect that" — that is a guess, not evidence.

## Grep false-positives: match the line form, not the bare token
Searching a log/audit for a token (an event type, a command name, an error word)
also matches YOUR OWN prior grep/commands recorded as message CONTENT. Match the
full typed line shape (e.g. `] EVENT stack=` in `.audit`; see **session-lifecycle**),
or grep with a pattern anchored to the record format. Count only real records.

## Different-sink confusion
The same fact can live in two places that AGREE only when both were written.
Example: a turn summary appears as `.context` metadata (read by `/ctx sum`) AND as
a `.audit` typed record — separate sinks, one can silently no-op while the other
fills. Confirm you are reading the sink you THINK you are. Two sinks agreeing is
weaker evidence than two INDEPENDENT methods agreeing.

## Read the real file; do not reason to a claim
Do not assert a line number, signature, config key, or path from memory or from a
diff summary — open the file at the claimed line and confirm. Verify a symbol
exists with an AST check (see **rlm-analysis**), not a grep that matches comments.

## Correct yourself loudly
When evidence contradicts something you already stated, say so explicitly and
prominently at the top of the next report — "my earlier claim X was wrong;
evidence Y". Silent correction leaves a wrong claim standing downstream.

## Related Skills
- `spawn` / `delegation` — manager pattern; verification step after any worker.
- `testing` — re-run the suite, evidence over claims, reproduce smallest-first.
- `code-review` — scoped, evidence-ranked review of diffs.
- `debug` — reproduce-isolate-fix-verify; live-session audit evidence.
