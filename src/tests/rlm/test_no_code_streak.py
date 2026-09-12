"""No-code streak escalation in run_rlm_loop (src/agent_loop.py).

Contract (specs/fence-pipeline-v2.md):
  - A plain-text reply is a WARNING, not an execution error. It must NOT feed
    `consecutive_errors` (that counter force-kills healthy turns at 5).
  - It gets its OWN streak: `agent._no_code_streak`, warning on the 1st
    occurrence, a firm message naming the fence tokens from the 2nd, force-end
    at `_NO_CODE_FORCE` (5), reset as soon as a code block executes.

Driven with a stub agent + patched loop collaborators rather than a real
TauErgon, so the escalation branch is exercised in milliseconds.
"""
from __future__ import annotations

import io
import contextlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent_loop
from agent_loop_detect import LoopDetector

BT3 = chr(96) * 3
CODE = f"{BT3}python\nx = 1\n{BT3}"
PROSE = "I think the answer is probably fine."


class _Ctx:
    def __init__(self):
        self._messages = []
        self.closed = []
        self.spawn_B = 0

    def validate(self):
        return []

    def append_assistant(self, content, reasoning=None, synthetic=False):
        self._messages.append({"role": "assistant", "content": content})

    def append_synthetic_user(self, *args, **kwargs):
        self._messages.append({"role": "user", "content": args[1] if len(args) > 1 else ""})

    def append_repl_error(self, *args, **kwargs):
        self._messages.append({"role": "user", "content": str(args[0])})

    def close_turn(self, text, **kwargs):
        self.closed.append(text)

    def get_usage_stats(self, *args, **kwargs):
        return (0, 0.1, 0, 0)

    def get_messages(self):
        return self._messages


class _Repl:
    def __init__(self):
        self.error_counts = []

    def execute_blocks(self, blocks, error_count=1):
        self.error_counts.append(error_count)
        return SimpleNamespace(success=True, output="ok", error=None)


class StubAgent:
    """Minimal surface run_rlm_loop actually touches."""

    def __init__(self, replies, fence_style="std"):
        self.replies = list(replies)
        self.calls = 0
        self.config = SimpleNamespace(rlm=SimpleNamespace(
            fence_style=fence_style, max_turns=50,
            repl=SimpleNamespace(fence_style=fence_style, max_output_chars=8192)))
        self._force_max_turns = None
        self.context = _Ctx()
        self._repl = _Repl()
        self.max_context_tokens = 200_000
        self._session = None
        self._queued_images = []
        self._pending_md_segments = []
        self._cleanup_pending = False
        self.spawn_B = 0
        self.loop_detector = LoopDetector()
        self._budget_warning_text = None
        self._repl_kernel = None
        self._repl_answer = None
        self.answer = SimpleNamespace(
            content="partial", ready=False,
            set_ready=lambda v: None, update_content=lambda c: None)

    def next_reply(self):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return r, None

    # --- methods the loop calls ---
    def _process_control_queue(self):
        pass

    def get_answer(self):
        return self.answer

    def reset_answer(self):
        pass

    def get_status(self):
        return {}


def _drive(replies, fence_style="std", pre_streak=None):
    """Run the loop with all external collaborators stubbed. Returns (ret, agent, feedback)."""
    agent = StubAgent(replies, fence_style)
    if pre_streak is not None:
        agent._no_code_streak = pre_streak      # simulate carry-over from a prior turn
    feedback: list[list[str]] = []

    def _noop_build(_agent, parts):
        feedback.append(list(parts))

    with patch.object(agent_loop, "call_llm", lambda a: agent.next_reply()), \
         patch.object(agent_loop, "validate_and_compress", lambda a, t: None), \
         patch.object(agent_loop, "check_work_budget", lambda a: None), \
         patch.object(agent_loop, "build_synthetic_feedback", _noop_build), \
         patch.object(agent_loop, "validate_eot", lambda a, t, n: []):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):     # silence the console helpers
            ret = agent_loop.run_rlm_loop(agent)
    return ret, agent, feedback


def _nocode_msgs(feedback):
    """The [SYSTEM: NO CODE] part of every synthetic feedback message."""
    return [part for turn in feedback for part in turn if "NO CODE" in part]


