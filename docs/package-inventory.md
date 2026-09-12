# Package Inventory for RLM REPL Kernel

**Generated:** 2026-09-12
**Python Version:** 3.13.5 (GCC 14.2.0)
**Working Directory:** <repo root>

---

## Summary

| Category | Count |
|----------|-------|
| Total pip packages | ~200 |
| stdlib (always available) | ~200+ modules |
| Commonly used (informational only) | ~30 |
| Opt-in (safe but heavy/specialized) | ~20 |
| ~~Blocked (security risk)~~ | ~~~15~~ (REJECTED — no blocking) |
| NVIDIA/CUDA (GPU-specific) | ~25 |

---

## Python Stdlib Modules (Always Available)

These are part of Python 3.13 stdlib and are always available in the REPL:

### Core
`builtins`, `abc`, `codecs`, `collections`, `contextlib`, `dataclasses`, `enum`, `functools`, `importlib`, `inspect`, `io`, `itertools`, `operator`, `os`, `pathlib`, `re`, `shutil`, `string`, `sys`, `textwrap`, `time`, `types`, `typing`, `unittest`, `weakref`

### Data & Serialization
`json`, `pickle`, `csv`, `html`, `xml`, `configparser`, `tomllib`, `struct`, `array`, `base64`, `binascii`, `hashlib`, `hmac`, `secrets`

### Math & Science
`math`, `cmath`, `decimal`, `fractions`, `random`, `statistics`

### Concurrency
`threading`, `multiprocessing`, `concurrent.futures`, `asyncio`, `queue`, `socket`, `select`, `selectors`

### System & OS
`subprocess`, `signal`, `errno`, `stat`, `tempfile`, `glob`, `fnmatch`, `linecache`, `tokenize`, `dis`, `traceback`, `warnings`, `logging`

### Network
`http`, `urllib`, `email`, `mime`, `smtplib`, `ftplib`, `imaplib`, `poplib`, `xmlrpc`

### Testing
`doctest`, `pdb`, `profile`, `cProfile`, `timeit`, `trace`

---

## Safe for REPL — Default Whitelist (~30 packages)

These packages are safe, commonly useful, and should be available by default:

| Package | Version | Purpose | REPL Use Case |
|---------|---------|---------|---------------|
| `numpy` | 2.5.1 | Numerical computing | Data manipulation, math |
| `pandas` | 3.0.3 | Data analysis | DataFrames, CSV processing |
| `sympy` | 1.14.0 | Symbolic math | Algebra, calculus |
| `scipy` | 1.18.0 | Scientific computing | Statistics, optimization |
| `scikit-learn` | 1.9.0 | Machine learning | ML algorithms |
| `networkx` | 3.6.1 | Graph algorithms | Graph analysis |
| `requests` | 2.32.3 | HTTP client | Web API calls |
| `httpx` | 0.28.1 | Async HTTP client | Async web calls |
| `beautifulsoup4` | 4.15.0 | HTML parsing | Web scraping |
| `PyYAML` | 6.0.3 | YAML parsing | Config files |
| `toml` | 0.10.2 | TOML parsing | Config files |
| `tomlkit` | 0.14.0 | TOML parsing (preserves comments) | Config files |
| `regex` | 2026.7.10 | Advanced regex | Pattern matching |
| `tabulate` | 0.10.0 | Table formatting | Output formatting |
| `rich` | 15.0.0 | Rich text formatting | Pretty output |
| `tqdm` | 4.67.3 | Progress bars | Progress display |
| `joblib` | 1.5.3 | Parallel computing | Parallel execution |
| `psutil` | 7.2.2 | System utilities | System info |
| `platformdirs` | 4.9.6 | Platform dirs | Path utilities |
| `filelock` | 3.29.0 | File locking | Concurrency |
| `chardet` | 5.2.0 | Charset detection | File encoding |
| `python-dateutil` | 2.9.0 | Date parsing | Date handling |
| `pytz` | 2026.2 | Timezone support | Timezone handling |
| `packaging` | 26.2 | Package utilities | Version comparison |
| `click` | 8.3.3 | CLI utilities | CLI building |
| `Jinja2` | 3.1.6 | Templating | Text templates |
| `MarkupSafe` | 3.0.2 | HTML escaping | Safe output |
| `dill` | 0.4.1 | Extended pickle | Serialization |
| `sortedcontainers` | 2.4.0 | Sorted collections | Data structures |

