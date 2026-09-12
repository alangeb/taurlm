# Designs Index

Design documents for TauRLM. See `../../src/TAU.md` for the developer guide.

| Document | Content |
|----------|---------|
| [A2A_PROTOCOL.md](A2A_PROTOCOL.md) | Agent-to-agent communication via Unix sockets: message types, constants, session discovery |
| [AUDIT.md](AUDIT.md) | Audit log design: append-only, immutable, structured events |
| [COMMANDS.md](COMMANDS.md) | Two-tier command dispatch (Python + Markdown), implementation guide |
| [COMPRESSION.md](COMPRESSION.md) | Context compression pipeline: 10-step progressive reduction |
| [CONTEXT.md](CONTEXT.md) | Context management patterns, message prefix protocol, synthetic cleanup |
| [DECISIONS.md](DECISIONS.md) | design decisions across categories |
| [INPUT_PROTOCOL.md](INPUT_PROTOCOL.md) | CLI input handling: `#`/`#!` multiline, `!` shell, `+` steering, `/` commands |
| [SKILLS.md](SKILLS.md) | Skill contract, implementation guide |
| [SPAWN.md](SPAWN.md) | Spawn system: persistent child agents, budget, handle API |
| [SPAWN_IMPLEMENTATION.md](SPAWN_IMPLEMENTATION.md) | Spawn implementation details: kernel spawning, namespace isolation |
| [TESTING.md](TESTING.md) | Manual testing, unit tests, e2e tests, test rules |
## Related

- [../INDEX.md](../INDEX.md) — general docs index (config schema, trust model, conventions, streaming spec)
- [../../src/TAU.md](../../src/TAU.md) — developer guide
