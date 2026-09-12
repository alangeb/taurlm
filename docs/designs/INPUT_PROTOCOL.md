# Input Protocol — TauRLM

## Overview

TauErgon supports multiple input modes through prefix characters that control how user input is processed. This document describes the complete input protocol and its implementation.

## Prefix Characters

### Regular Input (Outside Multiline Block)

| Prefix | Action | Example |
|--------|--------|---------|
| `#`    | Start multiline block | `# Write a function...` |
| `#!`   | Start multiline block (alternative) | `#! Write a function...` |
| `!`    | Execute shell command | `! ls -la` |
| `+`    | Steering control (when turn active) | `+stop` |
| `/`    | Dispatch slash command | `/help` |

### Inside Multiline Block

| Prefix | Action | Example |
|--------|--------|---------|
| `#!`   | Continue block (strip prefix) | `#! More content` |
| `#`    | Continue block (strip prefix) | `# Even more content` |
| `#+`   | Break multiline, route steering | `#+ stop` |
| `#/`   | Break multiline, execute command | `#/ help` |
| (blank) | Accumulate content | (regular text) |
| 2+ blank lines | Submit block | (empty lines) |

## Multiline Blocks

### Starting a Block

Use `#` or `#!` at the beginning of a line to start a multiline block:

```
# This starts a multiline block
# Multiple lines can be entered
# The # prefix is optional after the first line
```

or

```
#! This also starts a multiline block
#! Alternative syntax
```

### Ending a Block

A multiline block ends when:
1. Two or more consecutive blank lines are entered
2. `#+` is entered (routes steering command)
3. `#/` is entered (executes command)

### Steering Inside Multiline

Use `#+` to break out of a multiline block and inject a steering command:

```
# Working on a task...
#+ stop
```

This terminates the current turn and injects the stop command.

### Commands Inside Multiline

Use `#/` to break out and execute a slash command:

```
# Writing documentation...
#/ help
```

This executes `/help` while preserving the context of what was being written.

## Steering Commands

Steering commands are available when the agent turn is active:

| Command | Action |
|---------|--------|
| `stop` | Gracefully terminate the current turn |
| `redirect <task>` | Redirect to a new task, clearing context |
| `status` | Display current agent status |
| `<text>` | Inject text as a user message |

### Steering Outside Turn

When no turn is active, `+` prefixes are echoed as regular input starting with `+`.

## Shell Commands

The `!` prefix executes shell commands via subprocess:

```
! ls -la /tmp
! git status
! python3 script.py
```

## Slash Commands

The `/` prefix dispatches to the command system:

```
/help          # Show help
/status        # Show agent status
/ctx           # Display context
/goal          # Set/view goal
/agent        # List active spawns
/refine <text> # Refine current answer
/heartbeat     # Configure heartbeat
/autonomous    # Toggle autonomous mode
/continue      # Continue from context
/wiki          # Knowledge store operations
```

## Implementation Details

### Code Location

- **Input Handler**: `agent_input.py` - `InputHandler` class
- **Command Dispatch**: `agent_core.py` - `_handle_command()` (dispatches to `commands/` module)
- **Shell Execution**: `!` prefix executes via `subprocess.run()` in the main processing loop (60s timeout, 8192 char limit)

### Key Functions

```python
# Start multiline block
elif content.startswith("#") and not content.startswith("#/"):
    active, buffer = True, [content[1:]]

# Break multiline with steering
elif content.startswith("#+"):
    _route_steering(content[2:].strip())

# Break multiline with command
elif content.startswith("#/"):
    _execute_command(content[2:])

# Continue block (strip prefix)
elif content.startswith("#!") or content.startswith("#"):
    prefix_len = 2 if content.startswith("#!") else 1
    buffer.append(content[prefix_len:])
```

## Testing

Comprehensive tests are in `tests/test_input_protocol.py`:

```bash
# Run all input protocol tests
pytest tests/test_input_protocol.py -v

# Run specific test categories
pytest tests/test_input_protocol.py::TestMultilineHash -v
pytest tests/test_input_protocol.py::TestMultilineSteering -v
pytest tests/test_input_protocol.py::TestMultilineCommand -v
pytest tests/test_input_protocol.py::TestSingleCharPrefixes -v
pytest tests/test_input_protocol.py::TestMultilineEdgeCases -v
pytest tests/test_input_protocol.py::TestPrefixCombinations -v
```

## Design Decisions

### Why Multiple Prefixes?

1. **`#` vs `#!`**: `#!` is more explicit and less likely to conflict with comments in code. Both are supported for flexibility.

2. **`#+` and `#/`**: These provide a way to break out of multiline blocks while performing actions, without losing the accumulated content.

3. **`+` steering**: Allows interrupting a running turn without typing a full command.

### Why Not Just `#`?

A single `#` can be ambiguous:
- In shell: comment
- In code: comment
- In text: hashtag/number

The `#!` syntax is more explicit and follows the Unix convention of shebangs.

### Why 2+ Blank Lines?

Single blank lines are common in writing. Requiring 2+ prevents accidental block termination.

## Regression Prevention

To avoid regressions in the input protocol:

1. **Always update tests**: Any change to input handling must update `tests/test_input_protocol.py`
2. **Update documentation**: This document and inline docstrings must stay in sync
3. **Test all prefixes**: Ensure all 7 prefixes (`#`, `#!`, `!`, `+`, `/`, `#/`, `#+`) are tested
4. **Integration tests**: Test real-world usage patterns, not just unit cases

## Related Documentation

- [COMMANDS.md](COMMANDS.md) - Command reference
- [DECISIONS.md](DECISIONS.md) - Design decision history
- [TESTING.md](TESTING.md) - Testing guidelines
