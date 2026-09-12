# Design Decisions

## Architecture (8)

| # | Decision | Rationale |
|---|----------|-----------|
| 1.1 | **Central `TauErgon` orchestrator** — one class owns context, LLM, REPL kernel, loop detection, subagents | Single ownership, clear responsibility |
| 1.2 | **Message-driven architecture** — user input → context → LLM → REPL execution → repeat | Simple, composable flow |
| 1.3 | **Single entry point** (`tau.py`) | Clean entry point |
| 1.4 | **System prompt from `AGENT_RLM.md`** — externalized, not hardcoded | Configurable behavior without code changes |
| 1.5 | **`AgentInitConfig`** — fully-resolved initialization parameters in `agent_init.py` via `resolve_agent_init()`; separates config resolution from agent construction | Clean init pipeline |
| 1.6 | **`rlm/command_dispatch.py`** — unified command resolution and dispatch for RLM; provides `handle_command()`, `parse_multi_prompt()`, `strip_frontmatter()`, `get_available_commands()` | Centralized command routing |
| 1.7 | **`InputHandler`** — manages stdin thread, signal handling, and input dispatch in `agent_input.py`; separates input loop from agent core | Decoupled input processing |
| 1.8 | **`agent_subsystems.py`** — `SubsystemBundle`, `init_subsystems()`, `read_system_prompt()` encapsulate subsystem creation and wiring; reduces import burden on `agent_core.py`; makes subsystem init testable in isolation | Separation of concerns, testability |

## Compression (18)

| # | Decision | Rationale |
|---|----------|-----------|
| 2.1 | **Ten sequential algorithms**: PRUNE_IMAGES → OVERSIZE_USER_PRUNE → DROP_REASONING → COLLAPSE_TURNS_50 → REDACT_BLOCKS_50 → COLLAPSE_TURNS_FULL → REDACT_BLOCKS_FULL → FULL_RESET → CONVERSATION_SUMMARY → BLIND_TRUNCATE | Ordered by impact: image pruning first, then oversized user message removal, then structural redaction, then boundary-limited collapse/redaction, then full-context collapse/redaction, full reset, then deterministic conversation summary, then blind truncation as absolute last resort |
| 2.2 | **Fixed 50% byte boundary** — computed ONCE from original context, never moves during compression | Predictable, preserves recent context |
| 2.3 | **Right-to-left scanning** — compresses oldest blocks first to preserve KV cache prefix | KV cache efficiency |
| 2.4 | **Parameter consistency across LLM calls** — same model/params; only `messages` varies → enables full KV cache reuse | Maximize cache hits |
| 2.5 | **Compression prompt is a constant string** — never changes between calls → prefix stability | KV cache prefix stability |
| 2.6 | **`MAX_ITERATIONS = 100`** — cap on compression algorithm iterations to prevent infinite loops | Safety bound |
| 2.7 | **Minimum block size: 300 bytes** — avoid LLM call overhead on tiny blocks | Efficiency threshold |
| 2.8 | **Retry on short LLM responses (<10 bytes)** — compression summaries must be substantive | Quality gate |
| 2.9 | **Preserve message structure when pruning** — maintains role alternation compliance | API compatibility |
| 2.10 | **`OVERSIZE_USER_PRUNE` as first algorithm** — removes oversized user messages before other compression | Targets disproportionately large outputs first |
| 2.11 | **Two-tier boundary strategy** — steps 5–6 (COLLAPSE_TURNS_50, REDACT_BLOCKS_50) respect 50% boundary; steps 7–8 (COLLAPSE_TURNS_FULL, REDACT_BLOCKS_FULL) scan entire context | Graduated escalation: protect recent context first, then compress everything if needed |
| 2.12 | **`compress_drop_reasoning` as second algorithm** — strips reasoning fields before LLM summarization | Cheap, high-yield reduction before expensive LLM calls |
| 2.13 | **`compress_prune_images` as first algorithm** — replaces image blocks with text placeholders (keeps last image per message); images dominate context size; right-to-left within 50% boundary | Most aggressive reduction: images are the largest context consumers |
| 2.14 | **`compress_conversation_summary` as ninth algorithm** — deterministic restructuring (no LLM call) that condenses entire conversation into a single summary user message; preserves ALL interaction history in compact structured format; always OpenAI-alternation-compliant | Guaranteed compression when all LLM-based methods fail; zero API cost; preserves complete interaction history |
| 2.15 | **`compress_blind_truncate` as tenth algorithm** — last-resort truncation of summary message from the beginning; guaranteed to produce context within target_size_bytes; preserves most recent information | Absolute fallback: when even deterministic summary is too large, truncate from oldest end |
| 2.16 | **Dynamic compression threshold based on loop escalation** — `compress_threshold` starts at 0.85 (normal), drops to 0.65 at escalation_level 1, drops to 0.35 at escalation_level 2+ | When the loop is stuck, breaking it takes priority over preserving KV cache; aggressive compression before the LLM call prevents the model from operating on bloated context |
| 2.17 | **Compression runs both before AND after LLM call** — pre-call compression (line ~106) uses dynamic threshold based on escalation; post-call compression (line ~193) uses static 0.85 threshold as safety net for context growth during LLM call | Two-stage defense: pre-call prevents LLM from seeing bloated context, post-call catches growth that happens during the LLM call itself |
| 2.18 | **Pre-call compression location: after context validation, before LLM call** — ensures context is valid before compressing, and LLM always sees a clean context | Safety: validate first, compress second, then call LLM |
| 2.19 | **Compressed context MUST end on USER (D5 REVERTED 2026-09-12)** — compression is triggered by an overflow error MID-INVOKE and the compressed list is RE-SENT AS-IS to `_invoke_llm_streaming` (`invoke.py:703-704`), so it is the LIVE prompt and must end on the user's pending request; a trailing assistant makes the model prefill instead of answering (the failure `conversation_summary.py:184-187` warns about). The old assistant-end invariant (D5) appended a synthetic assistant in `framework.py`/`full_reset.py` and is REMOVED. Supersedes the assistant-end reading of §18.6. | Overflow-retry re-sends the compressed list as the live prompt; user-end is required for the model to answer |\n
## LLM Layer (19)

| # | Decision | Rationale |
|---|----------|-----------|
| 3.1 | **`SimpleOpenAIClient` — stdlib-only HTTP client** — no external dependencies for LLM communication | Zero-dependency core |
| 3.2 | **Drop-in OpenAI client interface** — wraps raw API to match OpenAI SDK semantics | Familiar interface, easy migration |
| 3.3 | **Unified retry logic** — `_invoke_llm_with_retry` handles all retries, backoff, validation | Centralized error handling |
| 3.4 | **Post-parse recovery** — extract tool calls from text content when LLM misses structured format **[OBSOLETE in RLM — no tool calls]** | Robustness against LLM quirks |
| 3.5 | **Validate before sending to API** — check tool call JSON, empty replies, length limits **[RLM: only empty replies and length limits apply; no tool call JSON]** | Fail fast, save API calls |
| 3.6 | **`InvalidReplyError` for retryable violations** — fast-fail on first error **[NOT IN CODE — no such exception exists; RLM uses `EmptyModelResponse` and `BadRequestError` instead]** | Clear error signaling |
| 3.7 | ~~**XML-style and pipe-style tag constants** — centralized in `agent_llm_tool_parse.py`~~ | ~~Single source of truth~~ → **SUPERSEDED**: `agent_llm_tool_parse.py` removed; RLM architecture doesn't use tool-call parsing
| 3.8 | **`PrefixCacheTracker`** — compares consecutive request bodies to estimate expected prefix cache hit rate; deduplicates warnings via `should_warn()` | Cache observability |
| 3.9 | **End-of-turn validation** — check for unclosed thinking tags, malformed tool-call syntax **[OBSOLETE in RLM — EOT is `answer['ready']`, not tag-based]** | Quality gate |
| 3.10 | **Pre-API field stripping** — remove non-LLM-relevant fields to avoid 400 errors | Defensive coding |
| 3.11 | **Defensive parsing** — `_safe_get`, deep copies, external tracking sets | Robust against malformed responses |
| 3.12 | **Graduated retry strategy** — thinking disabled after 5 failures | Adaptive behavior |
| 3.13 | **Bounded logging** — prevent console flooding | UX protection |
| 3.14 | **Cross-backend support** — handles both vLLM and llama.cpp formats | Backend agnostic |
| 3.15 | **Conservative post-parse** — only extracts clearly valid tool calls | Safety over flexibility |
| 3.16 | **`PrefixCacheTracker` in `agent_llm_cache.py`** — tracks expected vs actual prefix cache hits, reports divergence with param change detection | Cache observability |
| 3.17 | **`LLMCallConfig` dataclass** — unified configuration for LLM invocations (max_retries, disable_thinking_after, extra_kwargs, compress_*, agent, max_context_tokens, max_output_tokens) **[RLM: no tools/tool_choice fields]** | Centralized call config |
| 3.18 | **In-place compression replaces truncation** — overflow recovery uses LLM-based compression on real context, eliminating redundant copy compression | Overflow tracking |
| 3.19 | **5xx gateway retry** — 502/503/504 errors trigger 30s backoff + retry (not hard fail) | Transient infrastructure errors should be retried |