# ---------------------------------------------------------------------------

def test_force_threshold_is_five():
    assert agent_loop._NO_CODE_FORCE == 5


def test_first_no_code_is_a_plain_warning():
    _ret, agent, fb = _drive([PROSE, CODE])
    msgs = _nocode_msgs(fb)
    assert len(msgs) == 1
    assert "[SYSTEM: NO CODE]" in msgs[0]
    assert "attempt" not in msgs[0]              # 1st occurrence: no firmness yet
    assert agent._no_code_streak == 0            # reset by the code block


def test_second_no_code_is_firm_and_names_the_fence_tokens():
    _ret, agent, fb = _drive([PROSE, PROSE, CODE])
    msgs = _nocode_msgs(fb)
    assert len(msgs) == 2
    assert "attempt" not in msgs[0]
    assert "attempt 2 of 5" in msgs[1]
    assert "```python" in msgs[1] and "```" in msgs[1]
    assert agent._no_code_streak == 0


@pytest.mark.parametrize("style,tok", [("std", "```python"), ("at", "@PY"),
                                       ("html", "<py>"), ("quad", "``````python")])
def test_firm_message_uses_active_style(style, tok):
    _ret, _agent, fb = _drive([PROSE, PROSE, CODE], fence_style=style)
    assert tok in _nocode_msgs(fb)[1]


def test_force_end_at_the_fifth_no_code_turn():
    _ret, agent, fb = _drive([PROSE] * 10)
    assert agent.calls == 5                      # stopped, did not spin to max_turns
    assert agent._no_code_streak == 5
    assert agent._cleanup_pending is True
    closed = " ".join(agent.context.closed)
    assert "No code block produced 5 turns in a row" in closed


def test_no_code_does_not_touch_consecutive_errors():
    """The whole point of the separate streak: a no-code turn must not inflate
    the REPL error counter, or 5 prose replies would read as 5 exec failures."""
    _ret, agent, _fb = _drive([PROSE, PROSE, PROSE, CODE, CODE])
    assert agent._repl.error_counts, "the code block should have executed"
    # execute_blocks is handed consecutive_errors + 1; it must never climb above 1
    # just because the model answered in prose.
    assert set(agent._repl.error_counts) == {1}


def test_streak_resets_when_a_block_executes():
    _ret, agent, fb = _drive([PROSE, PROSE, CODE, PROSE, CODE])
    assert agent._no_code_streak == 0
    msgs = _nocode_msgs(fb)
    # streak restarts from 1 after the code block: no firmness on the 2nd run either
    assert len(msgs) == 3
    assert "attempt" not in msgs[2]


def test_prose_only_turn_is_not_reported_as_an_execution_error():
    """No REPL error text should be injected for a prose-only reply."""
    _ret, agent, fb = _drive([PROSE, CODE])
    joined = "\n".join(p for turn in fb for p in turn)
    assert "[REPL error]" not in joined
    assert "Traceback" not in joined


def test_streak_is_reset_at_loop_entry():
    """M1: a stale streak from the PREVIOUS user turn must not end this one.

    Repro: the model answered in prose 4 times at the end of turn N (streak 4,
    never reset because the turn ended on prose). Turn N+1 opens with one prose
    reply -> streak hits 5 and the loop force-ends before the model ever gets to
    emit code. The streak is now reset at loop entry, next to the other
    per-turn counters.
    """
    _ret, agent, fb = _drive([PROSE, CODE], pre_streak=4)
    # Pre-fix: 4 + 1 == 5 -> force-end on the FIRST reply of the new turn.
    closed = " ".join(agent.context.closed)
    assert "No code block produced" not in closed   # pre-fix: force-end here
    assert agent._no_code_streak == 0               # reset by the code block
    msgs = _nocode_msgs(fb)
    assert len(msgs) == 1 and "attempt" not in msgs[0]   # streak restarted at 1


def test_missing_streak_attribute_is_tolerated():
    """getattr(..., 0) default: a fresh agent never sets _no_code_streak up front."""
    _ret, agent, _fb = _drive([PROSE, CODE])
    assert not hasattr(StubAgent(["x"]), "_no_code_streak")   # not an __init__ attr
    assert agent._no_code_streak == 0
