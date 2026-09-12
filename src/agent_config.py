"""Configuration management for TauErgon — dataclasses, file/env loading."""

from __future__ import annotations

import json
import threading
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

from agent_llm_models import DEFAULT_MAX_CONTEXT_TOKENS

__all__ = [
    "MalformedConfig",
    "LoopDetectionConfig",
    "LLMGroup",
    "REPLConfig",
    "AutoSummaryConfig",
    "RLMConfig",
    "Config",
    "get_config",
]

# ---------------------------------------------------------------------------
# Nested config dataclasses (frozen, immutable defaults)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MalformedConfig:
    """Retry limits for malformed LLM responses."""
    silent_retries: int = 4
    enhanced_retries: int = 7
    explicit_retries: int = 10


@dataclass(frozen=True)
class LoopDetectionConfig:
    """Sliding-window loop detection parameters."""
    window_size: int = 30
    repeat_threshold: int = 3


@dataclass(frozen=True)
class REPLConfig:
    """REPL kernel configuration for RLM mode."""
    max_output_chars: int = 8192
    max_state_size_mb: float = 100.0
    bash_timeout_seconds: float = 180.0
    python_timeout_seconds: float = 180.0
    fence_style: str = "std"  # "std", "quad", "html", "at"


@dataclass(frozen=True)
class AutoSummaryConfig:
    """Configuration for automatic turn summarization."""
    enabled: bool = True
    model: str | None = None  # Use agent's model if None
    max_output_tokens: int = 512


@dataclass(frozen=True)
class RLMConfig:
    """RLM (Recursive Language Model) configuration.

    The agent uses a persistent Python REPL as its primary interface.
    The model executes Python code, spawns sub-LLMs via rlm(), and sets
    answer["content"] + answer["ready"] to signal completion.

    Attributes:
        repl: REPL kernel configuration
        max_turns: Maximum turns before forced end
        auto_summary: Automatic turn summarization configuration
    """
    repl: REPLConfig = field(default_factory=REPLConfig)
    max_turns: int = 1000
    auto_summary: AutoSummaryConfig = field(default_factory=AutoSummaryConfig)


