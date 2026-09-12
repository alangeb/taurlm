"""Tests for the /ctx fix command and context_fix engine."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from context_fix import fix_context
from commands.ctx import run, _ctx_fix


def _make_agent(messages: list[dict]) -> MagicMock:
    agent = MagicMock()
    agent.context = MagicMock()
    agent.context._messages = messages
    agent._ctx_stack = []
    return agent


def _sys(content: str = "You are TauRLM.") -> dict:
    return {"role": "system", "content": content}


def _real(content: str) -> dict:
    return {"role": "user", "content": f"[U:real | N:0 | M:0 | C:0%] {content}"}


def _repl(content: str) -> dict:
    return {"role": "user", "content": f"[U:repl | N:0 | M:1 | C:1%] {content}"}


def _meta(content: str) -> dict:
    return {"role": "user", "content": f"[U:meta | N:0 | M:2 | C:2%] {content}"}


def _asst(content) -> dict:
    return {"role": "assistant", "content": content}


def _healthy() -> list[dict]:
    return [
        _sys(),
        _real("do the thing"),
        _asst("```python\nprint(1)\n```"),
        _repl("1"),
        _asst("```python\nanswer['content'] = 'done'\nanswer['ready'] = True\n```"),
    ]


class TestS1EmptyAssistant:
    def test_null_content_replaced(self):
        msgs = [_sys(), _real("hi"), _asst(None)]
        fixed, report = fix_context(msgs)
        assert report.empty_assistant == 1
        assert fixed[2]["content"].startswith("[CTX-FIX: assistant response")
        # Original not mutated
        assert msgs[2]["content"] is None

    def test_empty_string_replaced(self):
        msgs = [_sys(), _real("hi"), _asst("   ")]
        _, report = fix_context(msgs)
        assert report.empty_assistant == 1

    def test_reasoning_noted(self):
        msgs = [_sys(), _real("hi"), _asst(None)]
        msgs[2]["reasoning"] = "x" * 50
        fixed, _ = fix_context(msgs)
        assert "reasoning-only" in fixed[2]["content"]

    def test_empty_assistant_between_real_users_preserves_alternation(self):
        # [sys, real, asst(None), real] → placeholder keeps alternation valid
        msgs = [_sys(), _real("a"), _asst(None), _real("b")]
        fixed, report = fix_context(msgs)
        assert report.empty_assistant == 1
        assert report.dummies_inserted == 0
        roles = [m["role"] for m in fixed]
        assert roles == ["system", "user", "assistant", "user"]
        from agent_context_validation import validate_context
        assert validate_context(fixed) == []

    def test_tool_calls_null_content_untouched(self):
        msgs = [_sys(), _real("hi"), _asst(None)]
        msgs[2]["tool_calls"] = [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]
        fixed, report = fix_context(msgs)
        assert report.empty_assistant == 0
        assert fixed[2]["content"] is None


class TestS2Sequences:
    def test_real_user_real_user_gets_dummy_assistant(self):
        msgs = [_sys(), _real("first"), _real("second")]
        fixed, report = fix_context(msgs)
        assert report.dummies_inserted == 1
        roles = [m["role"] for m in fixed]
        assert roles == ["system", "user", "assistant", "user"]
        # Real user content untouched
        assert fixed[1]["content"] == msgs[1]["content"]
        assert fixed[3]["content"] == msgs[2]["content"]
        assert fixed[2]["content"].startswith("[CTX-FIX: placeholder assistant")

    def test_repl_repl_merged(self):
        msgs = [_sys(), _real("hi"), _asst("code"), _repl("out1"), _repl("out2")]
        fixed, report = fix_context(msgs)
        assert report.merged == 1
        assert len(fixed) == 4
        assert "out1" in fixed[3]["content"] and "out2" in fixed[3]["content"]
        assert "[CTX-FIX: merged consecutive user" in fixed[3]["content"]

    def test_assistant_assistant_merged(self):
        msgs = [_sys(), _real("hi"), _asst("first"), _asst("second")]
        fixed, report = fix_context(msgs)
        assert report.merged == 1
        assert len(fixed) == 3
        assert "first" in fixed[2]["content"] and "second" in fixed[2]["content"]

    def test_assistant_after_system_gets_dummy_user(self):
        msgs = [_sys(), _asst("orphan"), _real("hi"), _asst("ok")]
        fixed, report = fix_context(msgs)
        assert report.dummies_inserted == 1
        assert fixed[1]["role"] == "user"
        assert "[U:meta" in fixed[1]["content"]
        assert "[CTX-FIX: placeholder user" in fixed[1]["content"]

    def test_cascade_fixed_point(self):
        # Three consecutive real users → two dummies needed
        msgs = [_sys(), _real("a"), _real("b"), _real("c")]
        fixed, report = fix_context(msgs)
        roles = [m["role"] for m in fixed]
        assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
        assert report.dummies_inserted == 2


class TestS3Truncation:
    def test_oversize_truncated(self):
        big = "x" * 20000
        msgs = [_sys(), _real("hi"), _asst(big)]
        fixed, report = fix_context(msgs)
        assert report.truncated == 1
        content = fixed[2]["content"]
        assert len(content.encode("utf-8")) <= 10240 + 100  # + marker
        assert "[CTX-FIX: truncated from 20000 bytes" in content

    def test_system_truncated_too(self):
        msgs = [_sys("y" * 15000), _real("hi"), _asst("ok")]
        _, report = fix_context(msgs)
        assert report.truncated == 1

    def test_real_user_oversize_skipped(self):
        msgs = [_sys(), _real("r" * 20000), _asst("ok")]
        fixed, report = fix_context(msgs)
        assert report.truncated == 0
        assert fixed[1]["content"] == msgs[1]["content"]

    def test_under_limit_untouched(self):
        msgs = [_sys(), _real("hi"), _asst("x" * 10000)]
        _, report = fix_context(msgs)
        assert report.truncated == 0


class TestS4Entropy:
    def test_repeated_traceback_caught_by_dup_ratio(self):
        block = [
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            "    foo()",
            '  File "b.py", line 2, in foo',
            "    bar()",
            "ValueError: bad thing",
        ]
        text = "\n".join(block * 30)  # 180 lines, 6 unique → dup ~0.97
        msgs = [_sys(), _real("hi"), _asst("```python\n" + text + "\n```")]
        fixed, report = fix_context(msgs)
        assert report.entropy_fixed == 1
        assert "[CTX-FIX: entropy correction" in fixed[2]["content"]
        assert len(fixed[2]["content"]) < 300

    def test_degenerate_repeats_caught_by_entropy(self):
        text = ("ab" * 400)  # 800 chars, low line entropy
        lines = [text] * 10
        msgs = [_sys(), _real("hi"), _asst("\n".join(lines))]
        fixed, report = fix_context(msgs)
        assert report.entropy_fixed == 1

    def test_normal_prose_untouched(self):
        lines = [f"Line {i}: the quick brown fox jumps over the lazy dog #{i}" for i in range(60)]
        text = "\n".join(lines)
        msgs = [_sys(), _real("hi"), _asst(text)]
        _, report = fix_context(msgs)
        assert report.entropy_fixed == 0

    def test_small_message_skipped(self):
        text = ("ab" * 100)  # < 1000 bytes
        msgs = [_sys(), _real("hi"), _asst(text)]
        _, report = fix_context(msgs)
        assert report.entropy_fixed == 0

    def test_system_excluded(self):
        text = ("same line here\n" * 200)
        msgs = [_sys(text), _real("hi"), _asst("ok")]
        _, report = fix_context(msgs)
        assert report.entropy_fixed == 0


class TestSafety:
    def test_idempotent(self):
        msgs = [
            _sys(),
            _real("a"),
            _real("b"),
            _asst(None),
            _asst("x" * 20000),
        ]
        once, r1 = fix_context(msgs)
        twice, r2 = fix_context(once)
        assert r1.total_fixes > 0
        assert r2.total_fixes == 0
        assert twice == once

    def test_real_user_invariant(self):
        from agent_message_utils import is_real_user_request

        msgs = [
            _sys(),
            _real("precious user content " + "z" * 20000),
            _real("second real"),
            _asst(None),
        ]
        fixed, _ = fix_context(msgs)
        real_before = [m["content"] for m in msgs if is_real_user_request(m)]
        real_after = [m["content"] for m in fixed if is_real_user_request(m)]
        assert real_before == real_after

    def test_healthy_context_zero_fixes(self):
        msgs = _healthy()
        fixed, report = fix_context(msgs)
        assert report.total_fixes == 0
        assert fixed == msgs

    def test_original_not_mutated(self):
        msgs = [_sys(), _real("hi"), _asst(None)]
        snapshot = [dict(m) for m in msgs]
        fix_context(msgs)
        assert msgs == snapshot

    def test_preflight_no_system_aborts(self):
        msgs = [_real("hi"), _asst("ok")]
        fixed, report = fix_context(msgs)
        assert report.aborted
        assert fixed is msgs

    def test_empty_context_aborts(self):
        fixed, report = fix_context([])
        assert report.aborted
        assert fixed == []


class TestCtxFixCommand:
    def test_fix_applies_and_backs_up(self):
        msgs = [_sys(), _real("hi"), _asst(None)]
        agent = _make_agent(msgs)
        result = _ctx_fix(agent, dry=False)
        assert agent.context._messages is not msgs
        assert len(agent._ctx_stack) == 1
        assert agent._ctx_stack[0] == msgs  # backup intact
        assert "1 fix(es)" in result

    def test_fix_check_does_not_mutate(self):
        msgs = [_sys(), _real("hi"), _asst(None)]
        agent = _make_agent(msgs)
        result = _ctx_fix(agent, dry=True)
        assert agent.context._messages is msgs
        assert not hasattr(agent, "_ctx_stack") or not agent._ctx_stack
        assert "DRY RUN" in result

    def test_fix_healthy_reports_zero(self):
        agent = _make_agent(_healthy())
        result = _ctx_fix(agent, dry=False)
        assert "0 fixes needed" in result

    def test_run_dispatch(self):
        agent = _make_agent([_sys(), _real("hi"), _asst(None)])
        result = run(agent, ["fix"])
        assert "CTX FIX REPORT" in result

    def test_run_dispatch_check(self):
        agent = _make_agent([_sys(), _real("hi"), _asst(None)])
        result = run(agent, ["fix", "check"])
        assert "DRY RUN" in result

    def test_empty_context_message(self):
        agent = _make_agent([])
        result = _ctx_fix(agent)
        assert "empty" in result.lower()
