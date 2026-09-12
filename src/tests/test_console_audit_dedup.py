"""Regression tests for P7-B6-01: console audit messages logged exactly once.

display_warn_audit / display_error / display_warn and audit=True templates
must emit exactly ONE audit record per call (no double/triple-logging).
"""

from contextlib import contextmanager

import agent_audit_bridge as bridge
from agent_console.audit import display_warn_audit
from agent_console.primitives import display_warn, display_error
from agent_console.templates import _msg


class _StubWriter:
    def __init__(self):
        self.recs = []

    def _console_error(self, m): self.recs.append(("ERR", m))
    def _console_warning(self, m): self.recs.append(("WARN", m))
    def _console_info(self, m): self.recs.append(("INFO", m))
    def _console_success(self, m): self.recs.append(("OK", m))


@contextmanager
def _stub():
    """Install a stub audit writer, restoring the original afterward."""
    s = _StubWriter()
    original = bridge._audit_writer
    bridge.set_audit_writer(s)
    try:
        yield s
    finally:
        bridge.set_audit_writer(original)


def test_display_warn_audit_single_record():
    with _stub() as s:
        display_warn_audit("boom")
        assert s.recs == [("WARN", "boom")], s.recs


def test_display_warn_single_record():
    with _stub() as s:
        display_warn("boom")
        assert s.recs == [("WARN", "boom")], s.recs


def test_display_error_single_record():
    with _stub() as s:
        display_error("boom")
        assert s.recs == [("ERR", "boom")], s.recs


def test_template_warning_audit_single_record():
    with _stub() as s:
        msg = _msg("warning", "T{}", audit=True)
        msg("v")
        assert s.recs == [("WARN", "Tv")], s.recs


def test_template_error_audit_single_record():
    with _stub() as s:
        msg = _msg("error", "E{}", audit=True)
        msg("v")
        assert s.recs == [("ERR", "Ev")], s.recs