## REPL Kernel (5)

| # | Decision | Rationale |
|---|----------|-----------|
| 4.1 | **Trust model** — no security sandbox, full Python access by design | RLM operates on trust, not restrictions |
| 4.2 | **Persistent namespace** — variables, imports, functions persist across turns | Maintains working context without bloating conversation |
| 4.3 | **SIGALRM-based timeout** — Python blocks use `signal.setitimer(ITIMER_REAL)` + `SIGALRM` handler (single-threaded, interrupts any blocking C call); bash blocks use `subprocess.Popen` with `communicate(timeout)` + process group kill | Clean interruption, no orphaned processes |
| 4.4 | **Output capture** — stdout/stderr captured and truncated to prevent context explosion | Token economy |
| 4.5 | **Magic commands** — `%cd` (change working directory) and `%timeout N` (per-block timeout override, 1-3600s) | Convenient REPL extensions |

## Console & Communications (8)

| # | Decision | Rationale |
|---|----------|-----------|
| 5.1 | **Console output via standalone functions** — `agent_console/` package provides: primitives (low-level I/O: `_cw()`, `_role_color()`, `Colors`, `echo()`, `status()`, etc. + display helpers: `display_error()`, `display_warning()`, `display_success()`, `display_info()`, `display_synthetic()`), templates (`_ConsoleMessage` declarative templates + message definitions + `register_console_messages()`); no singleton class | Consistent formatting, no global state, focused modules |
| 5.2 | **Semantic color coding** — RED=error, YELLOW=warning, CYAN=status, GREEN=success, TEAL=reasoning | Visual clarity |
| 5.3 | **A2A via Unix domain sockets** — inter-agent communication protocol with JSON messages | Process-local, secure |
| 5.4 | **`InputMessage` factory pattern** — `from_a2a()`, `from_interactive()`, `from_command_line()` → unified input type | Source-agnostic processing |
| 5.5 | **Auto-timestamping via `__post_init__`** — all messages get timestamps automatically | Traceability |
| 5.6 | **Audit display truncation** — `audit_display.py` truncates long lines for console display (configurable max length) | UX protection |
| 5.7 | **Dynamic `sys.stdout`** — supports output redirection | Testability |
| 5.8 | **Input prefix protocol** — `#`/`#!` start multiline blocks, `!` executes shell, `+` steers, `/` dispatches commands; inside blocks: `#+` routes steering, `#/` executes commands, 2+ blank lines submit | Flexible input modes with clear separation; prevents ambiguity between comments and commands |

## Subagent & Fork (11) [SUPERSEDED by SPAWN.md 2026-09-12]

| # | Decision | Rationale |
|---|----------|-----------|
| 6.1 | **Subagent = blank slate** — fresh context, no parent history | Maximum isolation |
| 6.2 | **Fork = deep copy** — inherits full parent context + conversation | Context continuity |
| 6.3 | **Nesting depth threshold** — configurable limit on subagent/fork depth | Infinite recursion prevention |
| 6.4 | **Nesting restriction text injected into system prompt** — tells subagents their depth limit | Self-aware agents |
| 6.5 | **`_create_subagent` inherits parent config** — same LLM, same settings | Consistent behavior |
| 6.6 | **Unrestricted child tools by default** — children get full tool access unless filtered | Flexibility |
| 6.7 | **Fresh fork metadata** — not inherited from parent | Clean state |
| 6.8 | **`/fork {prompt} user message`** — signals fork context | Clear context markers |
| 6.9 | **Local imports** — avoids circular dependencies | Module independence |
| 6.10 | **Fork isolation via `_create_fork_isolation`** — each fork gets unique `fork_id` and isolated temp directory; cleaned up after completion | Resource isolation |
| 6.11 | **Forks are synchronous/blocking** — parent blocks until fork returns; no concurrent fork execution | Simplicity, predictable behavior |
| 6.12 | **ALL delegation methods are synchronous** — `subagent()`, `fork()`, `delegate()`, and `rlm()` all block until the child completes; this is intentional, we only want synchronous agents | Simplicity, predictable behavior, no race conditions |
| 6.13 | **ALL agents are one-shot** — each child processes exactly one prompt, returns its result, then is fully cleaned up (process killed, temp dir removed, registry entry deleted) | Resource cleanup, no stale state |
| 6.14 | **No multiprompt/resume** — there is no mechanism to send additional prompts to a finished child or resume a prior conversation | Simplicity, clear lifecycle boundaries |
| 6.15 | **Persistent agents are the exception to 6.13/6.14** — `spawn(persistent=True)` returns a handle with `.send()`/`.close()` for multi-turn steering; auto-compresses at ~70% context | Long-running workers, iterative refinement |
| 6.16 | **Shared audit file** — ALL agents (master, subagent, fork, persistent) write to the same `{SESSION_PREFIX}.audit` file; forks use `TAU_PARENT_AUDIT_FILE` env, persistent agents pass explicit `audit_file=` path | Unified audit trail, single source of truth |

## Delegate Mode (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 7.1 | **[OBSOLETE - delegate mode removed]**  **Delegate mode via `/delegate` command** (`commands/delegate.py`) — ToolFilter enforces read-only behavior at execution time; all tools still announced (prefix cache preserved); orchestrator plans and delegates via fork/subagent | Safety + cache safety |
| 7.2 | **[OBSOLETE - delegate mode removed]**  **Delegate uses ToolFilter allowlist** — `_ALLOWED_DELEGATE_TOOLS` in `commands/delegate.py` restricts to read/analysis + delegation tools; non-allowed tools blocked with denied message; prefix cache preserved because tools are still announced | Execution-time enforcement |
| 7.3 | **[OBSOLETE - delegate mode removed]**  **ToolFilter changes in delegate mode** — `tool_filter` IS modified at runtime; this does NOT break prefix caching because `available_tool_names` (announced to LLM) is unchanged; filter only affects execution-time blocking | Prefix cache preservation |
| 7.4 | **[OBSOLETE - delegate mode removed]**  **`DELEGATE_INSTRUCTIONS` injected into context** — self-correcting behavior via prompt instructions | Enforced pattern |
| 7.5 | **[OBSOLETE in RLM — EOT is `answer['ready']`]** ~~`end_turn` as explicit loop terminator~~ — no hard iteration limit | Flexible orchestration |
| 7.6 | **[OBSOLETE in RLM — no invoke_with_tools]** ~~Loop exit: check `invoke_with_tools()` return value~~ — loop only continues if result is `None` (interrupted); exits for any string response (normal or error) | Correct termination |
| 7.7 | **[OBSOLETE in RLM — max_turns=1000]** ~~Max iteration limit (10)~~ — safety net to prevent infinite loops on continuous interrupts | Robustness |

## Input & Interaction (6)

| # | Decision | Rationale |
|---|----------|-----------|
| 8.1 | **Multiline input with `#` prefix** — two blank lines to end | Natural editing |
| 8.2 | **Two-level Ctrl+C** — graceful shutdown → force exit | User control |
| 8.3 | **Thread-safe everywhere** — `OutputCapture` with `threading.Lock`, `queue.Queue` for input | Concurrency safety |
| 8.4 | **System-wide flags** (`_interrupted`, `_exit_requested`) — cooperative cross-thread shutdown | Clean termination |
| 8.5 | **`InputHandler` stdin daemon thread** — reads stdin in background with `select()`, puts messages in `input_queue`; main loop dispatches `/commands`, `!shell`, and regular input via `_process_input()` | Non-blocking input |
| 8.6 | **`!` shell path intentionally NOT hardened against stdin inheritance (design decision, 2026-09-12)** — `!command` runs via `subprocess.run(shell=True)` in the input loop (`agent_input.py:490`) WITHOUT `stdin=DEVNULL`/`start_new_session`, and the input loop reads fd 0 via `select`+`readline` with no `isatty`/leak guard (`agent_input.py:269-280`). This means a command that reads stdin with no input (e.g. `!cat`, `!wc -l`) inherits the terminal stdin and can block the REPL. This is ACCEPTED, not a bug to fix: the `!` prefix is an explicit, deliberate user action — the user types it and is responsible for what it runs. We trust the user knows what they are doing and consider them responsible for their own `!` commands. Contrast with the AUTOMATED bash-block path (`block_executor.py:285-291`), which IS hardened (DEVNULL + `start_new_session` + `killpg`) because that path runs model-generated code the user never explicitly chose. The distinction is trust: model-authored code must be sandboxed from the terminal; user-authored `!` commands are trusted. If a `!` command ever deadlocks the REPL, Ctrl-C (two-level shutdown, §8.2) recovers it. **This will NOT be changed.** | The `!` prefix is an explicit user responsibility boundary; over-hardening it would fight legitimate use cases (`!ssh`, `!python` interactive) and add complexity for a self-inflicted, recoverable footgun. Sandbox the machine's code, trust the human's code. |

