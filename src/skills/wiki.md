---
name: wiki
description: 'Wiki operations: search, add, retrieve, index persistent knowledge. Use for storing decisions, notes, and cross-session context. Keywords: wiki, knowledge, memory, persist, store, retrieve, search, decision, note, context, cross-session.'
category: knowledge
keywords: 'wiki, knowledge, memory, persist, store, retrieve, search, decision, note, context, cross-session'
---

# Wiki (Persistent Knowledge)

## Workflow

1. **Search first** — check if knowledge already exists before adding
2. **Add** — use appropriate entry_type (decision, fact, note, session); default is `session`
3. **Retrieve** — get latest entry for a topic when resuming work
4. **Index** — check `wiki.idx()` to see what is stored

## Gotchas (verified)
- **retrieve() default cap is 4000 chars** (silent head-truncation). For living/contract docs: keep the contract in the first ~3900 chars, or use `wiki.retrieve_full(topic)` for the whole file. `wiki.set_cut_marker("SENTINEL-X")` registers a sentinel that MUST survive the cut - retrieve() then returns LOUD WARNING lines instead of a silently-truncated contract. `retrieve(topic, max_chars=N)` overrides the cap per call.
- Wiki is a **separate git repo** at `~/.local/tau/wiki/` (NOT `~/.local/taurlm/log` — different dir from session logs); `TAU_WIKI_DIR` overrides (wiki.py:31,35). `add()`/`log()` auto `git add -A` + commit into THAT repo (wiki.py:129-130,163-164), so entries are shared host-wide across every agent — write deliberately, it is not private scratch.

## Examples

```python
# Search for prior decisions on a topic
results = wiki.search("error handling strategy")
print(results)

# Store a decision (auto-commits into the wiki git repo)
wiki.add("auth-middleware",
         "Chose JWT over session cookies. Reason: stateless, scales horizontally.",
         entry_type="decision")

# Full, uncapped view (or: wiki.retrieve(topic, max_chars=20000))
entry = wiki.retrieve_full("auth-middleware")

# Protect a living-doc contract: sentinel must survive the 4000-char cut
wiki.set_cut_marker("DOC-SENTINEL-K9")

# Retrieve latest for a topic
entry = wiki.retrieve("auth-middleware")
print(entry)

# Check overall wiki state
print(wiki.status())
print(wiki.idx())
```

## Related Skills

- `spawn` — delegate batch-ingest of wiki entries to keep your context lean
- `code-analysis` — search codebase to complement wiki search
- `debug` — store root-cause findings in wiki for future reference
- `testing` — log test strategy decisions for cross-session continuity
- `session-lifecycle` — what/when session files persist across sessions
