---
name: model-serving
description: 'Model serving: sglang/dflash/qwen3 deployment, health checks, GPU monitoring, scaling, benchmarking. Use for deploying LLMs, checking inference performance, managing GPU resources. Keywords: sglang, dflash, qwen, llm, inference, gpu, benchmark, deployment, health.'
category: ml
keywords: 'sglang, dflash, qwen, llm, inference, gpu, benchmark, deployment, health'
---

# Model Serving

Environment check FIRST: sglang/dflash are NOT installed in the default env (verify:
`python -c 'import sglang'`, `which dflash`). TauRLM itself just consumes HTTP endpoints
via `--llm GROUP` config — this skill is for when you must stand up the server yourself.

## Health Check

    import requests, subprocess

    def check_serving(url="http://localhost:8000", timeout=5):
        try:
            r = requests.get(f"{url}/health", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def gpu_status():
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
        return r.stdout.strip()

## Starting a Server (sglang example)

    # Use tmux for long-running servers
    cmd = ("tmux new-session -d -s sglang "
           "'python -m sglang.launch_server "
           "--model-path /models/qwen3-8b "
           "--port 8000 --tp 1'")
    subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL, timeout=10)

    # Verify startup (poll)
    import time
    for i in range(30):
        if check_serving():
            print("Server ready")
            break
        time.sleep(2)

## Benchmarking
Serial `/v1/completions` timing is a smoke test, not a benchmark (no concurrency). For real
numbers use the server's own bench tool (`python -m sglang.bench_serving ...`) — it measures
concurrent throughput; report req/s AND p50/p99 latency, serial-only numbers hide queueing.

## Scaling & Restart
- Check `gpu_status()` before starting new instances
- Kill old: `tmux kill-session -t sglang`
- Restart with new config
- Log to tmux: `tmux capture-pane -t sglang -p | tail -20`

## NEVER
- Start a server without checking GPU availability first
- Run multiple TP instances on the same GPU
- Kill a serving process without checking for in-flight requests

## Related Skills
- **docker** — containerized serving
- **training** — weights source; NEVER co-locate training + serving on one GPU
- **wiki** — endpoint/alias conventions (`wiki.search("llmproxy")`)
- `environment` — project config, .venv interpreter, TAU_ env vars.
