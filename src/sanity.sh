#!/bin/bash
# Sanity Test Suite for Tau RLM Agent — BLACKBOX TESTS
# Each test is a SINGLE invocation of tau.py via CLI.
# No internal imports, no whitebox checks. Just prompts in, output parsed.
#
# Guided by ../tau/src/sanity.sh (handcoded reference).

# ============================================================================
# CRITICAL: sanity.sh IS THE GOLD STANDARD. IT MUST PASS 100%.
# ============================================================================
#
# ABSOLUTE RULES:
#   1. sanity.sh MUST pass 100% — zero failures, zero exceptions.
#   2. "Pre-existing error" or "not caused by current edits" is NOT valid.
#   3. If sanity.sh fails, STOP EVERYTHING and fix the root cause.
#   4. Do not assume model failure — always investigate the code.
#   5. NEVER modify tests, prompts, or expectations in sanity.sh.
#   6. You cannot move forward until sanity.sh passes 100%.
#
# WHY THIS MATTERS:
#   sanity.sh is the only gate between broken code and production. A single
#   failure means something fundamental is broken. Patching around failures
#   or blaming the model compounds technical debt. The correct response to
#   ANY failure is: investigate, find root cause, fix the code, re-run.
#
# WHEN sanity.sh FAILS:
#   1. Read the full log: cat $SANITY_LOG
#   2. Check agent log: cat $SANITY_AGENT_LOG
#   3. Reproduce the failing test manually
#   4. Investigate the code path that caused the failure
#   5. Fix the root cause — never change the test
#   6. Re-run sanity.sh until it passes 100%.
#
# ============================================================================
# The prompts (questions) are deliberately crafted the way they are -
# they should NOT be modified by LLM.
# ============================================================================

# Resolve script directory — all paths are relative to this
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

# DUT — Device Under Test (tau.py)
# IMPORTANT: We use a COPY (tau-sanity.py) so that cleanup kills only the test
# process and does NOT kill other tau.py sessions running on this system.
DUTFILE="$SCRIPT_DIR/tau.py"
SANITY_DUTFILE="$SCRIPT_DIR/tau-sanity.py"

# Create the copy (always fresh to pick up latest changes)
cp -f "$DUTFILE" "$SANITY_DUTFILE"

# Kill any leftover tau-sanity.py processes (NOT tau.py — that would kill others)
pkill -f "tau-sanity.py" 2>/dev/null || true
sleep 1

# Cleanup function: kill test process + remove copy
cleanup_sanity() {
    pkill -f "tau-sanity.py" 2>/dev/null || true
    rm -f "$SANITY_DUTFILE"
}
trap cleanup_sanity EXIT

