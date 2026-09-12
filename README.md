# TauRLM — Recursive Language Model Agent

[📄 License](LICENSE) · [📋 Disclaimer](DISCLAIMER.md) · [🔒 Security](SECURITY.md)

## ⚠️ Warning

This project is an **experimental AI agent** capable of generating and executing code.
It may produce incorrect, unsafe, or incomplete outputs. Always review all outputs
before use in production or sensitive environments. Run in a sandboxed environment.
Use at your own risk.

See [DISCLAIMER.md](DISCLAIMER.md) for full terms and [SECURITY.md](SECURITY.md) for safe usage guidelines.

---

## What Is TauRLM?

TauRLM is a **Recursive Language Model (RLM)** agent: the model works inside a
**persistent Python REPL** instead of calling separate tools. The main agent has zero
tools — everything (file I/O, shell, sub-agents, knowledge store) is plain Python code
in one long-lived kernel whose state persists across turns.

Completion is signaled through the kernel namespace (`answer["content"]`,
`answer["ready"] = True`), heavy work is folded into Python variables or delegated to
child agents via `spawn()`, and context is progressively compressed when it fills.

Key capabilities: persistent REPL, sub-agent spawning with work budgets, context
compression, a persistent wiki/knowledge store, a skills system, slash commands, shell
execution via fenced bash blocks, loop detection, and agent-to-agent (A2A) messaging.

---

## Quickstart

### Requirements

- **Python 3.10+** (stdlib only — no pip installs needed)
- An OpenAI-compatible API endpoint (e.g., vLLM, Ollama, llama.cpp)

### Install

```bash
git clone https://github.com/alangeb/taurlm.git
cd taurlm
```

Configure your LLM endpoint in `src/tau.json` (set `api_base`, `model`, etc. under
`llm_groups`). Environment variables override any key (prefix `TAU_`).

### Run

```bash
python src/tau.py                      # interactive mode
python src/tau.py "Hello, what can you do?"   # one-shot
python src/tau.py --continue           # continue previous session
python src/tau.py --continue-from FILE # continue from a specific context file
python src/tau.py --llm cuda "prompt"  # pick an LLM group
```

### Typical Workflows

```bash
python src/tau.py "Read agent_core.py and summarize its structure"
python src/tau.py "!ls -la"            # '!' prefix runs a shell command
python src/tau.py --keep-alive "Monitor PID 1234"   # stay up for A2A queries
python src/tau.py --pid 1234 "What's the status?"   # query a running agent
```

See [A2A_PROTOCOL.md](docs/designs/A2A_PROTOCOL.md) for the inter-agent communication contract.

---

## Kernel Namespace

When the RLM agent runs, the following functions and variables are pre-loaded in the Python REPL namespace:

| Name | Type | Description |
|------|------|-------------|
| `answer` | dict | Set `answer["content"]` and `answer["ready"] = True` to signal completion |
| `spawn()` | callable | Spawn child agents: `spawn("task description", name="worker")` |
| `host_request()` | callable | Make typed requests: `host_request("goal", "set", content="...")` |
| `wiki` | module | Persistent knowledge store: `wiki.add()`, `wiki.search()`, `wiki.retrieve()` |
| `available_skills()` | callable | List available skills |
| `find_skills()` | callable | Search skills by name, description, or keywords |
| `load_skill()` | callable | Load a skill: `load_skill("file-ops")` |
| `list_spawns()` | callable | List active spawns: `list_spawns()` or `list_spawns(all=True)` |
| `get_spawn()` | callable | Get spawn handle by name: `get_spawn("worker")` |

Plus all Python standard library modules (`os`, `sys`, `json`, `re`, `math`, `pathlib`, etc.) and all installed packages.

### Examples

```python
# Signal completion with an answer
answer["content"] = "The result is 42"
answer["ready"] = True

# Spawn a child agent
handle = spawn("Review the authentication flow", name="auth-reviewer")
print(handle.name, handle.status)

# Set a goal via host bridge
host_request("goal", "set", content="Complete the code review")

# Get current goal
result = host_request("goal", "get")
print(result.data)

# Set heartbeat interval
host_request("heartbeat", "set", interval_seconds=300)

# Send message to child agent
host_request("agent_message", "send", content="Check the regression test.", sender="me", receiver="auth-reviewer")

# Check for incoming messages
result = host_request("agent_message", "receive")
if result.success and result.data:
    for msg in result.data:
        print(msg["content"])

# List and load skills
skills = available_skills()
matches = find_skills("file-ops")
load_skill("file-ops")
```

## Commands

Slash commands are available inside the REPL:

| Command | Description |
|---------|-------------|
| `/goal` | Show, set, or update the current goal |
| `/agent` | List/manage active spawns (list, status, send, close) |
| `/heartbeat on\|off` | Toggle the idle heartbeat |
| `/autonomous` | Manage autonomous mode (start/stop/config) |
| `/refine <text>` | Refine the current answer |
| `/continue` | Continue from saved context |
| `/ctx` | Context management (compress, undo, push, pop) |
| `/wiki` | Knowledge store operations |
| `/skills [keyword]` | List or filter skills |
| `/llm` | Switch LLM group |
| `/status` | Show agent status |
| `/tweak` | Adjust runtime settings |

See [COMMANDS.md](docs/designs/COMMANDS.md) for the command system design.

---

## Configuration

`src/tau.json` controls LLM endpoints, timeouts, compression, and more. Key sections:

- **`llm_groups`** — named LLM configurations (model, api_base, params)
- **`rlm`** — RLM settings (max turns, REPL behavior)
- **`loop_detection`** — repetitive-behavior detection

Full reference: [docs/config-schema.md](docs/config-schema.md).

---

## Testing

```bash
PYTHONPATH=src python -m pytest src/tests -q   # unit tests
cd src && bash sanity.sh                        # live-LLM sanity suite
```

---

## Documentation

- [docs/INDEX.md](docs/INDEX.md) — documentation index (RLM design docs, conventions, specs)
- [docs/designs/INDEX.md](docs/designs/INDEX.md) — design documents (decisions, spawn, context, A2A, …)
- [src/TAU.md](src/TAU.md) — developer guide (style, debugging, testing)
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md) — code conventions
- [docs/streaming-spec.md](docs/streaming-spec.md) — streaming LLM API spec
- [specs/INDEX.md](specs/INDEX.md) — SDD specs
- [SECURITY.md](SECURITY.md) · [DISCLAIMER.md](DISCLAIMER.md) · [LICENSE](LICENSE)

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for full terms.
