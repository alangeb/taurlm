"""P0: bash output is clamped at the SOURCE for the context copy, while the
FULL output survives in the _output{N} namespace var.

Regression: bash stdout/stderr were appended RAW to the transcript (block_executor
appended unbounded stdout; only the python path was capped via _OutputCapture),
so a single large bash result could silently blow the context window.
"""
from agent_repl_parse import CodeBlock
from rlm.kernel import PythonKernel


def _bash(code: str) -> CodeBlock:
    return CodeBlock(language="bash", code=code)


def test_bash_context_copy_clamped_and_output_full():
    cap = 1000  # small cap so the test is fast
    kernel = PythonKernel(max_output_chars=cap)
    # Produce ~5000 bytes of stdout.
    result = kernel.execute_blocks([_bash("yes x | head -n 5000")])
    assert result.success, result.error
    # Context copy must be <= cap + marker.
    assert len(result.output) <= cap + 100, (
        f"context output {len(result.output)} exceeds cap+marker"
    )
    assert "full output in _output" in result.output
    # The namespace copy retains the FULL (or ~1MB-capped) output.
    ns_key = f"_output{result.code_seq}"
    assert ns_key in kernel.namespace
    stored = kernel.namespace[ns_key]
    # 5000 lines of "x" + newlines ~= 10000 chars; far above the 1000 cap.
    assert len(stored) > cap, f"_output only {len(stored)} chars, expected full"


def test_bash_small_output_not_clamped():
    kernel = PythonKernel(max_output_chars=8192)
    result = kernel.execute_blocks([_bash("echo hello")])
    assert result.success, result.error
    assert "hello" in result.output
    assert "truncated" not in result.output


def test_aggregate_multi_block_clamped():
    # Many bash blocks each under the per-block cap but summing over the
    # aggregate cap must be clamped at the aggregate level.
    from rlm.block_executor import AGGREGATE_OUTPUT_CAP
    cap = 8192
    assert AGGREGATE_OUTPUT_CAP > cap
    kernel = PythonKernel(max_output_chars=cap)
    # Each block ~7000 chars (under per-block cap), 8 blocks -> ~56k > 32k.
    blocks = [_bash("yes x | head -n 3500") for _ in range(8)]
    result = kernel.execute_blocks(blocks)
    assert result.success, result.error
    assert len(result.output) <= AGGREGATE_OUTPUT_CAP + 200, len(result.output)
    assert "aggregate output truncated" in result.output


def test_output_ns_ram_guard():
    # _output{N} is RAM-guarded (~1MB) but NOT capped at max_output_chars.
    kernel = PythonKernel(max_output_chars=100)
    big = 2_000_000
    result = kernel.execute_blocks([_bash(f"yes x | head -n {big//2}")])
    assert result.success, result.error
    stored = kernel.namespace[f"_output{result.code_seq}"]
    # RAM guard caps near 1MB, not at 100.
    assert len(stored) > 100_000, len(stored)
    assert len(stored) <= kernel._OUTPUT_NS_RAM_GUARD + 100, len(stored)