## Commands (16)

| # | Decision | Rationale |
|---|----------|-----------|
| 9.1 | **Static command registry** — `commands.COMMANDS` dict in `commands/__init__.py` maps name → module | Simple, no dynamic discovery overhead |
| 9.2 | **Python commands: full agent access** — `run(agent, args: list[str]) -> str` or `run(args: str) -> str` (varies by command); manages own context | Arbitrary program logic |
| 9.5 | **Static import** — commands imported once at startup via `commands/__init__.py` | Simplicity |
| 9.16 | **Markdown commands** — `commands/*.md` files discovered into `MD_COMMANDS` dict; expand into segments flowing through input pipeline as simulated user input | Indistinguishable from real prompts, full LLM turn per segment |
| 9.17 | **Placeholder substitution** — `$1`, `$2`, `$*`, `$1+`, `${time}`, `${date}`, `${datetime}`; regex-based with reverse-order processing to prevent substring collisions ($1 not matching inside $10) | Safety + flexibility |
| 9.18 | **Multiprompting** — split on `---` delimiters; each segment = full LLM turn with context accumulation | Multi-phase workflows |
| 9.19 | **Command recursion** — segments starting with `/` dispatch to other commands; depth guard `MAX_MD_RECURSION=5` via `_cmd_dispatch_depth` counter | Composition without infinite loops |
| 9.20 | **Recursion limit: no unit test** — MAX_MD_RECURSION tested via sanity.sh (Test 8) only; unit tests intentionally omitted as the limit is a safety guard, not core functionality; try/finally depth counter guarantees correctness | Test pragmatism |
| 9.10 | **`ralph` command** — iterative task execution with explicit `<complete>` tag confirmation; maintains task state in JSON files under `~/.local/taurlm/ralph/` | Structured task workflow |
| 9.11 | **`plan` command** — hierarchical task plan management (create, add, complete, block, unblock, status, next, progress, update, delete, clear) | Task organization |
| 9.12 | **Command origin tracked by registry** — `.py` commands in `COMMANDS` dict (name→module), `.md` commands in `MD_COMMANDS` dict (name→Path); origin implicit from which registry resolves | Debugging & precedence |
| 9.13 | **Recursion guard in `_handle_command`** — `MAX_MD_RECURSION=5` (in `rlm/command_dispatch.py`) with `_cmd_dispatch_depth` counter prevents infinite .md command chains | Safety against recursive prompts |
| 9.14 | **`health` command** (`commands/health.py`) — model server health monitoring dashboard with subcommands `status`, `reset`, `check`; displays `CircuitState` (closed/open/half_open), failure rate, consecutive failures/successes, recovery attempts, last error | Operational observability for LLM server health |
| 9.15 | **`_tautest` command** (`commands/_tautest.md`) — general testing orchestrator: runs `./tau.py` with parameters, creates test plans, fixes issues found; standalone command (not part of dream cycle) | General-purpose testing interface |

| 9.16 | **MD_COMMANDS not wired into dispatch** — `MD_COMMANDS` dict is populated in `commands/__init__.py` but `handle_command()` in `rlm/command_dispatch.py` only routes to `COMMANDS` (.py). Markdown command dispatch is planned but unimplemented. | Avoids documenting unimplemented behavior as current; keeps doc truthful until dispatch is wired. |
| 9.21 | **MD commands not wired into dispatch** — `handle_command()` in `rlm/command_dispatch.py` only checks `COMMANDS` (.py) dict. `parse_multi_prompt()`, `strip_frontmatter()`, and `MD_COMMANDS` are implemented but never called. Supersedes §9.16 (which describes MD dispatch as active) | MD dispatch infrastructure exists but is not connected; doc updated to reflect current state |
| 9.22 | **`commands/__init__.py` docstring updated** — lists all 10 commands: goal, refine, heartbeat, autonomous, ctx, status, agent, wiki, llm, continue | Docstring was stale (listed 7, missing ctx/wiki/llm, had "agents" instead of "agent") |


## Audit (6)

| # | Decision | Rationale |
|---|----------|-----------|
| 11.1 | **No audit rotation** — audit files are immutable, perpetual, never truncated/rotated/deleted by the system | Forensic integrity; external tools handle lifecycle |
| 11.2 | **100% console-to-audit bridging** — all user-visible output logged (display_*, echo, status, templates, commands) | Complete traceability |
| 11.3 | **Nesting stack attribution** — `stack=SS` in every record (e.g., `stack=.`, `stack=S`, `stack=SS`); identifies which agent wrote each record | Filter by agent identity, not just depth |
| 11.4 | **Exception-safe audit** — all audit calls wrapped in try/except; failures never suppress console output | Reliability |
| 11.5 | **Buffered writes with flush-at-turn** — performance optimized but turn boundaries are on disk | Balance performance/safety |
| 11.6 | **`~/.local/taurlm/` base path** — taurlm uses separate directory from tau (`~/.local/tau/`) | Isolation, no conflicts |

## Skills (6)

| # | Decision | Rationale |
|---|----------|-----------|
| 10.1 | **Skills loaded from `skills/` directory** — each skill is a directory with `SKILL.md` | Easy authoring |
| 10.2 | **Cached skill list** — loaded once, cached after first call | Performance |
| 10.3 | **Fuzzy/case-insensitive skill matching** — flexible lookup | User-friendly |
| 10.4 | **Skill loading via `load_skill()`** — prints full `SKILL.md` content to REPL output (appears in context) | Context-aware execution |
| 10.5 | **Skill discovery in `rlm/skills.py`** — `SkillLoader.discover_skills()` scans `skills/` for directory skills (`<name>/SKILL.md`) and flat skills (`<name>.md`); `SkillMetadata.score()`/`matches()` do case-insensitive, ranked name/keyword/description matching; `load_skill()` prints full skill content to REPL | Simple, flexible skill loading |

| 10.6 | **Standalone .md skills are first-class** — bare `.md` files in `skills/` (e.g., `docker.md`, `spawn.md`, `training.md`) are discovered and loaded, with directory skills winning name collisions. | Keeps discovery clean while allowing lightweight single-file skills. |
| 10.7 | **YAML frontmatter is supported** — `_parse_frontmatter()` parses `name`, `description`, `category`, `keywords`, `version`, and `author`; malformed or unterminated frontmatter is reported with warnings. | Enables richer metadata without breaking legacy plain markdown. |
| 10.8 | **Ranked search API** — `find_skills(query)` and `/skills <query>` search names, descriptions, and keywords; exact names and exact keywords outrank substrings to prevent short queries from selecting similarly named skills. | Improves discoverability and avoids false positives such as `git` matching `git-snapshot`. |


## Background Processes (TMUX) (3)

| # | Decision | Rationale |
|---|----------|-----------|
| 11.1 | **Session naming convention: `tmux-agent-{uuid}`** — auto-generated UUID, prefix for filtering | Unique identification |
| 11.2 | **Session lifecycle: new → exec → capture/tail → kill** — full lifecycle management | Complete control |
| 11.3 | **Kill all via prefix filter** — `tmux-agent-*` pattern for bulk cleanup | Efficient cleanup |

## Web Interaction (7) [OBSOLETE in RLM — no web tools]

| # | Decision | Rationale |
|---|----------|-----------|
| 12.1 | **Crawl4AI first-attempt with native fallback** — `fetch` tries Crawl4AI `/md` endpoint first, falls back to native HTML-to-markdown conversion | Flexible extraction |
| 12.2 | **SearXNG for searching** — `web_search` uses SearXNG for privacy-friendly search | Privacy |
| 12.3 | **Cache flag** — `cache=True` default in tool schema; Crawl4AI first-attempt uses `"c": "0"` to disable its cache; native fetch fallback uses local file cache with TTL | Fresh data by default |
| 12.4 | **Subagent/fork context recommended** — web fetching should be delegated for isolation; advisory only, not enforced | Isolation guidance |
| 12.5 | **Single URL → `/md` endpoint** (markdown), Multiple URLs → `/crawl` endpoint (JSON) | Optimized endpoints |
| 12.6 | **Filter types**: raw/fit/bm25/llm — configurable extraction strategies | Flexible extraction |
| 12.7 | **Multi-engine search** — `search` tool uses SearXNG → DuckDuckGo HTML → Mojeek cascade; `lookup` uses Wikipedia API + DuckDuckGo Instant Answer | Redundant search coverage |

