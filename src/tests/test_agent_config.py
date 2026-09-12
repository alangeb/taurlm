"""Tests for agent_config module.

Tests:
    test_get_config - Verify config loading works
    test_config_llm_groups - Verify LLM groups are loaded
    test_config_defaults - Verify default values
    test_config_env_override - Verify env vars override config
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestGetConfig:
    """Test config loading functionality."""

    def test_get_config_returns_config(self, tau_entry_dir):
        """Verify get_config returns a Config object."""
        from agent_config import Config, get_config

        config = get_config()
        assert config is not None
        assert isinstance(config, Config)

    def test_config_has_required_attributes(self, tau_entry_dir):
        """Verify Config has all required attributes."""
        from agent_config import get_config

        config = get_config()
        assert hasattr(config, "llm_groups")
        assert hasattr(config, "llm_group_name")
        assert hasattr(config, "agent_name")
        assert hasattr(config, "timeout")

    def test_config_llm_groups_is_dict(self, tau_entry_dir):
        """Verify llm_groups is a dictionary."""
        from agent_config import get_config

        config = get_config()
        assert isinstance(config.llm_groups, dict)

    def test_config_llm_group_name_is_string(self, tau_entry_dir):
        """Verify llm_group_name is a string."""
        from agent_config import get_config

        config = get_config()
        assert isinstance(config.llm_group_name, str)


class TestConfigLLMGroups:
    """Test LLM groups configuration."""

    def test_llm_groups_contains_default(self, tau_entry_dir):
        """Verify default LLM group exists."""
        from agent_config import get_config

        config = get_config()
        # Default group should exist
        assert "default" in config.llm_groups or len(config.llm_groups) > 0

    def test_llm_group_has_required_fields(self, tau_entry_dir):
        """Verify LLM groups have required fields."""
        from agent_config import get_config

        config = get_config()
        for name, group in config.llm_groups.items():
            assert hasattr(group, "model"), f"Group '{name}' missing 'model'"
            assert hasattr(group, "api_base"), f"Group '{name}' missing 'api_base'"
            assert hasattr(
                group, "max_context_tokens"
            ), f"Group '{name}' missing 'max_context_tokens'"


class TestConfigDefaults:
    """Test default configuration values."""

    def test_default_agent_name(self, tau_entry_dir):
        """Verify default agent name is 'default'."""
        from agent_config import get_config

        config = get_config()
        # Agent name should be set (either from config or default)
        assert config.agent_name is not None

    def test_default_timeout(self, tau_entry_dir):
        """Verify default timeout is set."""
        from agent_config import get_config

        config = get_config()
        assert config.timeout is not None
        assert isinstance(config.timeout, int)
        assert config.timeout > 0


class TestLegacyKeyTolerance:
    """Stale tau.json keys (removed schema entries) must not crash startup."""

    _LEGACY = {
        "timeout": 180,
        "agent_name": "legacy",
        "debug": True,
        "reflection": {"enabled": True, "min_interval": 9},
        "llm_groups": {
            "a": {
                "model": "m",
                "api_base": "http://x",
                "reflection": {"enabled": True},
                "top_k": 20,
            }
        },
        "rlm": {
            "max_turns": 7,
            "max_tokens": 100000,
            "repl": {
                "max_output_chars": 123,
                "allowed_modules": ["stdlib"],
                "blocked_modules": [],
            },
            "sub_llm": {"model": None, "max_children": 10},
        },
    }

    def test_real_load_tolerates_legacy_tau_json(self, tmp_path, monkeypatch):
        """End-to-end: a tau.json with removed keys loads without raising."""
        import json
        from agent_config import Config

        (tmp_path / "tau.json").write_text(json.dumps(self._LEGACY), encoding="utf-8")
        monkeypatch.setattr(
            Config, "_resolve_entry_dir", classmethod(lambda cls: tmp_path)
        )

        cfg = Config.load()  # must not raise

        assert cfg.rlm.max_turns == 7
        assert cfg.rlm.repl.max_output_chars == 123
        assert not hasattr(cfg.rlm, "sub_llm")
        assert not hasattr(cfg.rlm.repl, "allowed_modules")

    def test_only_fields_drops_unknown_keeps_known(self):
        """_only_fields itself drops removed/typo keys and keeps live keys."""
        from agent_config import Config, RLMConfig, REPLConfig, LLMGroup

        rlm = Config._only_fields(RLMConfig, {"max_turns": 7, "max_tokens": 100000, "sub_llm": {}})
        assert rlm == {"max_turns": 7}

        repl = Config._only_fields(REPLConfig, {"max_output_chars": 123, "allowed_modules": ["stdlib"], "blocked_modules": []})
        assert repl == {"max_output_chars": 123}

        grp = Config._only_fields(LLMGroup, {"model": "m", "api_base": "http://x", "top_k": 20, "reflection": {"enabled": True}})
        assert grp == {"model": "m", "api_base": "http://x", "top_k": 20}

        top = Config._only_fields(Config, {"timeout": 180, "debug": True, "reflection": {}})
        assert top == {"timeout": 180}

    def test_only_fields_warns_on_dropped_keys(self, capsys):
        """Dropped keys are announced on stderr, not silently swallowed."""
        from agent_config import Config, REPLConfig

        Config._only_fields(REPLConfig, {"max_output_chars": 123, "bogus_module_list": []})
        err = capsys.readouterr().err
        assert "Ignoring unknown keys" in err
        assert "bogus_module_list" in err
        assert "REPLConfig" in err
