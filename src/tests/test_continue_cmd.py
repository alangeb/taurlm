"""Regression tests for /continue file indexing (src/commands/continue.py).

M2: the "exclude the current session's context" filter must compare RESOLVED
paths, because get_all_context_files() dedups on resolve(). A symlinked or
relative startup context did not match a plain str() comparison, so it stayed in
the list and every index >= it shifted - /continue <n> loaded the wrong file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import importlib
continue_cmd = importlib.import_module("commands.continue")


def _mk_ctx(path: Path, text: str = "hello") -> Path:
    path.write_text(json.dumps([{"role": "user", "content": text}]), encoding="utf-8")
    return path


@pytest.fixture()
def _env(tmp_path, monkeypatch):
    """Two real context files + a symlink to the first one."""
    real = tmp_path / "real"
    real.mkdir()
    a = _mk_ctx(real / "1_a.context")
    b = _mk_ctx(real / "2_b.context")
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    link = link_dir / "1_a.context"
    try:
        os.symlink(a, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    import agent_context_utils as acu
    monkeypatch.setattr(acu, "get_all_context_files", lambda: [a, b])
    # The startup context is the SYMLINK (what a user launched through), while
    # the listing hands back resolved paths.
    agent = SimpleNamespace(
        _continue_files_cache=None,
        _continue_exclude_ctx=None,
        _session=SimpleNamespace(context_file=link),
    )
    return agent, a, b


def test_symlinked_current_context_is_excluded(_env):
    agent, a, b = _env
    files = continue_cmd._get_valid_context_files(agent)
    assert [f.name for f in files] == ["2_b.context"]      # the current one is gone


def test_relative_current_context_is_excluded(_env):
    agent, a, b = _env
    agent._session.context_file = Path(os.path.relpath(a))
    files = continue_cmd._get_valid_context_files(agent)
    assert [f.name for f in files] == ["2_b.context"]


def test_plain_current_context_still_excluded(_env):
    agent, a, b = _env
    agent._session.context_file = a
    files = continue_cmd._get_valid_context_files(agent)
    assert [f.name for f in files] == ["2_b.context"]


def test_unrelated_context_file_keeps_both(_env):
    agent, a, b = _env
    agent._session.context_file = None
    files = continue_cmd._get_valid_context_files(agent)
    assert [f.name for f in files] == ["1_a.context", "2_b.context"]


# ---------------------------------------------------------------------------
# M3 REGRESSION: the snapshot must notice NEW context files
# ---------------------------------------------------------------------------

@pytest.fixture()
def _logdir(tmp_path, monkeypatch):
    """A fake LOG_DIR plus a get_all_context_files that scans it."""
    import agent_session as asess
    import agent_context_utils as acu
    log = tmp_path / "logs"
    log.mkdir()
    monkeypatch.setattr(asess, "LOG_DIR", log)
    monkeypatch.setattr(acu, "get_all_context_files",
                        lambda: sorted(log.glob("*.context")))
    return log


def _agent(current=None):
    return SimpleNamespace(_continue_files_cache=None, _continue_exclude_ctx=None,
                           _continue_files_cache_ts=None,
                           _session=SimpleNamespace(context_file=current))


def test_new_context_file_invalidates_the_cache(_logdir):
    a = _mk_ctx(_logdir / "1_a.context")
    b = _mk_ctx(_logdir / "2_b.context")
    agent = _agent()
    assert [f.name for f in continue_cmd._get_valid_context_files(agent)] == [
        "1_a.context", "2_b.context"]
    assert agent._continue_files_cache_ts                       # stamped at build

    c = _mk_ctx(_logdir / "3_c.context")                        # newer arrival
    os.utime(c, (c.stat().st_atime, c.stat().st_mtime + 10))
    names = [f.name for f in continue_cmd._get_valid_context_files(agent)]
    assert names == ["1_a.context", "2_b.context", "3_c.context"]


def test_cache_is_reused_when_nothing_changed(_logdir):
    _mk_ctx(_logdir / "1_a.context")
    agent = _agent()
    first = continue_cmd._get_valid_context_files(agent)
    stamp = agent._continue_files_cache_ts
    again = continue_cmd._get_valid_context_files(agent)
    assert again == first and agent._continue_files_cache_ts == stamp   # no rebuild


def test_current_session_context_changing_does_not_invalidate(_logdir):
    """The live session rewrites its own context every turn - ignore its mtime."""
    a = _mk_ctx(_logdir / "1_a.context")
    b = _mk_ctx(_logdir / "2_b.context")
    agent = _agent(current=a)
    assert [f.name for f in continue_cmd._get_valid_context_files(agent)] == ["2_b.context"]
    stamp = agent._continue_files_cache_ts
    os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 10))    # current file touched
    assert [f.name for f in continue_cmd._get_valid_context_files(agent)] == ["2_b.context"]
    assert agent._continue_files_cache_ts == stamp              # snapshot kept


# ---------------------------------------------------------------------------
# M4 REGRESSION: blank placeholder messages must not count
# ---------------------------------------------------------------------------

def test_read_context_metadata_ignores_blank_messages(tmp_path):
    from agent_context_utils import read_context_metadata
    q = tmp_path / "blank.context"
    q.write_text(json.dumps([
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "   "},
    ]), encoding="utf-8")
    assert read_context_metadata(q) == (0, "")          # nothing loadable

    r = tmp_path / "mixed.context"
    r.write_text(json.dumps([
        {"role": "user", "content": ""},
        {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        {"role": "user", "content": "real question"},
    ]), encoding="utf-8")
    count, last_user = read_context_metadata(r)
    assert count == 1 and last_user == "real question"  # (count, last_user) contract

    i = tmp_path / "img.context"
    i.write_text(json.dumps([
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
    ]), encoding="utf-8")
    assert read_context_metadata(i)[0] == 1             # an image IS content

    legacy = tmp_path / "legacy.context"
    legacy.write_text(json.dumps([{"role": "user", "content": ""}]), encoding="utf-8")
    assert read_context_metadata(legacy) == (0, "")     # bare-array format too
