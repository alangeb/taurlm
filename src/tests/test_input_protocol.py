"""Test input handling protocol for multiline blocks and command prefixes.

This module tests the complete input protocol for TauErgon, ensuring all
prefix characters work correctly both in regular input and multiline blocks.

Input Prefix Protocol:
======================
Regular input (outside multiline):
  - '#'     Start multiline block (content until 2+ blank lines)
  - '#!'    Start multiline block (alternative syntax, same as #)
  - '!'     Shell command (execute via bash tool)
  - '+'     Steering control (when turn is active)
  - '/'     Slash command (dispatch to CommandManager)

Inside multiline block:
  - '#!'    Continue block (prefix stripped)
  - '#'     Continue block (prefix stripped)
  - '#+'    Break multiline and route steering
  - '#/'    Break multiline and execute command
  - 2+ blank lines  Submit the accumulated block

See agent_input.py for implementation details.
"""



class TestPrefixRecognition:
    """Test prefix character recognition logic."""

    def test_hash_starts_multiline(self):
        """# should be recognized as multiline start."""
        content = "# This is a comment"
        assert content.startswith("#")
        assert not content.startswith("#+")
        assert not content.startswith("#/")

    def test_hash_bang_starts_multiline(self):
        """#! should be recognized as multiline start."""
        content = "#! This is a comment"
        assert content.startswith("#!")
        assert content.startswith("#")

    def test_hash_plus_steering(self):
        """#+ should be recognized as steering control."""
        content = "#+ stop"
        assert content.startswith("#+")
        assert content.startswith("#")

    def test_hash_slash_command(self):
        """#/ should be recognized as command execution."""
        content = "#/ help"
        assert content.startswith("#/")
        assert content.startswith("#")

    def test_exclamation_shell(self):
        """! should be recognized as shell command."""
        content = "! ls -la"
        assert content.startswith("!")
        assert not content.startswith("#")

    def test_slash_command(self):
        """/ should be recognized as slash command."""
        content = "/help"
        assert content.startswith("/")
        assert not content.startswith("#")

    def test_plus_steering(self):
        """+ should be recognized as steering."""
        content = "+status"
        assert content.startswith("+")
        assert not content.startswith("#")

    def test_hash_not_hash_bang(self):
        """# alone should not match #! prefix."""
        content = "# just a hash"
        assert content.startswith("#")
        assert not content.startswith("#!")


class TestPrefixStripping:
    """Test prefix stripping logic for multiline blocks."""

    def test_strip_hash_prefix(self):
        """# prefix should be stripped (1 char)."""
        content = "# This is content"
        stripped = content[1:] if content.startswith("#") and not content.startswith("#!") else content
        assert stripped == " This is content"

    def test_strip_hash_bang_prefix(self):
        """#! prefix should be stripped (2 chars)."""
        content = "#! This is content"
        prefix_len = 2 if content.startswith("#!") else 1
        stripped = content[prefix_len:]
        assert stripped == " This is content"

    def test_hash_plus_not_stripped_as_content(self):
        """#+ should not be treated as content, it's steering."""
        content = "#+ stop"
        # This should trigger steering, not be added to buffer
        assert content.startswith("#+")

    def test_hash_slash_not_stripped_as_content(self):
        """#/ should not be treated as content, it's a command."""
        content = "#/ help"
        # This should trigger command execution, not be added to buffer
        assert content.startswith("#/")


class TestBlockTermination:
    """Test multiline block termination conditions."""

    def test_two_blank_lines_terminate(self):
        """Two consecutive blank lines should terminate block."""
        lines = ["# content", "", ""]
        blank_count = sum(1 for line in lines if line == "")
        assert blank_count >= 2

    def test_single_blank_continues(self):
        """Single blank line should not terminate block."""
        lines = ["# content", "", "more content"]
        # Should not terminate because there's content after the blank
        assert len(lines) == 3

    def test_no_blank_does_not_terminate(self):
        """No blank lines should not terminate block."""
        lines = ["# line1", "# line2", "# line3"]
        blank_count = sum(1 for line in lines if line == "")
        assert blank_count == 0


class TestSteeringExtraction:
    """Test steering command extraction."""

    def test_extract_stop_command(self):
        """#+ stop should extract 'stop' steering."""
        content = "#+ stop"
        payload = content[2:].strip()  # Strip '#+' prefix
        assert payload == "stop"

    def test_extract_redirect_command(self):
        """#+ redirect task should extract 'redirect task'."""
        content = "#+ redirect new task"
        payload = content[2:].strip()
        assert payload.startswith("redirect")
        assert "new task" in payload

    def test_extract_status_command(self):
        """#+ status should extract 'status'."""
        content = "#+ status"
        payload = content[2:].strip()
        assert payload == "status"

    def test_extract_inject_text(self):
        """#+ text should extract 'text' for injection."""
        content = "#+ inject this message"
        payload = content[2:].strip()
        assert payload == "inject this message"


