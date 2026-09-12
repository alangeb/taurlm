"""Tests for audit log parsing and rendering."""

from pathlib import Path

from agent_console import parse_audit_file


def test_parse_audit_file_empty(tmp_path: Path):
    """Test parsing an empty audit file."""
    f = tmp_path / "test.audit"
    f.write_text("")

    records = list(parse_audit_file(f))
    assert len(records) == 0


def test_parse_audit_file_basic(tmp_path: Path):
    """Test parsing a basic audit file with one record (new stack=SS format)."""
    f = tmp_path / "test.audit"
    f.write_text("[2026-09-12T14:57:03+00:00] USER stack=.\n  | hello world\n")

    records = list(parse_audit_file(f))
    assert len(records) == 1
    record = records[0]
    assert record.record_type == "USER"
    assert record.nesting == 0
    assert "hello world" in record.content_blocks.get("", ["hello world"])


def test_parse_audit_file_nested_new_format(tmp_path: Path):
    """Test parsing audit file with nested records (new stack=SS format)."""
    f = tmp_path / "test.audit"
    f.write_text(
        "[2026-09-12T14:57:03+00:00] FORK_START task=\"test\" stack=.\n"
        "[2026-09-12T14:57:03+00:00] USER stack=S\n"
        "  | nested message\n"
        "[2026-09-12T14:57:03+00:00] FORK_END duration_s=1.0 stack=.\n"
    )

    records = list(parse_audit_file(f))
    assert len(records) == 3
    assert records[0].record_type == "FORK_START"
    assert records[0].nesting == 0
    assert records[1].record_type == "USER"
    assert records[1].nesting == 1
    assert records[2].record_type == "FORK_END"
    assert records[2].nesting == 0


def test_parse_audit_file_nested(tmp_path: Path):
    """Test parsing audit file with nested records (old nesting=N format)."""
    f = tmp_path / "test.audit"
    f.write_text(
        "[2026-09-12T14:57:03+00:00] FORK_START task=\"test\" nesting=0\n"
        "[2026-09-12T14:57:03+00:00] USER nesting=1\n"
        "  | nested message\n"
        "[2026-09-12T14:57:03+00:00] FORK_END duration_s=1.0 nesting=0\n"
    )

    records = list(parse_audit_file(f))
    assert len(records) == 3
    assert records[0].record_type == "FORK_START"
    assert records[0].nesting == 0
    assert records[1].record_type == "USER"
    assert records[1].nesting == 1
    assert records[2].record_type == "FORK_END"
    assert records[2].nesting == 0


def test_parse_audit_file_old_format(tmp_path: Path):
    """Test parsing old audit file without nesting field."""
    f = tmp_path / "test.audit"
    f.write_text("[2026-09-12T14:57:03+00:00] USER\n  | hello\n")

    records = list(parse_audit_file(f))
    assert len(records) == 1
    record = records[0]
    assert record.record_type == "USER"
    assert record.nesting == 0  # Default for old files


def test_parse_audit_file_with_fields(tmp_path: Path):
    """Test parsing audit file with fields (new stack=SS format)."""
    f = tmp_path / "test.audit"
    f.write_text(
        "[2026-09-12T14:57:03+00:00] TOOL_CALL id=call_1 final_name=fetch fixes=none stack=.\n"
        "  | final_args:\n"
        '  |   {"url": "https://example.com"}\n'
    )

    records = list(parse_audit_file(f))
    assert len(records) == 1
    record = records[0]
    assert record.record_type == "TOOL_CALL"
    assert record.fields["id"] == "call_1"
    assert record.fields["final_name"] == "fetch"
    assert record.nesting == 0


def test_parse_audit_file_quoted_values(tmp_path: Path):
    """Test parsing audit file with quoted values (new stack=SS format)."""
    f = tmp_path / "test.audit"
    f.write_text(
        "[2026-09-12T14:57:03+00:00] FORK_START task='test task with spaces' stack=.\n"
    )

    records = list(parse_audit_file(f))
    assert len(records) == 1
    record = records[0]
    assert record.record_type == "FORK_START"
    assert record.fields["task"] == "test task with spaces"
