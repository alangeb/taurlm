---
name: testing
description: 'General testing workflow - write/run/interpret tests (pytest, TDD, coverage, regression), order gates by cost, gather evidence, reproduce bugs - PLUS project-specific gates for THIS repo (TauRLM: pytest suite, sanity.sh blackbox, manual ./tau.py). Use for any change verification, bug reproduction, or proving a behavior works. Keywords: test, pytest, unit test, integration, coverage, fixture, mock, TDD, assert, parametrize, regression, reproduce bug, blackbox, e2e, sanity.sh, tau.py, manual test, evidence, timeout, flaky.'
category: development
keywords: 'test, pytest, unit test, integration, coverage, fixture, mock, TDD, assert, parametrize, regression, reproduce bug, blackbox, e2e, sanity.sh, tau.py, manual test, evidence, timeout, flaky'
---

# Testing

General discipline for testing software. It applies anywhere; the **Project-specific:
TauRLM** section at the bottom names the exact commands, files, and gotchas for THIS repo.

## When to use
- Any change that needs verification; bug reproduction; proving a behavior exists.

## Core principles (any project)
1. **Order gates by cost: unit -> integration/e2e -> manual/live.** Cheapest first; a red
   cheap gate means stop before spending on an expensive one.
2. **Evidence, not claims.** "It should work" is not a result. Capture a passing test or a
   log snippet that literally contains the output the feature must emit.
3. **Reproduce smallest-first.** The first artifact is the smallest deterministic failing
   test. If you cannot write one, you do not yet understand the bug - gather more data.
4. **Keep the failing test as a regression test.** A fix without a once-failing test is a
   bug waiting to return.
5. **Deterministic where you can, forced where you cannot.** Live layers (network, LLMs,
   clocks, concurrency) are stochastic. If a scenario does not trigger the path, that is a
   scenario problem, not a code problem - make the input force it (exact payload, forbid the
   shortcut).
6. **Long gates run detached.** Anything that can exceed your execution timeout (builds, e2e,
   live-model suites) goes in the background with a sentinel file you poll; never block the
   foreground on it.
7. **Code changes need a fresh process.** A running interpreter keeps old modules in memory;
   restart to pick up edits. Do not "resume" a session when validating new code.

## Coverage (pytest-cov IS installed here)

```python
import subprocess
r = subprocess.run(["python","-m","pytest","src/tests","-x","-q","--tb=short",
                    "--cov=rlm","--cov-report=term-missing"],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
print(r.stdout[-3000:])
```

## Bug -> regression recipe (general)
1. Smallest deterministic failing test that reproduces it.
2. For anything on an async/live feedback path, add a manual reproduction and save its log -
   a bug you cannot see in a log is a bug you cannot verify as fixed.
3. Fix, then re-run in cost order (unit -> full suite -> e2e -> manual). All green, or it is
   not closed. Keep the failing test as the regression test.

## Reading evidence from logs (general)
Strip ANSI color, grep for the exact string the feature emits, and quote the surrounding
snippet as proof. Prefer durable artifacts (audit/trace/replay files) over terminal scrollback.
```python
import re, pathlib
c = re.sub(r"\x1b\[[0-9;]*m", "", pathlib.Path(logfile).read_text())
for pat in ("...",):                 # exact strings the feature must emit
    print(pat, c.count(pat))
i = c.find(pat); print(c[max(0,i-600):i+900])   # quote this as evidence
```

---

# Project-specific: TauRLM (this repo)

Three gates, cost order: **py tests -> sanity.sh -> manual ./tau.py**. All three are
mandatory for kernel/loop/prompt changes; py tests alone are never sufficient because they
cannot see the live-LLM feedback path.

## Gate 1 - py tests (fast, no LLM)
```bash
cd ~/taurlm && PYTHONPATH=src python -m pytest src/tests -q     # ~30s, baseline ~1139 passed
PYTHONPATH=src python -m pytest src/tests/rlm/test_kernel.py -q  # single file
PYTHONPATH=src python -m pytest src/tests/rlm/test_kernel.py::TestX::test_y -q
```
- Tests live in `src/tests/` (top level) and `src/tests/rlm/` (kernel/loop/agent);
  `PYTHONPATH=src` is belt-and-braces (src/pytest.ini already sets `pythonpath = .`). More flags/mechanics: `test-runner`.
- **Never pass `--timeout`** (no pytest-timeout plugin - see `test-runner`).
- A run over ~120s means something is hanging, not slow: kill it and bisect.

## Gate 2 - sanity.sh (gold standard, live LLM)
`src/sanity.sh`: 9 **blackbox** tests (Test 1-9), each a single real `./tau.py` invocation against
the live model - the only gate between broken code and production. **Run it detached (tmux + a
`.done` sentinel); it takes minutes and WILL blow the block timeout.** Exact invocation, ANSI-strip
verdict (`Failed: 0` + `EXIT=0`), the NEVER-edit rule and the audit path: see **sanity**.
- Disposition rule: a failure means the code is wrong; "the model was flaky" / "pre-existing" are
  not valid dispositions. Fix the source, re-run.
- Gate 2 audits land in `~/.local/tau/logtest/*.audit` (sanity.sh:79 exports `TAU_LOG_DIR`), NOT
  the normal `~/.local/taurlm/log` - a common wrong-grep.

## Gate 3 - manual ./tau.py (behavioral evidence)
Run from `src/`. All three input modes are useful:
```bash
./tau.py "single prompt"                      # one turn, exits
./tau.py "prompt 1" "prompt 2" "prompt 3"     # multiprompts (nargs="*"), ONE shared session
echo "prompt" | ./tau.py                      # stdin pipe (multi-line prompts)
./tau.py --llm cuda "prompt"                  # pick an LLM group from config
./tau.py -c                                   # resume last session (NOT for validating new code)
```
Multiprompts are the right tool when behavior spans turns (error -> recovery, `_output{N}`
reuse in a later block). Same detached/tmux rule as sanity.sh (live LLM, exceeds 180s):
```bash
cd ~/taurlm/src && rm -f /tmp/tau1.log /tmp/tau1.done
tmux kill-session -t tau1 2>/dev/null
tmux new-session -d -s tau1 'cd ~/taurlm/src && timeout 700 ./tau.py "P1" "P2" > /tmp/tau1.log 2>&1; echo EXIT=$? > /tmp/tau1.done'
```
Evidence is in the console log: `[REPL ERROR]` / `[REPL OUTPUT]` / `[SYNTHETIC USER:
repl_feedback]` show exactly what the model was sent - strip color (general snippet above),
grep for the emitted string, quote it. Durable artifacts: `~/.local/taurlm/log/<pid>_<ts>_<turn>.audit`
(full prompt/response) and `.context` (replayable session); `tau.py` prints both paths in its
EXIT SUMMARY. If a prompt embeds a code block, keep one statement per line - a collapsed
one-liner changes which line the error lands on and invalidates line-number assertions.

## Related Skills
- `test-runner` - generic pytest mechanics
- `sanity` - running sanity.sh (the tmux rule is not optional)
- `debug` - root-cause workflow
- `code-analysis` - understand what to test via call graph
- `refactor` - tests are the safety net for refactoring
- `delegation` - spawn a worker to run the full suite in parallel
- `verification-discipline` — never trust a worker test count; re-run + prove pre-existing
- `session-lifecycle` — .context/.audit sinks, exit/save paths for session-level bugs