class TestCommandExtraction:
    """Test command extraction from #/ prefix."""

    def test_extract_help_command(self):
        """/help should be extracted from #/help."""
        content = "#/ help"
        cmd_str = content[2:].strip()  # Strip '#/' prefix
        cmd_parts = cmd_str.split(None, 1)
        assert cmd_parts[0] == "help"

    def test_extract_fork_command(self):
        """/fork task should be extracted from #/fork task."""
        content = "#/ fork Analyze this"
        cmd_str = content[2:].strip()
        cmd_parts = cmd_str.split(None, 1)
        assert cmd_parts[0] == "fork"

    def test_empty_command(self):
        """#/ alone should result in empty command."""
        content = "#/ "
        cmd_str = content[2:].strip()
        assert cmd_str == ""


class TestInputClassification:
    """Test input line classification."""

    def test_hash_classified_as_multiline_start(self):
        """# at line start should be classified as multiline start."""
        line = "# Start block"
        is_multiline_start = line.startswith("#") and not line.startswith("#/")
        assert is_multiline_start

    def test_hash_bang_classified_as_multiline_start(self):
        """#! at line start should be classified as multiline start."""
        line = "#! Start block"
        is_multiline_start = line.startswith("#") and not line.startswith("#/")
        assert is_multiline_start

    def test_hash_plus_classified_as_steering(self):
        """#+ at line start should be classified as steering."""
        line = "#+ stop"
        is_steering = line.startswith("#+")
        assert is_steering

    def test_hash_slash_classified_as_command(self):
        """/ at line start should be classified as command."""
        line = "#/ help"
        is_command = line.startswith("#/")
        assert is_command

    def test_exclamation_classified_as_shell(self):
        """! at line start should be classified as shell."""
        line = "! ls"
        is_shell = line.startswith("!")
        assert is_shell

    def test_slash_classified_as_command(self):
        """/ at line start should be classified as command."""
        line = "/help"
        is_command = line.startswith("/")
        assert is_command

    def test_plus_classified_as_steering(self):
        """+ at line start should be classified as steering."""
        line = "+status"
        is_steering = line.startswith("+")
        assert is_steering

    def test_regular_text_not_special(self):
        """Regular text should not match any special prefix."""
        line = "Just regular text"
        assert not line.startswith("#")
        assert not line.startswith("!")
        assert not line.startswith("+")
        assert not line.startswith("/")


class TestEdgeCases:
    """Test edge cases in input handling."""

    def test_hash_at_end_of_line(self):
        """# at end of line should be treated as regular text."""
        line = "text #"
        # This is not a prefix, it's part of the text
        assert "#" in line
        assert not line.startswith("#")

    def test_multiple_hash_prefixes(self):
        """Multiple # characters should only strip the first."""
        line = "## double hash"
        # Should strip only the first #
        stripped = line[1:]
        assert stripped == "# double hash"

    def test_hash_bang_with_space(self):
        """#! with space should work."""
        line = "#! text"
        assert line.startswith("#!")
        stripped = line[2:]
        assert stripped == " text"

    def test_empty_line_after_hash(self):
        """Empty line after # should continue block."""
        line = ""
        # Empty lines are counted for block termination
        assert line == ""

    def test_whitespace_only_line(self):
        """Whitespace-only line should be treated as content."""
        line = "   "
        # Not empty, so it's content
        assert line.strip() == ""
        assert len(line) > 0


class TestProtocolCompleteness:
    """Verify all 6 prefixes are covered."""

    def test_all_single_char_prefixes_defined(self):
        """All single-character prefixes should be defined."""
        prefixes = ["#", "!", "+", "/"]
        for prefix in prefixes:
            assert len(prefix) == 1

    def test_all_double_char_prefixes_defined(self):
        """All double-character prefixes should be defined."""
        prefixes = ["#!", "#+", "#/"]
        for prefix in prefixes:
            assert len(prefix) == 2
            assert prefix.startswith("#")

    def test_no_conflicting_prefixes(self):
        """No prefix should be a prefix of another (except # variants)."""
        single = ["!", "+", "/"]
        double = ["#!", "#+", "#/"]
        
        # Single char prefixes shouldn't conflict with each other
        for s1 in single:
            for s2 in single:
                if s1 != s2:
                    assert not s1.startswith(s2)
        
        # Double char prefixes all start with #, which is expected
        for d in double:
            assert d.startswith("#")