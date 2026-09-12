# SPEC v3 FINAL: Streaming LLM API Migration

## Key Facts (Verified)
- All LLM calls: stream=False, single JSON read
- Display: raw colored lines {color}{line}\033[0m\n (NO prefixes in RLM loop)
- Reasoning color: \033[38;5;109m, Content color: \033[92m
- record_call_stats(resp.stats) called in pipeline
- Status line in display_status.py:print_context_status()
- Compression: stream=False, already hidden
- Tests mock at chat.completions.create() level
- vLLM servers, OpenAI-compatible SSE format

## Implementation Plan

### File 1: agent_llm_client.py
Add method to SimpleOpenAIClient:
  chat_completions_create_stream(**kwargs) -> Iterator[dict]
  - Same URL, headers, request logging, cache tracking
  - kwargs['stream']=True, kwargs['stream_options']={'include_usage':True}
  - urlopen with timeout (per-read inactivity)
  - Read line by line, parse SSE:
    - 'data: {json}' or 'data:{json}' -> yield json.loads(payload)
    - 'data: [DONE]' -> stop
    - ':', 'event:', 'id:', 'retry:', empty -> skip
    - Handle CRLF
  - Mid-stream errors propagate

Also add to SimpleChatCompletion:
  create_stream(**kwargs) -> delegates to client.chat_completions_create_stream

### File 2: agent_llm_models.py
Add to CallStats:
  stream_duration: float = 0.0
  first_token_time: float = 0.0
  tokens_generated: int = 0
  tg_tps: float = 0.0
  ttft: float = 0.0  # time to first token

Add to LLMCallConfig:
  on_token: Callable | None = None
  on_reasoning: Callable | None = None
  hidden: bool = False

### File 3: agent_llm_invoke.py
New function: _invoke_llm_streaming(client, model_name, messages, config) -> LLMResponse
  - Build kwargs via _build_call_kwargs with stream=True
  - Call client.chat.completions.create_stream(**kwargs)
  - Accumulate deltas: content, reasoning, finish_reason, usage
  - Call config.on_token / config.on_reasoning per delta
  - Track timing: start, first_token, end
  - Compute tg_tps, ttft
  - Early abort: check StreamAbortChecker per chunk
  - Build synthetic Response for _extract_call_stats
  - Return LLMResponse

Modify _invoke_llm_with_retry:
  - When stream=True: call _invoke_llm_streaming
  - Retry only if zero tokens displayed (track via counter)
  - Pre-stream errors (400, 401): same retry as before
  - Mid-stream errors: NO retry, raise

### File 4: agent_pipeline.py
Modify call_llm:
  - Create _StreamDisplay instance
  - Set config.on_token = display.on_token
  - Set config.on_reasoning = display.on_reasoning
  - Set stream=True in _invoke_llm_with_retry call
  - After call: display.finalize() handles all output
  - display_llm_response removed entirely (was dead code after streaming migration)

New class: _StreamDisplay
  - __init__(hidden=False)
  - State: _reasoning_buf, _content_buf, _phase, _reasoning_done
  - on_reasoning(token): buffer, flush complete lines in REASONING color
  - on_token(token): if in reasoning phase, flush+newline. Buffer, flush lines in GREEN
  - finalize(): flush remaining, newline, single audit call
  - _flush_lines(buf, color): write complete lines, keep partial in buffer
  - hidden mode: no display, just accumulate

### File 5: agent_console/primitives.py
Add:
  stream_write(text, color): write colored text to stdout
  stream_flush(): flush stdout
  stream_audit(text): single audit call

### File 6: agent_loop_detect.py
Add StreamAbortChecker:
  - __init__(max_tokens=0)
  - feed(token): add to window
  - should_abort() -> (bool, reason)
  - Checks: repeat token 5x, repeat bigram 3x, error patterns in first 50 chars

### File 7: agent_token_tracker.py
Modify record_call_stats to also store:
  self.last_tg_tps = stats.tg_tps
  self.last_ttft = stats.ttft

### File 8: agent_console/display_status.py
Modify print_context_status:
  - Add t/s display if available: | {tg_tps:.1f} t/s | ttft: {ttft:.2f}s

### File 9: agent_models.py (AgentStatus)
Add fields:
  last_tg_tps: float = 0.0
  last_ttft: float = 0.0

### NOT Modified:
- agent_context_compress/* (stream=False)
- agent_audit_writer.py
- agent_core.py, agent_loop.py
- agent_console/templates.py

## Testing
1. Unit: SSE parser edge cases
2. Unit: Delta accumulation
3. Unit: StreamAbortChecker
4. Unit: _StreamDisplay colors/state
5. Unit: CallStats computation
6. Integration: Mock SSE server
7. Integration: Partial stream failure
8. Integration: Compression unchanged
9. Integration: Audit records
10. Manual: tau.py with real LLM


## Design Decisions

### DD-1: Partial Stream Retry Shows Duplicated Content (Accepted)
When a mid-stream failure occurs after tokens have been displayed, the retry
prints a yellow warning and re-invokes the LLM. The partial output from the
failed attempt is NOT retracted from stdout, so the user sees:

1. Partial content (from failed attempt)
2. Yellow RETRY warning
3. Full content (from successful retry)

This is an accepted tradeoff. Retreating/clearing partial stdout output would
require terminal escape sequences (ANSI cursor-up + erase) that complicate
the display layer and interact poorly with terminal multiplexers, logging
redirects, and non-TTY output. The yellow warning makes the duplication
visible and explainable to the user.
