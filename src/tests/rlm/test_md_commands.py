"""Tests for .md command dispatch in rlm/command_dispatch.py.

Tests:
    TestStripFrontmatter - strip_frontmatter() removes YAML frontmatter
    TestSubstitutePlaceholders - $1, $2, $*, $1+ substitution
    TestDynamicPlaceholders - ${time}, ${date}, ${datetime} substitution
    TestParseMultiPrompt - split on ---, substitute placeholders
    TestMdCommandDiscovery - MD_COMMANDS dict populated from commands/*.md
"""

import pytest
from pathlib import Path
from datetime import datetime

from rlm.command_dispatch import (
    strip_frontmatter,
    substitute_placeholders,
    parse_multi_prompt,
)


class TestStripFrontmatter:
    """Test strip_frontmatter() function."""

    def test_strips_yaml_frontmatter(self):
        content = "---\ndescription: test\n---\nBody text"
        assert strip_frontmatter(content) == "Body text"

    def test_no_frontmatter(self):
        content = "Just body text"
        assert strip_frontmatter(content) == "Just body text"

    def test_empty_content(self):
        assert strip_frontmatter("") == ""

    def test_only_frontmatter(self):
        content = "---\ndescription: test\n---"
        assert strip_frontmatter(content) == ""

    def test_frontmatter_with_multiline_description(self):
        content = "---\ndescription: test\nother: value\n---\nBody"
        assert strip_frontmatter(content) == "Body"


class TestSubstitutePlaceholders:
    """Test substitute_placeholders() function."""

    def test_positional_args(self):
        assert substitute_placeholders("Hello $1", ["World"]) == "Hello World"

    def test_multiple_positional(self):
        assert substitute_placeholders("$1 and $2", ["A", "B"]) == "A and B"

    def test_all_args(self):
        assert substitute_placeholders("$*", ["a", "b", "c"]) == "a b c"

    def test_range_args(self):
        assert substitute_placeholders("$1+", ["first", "second"]) == "first second"

    def test_no_args(self):
        assert substitute_placeholders("Hello $1", []) == "Hello "

    def test_no_placeholders(self):
        assert substitute_placeholders("Hello World", ["test"]) == "Hello World"

    def test_out_of_range(self):
        assert substitute_placeholders("$5", ["a"]) == ""

    def test_complex_substitution(self):
        result = substitute_placeholders("2 * ($1 + $2)", ["1000", "1"])
        assert result == "2 * (1000 + 1)"

    def test_no_substring_collision(self):
        """Verify $1 doesn't match inside $10."""
        # With only 2 args, $10 should not be expanded (only $1-$9 supported)
        result = substitute_placeholders("$1 and $2", ["a", "b"])
        assert result == "a and b"

    def test_reverse_order_prevents_collision(self):
        """Verify $9 is processed before $1 to avoid partial matches."""
        result = substitute_placeholders("$9", ["a", "b", "c", "d", "e", "f", "g", "h", "i"])
        assert result == "i"


class TestDynamicPlaceholders:
    """Test dynamic placeholder substitution via parse_multi_prompt."""

    def test_time_placeholder(self):
        result = parse_multi_prompt("Current time: ${time}", [])
        assert len(result) == 1
        # Verify it's a valid time format HH:MM:SS
        time_str = result[0].replace("Current time: ", "")
        parts = time_str.split(":")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_date_placeholder(self):
        result = parse_multi_prompt("Today: ${date}", [])
        assert len(result) == 1
        # Verify it contains day name and month
        assert "Today:" in result[0]

    def test_datetime_placeholder(self):
        result = parse_multi_prompt("Now: ${datetime}", [])
        assert len(result) == 1
        # Verify it's a valid datetime format YYYY-MM-DD HH:MM:SS
        dt_str = result[0].replace("Now: ", "")
        assert len(dt_str) == 19
        assert dt_str[4] == "-" and dt_str[7] == "-"


class TestParseMultiPrompt:
    """Test parse_multi_prompt() function."""

    def test_single_prompt(self):
        result = parse_multi_prompt("Hello", [])
        assert len(result) == 1
        assert result[0] == "Hello"

    def test_multiple_prompts(self):
        content = "First\n---\nSecond"
        result = parse_multi_prompt(content, [])
        assert len(result) == 2
        assert result[0] == "First"
        assert result[1] == "Second"

    def test_empty_content(self):
        assert parse_multi_prompt("", []) == []

    def test_placeholder_substitution(self):
        content = "Hello $1\n---\nGoodbye $1"
        result = parse_multi_prompt(content, ["World"])
        assert len(result) == 2
        assert result[0] == "Hello World"
        assert result[1] == "Goodbye World"

    def test_md_command_recursion(self):
        content = "/cmd1 arg1\n---\n/ cmd2 arg2"
        result = parse_multi_prompt(content, [])
        assert len(result) == 2
        assert result[0] == "/cmd1 arg1"
        assert result[1] == "/ cmd2 arg2"


class TestMdCommandDiscovery:
    """Test MD_COMMANDS discovery from commands/*.md."""

    def test_md_commands_dict_exists(self):
        from commands import MD_COMMANDS
        assert isinstance(MD_COMMANDS, dict)

    def test_sanitytest1_discovered(self):
        from commands import MD_COMMANDS
        assert "sanitytest1" in MD_COMMANDS

    def test_sanitytest2_discovered(self):
        from commands import MD_COMMANDS
        assert "sanitytest2" in MD_COMMANDS

    def test_md_command_paths_are_valid(self):
        from commands import MD_COMMANDS
        for name, path in MD_COMMANDS.items():
            assert isinstance(path, Path)
            assert path.exists()
            assert path.suffix == ".md"
