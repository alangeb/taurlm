---
name: docker
description: 'Docker in this environment: subprocess wrapper pattern (DEVNULL+timeout), GPU passthrough flags, compose stacks, safe cleanup order, what NOT to prune. Use for container builds/services/isolation when tmux is insufficient. Keywords: docker, containers, compose, gpu, images, cleanup, isolation.'
category: operations
keywords: 'docker, containers, compose, gpu, images, cleanup, isolation'
---

# Docker Operations

Docker when tmux isn't enough (isolation, GPU, networking); tmux for host long-runners/interactive. Generic build/run/logs knowledge omitted — the footguns and patterns below are the point.

## Wrapper pattern (always DEVNULL + timeout, fold output)

    import subprocess
    def docker(*args, check=True, timeout=60):
        r = subprocess.run(["docker", *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args)}: {r.stderr}")
        return r.stdout.strip()
    docker("ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}")
    docker("logs", "--tail", "50", "myapp")   # never bare `docker logs` — floods context

Compose: same wrapper via `["docker","compose",...]` (`up -d` / `ps` / `logs --tail 30` / `down`); give `up` timeout>=120.

## GPU passthrough

    docker("run", "--gpus", "all", "-e", "NVIDIA_VISIBLE_DEVICES=0",
           "-v", "/data:/data", "training-image:latest")

## Cleanup order (least to most destructive)
1. `docker container prune -f` (stopped only)
2. `docker image ls` THEN `docker image prune -f`
3. `docker volume ls` — volumes hold data; remove individually, never blindly

## NEVER
- `docker system prune -a` without listing what it removes
- Remove volumes without verifying no data loss
- `--privileged` unless explicitly required
- Prune/system-prune on THIS host: `docker ps` shows shared serving containers you do NOT own (e.g. `llm-haproxy`, `*-offload-live`, GPU boxes) — `container prune -f` / `system prune` nukes them. Only stop/remove containers you started this session.
- No `Dockerfile`/`docker-compose*` exists in this repo — docker here is for ad-hoc isolation/GPU runs, not building the project.

## Related Skills
- **model-serving** — containerized model deployment
- **training** — GPU training in containers
- `environment` — project config, .venv interpreter, TAU_ env vars.
