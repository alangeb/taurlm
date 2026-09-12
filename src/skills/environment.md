---
name: environment
description: 'Project environment facts: config in src/tau.json (llm_groups + rlm sections), TAU_ env-var overrides, TAU_LOG_DIR, the .venv interpreter + the no-global-install rule, pyproject/deps. Use for config changes, pip/venv/install questions, "where is the config". Keywords: config, configuration, tau.json, llm_groups, env, environment variable, TAU_, TAU_LOG_DIR, venv, .venv, virtualenv, pip, pip install, install, requirements, pyproject, python version, setup, docs/config-schema.'
category: system
keywords: 'config, configuration, tau.json, llm_groups, env, environment variable, TAU_, TAU_LOG_DIR, venv, .venv, virtualenv, pip, pip install, install, requirements, pyproject, python version, setup, docs/config-schema'
---

# environment

Config + interpreter facts. NOT general subprocess usage (see `shell`), NOT model
deployment tuning (see `model-serving`).

## Config = `src/tau.json`
- LLM endpoints/models under **`llm_groups`** (model, api_base, params); RLM knobs under
  **`rlm`** (max turns, REPL behavior); **`loop_detection`** for repetition guards.
  `agent_init.py` raises during resolution if `llm_groups` is empty.
- Any key overridable by env var, prefix **`TAU_`**. Full ref: `docs/config-schema.md`.

## Interpreter / venv — hard rule (guardrail-enforced)
- NEVER global install. No bare `pip install`, no `npm install -g`, no `apt install`
  unless user explicitly authorizes it this session.
- Run with the project venv interpreter directly: `.venv/bin/python ...`
  (dominant pattern in audit logs). Activate only when interactive.
- `pip install -e .` for the package; `--break-system-packages` / `--no-deps` appear in
  this repo's logs but are **escalations** — confirm with user before reaching for them.
- Deps: `pyproject.toml` `requires-python >= 3.13`, runtime dep `numpy`; `[dev]` = pytest.
  ⚠ README says "Python 3.10+" — pyproject (`>=3.13`) is authoritative; README is stale.

## Audit/state log dir
- `LOG_DIR = $TAU_LOG_DIR` else `~/.local/taurlm/log`
  (`agent_session.py:42-43`; mirrored in `rlm/skill_telemetry.py:27`).
- Files: `<pid>_<timestamp>_<turn>.audit|.context|.plan`. See `session-lifecycle`.
- ⚠ `agent_model_health.py:149` reads `TAU_LOG_DIR` with fallback `~/.local/tau/log`
  (missing `rlm`) — inconsistent default. Don't trust it as the canonical path.

## Related Skills
- `shell` — subprocess/tmux execution patterns.
- `test-runner` — exact pytest invocation (PYTHONPATH).
- `model-serving` — model/env deployment specifics.
- `docker` — containerized env.
