"""M5: the registry fallback in get_all_context_files must stay narrow.

A missing/unreadable registry is an expected fallback (the LOG_DIR scan recovers
the files) and is logged at WARNING. Anything else - TypeError, AttributeError -
is a real bug in the registry and must propagate, not be swallowed.
"""
from __future__ import annotations

import json
import logging

import pytest


@pytest.fixture()
def _logdir(tmp_path, monkeypatch):
    import agent_context_utils as acu
    log = tmp_path / "logs"
    log.mkdir()
    monkeypatch.setattr(acu, "LOG_DIR", log)
    f = log / "1_20240101000000_0.context"
    f.write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")
    return log


def test_registry_import_error_falls_back_and_warns(_logdir, monkeypatch, caplog):
    import agent_session_registry as reg
    import agent_context_utils as acu

    def boom():
        raise ImportError("no registry here")

    monkeypatch.setattr(reg, "get_registry", boom)
    with caplog.at_level(logging.WARNING):
        files = acu.get_all_context_files()
    assert [f.name for f in files] == ["1_20240101000000_0.context"]        # LOG_DIR scan recovered it
    assert any(r.levelno == logging.WARNING and "registry unavailable" in r.getMessage()
               for r in caplog.records)                      # was logging.debug before


def test_registry_unexpected_error_is_not_swallowed(_logdir, monkeypatch):
    import agent_session_registry as reg
    import agent_context_utils as acu

    def boom():
        raise TypeError("a real bug in the registry")

    monkeypatch.setattr(reg, "get_registry", boom)
    with pytest.raises(TypeError):
        acu.get_all_context_files()
