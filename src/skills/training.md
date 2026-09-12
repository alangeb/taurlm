---
name: training
description: 'Training workflows: launch, monitor, checkpoint, resume, performance tuning, GPU utilization. Use for starting training runs, checking progress, managing checkpoints, optimizing throughput. Keywords: training, checkpoint, gpu, tmux, resume, monitor, throughput.'
category: ml
keywords: 'training, checkpoint, gpu, tmux, resume, monitor, throughput'
---

# Training Workflows

## Launch

    import subprocess, time

    def launch_training(script, gpus="0,1", name="train", extra_args=""):
        cmd = (f"tmux new-session -d -s {name} "
               f"'CUDA_VISIBLE_DEVICES={gpus} python {script} {extra_args}'")
        subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL, timeout=10)
        print(f"Training launched in tmux session: {name}")

    # Check it started
    time.sleep(5)
    r = subprocess.run(["tmux", "capture-pane", "-t", "train", "-p"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print(r.stdout[-500:])

## Monitor

    def training_status(session="train"):
        r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        lines = r.stdout.strip().splitlines()
        for line in lines[-20:]:
            if any(k in line.lower() for k in ['loss', 'step', 'epoch', 'it/s']):
                print(line)

    def gpu_util():
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
             "--format=csv,noheader"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
        print(r.stdout.strip())

## Checkpoint & Resume

    from pathlib import Path
    import glob

    def latest_checkpoint(dir="/checkpoints"):
        cks = sorted(glob.glob(f"{dir}/*/checkpoint-*"))
        return cks[-1] if cks else None

    # Resume: pass --resume_from_checkpoint <path> to your trainer

## Performance Tuning
- **Throughput low?** Check `gpu_util()` — if <80% the bottleneck is the input pipeline, not the math: raise `num_workers`/prefetch first. Raising batch helps only if VRAM headroom exists; grad_accum raises effective batch at flat memory (fewer optimizer steps), reducing it does not speed anything up
- **NaN loss?** Reduce LR, check for bad batches, verify loss scaling (fp16 overflow -> inf/nan: see **ml**)
- **This host's GPUs are NOT idle** — `nvidia-smi` shows serving already resident (e.g. ~92/98 GiB on GPU 0, plus `llm-haproxy`/`*-offload-live` containers). ALWAYS run `gpu_util()` first; launching training onto an occupied GPU OOMs or starves the server.

## NEVER
- Kill training without saving a checkpoint first
- Change hyperparameters mid-run without understanding the impact
- Run training on GPUs that have serving workloads

## Related Skills
- **model-serving** — deploy trained weights (don't share GPUs with it)
- **docker** — containerized training environments
- **debug** — NaN-loss / stall root-causing
- **ml** — torch footguns (`weights_only` default, no `.dtype` on Module, fp16 inf/nan)