@dataclass(frozen=True)
class LLMGroup:
    """Named LLM configuration with model, API, and generation parameters."""
    name: str
    model: str
    api_base: str
    api_key: str = ""
    timeout: int = 300
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    repetition_penalty: float | None = None
    chat_template_kwargs: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Main Config class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """All tunable parameters for TauErgon.

    Resolution: tau.json → env vars → defaults.
    Active LLM group selected via llm_group_name (first group by default).
    """

    timeout: int = 180
    agent_name: str = "default"
    heartbeat_seconds: int | None = None

    # Nested configs
    malformed: MalformedConfig = field(default_factory=MalformedConfig)
    loop_detection: LoopDetectionConfig = field(default_factory=LoopDetectionConfig)

    rlm: RLMConfig = field(default_factory=RLMConfig)

    # LLM inference parameters (forwarded directly to API, passthrough)
    inference_params: dict[str, Any] | None = None

    # LLM groups configuration
    llm_groups: dict[str, LLMGroup] | None = None
    llm_group_name: str | None = None

    # -----------------------------------------------------------------------
    # Environment variable mappings
    # Each entry: (env_var, config_key, optional_sub_key, converter)
    # sub_key is None for flat keys, present for nested keys.
    # -----------------------------------------------------------------------
    _ENV_OVERRIDES: ClassVar[tuple[tuple[str, str, str | None, Any], ...]] = (
        # Flat keys
        ("TAULLM", "llm_group_name", None, str),
        ("TAUTIMEOUT", "timeout", None, int),
        ("TAUERAGONNAME", "agent_name", None, str),
        ("TAU_HEARTBEAT", "heartbeat_seconds", None, int),
        # Nested keys
        ("TAU_MALFORMED_SILENT_RETRIES", "malformed", "silent_retries", int),
        ("TAU_MALFORMED_ENHANCED_RETRIES", "malformed", "enhanced_retries", int),
        ("TAU_MALFORMED_EXPLICIT_RETRIES", "malformed", "explicit_retries", int),
        ("TAU_LOOP_WINDOW", "loop_detection", "window_size", int),
        ("TAU_LOOP_REPEAT", "loop_detection", "repeat_threshold", int),
        # RLM nested keys
        ("TAU_RLM_MAX_TURNS", "rlm", "max_turns", int),
        ("TAU_RLM_REPL_MAX_OUTPUT", "rlm", "repl_max_output_chars", int),
    )

    # Mapping of config keys to their nested dataclass types
    _NESTED_TYPES: ClassVar[dict[str, type]] = {
        "malformed": MalformedConfig,
        "loop_detection": LoopDetectionConfig,
        "rlm": RLMConfig,
        "rlm.repl": REPLConfig,
    }

    @classmethod
    def _resolve_entry_dir(cls) -> Path:
        """Directory containing the entry script (tau.py)."""
        script = Path(sys.argv[0]).resolve()
        _file_dir = Path(__file__).parent  # L-Pa1: secondary fallback
        if (_file_dir / 'tau.json').exists():
            return _file_dir
        return script.parent if script.exists() else Path.cwd()

    @classmethod
    def _load_file_config(cls) -> dict[str, Any]:
        """Load tau.json from entry dir. Returns {} on missing/invalid file."""
        config_path = cls._resolve_entry_dir() / "tau.json"
        if not config_path.exists():
            return {}
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            import sys
            print(f"WARNING: Failed to parse config {config_path}: {e}", file=sys.stderr)
            return {}
    @classmethod
    def _apply_env_overrides(cls, cfg_dict: dict[str, Any]) -> dict[str, Any]:
        """Apply environment variable overrides to config dict."""
        for env_var, key, sub_key, converter in cls._ENV_OVERRIDES:
            env_val = os.getenv(env_var)
            if env_val is None:
                continue
            try:
                if sub_key is None:
                    cfg_dict[key] = converter(env_val)
                else:
                    if not isinstance(cfg_dict.get(key), dict):
                        cfg_dict[key] = {}
                    cfg_dict[key][sub_key] = converter(env_val)
            except (ValueError, TypeError):
                import sys
                print(f"WARNING: Invalid env var {env_var}={env_val!r}, using default", file=sys.stderr)
                continue
        return cfg_dict

    @staticmethod
    def _only_fields(cls_type: type, values: dict[str, Any]) -> dict[str, Any]:
        """Drop keys not present as dataclass fields (tolerate stale tau.json keys).

        Tolerance is not silence: dropped keys are reported on stderr so typos
        and dead keys stay visible.
        """
        _valid = {f.name for f in fields(cls_type)}
        _dropped = sorted(k for k in values if k not in _valid)
        if _dropped:
            print(
                f"WARNING: Ignoring unknown keys for {cls_type.__name__}: "
                f"{', '.join(_dropped)}",
                file=sys.stderr,
            )
        return {k: v for k, v in values.items() if k in _valid}

    @classmethod
    def _merge_nested(cls, merged: dict[str, Any]) -> dict[str, Any]:
        """Convert nested dicts to dataclass instances and LLMGroup objects."""
        # Migrate flat rlm.repl_max_output_chars -> rlm.repl.max_output_chars
        rlm_dict = merged.get("rlm")
        if isinstance(rlm_dict, dict) and "repl_max_output_chars" in rlm_dict:
            val = rlm_dict.pop("repl_max_output_chars")
            if not isinstance(rlm_dict.get("repl"), dict):
                rlm_dict["repl"] = {}
            rlm_dict["repl"]["max_output_chars"] = val

        # Handle second-level nesting (rlm.repl) AFTER the flat migration so the
        # conversion always sees a raw dict, never a half-migrated one.
        rlm_nested = {
            "rlm.repl": REPLConfig,
        }
        for full_key, cls_type in rlm_nested.items():
            parent_key, child_key = full_key.split(".")
            parent = merged.get(parent_key)
            if isinstance(parent, dict) and isinstance(parent.get(child_key), dict):
                parent[child_key] = cls_type(**cls._only_fields(cls_type, parent[child_key]))

        # Handle first-level nesting
        for key, cls_type in cls._NESTED_TYPES.items():
            if isinstance(merged.get(key), dict):
                merged[key] = cls_type(**cls._only_fields(cls_type, merged[key]))

        # Alias "inference" -> "inference_params"
        if isinstance(merged.get("inference"), dict):
            merged["inference_params"] = merged.pop("inference")

        # Convert llm_groups dict to LLMGroup instances
        if isinstance(merged.get("llm_groups"), dict):
            merged["llm_groups"] = {
                name: LLMGroup(name=name, **cls._only_fields(LLMGroup, g))
                for name, g in merged["llm_groups"].items()
                if isinstance(g, dict)
            }
        return merged

    @classmethod
    def load(cls) -> Config:
        """Load and merge configuration: tau.json → env vars → defaults."""
        file_cfg = cls._load_file_config()
        merged = cls._apply_env_overrides(file_cfg)
        merged = cls._merge_nested(merged)

        if not merged.get("llm_groups"):
            raise ValueError(
                "No LLM groups configured. "
                "Define at least one group in tau.json under 'llm_groups'."
            )
        if not merged.get("llm_group_name") and merged["llm_groups"]:
            merged["llm_group_name"] = next(iter(merged["llm_groups"]))
        # Filter to valid dataclass fields (single code path, see _only_fields)
        merged = cls._only_fields(cls, merged)
        return cls(**merged)


# Module-level config cache — explicit, type-safe, and easy to reason about.
_config_cache: Config | None = None
_config_cache_lock = threading.Lock()  # L-Pa2


def get_config() -> Config:
    """Load and return the global configuration.

    Cached after first call — subsequent calls return the same Config instance.
    """
    global _config_cache
    with _config_cache_lock:  # L-Pa2
        if _config_cache is None:
            _config_cache = Config.load()
    return _config_cache

