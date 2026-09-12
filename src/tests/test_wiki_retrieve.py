"""Tests for wiki retrieve truncation controls (max_chars, retrieve_full, cut marker)."""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture()
def wik(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_WIKI_DIR", str(tmp_path / "wiki"))
    for name in list(sys.modules):
        if name == "wiki":
            del sys.modules[name]
    w = importlib.import_module("wiki")
    return w


def _long_doc(n=200):
    return "\n".join(f"line {i} of the long living doc" for i in range(n))


class TestRetrieveCaps:
    def test_default_cap_truncates(self, wik, tmp_path):
        wik.add("t1", _long_doc(), entry_type="note")
        out = wik.retrieve("t1")
        assert "... [truncated]" in out
        assert len(out) <= wik._wiki.RETRIEVE_MAX_CHARS + len("... [truncated]") + 64

    def test_max_chars_override_larger(self, wik):
        wik.add("t2", _long_doc(), entry_type="note")
        out_small = wik.retrieve("t2", max_chars=300)
        out_big = wik.retrieve("t2", max_chars=100000)
        assert "... [truncated]" in out_small
        assert "... [truncated]" not in out_big

    def test_retrieve_full_uncapped(self, wik):
        doc = _long_doc(400)  # > 4000 chars
        wik.add("t3", doc, entry_type="note")
        full = wik.retrieve_full("t3")
        assert "... [truncated]" not in full
        assert "line 399 of the long living doc" in full
        assert len(full) > wik._wiki.RETRIEVE_MAX_CHARS


class TestCutMarker:
    def test_loud_warning_when_marker_cut(self, wik):
        doc = "CORE starts\n" + _long_doc(400) + "\nSENTINEL-Q9XZ\ndetail below"
        wik.add("t4", doc, entry_type="note")
        wik.set_cut_marker("SENTINEL-Q9XZ")
        out = wik.retrieve("t4")
        assert "WARNING" in out
        assert "SENTINEL-Q9XZ" in out
        assert "INCOMPLETE" in out
        # loud-warning case must still be bounded, not a silent full-file dump
        assert len(out) < 5000

    def test_no_warning_when_marker_fits(self, wik):
        wik.set_cut_marker("SENTINEL-Q9XZ")
        wik.add("t5", "short doc\nSENTINEL-Q9XZ\nrest", entry_type="note")
        out = wik.retrieve("t5")
        assert "WARNING" not in out
        assert "SENTINEL-Q9XZ" in out
        assert "... [truncated]" not in out

    def test_marker_cleared_by_empty_string(self, wik):
        wik.add("t6", _long_doc(400) + "\nMARK2", entry_type="note")
        wik.set_cut_marker("MARK2")
        assert "WARNING" in wik.retrieve("t6")
        wik.set_cut_marker("")
        assert "WARNING" not in wik.retrieve("t6")

    def test_retrieve_full_ignores_marker(self, wik):
        doc = _long_doc(400) + "\nSENT-X"
        wik.add("t7", doc, entry_type="note")
        wik.set_cut_marker("SENT-X")
        full = wik.retrieve_full("t7")
        assert "WARNING" not in full
        assert "SENT-X" in full
