"""Tests for agent_console.primitives module."""

import sys
from pathlib import Path
from unittest import TestCase

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_console.primitives import format_duration_ms


class TestFormatDurationMs(TestCase):
    """Test format_duration_ms function."""

    def test_zero_ms(self):
        """Zero ms formats as 0ms."""
        self.assertEqual(format_duration_ms(0), "0ms")

    def test_sub_second(self):
        """Sub-second durations format as Xms."""
        self.assertEqual(format_duration_ms(500), "500ms")
        self.assertEqual(format_duration_ms(999), "999ms")

    def test_one_second(self):
        """Exactly 1000ms formats as 1.0s."""
        self.assertEqual(format_duration_ms(1000), "1.0s")

    def test_over_one_second(self):
        """Over 1000ms formats as X.Xs."""
        self.assertEqual(format_duration_ms(1500), "1.5s")
        self.assertEqual(format_duration_ms(2345), "2.3s")

    def test_large_duration(self):
        """Large durations format correctly."""
        self.assertEqual(format_duration_ms(12345), "12.3s")
        self.assertEqual(format_duration_ms(60000), "60.0s")

    def test_fractional_ms(self):
        """Fractional ms rounds correctly."""
        self.assertEqual(format_duration_ms(999.9), "1000ms")
        self.assertEqual(format_duration_ms(1000.0), "1.0s")
        self.assertEqual(format_duration_ms(1000.4), "1.0s")
        self.assertEqual(format_duration_ms(1000.6), "1.0s")