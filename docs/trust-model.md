# RLM Trust Model

## Design Decision: No Security Sandbox

RLM operates on TRUST, not sandboxing. The model is a trusted agent with full access to the Python REPL and system.

## Rationale

1. **Expert users**: RLM is designed for users who understand the risks and trust their models
2. **Complexity**: Sandboxing adds complexity without meaningful security
3. **False security**: Any determined model can escape sandboxes
4. **By design**: The model has full access — this is intentional

## What This Means

- No import restrictions
- No function blocklists
- No path restrictions
- No subprocess restrictions
- No eval/exec restrictions

## If You Don't Trust the Model

Do NOT give it REPL access. Use a tool-calling agent with restricted tools instead.

## Security Recommendations

1. Run RLM in a container/VM
2. Use separate user account
3. Monitor resource usage
4. Set execution timeouts
5. Review code before production deployment

## Decision History

- **Date**: 2026-09-12
- **Decision**: Remove SecuritySandbox, adopt trust model
- **Rationale**: RLM is for expert users who trust their models; sandboxing adds complexity without meaningful security
