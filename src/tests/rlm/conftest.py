"""RLM Test Fixtures

Shared fixtures for RLM module tests.
"""
import pytest
import sys
from pathlib import Path

# Add src/ to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.fixture
def tmp_dir(tmp_path):
    """Create a temporary directory for test files."""
    return tmp_path


@pytest.fixture
def mock_config():
    """Return a mock RLM config dict."""
    return {
        "rlm": {
            "enabled": True,
            "repl": {
                "max_output_chars": 8192,
                "max_state_size_mb": 100,
                "bash_timeout_seconds": 5,
            },
            "max_turns": 1000,
        }
    }


@pytest.fixture
def sample_python_code():
    """Return sample Python code for testing."""
    return """
import numpy as np
data = np.array([1, 2, 3, 4, 5])
result = data.mean()
print(f"Mean: {result}")
"""


@pytest.fixture
def sample_markdown_with_code():
    """Return sample markdown with embedded Python code."""
    return """
Let me analyze the data:

```python
import numpy as np
data = np.array([1, 2, 3, 4, 5])
result = data.mean()
print(f"Mean: {result}")
```

The mean is {result}.
"""


@pytest.fixture
def sample_bash_code():
    """Return sample bash code for testing."""
    return """
bash block
ls -la /tmp
echo "Hello from bash"
"""


@pytest.fixture
def mock_llm_response():
    """Return a mock LLM response dict."""
    return {
        "content": "Let me calculate that.\n\n```python\nresult = 2 + 2\nprint(result)\n```\n\nThe answer is 4.",
        "tool_calls": [],
        "reasoning": "",
    }


@pytest.fixture
def mock_llm_response_with_answer():
    """Return a mock LLM response that sets answer."""
    return {
        "content": "I have completed the analysis.\n\n```python\nanswer[\"content\"] = \"The result is 42\"\nanswer[\"ready\"] = True\n```\n\nDone.",
        "tool_calls": [],
        "reasoning": "",
    }
