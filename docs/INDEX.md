# Project Documentation

- [STRUCTURE.md](STRUCTURE.md) — project-structure & cleanup principles (what belongs where, why)
Documentation Index

Documentation for TauRLM, the Recursive Language Model agent.

## RLM Design & Reference (`docs/`)

| Document | Content |
|----------|---------|
| [config-schema.md](config-schema.md) | Full `tau.json` configuration reference |
| [git-strategy.md](git-strategy.md) | Branch/commit strategy for the RLM transformation |
| [loop-design.md](loop-design.md) | RLM loop design and LLM call changes |
| [package-inventory.md](package-inventory.md) | Package/module inventory |
| [rlm-call-design.md](rlm-call-design.md) | RLM call flow design |
| [rollback-procedure.md](rollback-procedure.md) | Rollback procedure |
| [session-lifecycle.md](session-lifecycle.md) | Session prefix claim/peek, turn-summary sinks, exit paths, empty `.context` files |
| [streaming-spec.md](streaming-spec.md) | Streaming LLM API migration spec |
| [trust-model.md](trust-model.md) | Trust model rationale (no sandbox by design) |

## Conventions

- [CONVENTIONS.md](CONVENTIONS.md) — code style and structure rules

## Related

- [../README.md](../README.md) — user-facing README
- [designs/INDEX.md](designs/INDEX.md) — design documents (decisions, spawn, context, A2A, commands, skills, testing)
- [../src/TAU.md](../src/TAU.md) — developer guide
- [../specs/INDEX.md](../specs/INDEX.md) — SDD specs
