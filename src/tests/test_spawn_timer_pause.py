"""Tests for _paused_parent_timer: parent SIGALRM must be PAUSED (not lost,
not double-counted) across a blocking child delegation call."""
from __future__ import annotations

import signal
import time

import pytest

from rlm.kernel_types import _REPLTimeout, _alarm_handler
from rlm.spawn import _paused_parent_timer


@pytest.fixture(autouse=True)
def _install_handler():
    old = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, 0.0)
    yield
    signal.setitimer(signal.ITIMER_REAL, 0.0)
    signal.signal(signal.SIGALRM, old)


def test_noop_when_no_timer_armed():
    signal.setitimer(signal.ITIMER_REAL, 0.0)
    with _paused_parent_timer():
        pass
    remaining = signal.setitimer(signal.ITIMER_REAL, 0.0)[0]
    assert remaining == 0.0


def test_cancels_during_block():
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    with _paused_parent_timer():
        inside = signal.setitimer(signal.ITIMER_REAL, 0.0)[0]
        assert inside == 0.0


def test_restores_remaining_time():
    signal.setitimer(signal.ITIMER_REAL, 10.0)
    with _paused_parent_timer():
        time.sleep(0.2)
    remaining = signal.setitimer(signal.ITIMER_REAL, 0.0)[0]
    assert 9.0 < remaining < 10.0


def test_long_blocking_call_not_killed():
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        with _paused_parent_timer():
            time.sleep(0.7)
    except _REPLTimeout:
        pytest.fail("parent alarm fired during paused blocking call")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)


def test_parent_budget_fires_after_exit():
    signal.setitimer(signal.ITIMER_REAL, 0.4)
    with _paused_parent_timer():
        pass
    fired = False
    try:
        time.sleep(0.6)
    except _REPLTimeout:
        fired = True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
    assert fired
    assert issubclass(_REPLTimeout, BaseException)
