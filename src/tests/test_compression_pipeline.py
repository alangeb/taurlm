"""Test compression pipeline execution with a synthetic large context.

Verifies the 9 pipeline steps execute in order and that debug output
is generated correctly. Uses a synthetic context (no external file needed).
"""

import pytest


def _make_large_context(num_turns: int = 30) -> list[dict]:
    """Build a realistic RLM context large enough to exercise all pipeline steps.

    Pattern: system → (user(real) → assistant(code) → user(repl) → assistant(summary)) × N
    """
    context = [
        {"role": "system", "content": "You are a helpful Python assistant working in a REPL. Execute code to solve tasks."}
    ]

    for i in range(num_turns):
        context.append({
            "role": "user",
            "content": f"[U:real | N:0 | M:{i*4} | C:{i*3}%] Task {i}: Write a comprehensive data processing module for dataset {i}. Include: main function with type hints, input validation with custom exceptions, logging at DEBUG/INFO/WARNING levels, unit test examples in the docstring, and a CLI entry point using argparse. The data format is CSV with columns: id, name, value, timestamp. Handle missing values, duplicate IDs, and malformed timestamps gracefully with appropriate error messages and fallback behavior."
        })
        code = (
            f"```python\n"
            f"import logging\n"
            f"from dataclasses import dataclass\n"
            f"from typing import Optional\n"
            f"from datetime import datetime\n\n"
            f"logger = logging.getLogger(__name__)\n\n"
            f"class DataValidationError(Exception):\n"
            f'    """Raised when input data fails validation."""\n'
            f"    pass\n\n"
            f"@dataclass\n"
            f"class Record:\n"
            f"    id: int\n"
            f"    name: str\n"
            f"    value: float\n"
            f"    timestamp: Optional[datetime]\n\n"
            f"def process_dataset_{i}(csv_data: list[dict]) -> list[Record]:\n"
            f'    """Process dataset {i} from CSV rows.\n\n'
            f"    Handles: missing values, duplicate IDs, malformed timestamps.\n\n"
            f"    Example:\n"
            f'        >>> rows = [{{"id": "1", "name": "test", "value": "3.14", "timestamp": "2026-09-12"}}]\n'
            f"        >>> records = process_dataset_{i}(rows)\n"
            f"        >>> records[0].value\n"
            f"        3.14\n"
            f'    """\n'
            f"    seen_ids = set()\n"
            f"    records = []\n"
            f"    for row_idx, row in enumerate(csv_data):\n"
            f"        try:\n"
            f'            rec_id = int(row.get("id", ""))\n'
            f"            if rec_id in seen_ids:\n"
            f'                logger.warning(f"Duplicate ID {{rec_id}} at row {{row_idx}}, skipping")\n'
            f"                continue\n"
            f"            seen_ids.add(rec_id)\n"
            f'            name = row.get("name", "").strip() or f"unnamed_{{rec_id}}"\n'
            f'            value = float(row.get("value", "0") or "0")\n'
            f'            ts_raw = row.get("timestamp", "")\n'
            f"            ts = None\n"
            f'            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):\n'
            f"                try:\n"
            f"                    ts = datetime.strptime(ts_raw, fmt)\n"
            f"                    break\n"
            f"                except ValueError:\n"
            f"                    continue\n"
            f"            if ts is None and ts_raw:\n"
            f'                logger.warning(f"Malformed timestamp: {{ts_raw!r}}")\n'
            f"            records.append(Record(rec_id, name, value, ts))\n"
            f"        except (ValueError, TypeError) as e:\n"
            f'            logger.error(f"Row {{row_idx}} failed: {{e}}")\n'
            f"            continue\n"
            f'    logger.info(f"Dataset {i}: processed {{len(records)}}/{{len(csv_data)}} rows")\n'
            f"    return records\n\n"
            f'if __name__ == "__main__":\n'
            f"    import argparse, csv\n"
            f'    parser = argparse.ArgumentParser(description=f"Process dataset {i}")\n'
            f'    parser.add_argument("--input", required=True)\n'
            f'    parser.add_argument("--verbose", action="store_true")\n'
            f"    args = parser.parse_args()\n"
            f"    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)\n"
            f'    with open(args.input) as f:\n'
            f"        rows = list(csv.DictReader(f))\n"
            f"    records = process_dataset_{i}(rows)\n"
            f'    print(f"Processed {{len(records)}} records")\n'
            f"```"
        )
        context.append({"role": "assistant", "content": code})
        repl_out = (
            "[U:repl | N:0 | M:%d | C:%d%%] [REPL output]\n"
            ">>> from module_%d import process_dataset_%d\n"
            ">>> test_data = [{'id': '1', 'name': 'a', 'value': '1.0'}]\n"
            ">>> results = process_dataset_%d(test_data)\n"
            "INFO:root:Dataset %d: processed 1/1 rows\n"
            ">>> len(results)\n1" % (i*4+2, i*3+2, i, i, i, i)
        )
        context.append({"role": "user", "content": repl_out})
        context.append({
            "role": "assistant",
            "content": f"Done. module_{i}.py is complete with full validation, logging, and CLI entry point. The process_dataset_{i}() function handles all edge cases: missing values default safely, duplicates are skipped with a warning, and malformed timestamps return None with a log entry.",
            "summary": f"Created module_{i}.py - full data processing with validation, logging, CLI"
        })

    # Current in-progress turn
    context.append({
        "role": "user",
        "content": "[U:real | N:0 | M:120 | C:95%] Now create a master pipeline that runs all modules in sequence, with checkpointing so we can resume from failure. Use pickle for checkpoints and add a progress bar."
    })
    context.append({
        "role": "assistant",
        "content": "```python\nimport pickle, os\nfrom pathlib import Path\nfrom tqdm import tqdm\n\nclass Pipeline:\n    def __init__(self, modules, checkpoint_dir='checkpoints'):\n        self.modules = modules\n        self.ckpt_dir = Path(checkpoint_dir)\n        self.ckpt_dir.mkdir(exist_ok=True)\n    \n    def run(self, data):\n        for i, module in enumerate(tqdm(self.modules, desc='Pipeline')):\n            ckpt_file = self.ckpt_dir / f'step_{i}.pkl'\n            if ckpt_file.exists():\n                data = pickle.loads(ckpt_file.read_bytes())\n                continue\n            data = module(data)\n            ckpt_file.write_bytes(pickle.dumps(data))\n        return data\n```\nBuilding the pipeline with checkpointing and progress tracking..."
    })
    context.append({
        "role": "user",
        "content": "[U:repl | N:0 | M:122 | C:96%] [REPL output]\n>>> pipeline = Pipeline([process_dataset_0, process_dataset_1, process_dataset_2])\n>>> # testing checkpoint resume\n"
    })

    return context


