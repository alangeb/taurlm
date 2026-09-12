---
name: test-runner
description: 'Pytest mechanics for this repo: exact invocation, output folding, no-pytest-timeout rule, fast iteration flags, coverage. Use for running test suites/tests, rerunning failures, interpreting results. Keywords: pytest, test runner, run tests, test suite, -q, --tb, --lf, coverage, -k, -x, timeout, PYTHONPATH.'
category: development
keywords: 'pytest, test runner, run tests, test suite, -q, --tb, --lf, coverage, -k, -x, timeout, PYTHONPATH'
---

# Pytest Mechanics (TauRLM)

Workflow, gate ordering, and sanity.sh live in **testing** — this file is invocation mechanics only.

- **Full suite:** `cd <repo> && PYTHONPATH=src python -m pytest src/tests -q`. Tests live in `src/tests/` (+ `src/tests/rlm/`). `PYTHONPATH=src` is belt-and-braces, NOT strictly required: `src/pytest.ini` sets `pythonpath = .` and rootdir is `src`, so the suite collects+passes with PYTHONPATH stripped (verified).
- **NEVER pass `--timeout`** — pytest-timeout is NOT installed; argparse dies and you will misread the usage error as a test failure. Use the shell `timeout` binary instead.
- **Fold output** — raw suite output blows your context. In the REPL:

```python
import subprocess
r = subprocess.run(["python","-m","pytest","src/tests","-q","--tb=short"],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
print(r.stdout[-2000:])          # tail only; full text stays in r.stdout
```

- Fast loop: `-x -q --tb=short` → fix → `--lf` (last-failed only) → broaden. Filter: `-k expr` or node id `src/tests/rlm/test_kernel.py::TestX::test_y`.
- Coverage: pytest-cov IS installed — `--cov=rlm --cov-report=term-missing`.
- `stdin=subprocess.DEVNULL` on every subprocess call (terminal-inheritance deadlock footgun).

## Related Skills
- `testing` — gates/workflow/evidence (project section)
- `sanity` — live-LLM blackbox gate (tmux rule)
- `debug` — diagnosing failures
- `environment` — project config, .venv interpreter, TAU_ env vars.