---

## Opt-in — Safe but Heavy/Specialized (~20 packages)

These packages are safe but heavy or specialized — require explicit opt-in in config:

| Package | Version | Purpose | Why Opt-in |
|---------|---------|---------|------------|
| `torch` | 2.13.0 | Deep learning | Heavy (~2GB), GPU-dependent |
| `transformers` | 5.14.1 | Hugging Face models | Heavy, model downloads |
| `sentence-transformers` | 5.6.1 | Sentence embeddings | Heavy, model downloads |
| `tokenizers` | 0.22.2 | Fast tokenization | NLP-specific |
| `huggingface_hub` | 1.15.0 | HF hub access | Network-dependent |
| `datasets` | 4.8.5 | HF datasets | Large downloads |
| `pyarrow` | 24.0.0 | Apache Arrow | Big data |
| `matplotlib` | — | Plotting | Not installed, GUI |
| `Cython` | 3.2.5 | C extensions | Compilation |
| `hypothesis` | 6.154.2 | Property testing | Testing-specific |
| `GitPython` | 3.1.50 | Git operations | Git-specific |
| `black` | 26.3.1 | Code formatting | Dev tool |
| `ruff` | 0.15.11 | Linting | Dev tool |
| `pylint` | 4.0.5 | Linting | Dev tool |
| `mypy` | 1.20.1 | Type checking | Dev tool |
| `isort` | 8.0.1 | Import sorting | Dev tool |
| `pytest` | 9.0.3 | Testing | Testing-specific |
| `coverage` | 7.14.1 | Coverage | Testing-specific |
| `docker` | 7.1.0 | Docker SDK | Docker-specific |
| `modal` | 1.5.0 | Cloud compute | Cloud-specific |

---

## Historical: Proposed Security Restrictions (REJECTED)

> **NOTE:** The following restrictions were proposed but REJECTED. See `docs/trust-model.md`.
> RLM operates on TRUST — there are NO import restrictions, NO function blocklists,
> NO path restrictions, NO subprocess restrictions. The model has full Python access.

The following were originally proposed for blocking:

### Completely Blocked Modules
| Module | Reason |
|--------|--------|
| `os` (partial) | `os.system()`, `os.popen()`, `os.exec*()`, `os.kill()` are dangerous |
| `subprocess` (partial) | `subprocess.call()`, `subprocess.run()` with `shell=True` are dangerous |
| `shutil` (partial) | `shutil.rmtree()`, `shutil.move()` can delete files |
| `ctypes` | Direct C library access — arbitrary code execution |
| `cffi` | Foreign function interface — arbitrary code execution |
| `gc` | Garbage collector manipulation |
| `site` | Site configuration manipulation |
| `sysconfig` | System configuration |
| `venv` | Virtual environment creation |
| `ensurepip` | Pip bootstrap |
| `zipimport` | Zip file imports |
| `runpy` | Run Python modules |
| `importlib.util` (partial) | Dynamic module loading |

### Blocked Functions (within otherwise allowed modules)
| Function | Reason |
|----------|--------|
| `os.system()` | Arbitrary command execution |
| `os.popen()` | Arbitrary command execution |
| `os.exec*()` | Process replacement |
| `os.kill()` | Process killing |
| `os.setuid()` | Privilege escalation |
| `os.setgid()` | Privilege escalation |
| `subprocess.call(shell=True)` | Shell injection |
| `subprocess.run(shell=True)` | Shell injection |
| `shutil.rmtree()` | Recursive deletion |
| `shutil.move()` | File relocation |
| `__import__()` | Dynamic import (use importlib instead) |
| `eval()` | Arbitrary code evaluation |
| `exec()` | Arbitrary code execution (used internally by REPL, but not by model) |
| `compile()` | Code compilation |
| `getattr(builtins, ...)` | Builtins access |
| `sys.exit()` | Process termination |
| `sys.modules` manipulation | Module system tampering |

### Network-Related Restrictions
| Module/Function | Restriction |
|-----------------|-------------|
| `socket` | Allow only HTTP/HTTPS connections |
| `http.client` | Allow outbound only |
| `urllib.request` | Allow outbound only |
| `requests` | Allow outbound only |

---

## NVIDIA/CUDA Packages (GPU-Specific, ~25 packages)

These are NVIDIA/CUDA packages installed for GPU support. They are safe but heavy:

