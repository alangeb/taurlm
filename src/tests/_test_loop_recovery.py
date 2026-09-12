#!/usr/bin/env python3
"""Loop recovery test harness.

Standalone script for manual observation of escalation behavior.
Simulates a looping agent and prints the full escalation ladder.

Usage: python3 tests/_test_loop_recovery.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_loop_detect import LoopDetector


def main():
    print("=" * 70)
    print("LOOP ESCALATION RECOVERY HARNESS")
    print("=" * 70)

    detector = LoopDetector(
        repeat_threshold=1,
        warn_threshold=1,
        inject_threshold=3,
        force_think_threshold=5,
        end_turn_threshold=8,
    )

    print("\nConfiguration:")
    print("  repeat_threshold: 1")
    print(f"  warn_threshold: {detector.warn_threshold}")
    print(f"  inject_threshold: {detector.inject_threshold}")
    print(f"  force_think_threshold: {detector.force_think_threshold}")
    print(f"  end_turn_threshold: {detector.end_turn_threshold}")

    print(f"\n{'Call':>4} {'Warning':>8} {'Level':>6} {'Injection':>11} {'ForceThink':>11}  Message")
    print("-" * 70)

    for i in range(1, 16):
        warning = detector.detect_tool_loop("file_read", {"path": "same_file.txt"})
        info = detector.get_escalation_info()

        level_label = {
            0: "normal",
            1: "warn",
            2: "inject",
            3: "force-think",
            4: "end-turn",
        }.get(info["escalation_level"], "?")

        msg_preview = ""
        if warning:
            msg_preview = warning[:60] + "..." if len(warning) > 60 else warning

        print(
            f"  {i:>3}  {('W' if warning else '-'):>8}  "
            f"{level_label:>6}  "
            f"{str(info['needs_injection']):>11}  "
            f"{str(info['needs_force_think']):>11}  "
            f"{msg_preview}"
        )

    print("\n" + "=" * 70)
    print("FINAL STATE:")
    print(f"  total_warnings: {detector.total_warnings}")
    print(f"  escalation_level: {detector.escalation_level}")
    print(f"  tool_warnings: {detector.tool_warnings}")
    print(f"  needs_injection: {detector.escalation_level >= 2}")
    print(f"  needs_force_think: {detector.escalation_level >= 3}")
    print("=" * 70)

    # Test reset
    print("\nTesting reset...")
    detector.reset()
    info = detector.get_escalation_info()
    print(f"  After reset: {info}")
    assert info["total_warnings"] == 0
    assert info["escalation_level"] == 0
    print("  Reset: PASS")

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
