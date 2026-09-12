"""Regression test for P7-B9-03: dead test file (importing deleted
`commands.ralph`) must not exist in the suite.

test_ralph_loop.py imported `from commands.ralph import ...` but that module
was deleted in the RLM transformation, so the whole 744-line file was dead at
collection time (silently excluded via conftest.collect_ignore). It has been
removed. This test guards against it silently returning.
"""

from pathlib import Path

import pytest


def test_ralph_test_file_removed():
    dead = Path(__file__).parent / "test_ralph_loop.py"
    assert not dead.exists(), "dead test_ralph_loop.py reappeared"


def test_ralph_not_in_collect_ignore():
    text = (Path(__file__).parent / "conftest.py").read_text()
    assert "test_ralph_loop.py" not in text


def test_commands_ralph_absent():
    with pytest.raises(ModuleNotFoundError):
        import commands.ralph  # noqa: F401
