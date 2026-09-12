---
name: ml
description: 'PyTorch here (torch 2.13+cu130 installed): weights_only semantics, correct CUDA memory attribute names, dtype/device footguns, GPU selection via CUDA_VISIBLE_DEVICES. Use for tensor ops, model loading, inference, shape/dtype debugging. Keywords: pytorch, torch, tensor, cuda, gpu, dtype, device, inference, weights.'
category: ml
keywords: 'pytorch, torch, tensor, cuda, gpu, dtype, device, inference, weights'
---

# ML / Torch Workflows (non-obvious only)

Env: torch 2.13.0+cu130 installed. Generic device/no_grad/shape-print knowledge omitted.

## Footguns that actually bite
- `torch.load`: since torch 2.6 `weights_only=True` is the DEFAULT — legacy checkpoints with pickled objects now FAIL; fix is `weights_only=False` only for trusted files (arbitrary-code risk), better: re-save clean state dicts.
- CUDA memory attrs: `get_device_properties(0).total_memory` — `total_mem` does NOT exist (AttributeError). Live usage: `torch.cuda.memory_allocated()` vs `torch.cuda.max_memory_allocated()` (peak — the number you want for batch sizing).
- `torch.cuda.empty_cache()` frees CACHED blocks only, not tensors still referenced — an OOM from a live tensor needs `del t` first.
- dtype: `nn.Module` has NO `.dtype` attribute (AttributeError) — read `next(model.parameters()).dtype` and cast inputs to it: `x = x.to(next(model.parameters()).dtype)`. dtype mismatch is LOUD (`RuntimeError: mat1 and mat2 must have the same dtype`); the SILENT one is fp16 overflow -> `inf`/`nan` loss: check `torch.isfinite(out).all()`.
- GPU selection for subprocesses: `CUDA_VISIBLE_DEVICES=2` env var (see **training**) — torch sees re-indexed devices (cuda:0 = physical 2).
- NaN debugging: `torch.autograd.set_detect_anomaly(True)` pinpoints the first NaN-producing op (slow; debug only).

## Related Skills
- `training` — full runs, checkpoints, GPU utilization
- `model-serving` — deploying for inference (sglang, dflash)
- `debug` — general diagnosis