# Optional: pass a positional parameter to override the LLM group
DUT="python3 $SANITY_DUTFILE"
if [ $# -ge 1 ]; then
    DUT="python3 $SANITY_DUTFILE --llm $1"
fi

# Redirect test log files away from the real log directory.
export TAU_LOG_DIR="${TAU_LOG_DIR:-$HOME/.local/tau/logtest}"

# Temp files for capturing output and test data
TEMP_FILE="/tmp/sanity_test_$$"
SANITY_LOG="${SANITY_LOG:-/tmp/sanity_complete.log}"
SANITY_AGENT_LOG="${SANITY_AGENT_LOG:-/tmp/sanity_agent.log}"

# Test data files (cleaned up at end)
TEST_FILE_X="/tmp/sanity_x_$$"
TEST_FILE_Y="/tmp/sanity_y_$$"
TEST_FILE_RESULT="/tmp/sanity_result_$$"

# Cleanup test data files
cleanup_files() {
    rm -f "$TEST_FILE_X" "$TEST_FILE_Y" "$TEST_FILE_RESULT" "$TEMP_FILE"
}
trap cleanup_files EXIT

# Initialize the complete sanity log (delete any leftover from previous run)
> "$SANITY_LOG"

# Append test output + metadata to the complete sanity log.
# Usage: log_test "Test description" duration result
log_test() {
    local desc="$1"
    local duration="$2"
    local result="$3"
    {
        echo "========================================"
        echo "TEST: $desc | $(date '+%Y-%m-%d %H:%M:%S') | $result | ${duration}s"
        echo "========================================"
        cat "$TEMP_FILE"
        echo ""
    } >> "$SANITY_LOG"
}

# Compute PASS/FAIL based on whether PASSED or FAILED increased since last call.
# A test is FAIL if ANY failure occurred (FAILED increased), regardless of PASSED.
# A test is PASS only if PASSED increased AND FAILED did not increase.
# Usage: result_since_test $PASSED_BEFORE $FAILED_BEFORE
result_since_test() {
    if [ "$FAILED" -gt "$2" ]; then
        echo "FAIL"
    elif [ "$PASSED" -gt "$1" ]; then
        echo "PASS"
    else
        echo "SKIP"
    fi
}

pass() {
    echo -e "${GREEN}✅ $1${NC}"
    ((PASSED++))
}

fail() {
    echo -e "${RED}❌ $1${NC}"
    ((FAILED++))
}

show_failure_output() {
    local file="$1"
    echo "  Output:"
    cat "$file"
}

# Check for unhandled exceptions or critical errors in $TEMP_FILE.
# Call at the start of EVERY test validation (before pass/expect/expect_not).
# Returns 0 if errors found (caller should fail), 1 if clean.
has_exceptions() {
    grep -qiE "Traceback|AttributeError|cannot append assistant after role|consecutive assistant messages|append assistant message after system message" "$TEMP_FILE"
}

# Expect patterns to be present in output file.
# Usage: expect "$TEMP_FILE" "test description" "pattern1" "pattern2" ...
expect() {
    local file="$1"
    local msg="$2"
    shift 2

    if has_exceptions; then
        fail "$msg FAILED (unhandled exception in output)"
        show_failure_output "$file"
        return 1
    fi

    local pattern
    for pattern in "$@"; do
        if ! grep -qi -- "$pattern" "$file"; then
            fail "$msg FAILED (expected: $pattern)"
            show_failure_output "$file"
            return 1
        fi
    done

    pass "$msg WORKS" || return 1
}

# Expect patterns to be ABSENT from output file.
# Usage: expect_not "$TEMP_FILE" "test description" "pattern1" "pattern2" ...
expect_not() {
    local file="$1"
    local msg="$2"
    shift 2

    if has_exceptions; then
        fail "$msg FAILED (unhandled exception in output)"
        show_failure_output "$file"
        return 1
    fi

    local pattern
    for pattern in "$@"; do
        if grep -qi -- "$pattern" "$file"; then
            fail "$msg FAILED (unexpected: $pattern)"
            show_failure_output "$file"
            return 1
        fi
    done

    pass "$msg WORKS" || return 1
}

echo "========================================"
echo "  Tau RLM Agent Sanity Test Suite (Blackbox)"
echo "========================================"

# ============================================
# TEST 1: Math via stdin pipe
# ============================================
# Pipe a single math prompt into the agent via stdin.
# The agent should compute X+Y and output the correct result.
# ============================================
echo -e "\nTest 1: Math via stdin pipe..."
{
    echo "Lets do some math. Just think about this and answer. X=33333, Y=22222, what is X+Y"
} | timeout 300 $DUT 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Math via stdin pipe (X+Y=55555)" \
    "55555"
log_test "Test 1: Math via stdin pipe" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 2: Math via single positional argument
# ============================================
# Pass the same math prompt as a single positional argument.
# The agent should compute X+Y and output the correct result.
# ============================================
echo -e "\nTest 2: Math via positional argument..."
{
    timeout 300 $DUT "Lets do some math. Just think about this and answer. X=33333, Y=22222, what is X+Y"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Math via positional argument (X+Y=55555)" \
    "55555"
log_test "Test 2: Math via positional argument" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 3: Math via multiple positional arguments
# ============================================
# Pass the math problem as multiple positional arguments (multi-turn simulation).
# The agent should carry state across arguments and compute the result.
# ============================================
echo -e "\nTest 3: Math via multiple positional arguments..."
{
    timeout 300 $DUT "Lets do some math. Just think about it and answer when asked. X=33333" "Y=22222" "What is X+Y"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Math via multiple positional arguments (X+Y=55555)" \
    "55555"
log_test "Test 3: Math via multiple positional arguments" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 4: File I/O — read X, read Y, write result
# ============================================
# Create files with X and Y values. Instruct the agent to read both files,
# compute X+Y, and write the result to a third file. Verify the result.
# ============================================
echo -e "\nTest 4: File I/O (read X, read Y, write result)..."

# Create input files
echo "33333" > "$TEST_FILE_X"
echo "22222" > "$TEST_FILE_Y"
rm -f "$TEST_FILE_RESULT"

# Run agent with file I/O instructions
{
    timeout 300 $DUT "Read the file $TEST_FILE_X to get value X. Read the file $TEST_FILE_Y to get value Y. Compute X+Y. Write the result (just the number) to the file $TEST_FILE_RESULT. Confirm the file was created and contains the correct value."
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0

# Check output contains the expected result
if ! expect \
    "$TEMP_FILE" \
    "File I/O output mentions result" \
    "55555"; then
    # Output didn't contain 55555 — check if file was at least created
    if [ -f "$TEST_FILE_RESULT" ]; then
        fail "File I/O output missing 55555 but result file exists"
    else
        fail "File I/O failed — no output confirmation and no result file"
    fi
else
    # Output looks good — verify the actual file content
    if [ -f "$TEST_FILE_RESULT" ]; then
        RESULT_CONTENT=$(cat "$TEST_FILE_RESULT" | tr -d '[:space:]')
        # Accept the integer or its float form (e.g. 55555, 55555.0, 55555.00);
        # reject any other numeric value (55555.5, 555550, 5555.5, ...).
        if [[ "$RESULT_CONTENT" =~ ^55555(\.0+)?$ ]]; then
            pass "File I/O result file contains 55555 (got: $RESULT_CONTENT)"
        else
            fail "File I/O result file has wrong content: '$RESULT_CONTENT' (expected '55555' or float '55555.0')"
        fi
    else
        fail "File I/O — result file $TEST_FILE_RESULT was not created"
    fi
fi

# Cleanup test files
rm -f "$TEST_FILE_X" "$TEST_FILE_Y" "$TEST_FILE_RESULT"

log_test "Test 4: File I/O (read X, read Y, write result)" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 5: Slash command /help displays command list
# ============================================
# Verify /help shows HELP header and lists known commands (both .py and .md).
# ============================================
echo -e "\nTest 5: Slash command /help..."
{
    timeout 120 $DUT "/help"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "/help shows HELP header" \
    "HELP"
expect \
    "$TEMP_FILE" \
    "/help lists /goal command" \
    "/goal"
expect \
    "$TEMP_FILE" \
    "/help lists /sanitytest1 command" \
    "/sanitytest1"
log_test "Test 5: Slash command /help" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 6: Unknown slash command shows error
# ============================================
# Verify unknown commands produce "Unknown command:" error message.
# ============================================
echo -e "\nTest 6: Unknown slash command error..."
{
    timeout 120 $DUT "/nonexistent_command_12345"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Unknown command error message" \
    "Unknown command: /nonexistent_command_12345"
log_test "Test 6: Unknown slash command error" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 7: /agents command lists sub-agents
# ============================================
# Verify /agents command works and shows agent status.
# ============================================
echo -e "\nTest 7: /agent command..."
{
    timeout 120 $DUT "/agent"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "/agent shows agent status" \
    "No persistent agents."
log_test "Test 7: /agent command" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 8: .md command with multiprompt and recursion
# ============================================
# /sanitytest1 1000 calls /sanitytest2 twice:
#   /sanitytest2 1000 1 → 2*(1000+1) = 2002
#   /sanitytest2 1000 2 → 2*(1000+2) = 2004
# Verify both results appear in output.
# ============================================
echo -e "\nTest 8: .md command multiprompt + recursion..."
{
    timeout 300 $DUT "/sanitytest1 1000"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    ".md command result 2*(1000+1)=2002" \
    "2002"
expect \
    "$TEMP_FILE" \
    ".md command result 2*(1000+2)=2004" \
    "2004"
log_test "Test 8: .md command multiprompt + recursion" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 9: /ctx command displays context
# ============================================
# Verify /ctx summary shows CONTEXT SUMMARY header and message count.
# ============================================
echo -e "\nTest 9: /ctx command..."
{
    timeout 120 $DUT "hello" "/ctx"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "/ctx shows CONTEXT SUMMARY" \
    "CONTEXT SUMMARY"
expect \
    "$TEMP_FILE" \
    "/ctx shows message count" \
    "messages"
log_test "Test 9: /ctx command" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# SUMMARY
# ============================================

echo ""
echo "========================================"
echo "  Results"
echo "========================================"
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [ $FAILED -eq 0 ]; then
    echo -e "\n${GREEN}All tests passed! ✅${NC}"
    rm -f "$TEMP_FILE"
    exit 0
else
    echo -e "\n${RED}Some tests failed!${NC}"
    echo "  Check logs for details:"
    echo "    $SANITY_LOG"
    echo "    $SANITY_AGENT_LOG"
    echo "  CRITICAL: sanity.sh has to be a 100% pass, no exceptions. Regardless of whether related or unrelated to recent changes, sanity.sh must fully pass. YOU OWN THIS!"
    exit 1
fi