`cuda-bindings`, `cuda-pathfinder`, `cuda-toolkit`, `flash_attn`, `nvidia-cublas`, `nvidia-cuda-cupti`, `nvidia-cuda-nvrtc`, `nvidia-cuda-runtime`, `nvidia-cudnn`, `nvidia-cufft`, `nvidia-cufile`, `nvidia-curand`, `nvidia-cusolver`, `nvidia-cusparse`, `nvidia-cusparselt`, `nvidia-nccl`, `nvidia-nvjitlink`, `nvidia-nvshmem`, `nvidia-nvtx`, `triton`, `safetensors`, `einops`

**Recommendation:** Block by default. Allow only if `torch` is explicitly opt-in.

---

## Historical: Proposed REPL Configuration (REJECTED)

> **NOTE:** The trust model means there is no whitelist. All packages are available.
> See `docs/trust-model.md` for the actual design decision.

### Originally Proposed Default Whitelist:
```json
{
  "rlm": {
    "repl": {
      "allowed_modules": [
        "stdlib",
        "numpy", "pandas", "sympy", "scipy", "scikit-learn", "networkx",
        "requests", "httpx", "beautifulsoup4", "PyYAML", "toml", "tomlkit",
        "regex", "tabulate", "rich", "tqdm", "joblib", "psutil",
        "platformdirs", "filelock", "chardet", "python-dateutil", "pytz",
        "packaging", "click", "Jinja2", "MarkupSafe", "dill", "sortedcontainers"
      ],
      "blocked_modules": [
        "ctypes", "cffi", "gc", "site", "sysconfig", "venv", "ensurepip",
        "zipimport", "runpy", "torch", "transformers", "sentence-transformers"
      ],
      "blocked_functions": [
        "os.system", "os.popen", "os.exec*", "os.kill", "os.setuid", "os.setgid",
        "subprocess.call", "subprocess.run", "shutil.rmtree", "shutil.move",
        "__import__", "eval", "exec", "compile", "sys.exit"
      ]
    }
  }
}
```

### Opt-in Packages (model can request)
```json
{
  "rlm": {
    "packages": {
      "opt_in": [
        "torch", "transformers", "sentence-transformers", "tokenizers",
        "huggingface_hub", "datasets", "pyarrow", "GitPython",
        "black", "ruff", "pylint", "mypy", "isort", "pytest",
        "coverage", "docker", "modal"
      ]
    }
  }
}
```

---

## Historical: Proposed Import Safety Strategy (REJECTED)

> **NOTE:** The following 4-level security strategy was proposed but REJECTED.
> RLM operates on TRUST — there are NO import restrictions, NO function blocklists,
> NO sandboxed execution, NO `__builtins__` limitations. The model has full Python access.
> See `docs/trust-model.md` for the actual design decision.


### Level 1: Module Whitelist
- Only modules in `allowed_modules` can be imported
- `import torch` → BLOCKED (not in whitelist)
- `import numpy` → ALLOWED (in whitelist)

### Level 2: Function Blocklist
- Within allowed modules, certain functions are blocked
- `os.path.join()` → ALLOWED
- `os.system()` → BLOCKED (in blocklist)

### Level 3: Sandboxed Execution
- All REPL code runs in a restricted namespace
- `__builtins__` is limited to safe functions
- `__import__` is replaced with a safe wrapper
- `eval()` and `exec()` are replaced with safe wrappers

### Level 4: Timeout Protection
- Each cell has a timeout (default: 120 seconds)
- Infinite loops are detected and killed
- Memory usage is monitored (default: 100MB limit)

---

## Testing Checklist

- [N/A] ~~Verify all whitelisted packages import successfully~~ (no whitelist — trust model)
- [N/A] ~~Verify all blocked modules raise ImportError~~ (no blocked modules — trust model)
- [N/A] ~~Verify blocked functions raise RuntimeError~~ (no blocked functions — trust model)
- [x] Verify timeout kills infinite loops
- [N/A] ~~Verify memory limit prevents bloat~~ (no memory limit — trust model)
- [ ] Verify `%%bash` works with timeout
- [ ] Verify `%cd` works and persists
- [ ] Verify state persists across cells
- [ ] Verify output truncation works

---

## Rollback

If package inventory is incorrect:
```bash
# Re-run inventory
pip list --format=json > docs/pip-list.json
# Update package-inventory.md
```