## Loop Detection (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 13.1 | **Triple-strategy**: consecutive repeat + Shannon entropy + answer stagnation | Comprehensive detection |
| 13.2 | **Repeat threshold: 3** (configurable) | Sensitivity tuning |
| 13.3 | **Entropy threshold: 1.5 bits** (hardcoded in `agent_loop_detect.py`, not configurable via `LoopDetectionConfig`) | Diversity threshold |
| 13.4 | **Rolling window: 30 calls** (configurable, min 15 = window_size // 2 for entropy) | Context window |
| 13.5 | **Stats observability** via `get_stats()` | Monitoring |
| 13.6 | **Escalation levels** — `LoopDetector` tracks warning levels 0–4+ (based on `total_warnings // 3`) with separate repeat, entropy, and answer-stagnation templates; `LoopEscalationManager` handles: level 0-1 (alert), level 2 (self-reflection), level 3 (guided introspection), level 4+ (termination) | Progressive intervention |
| 13.7 | **Sustained entropy detection** — `LoopDetector` tracks entropy over multiple rolling windows (`sustained_window=3`, `sustained_threshold=2.5`); triggers warnings when entropy stays consistently below threshold for N consecutive windows; catches 3-tool cycles (entropy ~1.585) that exceed the 1.5 instantaneous threshold; `sustained_warning_cooldown` parameter was added but never used (dead code) | Catches cycles above instantaneous threshold; avoids false positives from short bursts |
| 13.8 | **Escalation thresholds corrected** — actual thresholds are 0-5 (none), 6-8 (warning), 9-11 (escalation), 12+ (critical). Supersedes §13.6 which stated `total_warnings // 3` | Code in `agent_loop_escalation.py` uses explicit range checks, not division |


## A2A Protocol (15)

> **Protocol contract**: See [`A2A_PROTOCOL.md`](A2A_PROTOCOL.md) for the full v1.0 specification — message types, constants, client utilities, session discovery, server lifecycle, audit record types, CLI interface, and error handling.

| # | Decision | Rationale |
|---|----------|-----------|
| 14.1 | **JSON over Unix domain sockets** — all inter-agent messages are JSON-encoded, sent via `AF_UNIX` | Process-local, secure |
| 14.2 | **Socket naming: `/tmp/taua2a-{PID}.sock`** — each agent gets a unique socket path based on its PID | Collision-free |
| 14.3 | **Three request types: `agent_card` (sync), `status` (sync), and `query` (async)** — metadata and status are immediate, queries are queued | Lightweight discovery |
| 14.4 | **Request ID correlation (UUID)** — every query gets a unique `id`, responses carry the same `id` | Response matching |
| 14.5 | **Acknowledgment pattern** — server sends `{"type": "queued"}` immediately, then `{"type": "response"}` later | Client confirmation |
| 14.6 | **Daemon thread for accept loop** — `_accept_loop` runs as `daemon=True` | Clean shutdown |
| 14.7 | **Per-client daemon threads** — each connection spawns its own thread | Concurrent clients |
| 14.8 | **`threading.Event` for startup synchronization** — `_ready` event set after bind/listen | Startup coordination |
| 14.9 | **Socket timeout of 1.0s in accept loop** — allows periodic checking of `self.running` flag | Graceful shutdown |
| 14.10 | **`SO_REUSEADDR` on server socket** — prevents "address already in use" on restart | Restart safety |
| 14.11 | **Scan `/tmp` with glob `taua2a-*.sock`** — filesystem-based discovery | Simple discovery |
| 14.12 | **Active-only default for `list_agents` display** — `_filter_active_agents` strips non-active by default | Clean output |
| 14.13 | **`json.JSONDecoder.raw_decode()` for streaming** — handles concatenated/fragmented JSON | Robust parsing |
| 14.14 | **A2A CLI mode** (`a2a_cli_mode`) — no agent created for discovery queries; short-circuits to direct socket communication for `--list`, `--card`, `--query` | Efficient discovery |
| 14.15 | **Heartbeat protocol** — server sends periodic heartbeats (`HEARTBEAT_INTERVAL=5s`) while polling for response; client uses idle-based timeout (`HEARTBEAT_IDLE_TIMEOUT=30s`) instead of wall-clock timeout; slow agents work indefinitely as long as heartbeats flow; dead server detected via missed heartbeat | Replaces fixed timeouts with adaptive liveness detection |

## Entry Point (7)

| # | Decision | Rationale |
|---|----------|-----------|
| 15.1 | **`tau.py` and `tau-sanity.py`** — near-identical entry points; `tau-sanity.py` omits the `exclude` param in `get_context_file_by_parent_ppid()` to avoid self-exclusion during sanity testing | Test isolation |
| 15.2 | **Line-buffered stdout** — `sys.stdout.reconfigure(line_buffering=True)` at module level | Interactive responsiveness |
| 15.3 | **Pre-parser for `--llm`** — resolves LLM group before building full parser | Correct help text |
| 15.4 | **`None` defaults for `--base-url`, `--model`, `--ctx`** — distinguish "user set" vs "group default" | Runtime switching |
| 15.5 | **`nargs="*"` for positional `inputs`** — zero or more inputs | Flexible invocation |
| 15.6 | **A2A client flags short-circuit to `a2a_cli_mode()`** — no agent created | Efficient discovery |
| 15.7 | **`--keep-alive` flag** — for A2A server mode | Headless operation |

## Think Tool (5) [OBSOLETE in RLM — no think tool]

| # | Decision | Rationale |
|---|----------|-----------|
| 16.1 | **Read-only fork** — `Think` spawns a fork with restricted tool access | Safe reasoning |
| 16.2 | **Allowlist of safe tools** — `glob`, `file_read`, `head`, `wc`, `pyscan`, `pyanalyze`, `grep`, `info`, `skill` | Read-only operations |
| 16.3 | **`THINK_PROMPT` constant** — pre-defined prompt for focused thinking | Consistent behavior |
| 16.4 | **No arguments required** — task is implicit in context | Simple interface |
| 16.5 | **[OBSOLETE in RLM — no think tool]** ~~`THINK_TOOL_ALLOWLIST` in `tools/think.py`~~ — explicit `frozenset` of allowed tools; `_build_safe_fallback` provides graceful degradation when fork fails | Safety + resilience |

## Cross-Cutting Principles (11)

| # | Decision | Rationale |
|---|----------|-----------|
| 17.1 | **[OBSOLETE in RLM — no tools]** ~~`force_end_turn` mechanism~~ — any tool can terminate the current turn | Explicit control |
| 17.2 | **Graceful degradation** — features fail silently, agent continues operating | Resilience |
| 17.3 | **No external dependencies for core** — stdlib-only where possible | Zero-dependency core |
| 17.4 | **Heartbeat system** — idle detection with configurable interval | Self-monitoring |
| 17.5 | **[OBSOLETE in RLM — subagent/fork replaced by spawn]** ~~`SubAgentResult` captures output + token metrics~~ — structured result containers | Observability |
| 17.6 | **Atomic single-append writes** — no explicit file locking | Concurrency safety |
| 17.7 | **Config source annotations** — `[env]`, `[file]` transparency in status display | Configuration visibility |
| 17.8 | **`ErrorRateTracker`** — thread-safe sliding window error rate tracking with burst detection and alert thresholds in `agent_audit_writer.py` | Proactive error monitoring |
| 17.9 | **`TokenTracker`** — centralized token accounting in `agent_token_tracker.py` with session-wide and per-turn counters; integrates `PrefixCacheTracker` for cache hit rates | Granular token observability |
| 17.10 | **[OBSOLETE in RLM — no file_write tool]** ~~NO angle brackets in Python source~~ — Python files MUST NOT contain literal ``, `=` characters; use `chr(60)`, `chr(62)`, `chr(60)+chr(61)`, `chr(62)+chr(61)` or variable composition (`LT = chr(60)`) instead | `file_write` tool strips XML-like tags from content, corrupting Python code with comparison operators |
| 17.11 | **XML argument stripping REMOVED** — `_sanitize_arg_value()` in `tools/validation.py` was a NO-OP and has been removed; XML regex constants were deleted; the regex `r']*>'` stripped ALL `` sequences including legitimate Python comparison operators (`=`, `< threshold`); cannot distinguish legitimate XML/HTML in tool args vs. leaked LLM tags; root cause was over-aggressive regex, not a language constraint | `file_write` tool's XML stripping was the symptom; the correct fix was removing the sanitizer entirely rather than banning angle brackets from source code |

## Context Management (19)

| # | Decision | Rationale |
|---|----------|-----------|
| 18.1 | **`TauContext._fork_metadata` separation** — fork metadata (pending tool calls, fork identity) stored separately from conversation history | Clean compression while preserving fork state |
| 18.2 | **Context validation after every mutation** (`_validate_on_mutation`) — enforces alternating USER/ASSISTANT turns, matching tool call/result pairs, no orphaned tool calls **[RLM: tool-call validation is inert — no tool role exists]** | API compliance |
| 18.3 | **`close_turn` mechanism** — ensures context ends in valid terminal state after incomplete turns | Graceful recovery |
| 18.4 | **`is_synthetic_message()` unified detection** — single function checks `SYNTHETIC_PREFIX` marker on any message (user or assistant); replaces old `_is_synthetic_user_message()`; **utilities moved to `agent_message_utils.py`** (zero-dependency module) | Eliminated redundant detection logic |
| 18.5 | **Synthetic message protocol** — **[RLM: current prefix is `[U:type | N:stack | M:msgs | C:pct]`; `SYNTHETIC_PREFIX` is legacy backward-compat only]** all system-injected messages use `SYNTHETIC_PREFIX = "[SYSTEM-SYNTHETIC: "` marker; `make_synthetic_user(category, content)` factory creates them; recovery paths inject synthetic user messages (NOT tool calls) to maintain OpenAI alternation; `is_synthetic_message()` detects them; `get_last_real_user_prompt()` finds real user boundaries; synthetic messages excluded from consecutive-role validation; `TauContext.append_synthetic_user()` and `TauContext.cleanup_synthetic()` are public methods for synthetic message operations; `cleanup_synthetic()` removes bridges WITHOUT merging (merge is explicit via `merge_consecutive_assistants()`); **utility functions (`is_synthetic_message`, `make_synthetic_user`, `get_last_real_user_prompt`, `_sanitize_text`, `_sanitize_content`, `_SYNTHETIC_PREFIX`) moved to `agent_message_utils.py` — `agent_context.py` re-exports for backward compatibility** | Structured, consistent recovery; prevents context pollution; proper encapsulation |
| 18.6 | **OpenAI alternation INVARIANT** — `system → user ↔ assistant ↔ tool` **[RLM: no tool role — only `system → user ↔ assistant`]**; consecutive same-role messages are FORBIDDEN; synthetic bridges are NON-NEGOTIABLE (removing them breaks API compliance); any change bypassing bridges MUST prove alternation is maintained and pass all tests in `test_context_synthetic_bridge.py` + `test_recover_invalid_end_of_turn.py` | Prevents architectural oscillation; enforces API contract |
| 18.7 | **Explicit `merge_consecutive_assistants()`** — `cleanup_synthetic()` removes bridges ONLY (no merge); `merge_consecutive_assistants()` is public and caller-controlled; merges **assistant messages** (content, reasoning, refusal — **[RLM: no tool_calls or usage_metadata]**) ; merges **user messages** gracefully (content concatenated, warning logged); `close_turn()` explicitly calls `merge_consecutive_assistants()` after `cleanup_synthetic()` | **Assistant-only merge was expanded to user merge: synthetic bridges are always user-role messages inserted after assistant messages, so removing them can only create consecutive assistant pairs. Consecutive user messages can also appear from tool-result / post-parse edge cases. Graceful merge prevents crashes while logging warnings for debugging.** Explicit merge chosen over auto-merge and no-merge: auto-merge hides symptoms by embedding policy in cleanup; no-merge ignores the alternation problem entirely. Explicit merge separates data cleanup from policy decision: cleanup removes internal synthetic bridges, merge is a visible, auditable caller-controlled step. Prevents architectural oscillation by making the merge decision explicit and documented. |
| 18.8 | **End-turn recovery redesign** — `_recovery_active` flag (reset at start of `invoke_with_tools_loop()`, set in `_recover_from_missing_end_turn()`); `last_substantive_response` only updates when NOT in recovery mode (prevents recovery responses from clobbering the original); empty responses still trigger recovery; reminder includes first 40 chars preview of stored response; **content-only end-of-turn was REVERTED** (broke subagent tests — subagents returned early without calling `end_turn`) | **Recovery lock prevents the "clobbering bug" where recovery responses overwrote the original good response. Preview in reminder gives the model clear context about what end_turn() resolves to, reducing confusion. Content-only end-of-turn was reverted because it caused subagents to short-circuit the turn loop before completing their work.** The recovery mechanism is a "last resort" for cases where the model keeps failing to call `end_turn`. All responses still require explicit `end_turn` (except via `force_end_turn`). |
| 18.9 | **[OBSOLETE in RLM — no tools]** ~~NO syntax examples in LLM-facing messages~~ — all messages sent to the LLM (system prompt, tool metadata, recovery reminders, error messages, synthetic user messages) MUST NOT show Python-style function-call syntax like `tool_name(param='value')`. The LLM learns the tool interface from the JSON schema, not from examples. Showing syntax examples creates a contradiction: AGENT_RLM.md says "never describe tool calls as plain text" but our examples look exactly like plain-text tool calls. The LLM may try to reproduce the shown syntax as text instead of using the native tool-calling interface, causing malformed tool calls. Use natural language instead: "call the end_turn tool with the message parameter" → "call the end_turn tool with the message parameter". Applies to: `AGENT_RLM.md` (system prompt), `tools/end_turn.py` (tool metadata + error messages), `agent_core.py` (recovery reminders), `agent_loop_escalation.py` (escalation messages), `agent_tool_executor.py` (tool errors). Tool error messages describing what the LLM did wrong (e.g., "Original call: tool_name(args)") are acceptable — they describe the error, not teach syntax. | Prevents LLM confusion between natural language instructions and tool-calling syntax; eliminates contradiction with "never describe tool calls as plain text" rule; reduces malformed tool-call output |
| 18.10 | **Escalating end-turn recovery** — `_recover_from_missing_end_turn()` tracks `_cumulative_end_turn_failures` across the entire turn; issues tiered reminders: failures 1-2 (polite reminder), failures 3-4 (critical warning with termination threat), failures 5+ (termination warning demanding immediate `end_turn`); `_cumulative_end_turn_failures` resets at start of each turn; `max_outer_recovery` threshold (5) was NEVER triggered because `outer_recovery_counter` reset to 0 whenever tool calls were present (agent alternated tool calls → plain text → tool calls) | Forceful escalation breaks stubborn verification loops; cumulative counter prevents reset-by-alternation bug |
| 18.11 | **[OBSOLETE in RLM mode]** ~~EOT confirmation stack~~ — EOT logic extracted to `agent_eot_protection.py` (`EOTProtection` class); `TauErgon._eot_protection` instance manages confirmation stack and budget; `EOTProtection.handle_potential_eot()` stacks messages instead of overwriting; `EOTProtection.pop_all_confirmations()` removes all stacked layers at once (both assistant responses and synthetic user messages); `agent_endofturn_validate.py` was removed — implicit structural validation (truncation, unclosed tags, malformed tool calls) was replaced by explicit confirmation-only validation via `EOTProtection.handle_potential_eot()` and `EOTProtection.check_confirmation()` | Stacking preserves full context of the confirmation exchange; prevents losing previous held messages when the LLM keeps responding with plain text; simpler context management (no need to track "first" vs "subsequent" confirmations); removing implicit validation simplifies the codebase and reduces false positives; **EOTProtection extraction** follows established pattern (`agent_init.py`, `agent_session.py`) for isolating subsystems from the god class |
| 18.12 | **[OBSOLETE in RLM mode]** ~~Restricted nesting types (T/K) bypass EOT confirmation~~ — `TauErgon._is_restricted_nesting()` returns True for 'T' (think) and 'K' (skill) nesting types; these types accept plain text responses as end-of-turn WITHOUT requiring a sentinel; rationale: think/skill forks are bounded computations that should terminate cleanly, not open-ended conversations; the EOT confirmation dance wastes LLM calls and tokens in these restricted contexts; **OpenAI alternation is maintained** — `close_turn()` appends the assistant message before closing | Eliminates unnecessary confirmation rounds for bounded turns; reduces latency and token waste; maintains API compliance |
| 18.13 | **Restricted nesting types (T/K) abort loops at Level 1** — `LoopEscalationManager.handle_loop_escalation()` checks `_is_restricted_nesting()`; if True and `escalation_level >= 1` (3+ warnings), immediately sets `force_end_turn` and returns False; skips the full 5-level escalation ladder; uses `last_substantive_response` as the fallback content; rationale: think/skill forks should fail fast, not go through 15+ warnings of escalating interventions | Prevents wasted computation in bounded turns; think/skill forks have limited scope and should terminate quickly if stuck |
| 18.14 | **[OBSOLETE in RLM mode]**  ~~Single EOT sentinel~~ — removed `ENDOFTURN_ALLDONE`, `ENDOFTURN_GIVINGUP`, `ENDOFTURN_` variants; single sentinel assembled from `_ACCIDENTAL_EOT_PREFIX` at runtime; `check_confirmation()` returns `(bool, stripped_text)` instead of `(status_suffix, stripped_text)`; `accept_confirmation()` takes no parameters; confirmation prompt simplified to single sentinel; rationale: three variants added complexity without meaningful semantic distinction (success vs failure both result in same turn closure); single sentinel reduces prompt size, simplifies LLM instructions, reduces test surface area | Eliminates redundant sentinel variants; simplifies confirmation flow; reduces token usage in prompts; cleaner API (no status suffix needed) |
| 18.15 | **User message prefix protocol** — all user messages prefixed with `[U:TYPE | N:stack | M:msgs | C:pct]` format (optional `| B:budget%` for spawned children); `TauContext` stores `nesting_stack` attribute (e.g., `"0"`, `"F"`, `"S"`, `"SF"`); `append_user(content, user_type="real")` auto-prefixes content; `append_synthetic_user(category, content)` maps category to type (`continuation/turn_started/turn_closed` → `meta`, `eot_confirmation` → `confirm`, `parent_inject` → `inject`, `escalation/recovery` → `system`); `is_synthetic_message()` checks for synthetic types (`meta`, `confirm`, `inject`, `system`) vs non-synthetic types (`real`, `fork`, `subagent`, `redirect`); non-synthetic types preserved by `cleanup_synthetic()`; `get_last_real_user_prompt()` filters by non-synthetic messages; rationale: LLM needs context about message source and nesting level; distinguishes fork/subagent/redirect tasks from real user input; maintains OpenAI alternation while providing semantic clarity | Provides LLM with explicit message provenance; enables proper cleanup_synthetic() behavior (preserves fork/subagent/redirect tasks); simplifies debugging and context analysis; ~20-30 char overhead per message is acceptable |
| 18.16 | **NO context recovery — fix root cause, never patch** — `attempt_recovery()` was REMOVED from `agent_context_validation.py`; `TauContext.attempt_recovery()` method removed; `context_recovery_display()` removed from console; recovery synthetic bridges (`"recovery"` category) are DEAD CODE. Context validation errors (consecutive same-role messages, unresolved tool calls, tool→user violations) MUST be fixed at their source: compression must preserve alternation, tool execution must maintain assistant→tool→user ordering, EOT flow must not create consecutive users. **Rationale**: Recovery inserts synthetic bridge messages into the middle of context, which breaks the prefix/KV cache — the server can't reuse cached KV pairs from the insertion point forward. Every recovery insertion shifts the serialized JSON body, causing 0% actual cache hit even when 53% of bytes match. Recovery is a band-aid that masks bugs and destroys cache performance. We always address root cause, never patch over broken context. | Prefix cache preservation; forces proper alternation invariant at every mutation point; eliminates O(N) cache degradation from repeated recovery insertions |
| 18.17 | **SIGINT/Ctrl-C cooperative shutdown (NOT a design change — intentional behavior)** — `AgentLifecycle` class-level flags (`_interrupted`, `_exit_requested`) are shared across ALL agent instances in the same process, including forks and subagents (which run in-process, not subprocess); signal handler registered in `InputHandler._start_input_thread()` implements two-stage shutdown: (1) First Ctrl+C → `set_interrupted(True)` → `close_turn("[Interrupted]")` → `run_loop` returns `None`; (2) Second Ctrl+C → `set_exit_requested(True)` → `close_turn("[Session ended]")` → `run_loop` returns `None`. The `run_loop` function has dual exit-point checks: initial check at top of `while` loop catches exits from previous iteration (e.g. signal received during LLM call), post-control-queue check catches exits from parent supervisor forceful-terminate via A2A control queue. `/exit` command from the LLM is dispatched **within `run_loop`** via `/` prefix detection (`response_text.strip().startswith("/")`) → `_handle_command("exit", ...)` → `set_exit_requested(True)` → `continue` → top-of-loop check catches it. `/exit` from the interactive user is dispatched by `InputHandler._process_input` → same handler. Both paths converge on `_cmd_exit` → `AgentLifecycle.set_exit_requested(True)`. **This behavior is intentional and will NOT be changed** — cooperative shutdown with two-stage interrupt is the correct UX for an agent system. | First interrupt = graceful (let LLM finish current cycle); second interrupt = forceful (abandon immediately); shared class-level flags ensure all in-process forks/subagents respond to the same signal; `/exit` from LLM is a legitimate escape hatch handled in-run-loop, not a separate outer handler |
| 18.18 | **Fork/subagent interruption returns `last_substantive_response`** — when `invoke_fork_sync()` or `invoke_subagent_sync()` detects `run_loop` returned `None` (exit/interrupt), it now returns a descriptive string containing the fork's `last_substantive_response` (if any) instead of `None`; previously `None` propagated through `invoke_with_tools` → `invoke_fork_sync` → fork tool `run()` → `str(None)` = `"None"` as the parent's tool result, which was useless for parent reasoning. The new behavior: if `last_substantive_response` exists, returns `"[Fork/Subagent interrupted — received exit/interrupt signal while working. Last substantive response was: {response}]"`; if no substantive response was produced, returns `"[Fork/Subagent interrupted — received exit/interrupt signal before any substantive response was produced.]"`; the `think` tool benefits automatically since it also calls `invoke_fork_sync`. The delegate command's retry logic is unaffected — it calls `invoke_with_tools` directly (not `invoke_fork_sync`), so `result is None` still triggers retries. | Parent agent can act on the fork's partial work instead of seeing "None"; prevents tool result degradation to `str(None)`; maintains backward compatibility with delegate retry logic; think tool gets the fix for free |
| 18.19 | **[OBSOLETE in RLM mode]** ~~Self-confirming end-of-turn~~ — When the LLM returns plain text ending with the sentinel outside a confirmation round, the turn ends immediately without injecting a confirmation request; the sentinel is stripped and preceding content is used as the final response; audit event `EOT_SELF_CONFIRMED` is emitted; `last_substantive_response` is also stripped of the sentinel to prevent leakage into fallback paths; rationale: eliminates one round-trip (one LLM call + tokens) when the model already intends to end the turn; reduces latency and cost; preserves two-round safety net for ambiguous responses (plain text without sentinel) | Eliminates unnecessary confirmation round for the common case; preserves two-round safety net for ambiguous responses; sentinel stripped from `last_substantive_response` to prevent leakage into budget-exhaustion fallback |
| 18.20 | **`repl_error` maps to type `system`** — `repl_error` is not in `_SYNTHETIC_CATEGORY_TO_TYPE`, so it defaults to type `system`, which IS in `_SYNTHETIC_TYPES`. Therefore `repl_error` messages ARE removed by `cleanup_synthetic()`. Only `repl_output` and `repl_feedback` (type `repl`) survive | Corrects CONTEXT.md which implied all `repl`-type messages survive cleanup |
| 18.21 | **`answer["content"]` initialized to `""`** — `rlm/namespace.py` sets `{"content": "", "ready": False}`, not `content=None`. Code checking `if answer["content"] is None` will never trigger | Corrects CONTEXT.md key invariants section |


## Configuration (4)

| # | Decision | Rationale |
|---|----------|-----------|
| 19.1 | **`LLMGroup` for multi-LLM support** — named groups with independent model, api_base, params; switchable via `--llm` CLI flag or `/llm` command | Flexible deployment |
| 19.2 | **Environment variable overrides** (`TAU_*` prefix) — all config keys overridable via environment variables | Deployment flexibility |
| 19.3 | **Config resolution order** — `tau.json` → env overrides → dataclass defaults | Predictable precedence |
| 19.4 | **`PathSecurityConfig`** — configurable path whitelist for sandbox validation via `allowed_paths` in config | Flexible sandbox boundaries |

## Logging & Disk Management (2)

| # | Decision | Rationale |
|---|----------|-----------|
| 20.1 | **No log rotation** — audit logs and context files are never rotated, deleted, or compressed by the system | Simplicity, no data loss risk, operator responsibility |
| 20.2 | **[OBSOLETE in RLM — no tools]** ~~Oversized tool output to disk~~ — `write_oversized_output()` stores large outputs in `LOG_DIR`, context stays small | Disk backup, token economy |

## Model Health (3)

| # | Decision | Rationale |
|---|----------|-----------|
| 21.1 | **`ModelHealthMonitor` circuit breaker** — `agent_model_health.py` tracks LLM server health via `CircuitState` (closed/open/half_open); blocks calls when circuit is open, tests recovery in half_open state | Prevents cascading failures during server outages |
| 21.2 | **`HealthStatus` counters** — tracks consecutive failures/successes, total counts, last error timestamps; configurable thresholds via `HealthMonitorConfig` (failure_threshold=5, success_threshold=3, recovery_timeout=30s) | Granular health observability |
| 21.3 | **`get_health_monitor()` singleton** — per-`base_url` monitor instance; dashboard export to disk | Centralized health tracking without global state |

## Phantom Detection (3) [OBSOLETE in RLM — no tool calls]

| # | Decision | Rationale |
|---|----------|-----------|
| 22.1 | **Phantom tool call detection** — `agent_phantom_detect.py` detects tool-call-like XML tags that postparse missed; raises `InvalidReplyError` to trigger retry | Catches LLM-generated fake tool calls before they pollute context |
> **Note:** `agent_phantom_detect.py` was later removed from the codebase (see §29.1). This entry is historical.

| 22.2 | **Configurable rules via `phantom_rules.json`** — `PhantomRules` dataclass: suffix/prefix patterns, command keywords, whitelist tags, confidence threshold; loaded from file with graceful fallback | Adaptable detection without code changes |
| 22.3 | **Levenshtein scoring** — `_score_phantom()` computes edit distance against known tool names; confidence threshold filters false positives | Precision over recall for phantom detection |

## Privacy & Anonymization (2)

| # | Decision | Rationale |
|---|----------|-----------|
| 23.1 | **No personal information in project files** — skills, source code, task files, documentation, and all other project artifacts must NEVER contain real timestamps, user names, email addresses, personal paths, or any identifying information outside the project | Privacy protection; project remains shareable and anonymous |
| 23.2 | **`$HOME` over literal paths** — use `$HOME` or relative paths instead of literal `/home/<user>/...` paths; no personal username should appear anywhere in the codebase | Path portability; eliminates user identity from codebase |

## Audit Logging (12)

| # | Decision | Rationale |
|---|----------|-----------|
| 24.1 | **Structured audit log format** — `[TIMESTAMP] RECORD_TYPE stack=SS field1=value1 field2=value2` with `  | ` continuation lines; human-readable, machine-parseable, append-only | Single source of truth for debugging, post-mortem, and LLM learning |
| 24.2 | **NEVER TRUNCATE** — all content logged in full; no character/byte/line limits on audit records | Complete fidelity for post-mortem analysis |
| 24.3 | **NEVER ROTATE** — audit file grows for session lifetime; no rotation/archival/compression | Simplicity, no data loss risk, operator responsibility |
| 24.4 | **NEVER REVERT** — append-only; once written, immutable; corrections are new records | Audit trail integrity |
| 24.5 | **`AuditWriter` with buffering** — in-memory buffer (capped at 10000 lines) flushed via `open(file, "a")` + `write()`; thread-safe with `threading.Lock`; graceful degradation retains buffer on write failure | Concurrency safety, no data loss on transient disk errors |
| 24.6 | **`ErrorRateTracker`** — thread-safe sliding window error rate tracking with burst detection and alert thresholds | Proactive error monitoring without external dependencies |
| 24.7 | **Console-to-audit bridging** — `agent_audit_bridge.py` is the SINGLE entry point for all audit writes (no circular imports); `console_error/warning/info/success()` delegate to audit writer via `_safe_writer_call()`; `emit_console_warning()` delegates to registered callback | Unified observability, acyclic dependency graph |
| 24.8 | **Nesting stack attribution** — `stack=SS` on every record (e.g., `stack=.`, `stack=S`, `stack=SS`); set via `set_nesting_stack()` before each agent runs; identifies which agent wrote each record | Traceability across fork/subagent boundaries with human-readable attribution |
| 24.9 | **Fork audit inheritance** — `TAU_PARENT_AUDIT_FILE` env var allows forks to inherit parent's audit file path; `TAU_FORK_NESTING` sets initial nesting level | Continuous audit trail across process boundaries |
| 24.10 | **Dual API abandoned** — v2 methods (`user_manual`, `user_synthetic`, `assistant_response`, `compress_result`, etc.) were created for richer metadata but never wired into production code. Removed as dead code. The v1 API (`user`, `assistant`, `compress_action`) remains the sole interface | Migration path was abandoned; v2 was dead code from creation |
| 24.11 | **`agent_console/audit_display.py` parser** — regex-based parser with `AuditRecord` dataclass; supports `short`/`long`/`full` display modes; stream-safe iterator for large files | Flexible audit log consumption |
| 24.12 | **Audit file path resolution** — priority: explicit parameter → `TAU_PARENT_AUDIT_FILE` (fork) → `TAU_AUDIT_LOG_FILE` (env) → `LOG_DIR/{SESSION_PREFIX}.audit` (default) | Predictable file location with fork inheritance |

## Dream Orchestration (8)

| # | Decision | Rationale |
|---|----------|-----------|
| 25.1 | **Dream orchestrator** (`commands/_dream.md`) — self-improvement loop: enables heartbeat, runs 8-step cycle (tasks, re-arch, tests, skills, docs, logs, wiki); endless loop until critical problem | Autonomous self-improvement |
| 25.2 | **Dream cycle steps (8, ordered)** — process tasks → re-arch (x3) → test commands → test sanity → skill maintenance → doc sync → log review → wiki maintenance | Comprehensive self-improvement pipeline |
| 25.3 | **Task lifecycle folders** — `tasks/1_todo/` (waiting), `tasks/2_inprogress/` (active), `tasks/3_done/` (completed), `tasks/3_failed/` (failed) | Clear state machine for task processing |
| 25.4 | **Task ordering by filename** — `sorted(glob("1_todo/*.md"))` processes tasks in lexicographic order; `TASK_##.md` naming ensures numerical ordering | Predictable execution sequence |
| 25.5 | **`queue.sh` helper** — auto-generates sequentially numbered task files in `1_todo/` | Task creation convenience |
| 25.6 | **Internal `_tau*` commands** — `/_taudotask`, `/_taurearch`, `/_tautestcommands`, `/_tautestsanity`, `/_tauskillmaintenance`, `/_taudoc`, `/_taulogreview`, `/_tauwiki`; NEVER invoked directly, only via dream.py or `/_dream` | Encapsulated automation pipeline |
| 25.7 | **Dream.py programmatic orchestrator** — handles deterministic ops (file ops, git, testing, timeout, logging); invokes `tau.py` only for LLM-driven work | Separation of deterministic and LLM work |
| 25.8 | **Dream critical rule** — Tau must NEVER perform dream tasks on its own; Dream is ONLY invoked through `dream.py` or `/_dream` | Prevents uncontrolled self-modification |

## Testing & Quality Gates (4)

| # | Decision | Rationale |
|---|----------|-----------|
| 26.1 | **sanity.sh is the gold standard — MUST pass 100%** — `sanity.sh` is the only automated gate between broken code and production; tests CLI, positional args, tool calling, fork functionality, continue command, and other critical paths; **zero failures, zero exceptions, no partial passes**; "pre-existing error" or "not caused by current edits" is **NOT a valid excuse** — if sanity.sh fails, STOP ALL OTHER WORK and fix the root cause; you cannot move forward on ANY task until sanity.sh passes 100% | sanity.sh is the only end-to-end verification; a single failure means something fundamental is broken; patching around failures or blaming the model compounds technical debt |
| 26.2 | **NEVER modify sanity.sh tests, prompts, or expectations** — tests are deliberately crafted and immutable; when sanity.sh fails, the correct response is: investigate the failure, find the root cause in the code, fix the code, re-run; **never assume model failure** — model failures are symptoms, not root causes; always investigate the code path that caused the failure | Immutable tests ensure consistent verification; changing tests to make them pass hides real bugs; model failures are often caused by code issues (context corruption, malformed tool schemas, etc.) |
| 26.3 | **[OBSOLETE in RLM — no tools]** ~~AGENT_RLM.md must NEVER reference slash commands (`/fork`, `/status`, etc.)** — slash commands are CLI-level constructs for human users; the agent uses tools (e.g., `fork`, `subagent`, `bash`); if the agent outputs `/fork` as text, it will NOT be executed — it will just be plain text; AGENT_RLM.md must use explicit tool references only | Slash commands are parsed by the CLI before reaching the agent; the agent has no access to slash commands; referencing them in the system prompt causes the model to output invalid text instead of using the tool-calling interface |

| 26.4 | **`sanity.sh` is the canonical e2e entry point** — `tau-sanity.py` is a Python wrapper; `sanity.sh` is the shell script that orchestrates the full suite. All docs reference `sanity.sh`. | Single source of truth for e2e verification; avoids ambiguity between two entry points. |
| 26.5 | **Test suite reorganized** — `tests/` contains 43 test files (post-RLM). `tests_legacy/` contains 26 files (pre-RLM). sanity.sh has 9 tests (Test 1–9): stdin pipe, positional args, file I/O, /help, unknown command, /agent, .md multiprompt, /ctx. No spawn, continue, A2A, or loop-detection tests in sanity.sh | Supersedes §26.1–26.4 counts and coverage descriptions |


## Context Sequence Rules (5)

| # | Decision | Rationale |
|---|----------|-----------|
| 27.1 | **Strict OpenAI message alternation** — Context MUST always follow: `system → user → assistant → (tool)* → assistant → user → ...` cycle. No exceptions. Tool results must always be followed by an assistant message before any user message. **[RLM: no tool role — sequence is `system → user ↔ assistant` only]** | OpenAI API enforces this; violating it causes 400 errors or undefined model behavior |
| 27.2 | **No synthetic bypass** — Synthetic messages do NOT bypass alternation rules. If context ends with tool results, a synthetic assistant message MUST be added before any synthetic user message. The validation rule `last_role == "tool" and role == "user"` applies to ALL messages, synthetic or not. **[RLM: tool results never occur — bridge is only needed after user messages]** | Synthetic messages are system-injected but still must maintain valid alternation; bypassing rules creates fragile code that breaks when validation changes |
| 27.3 | **Full bridge requirement** — When injecting a synthetic user message after tool results OR after any user message (real or synthetic), always add a synthetic assistant bridge first. **[RLM: only "after user message" case applies]** The bridge can be minimal (e.g., `"[Processing new input...]"`) but MUST be present. Use `append_synthetic_user_with_bridge()` for atomic correctness. | Maintains alternation compliance without manual bookkeeping; prevents context corruption; eliminates consecutive user message warnings |
| 27.4 | **Root cause fixes only** — Never fix symptoms by relaxing validation rules. Always fix the code that creates invalid context. The goal is to never break context in the first place. If validation fails, the caller is at fault, not the validator. | Symptom fixes compound technical debt; root cause fixes prevent future bugs |
| 27.5 | **Bridge helper method** — `TauContext.append_synthetic_user_with_bridge()` appends both the assistant bridge and user message atomically, ensuring alternation compliance. All callers that inject synthetic user messages should prefer this helper. | Single source of truth for bridge logic; prevents caller mistakes |

## Orchestrate & Manifests (6) [OBSOLETE in RLM — no orchestrate tool]

| # | Decision | Rationale |
|---|----------|-----------|
| 28.1 | **Goal-driven delegation with accountability** — `orchestrate` tool spawns a fork to prepare a manifest + context, then runs an executor loop that delegates subtasks via subagent/fork; blocks exit until manifest is complete/failed; manifest tracks goals, success criteria, subtasks, and progress | Structured delegation with verifiable outcomes; prevents abandoned work |
| 28.2 | **Manifest file format** — YAML frontmatter in `.tau/manifests/` with fields: `id`, `title`, `goal`, `depth`, `parent`, `success_criteria`, `subtasks`, `progress`, `status`; `ManifestEntry` dataclass in `tools/lib/manifest.py` provides `read_manifest_frontmatter()` and `load_manifests()` | Machine-readable task tracking; enables auto-verification and resume mode |
| 28.3 | **Fork phase for investigation** — `orchestrate` spawns a fork with `FORK_INSTRUCTIONS` to investigate the codebase and create a manifest + `.ctx` file; fork output is parsed to extract manifest path; manifest is validated before executor phase | Fork inherits full context for informed investigation; manifest creation is isolated from execution |
| 28.4 | **Executor phase with tool restrictions** — `_ALLOWED_ORCHESTRATE_TOOLS` allowlist restricts executor to delegation + manifest management + read/analysis tools; all tools still announced (prefix cache preserved); non-allowed tools blocked at execution time with denied message | Safety + cache safety; executor cannot modify code directly, only delegates |
| 28.5 | **Resume mode** — `resume` parameter accepts manifest path; auto-resume detects one executing manifest matching the goal; skips fork phase and jumps directly to executor | Interrupted orchestration can be resumed without re-investigation; preserves work state |
| 28.6 | **Auto-verification** — `manifest_update` tool runs `Verify:` commands for completed subtasks; tracks retry state in `.state.json` file; `_VERIFY_ALLOWLIST` restricts verification to safe commands | Completed work is automatically verified; prevents false completions; retry tracking enables resilience |


## Spawn v2 (2026-09-12)

| # | Decision | Rationale |
|---|----------|-----------|
| 6.20 | **Single spawn() API** — no subagent/fork. Always persistent. Returns SpawnHandle | Simpler mental model, one API to learn |
| 6.21 | **B: work budget** — only decreases. Compression does not restore. B_start = budget - C_start | Measures work done, not space left. Solves fork inheritance issue |
| 6.22 | **5 exit statuses** — completed, yielded, budget_exhausted, error, closed. answer['yield']=True for voluntary pause | Clear state machine, child can request extension |
| 6.23 | **Max 5 global spawns, max nesting depth 3** | Prevents resource exhaustion |
| 6.24 | **list_spawns() returns handles. get_spawn(name) for direct access** | Full control interface |
| 6.25 | **No timeout** — budget is the bound | Trust the system, fewer async activities |
| 6.26 | **Live output** via _with_real_stdout | User sees child progress in real-time |
| 6.27 | **resume() not continue()** — Python keyword limitation | Language constraint |
| 6.28 | **_with_real_stdout for both spawn paths** | Persistent spawn was missing stdout swap (bug) |
| 6.29 | **REPL execution timeout via SIGALRM (180s default)** — synchronous, single-threaded, interrupts any blocking call (sleep, subprocess, socket, input). Bash uses subprocess timeout, Python uses SIGALRM. Same 180s default for both. | Prevents infinite hangs, preserves single-threaded model, no watchdog threads |

## Removed Modules (2026-09-12)

| # | Decision | Rationale |
|---|----------|-----------|
| 29.1 | **`agent_phantom_detect.py` removed** — phantom detection no longer exists in codebase; §22.1–22.3 are historical | RLM mode has no tool calls, making phantom detection inapplicable |
| 29.2 | **`commands/health.py` removed** — health command no longer exists in codebase; §9.14 is historical | Health monitoring folded into other subsystems or removed |
| 29.3 | **Additional removed modules confirmed (2026-09-12 audit)** — `commands/ralph.py`, `commands/plan.py`, `commands/health.py`, `commands/_dream.md`, `queue.sh`, `ErrorRateTracker`, `PathSecurityConfig`, `LoopDetectionConfig`, `make_synthetic_user()`, `get_last_real_user_prompt()`, `_recovery_active`, `_recover_from_missing_end_turn()`, `_cumulative_end_turn_failures`, `max_outer_recovery`, `invoke_fork_sync()`, `invoke_subagent_sync()`, `set_nesting_stack()`, `sustained_warning_cooldown` — none exist in codebase | DECISIONS.md audit: 18 decisions reference non-existent code; new decisions added in respective categories to supersede |

## Compression Gate (2026-09)

| # | Decision | Rationale |
|---|----------|-----------|
| 30.1 | **Single unconditional 85% compression gate** — `validate_and_compress()` compresses whenever live context usage is >= 85% of the window, every turn it applies. No cooldown, no separate 95% hard gate. | The old 5-turn cooldown existed only to avoid a compress retry-storm, but a successful compress drops usage below 85% so the storm is rare; the pathological case (oversized block / system-prompt floor above 85%) is exactly when we WANT to keep trying. An unconditional 85% gate subsumes the 95% gate, so the second gate was dead weight. Removing the cooldown also deletes the per-prompt `_compress_last_turn` reset and its bug class (compression could never re-fire after a stale reset). |
| 30.2 | **Gate reads a LIVE estimate, not cached `last_exact_context_tokens`** — computes `estimate_tokens()` + reserved output (`max_tokens`/`DEFAULT_MAX_OUTPUT_TOKENS`) fresh each turn. | `last_exact_context_tokens` is captured after the *previous* LLM response, before the newest tool result lands — exactly the delta that overflows the window. Trusting it let a child reach 128% (a real provider 400). The gate must reflect current reality. |
| 30.3 | **Compressor receives the reserve-FREE prompt count** — gate decides on `prompt+reserve` but passes `current_tokens=prompt_tokens` to `compress_to_target`. | `compress()` subtracts the output budget again in its target math; passing the reserve-inclusive count would double-count it and over-compress ~11 points of history per pass. Decision uses reserve (does prompt+response fit?), target math does not. |
| 30.4 | **Token estimator is conservative (`//3`, counts tool-call payloads)** — `_estimate_text_tokens` uses ~3 chars/token and sums `tool_calls[].function.arguments`/`name`. | The gate is now load-bearing, so the estimator must not under-count. Real tokenizers are ~3.5-4 chars/token for prose; `//4` under-counted (unsafe for a gate). Tool-call payloads are the largest per-message growth source and were previously invisible to the estimator. Over-counting biases the gate to fire early = safety margin. |


## Reconciliation (10)

| # | Decision | Rationale |
|---|----------|-----------|
| 10.1 | **Canonical form wins on normalisation, never a silent drop** — every messy-but-parseable value is normalised (ids upper-cased, dates to ISO-8601 UTC, quantities to numeric kW/kWh, periods to YYYY-MM, flags to boolean) and logged as `*.CANONICALISED` with raw + canonical values | Nothing is silently "fixed"; the anomaly log is a complete audit trail of every shape change |
| 10.2 | **Device exclusion is limited to identity/status/date integrity** (`DEVICE_DISQUALIFYING_RULES`); capacity problems are reported but do NOT remove a device from the active count | A corrupt capacity makes the capacity sum unreliable, not the active-device count — conflating them would let a bad `capacity_kw` change a headline device count |
| 10.3 | **Conflicting duplicates never get an arbitrary winner at device level** — a device with disagreeing duplicate rows is EXCLUDED and the disagreeing fields are named in the report; at reading/line level the first occurrence is kept and the disagreement logged | Picking one row would invent history for an asset register we do not own; exclusion is auditable, silent selection is not |
| 10.4 | **The count gap decomposes and always adds up** — raw active-looking rows = counted devices + `duplicate_rows_collapsed` + `disqualified_active_rows` + `keyless_active_rows` | The "why doesn't the number match" question must answer itself from the report, with no unexplained residual |
| 10.5 | **Ambiguity is rejected, not guessed** — a slash date where both day-first and month-first readings are valid calendar dates is excluded (`DATE_AMBIGUOUS`/`TIMESTAMP_AMBIGUOUS`); unambiguous ones (`15/04/2019`) normalise normally | A wrong-but-plausible date is worse than a flagged gap; guessing is only safe when one reading is impossible |
| 10.6 | **Cross-source entity sets are reported, not silently intersected** — telemetry-only, billing-only and register-only devices are separate populations | The active-count mismatch is often an entity-population gap (a billed device that isn't in the register), which an inner join would erase |