class TestCompressionPipeline:
    """Test that compression pipeline steps execute in order."""

    @pytest.fixture
    def context(self):
        """Build a synthetic large context."""
        return _make_large_context()

    def test_pipeline_steps_execute_in_order(self, context):
        """Verify pipeline steps execute in order with an aggressive target."""
        from agent_context_compress import compress_context, _COMPRESSION_PIPELINE

        expected_steps = [step.name for step in _COMPRESSION_PIPELINE]
        assert len(expected_steps) == 10, f"Expected 9 steps, got {len(expected_steps)}"

        result_context, summary, metadata = compress_context(
            context=context,
            client=None,
            model_name="test-model",
            compression_factor=0.5,
            verbose=False,
            last_known_tokens=100000,
            max_context_tokens=128000,
            max_output_tokens=4096,
        )

        algorithms_used = metadata.get("algorithms_used", [])
        assert 1 <= len(algorithms_used) <= 9, (
            f"Expected 1-9 steps to execute, but {len(algorithms_used)} ran: {algorithms_used}"
        )
        assert algorithms_used == expected_steps[:len(algorithms_used)], (
            f"Steps executed in wrong order:\n"
            f"Expected prefix of: {expected_steps}\n"
            f"Got: {algorithms_used}"
        )

        # Verify size reduction
        final_size = metadata.get("bytes_after", 0)
        original_size = metadata.get("bytes_before", 1)
        assert final_size < original_size, "No size reduction achieved"

    def test_pipeline_preserves_alternation(self, context):
        """Verify the compressed output maintains strict user/assistant alternation."""
        from agent_context_compress import compress_context

        result_context, summary, metadata = compress_context(
            context=context,
            client=None,
            model_name="test-model",
            compression_factor=0.5,
            verbose=False,
            last_known_tokens=100000,
            max_context_tokens=128000,
            max_output_tokens=4096,
        )

        roles = [m["role"] for m in result_context]
        for i in range(1, len(roles)):
            assert roles[i] != roles[i - 1], (
                f"Alternation violated at index {i}: {roles[i-1]} -> {roles[i]}"
            )

    def test_pipeline_preserves_system_message(self, context):
        """System message is always the first message in the output."""
        from agent_context_compress import compress_context

        result_context, _, _ = compress_context(
            context=context,
            client=None,
            model_name="test-model",
            compression_factor=0.5,
            verbose=False,
            last_known_tokens=100000,
            max_context_tokens=128000,
            max_output_tokens=4096,
        )

        assert result_context[0]["role"] == "system"
        assert result_context[0]["content"] == context[0]["content"]


class TestCompressionDebugOutput:
    """Test that debug output is generated correctly."""

    @pytest.fixture
    def context(self):
        """Build a synthetic large context."""
        return _make_large_context(num_turns=10)

    def test_debug_logging_shows_target(self, context, capsys):
        """Verify that verbose output shows the target size and outcome."""
        from agent_context_compress import compress_context

        result_context, summary, metadata = compress_context(
            context=context,
            client=None,
            model_name="test-model",
            compression_factor=0.5,
            verbose=True,
            last_known_tokens=100000,
            max_context_tokens=128000,
            max_output_tokens=4096,
        )

        captured = capsys.readouterr()

        # Check for [COMPRESS] header
        assert "[COMPRESS]" in captured.out, "Expected [COMPRESS] debug output"

        # Check for DONE (target reached) or all steps exhausted
        if "[COMPRESS] DONE" in captured.out:
            assert metadata.get("bytes_after", float("inf")) <= metadata.get("target_size", 0)
        else:
            # All steps exhausted without reaching target
            assert len(metadata.get("algorithms_used", [])) == 9
