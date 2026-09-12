---
name: sanity
description: 'Run src/sanity.sh - the gold-standard live-LLM blackbox gate (9 tau.py invocations). tmux-detached execution, NEVER-edit rule, log/audit paths, failure protocol. Use for pre-commit verification of kernel/loop/prompt code, or when live behavior must be proven. Keywords: sanity, sanity.sh, blackbox, gold standard, live LLM, gate, smoke test, verify, e2e.'
category: development
keywords: 'sanity, sanity.sh, blackbox, gold standard, live LLM, gate, smoke test, verify, e2e'
---

# sanity.sh — The Gold Standard Gate

9 blackbox tests (Test 1-9), each a single real `./tau.py` invocation against the live model. **NEVER edit sanity.sh** — no prompts, expectations, thresholds (sanity.sh:17 "NEVER modify tests"). "Pre-existing" / "model was flaky" are not valid dispositions: a failure means the code is wrong; fix the source and re-run (sanity.sh:8-26). Gate ordering: see **testing**.

## Run it detached — it WILL exceed the 180s block timeout
```bash
cd ~/taurlm/src && rm -f /tmp/sanity.log /tmp/sanity.done
tmux new-session -d -s sanity 'cd ~/taurlm/src && bash sanity.sh > /tmp/sanity.log 2>&1; echo EXIT=$? > /tmp/sanity.done'
[ -f /tmp/sanity.done ] && cat /tmp/sanity.done    # poll; keep each poll block under 180s
```
Verdict: strip ANSI (`re.sub(r"\x1b\[[0-9;]*m", "", log)`), expect `Failed: 0` and `EXIT=0`. `Passed:` counts assertions (~14-18), not tests.

## Where the evidence lives
- `SANITY_LOG` (default `/tmp/sanity_complete.log`), `SANITY_AGENT_LOG` (default `/tmp/sanity_agent.log`) — overridable env vars (sanity.sh:83-84).
- Per-test audit files: `~/.local/tau/logtest/*.audit` — sanity.sh exports `TAU_LOG_DIR=~/.local/tau/logtest` (sanity.sh:79), overriding the normal `~/.local/taurlm/log` default (agent_session.py:41-42). Exact prompt/response of a failing test lives there.
- Reproduce a failing test by hand-running its single `./tau.py` invocation (also detached).

## Gotchas
- sanity.sh runs from its own dir (SCRIPT_DIR cd) and exercises `tau.py` via a `tau-sanity.py` DUT wrapper; its cleanup kills only `tau-sanity.py` leftovers — never kill `tau.py` processes globally (you'd kill other live sessions).
- Takes several minutes per run; budget your polls.

## Related Skills
- `testing` — 3-gate ordering (py tests -> sanity -> manual)
- `debug` — root-causing a failing test
- `test-runner` — the cheap py-test gate
