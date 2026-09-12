"""Tests for validate_eot (src/agent_pipeline.py).

Contract (specs/fence-pipeline-v2.md):
  - MULTI-BLOCK EOT is decided by counting BLOCKS with the shared `scan_style`
    counter, not by counting opener LINES. Fence-like text inside prose or
    strings must not reject a healthy EOT.
  - Auto-closed blocks COUNT here even though the strict extractor refuses them
    for execution: "two blocks where the second is unclosed" is still a
    multi-block EOT and must be rejected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_pipeline import validate_eot
from agent_repl_parse import fence_tokens

BT3 = chr(96) * 3


class _Answer:
    def __init__(self, content="done", ready=True):
        self.content = content
        self.ready = ready

    def set_ready(self, v):
        self.ready = v

    def update_content(self, c):
        self.content = c


class _Kernel:
    def __init__(self):
        self.namespace = {"answer": {"content": "done", "ready": True}}


def _agent(fence_style="std", answer=None):
    return SimpleNamespace(
        config=SimpleNamespace(rlm=SimpleNamespace(repl=SimpleNamespace(fence_style=fence_style))),
        get_answer=lambda: answer,
        _repl_answer=answer,
        _repl_kernel=_Kernel(),
    )


def _eot(text, style="std"):
    ans = _Answer()
    parts = validate_eot(_agent(style, ans), text, turn_count=1)
    return parts, ans


class TestMultiBlockEot:
    def test_single_block_eot_accepted(self):
        parts, ans = _eot(f"intro\n{BT3}python\nanswer['ready'] = True\n{BT3}")
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    def test_two_blocks_rejected(self):
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2\n{BT3}"
        parts, ans = _eot(text)
        assert any("MULTI-BLOCK EOT" in p for p in parts)
        assert ans.ready is False

    def test_two_blocks_message_names_the_count(self):
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2\n{BT3}"
        parts, _ = _eot(text)
        msg = [p for p in parts if "MULTI-BLOCK" in p][0]
        assert "contains 2 code blocks" in msg

    def test_second_block_unclosed_is_still_multi_block(self):
        """REGRESSION F: auto-closed blocks count. The old opener-line counter
        also caught this, but the point is the block counter must not lose it
        just because the extractor would refuse to execute the tail block."""
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2"
        parts, ans = _eot(text)
        assert any("MULTI-BLOCK EOT" in p for p in parts)
        assert ans.ready is False

    def test_unclosed_single_block_is_not_multi_block(self):
        parts, ans = _eot(f"{BT3}python\nanswer['ready'] = True")
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    @pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
    def test_style_aware(self, style):
        ft = fence_tokens(style)
        text = (ft["py_open"] + "\na = 1\n" + ft["py_close"] + "\n"
                + ft["py_open"] + "\nb = 2\n" + ft["py_close"])
        parts, ans = _eot(text, style)
        assert any("MULTI-BLOCK EOT" in p for p in parts)
        assert ans.ready is False

    def test_wrong_style_fences_are_not_counted(self):
        """Only the ACTIVE style opens a block; other styles are prose."""
        at = fence_tokens("at")
        text = at["py_open"] + "\na = 1\n" + at["py_close"] + "\nmore prose"
        parts, ans = _eot(text, "std")
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True


class TestEmptyBlockNotCounted:
    """M6 REGRESSION: an EMPTY block (no body) carries no code. A healthy
    single real block plus a stray empty block must not be rejected as
    MULTI-BLOCK — only non-empty bodies count."""

    def test_empty_then_real_block_accepted(self):
        text = f"{BT3}\n{BT3}\n{BT3}python\nanswer['ready'] = True\n{BT3}"
        parts, ans = _eot(text)
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    def test_real_then_whitespace_body_block_accepted(self):
        text = f"{BT3}python\nanswer['ready'] = True\n{BT3}\n{BT3}\n   \n\t\n{BT3}"
        parts, ans = _eot(text)
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    def test_two_real_blocks_still_rejected(self):
        """Guard: the non-empty filter must not weaken the real rule."""
        text = f"{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2\n{BT3}"
        parts, ans = _eot(text)
        assert any("MULTI-BLOCK EOT" in p for p in parts)
        assert ans.ready is False

    def test_empty_plus_real_still_reports_two_when_both_real(self):
        text = f"{BT3}\n{BT3}\n{BT3}python\na = 1\n{BT3}\n{BT3}python\nb = 2\n{BT3}"
        parts, _ = _eot(text)
        msg = [p for p in parts if "MULTI-BLOCK" in p][0]
        assert "contains 2 code blocks" in msg

    @pytest.mark.parametrize("style", ["std", "quad", "html", "at"])
    def test_empty_block_ignored_in_every_style(self, style):
        ft = fence_tokens(style)
        text = (ft["py_open"] + "\n" + ft["py_close"] + "\n"
                + ft["py_open"] + "\nanswer['ready'] = True\n" + ft["py_close"])
        parts, ans = _eot(text, style)
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True


class TestFenceLikeProseNotCounted:
    """The reason the counter replaced line counting (design record evidence)."""

    def test_indented_fence_in_prose_not_counted(self):
        text = (f"{BT3}python\nanswer['ready'] = True\n{BT3}\n"
                f"\nExample of the same fence indented:\n  {BT3}python\n  x = 1\n  {BT3}")
        parts, ans = _eot(text)
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    def test_opener_token_inside_a_string_not_counted(self):
        """A fence token that is not ALONE at column 0 is prose."""
        text = (f"{BT3}python\nanswer['content'] = \"{BT3}python\"\n"
                f"answer['ready'] = True\n{BT3}")
        parts, ans = _eot(text)
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    def test_prose_only_eot_not_counted(self):
        parts, ans = _eot("The answer is 42. No code here.")
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True

    def test_orphan_closer_not_counted_as_block(self):
        text = f"{BT3}python\nanswer['ready'] = True\n{BT3}\n{BT3}"
        parts, ans = _eot(text)
        assert not any("MULTI-BLOCK" in p for p in parts)
        assert ans.ready is True


class TestOtherEotRulesUnchanged:
    def test_empty_content_rejected(self):
        parts, ans = _eot(f"{BT3}python\nx = 1\n{BT3}")
        ans.content = ""
        parts = validate_eot(_agent("std", ans), "", 1)
        assert any("EMPTY ANSWER" in p for p in parts)

    def test_no_answer_is_silent(self):
        assert validate_eot(_agent("std", None), f"{BT3}python\na\n{BT3}\n{BT3}python\nb\n{BT3}", 1) == []

    def test_not_ready_is_silent(self):
        assert validate_eot(_agent("std", _Answer("x", ready=False)),
                            f"{BT3}python\na\n{BT3}\n{BT3}python\nb\n{BT3}", 1) == []
