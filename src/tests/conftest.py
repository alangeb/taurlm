"""Pytest configuration and shared fixtures for the tau bot test suite.

Provides reusable fixtures to reduce duplication across test files:
- temp_dir: Temporary directory for file-based tests
- mock_agent: Minimal TauErgon mock for unit tests
- mock_llm_client: Mock OpenAI-compatible LLM client
- tau_entry_dir: Makes sys.argv[0] point to tau.py so tau.json is found
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Tests that require deleted modules (RLM transformation Phase 7)
# These are skipped during collection to avoid import errors
collect_ignore = [
    # Legacy tests with stale assertions (migrated from tests_legacy/)
]


@pytest.fixture
def tau_entry_dir():
    """Make sys.argv[0] point to tau.py so Config._resolve_entry_dir finds tau.json.

    The config loader looks for tau.json next to the entry script (sys.argv[0]).
    During tests, sys.argv[0] is the pytest runner, not tau.py.
    This fixture patches sys.argv[0] so tau.json can be discovered.

    Usage:
        def test_something(tau_entry_dir):
            from agent_config import get_config
            config = get_config()  # Can now find tau.json
    """
    import sys

    tau_py = Path(__file__).resolve().parent.parent / "tau.py"
    original_argv = sys.argv
    sys.argv = [str(tau_py)]
    try:
        yield str(tau_py)
    finally:
        sys.argv = original_argv


@pytest.fixture
def test_config():
    """Provide a Config with LLM groups for TauErgon instantiation.

    TauErgon now requires at least one LLM group configured.
    This fixture provides a minimal config suitable for testing.

    Usage:
        def test_something(test_config):
            agent = TauErgon(config=test_config, ...)
    """
    from agent_config import Config, LLMGroup

    return Config(
        llm_groups={
            "test": LLMGroup(
                name="test",
                model="test-model",
                api_base="http://test:8000/v1",
                api_key="",
                max_context_tokens=200000,
            ),
        },
        llm_group_name="test",
    )


@pytest.fixture
def temp_dir():
    """Create temporary directory for file-based tests.

    Usage:
        def test_something(temp_dir):
            file = temp_dir / "test.txt"
            file.write_text("hello")
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_agent():
    """Create a minimal mock TauErgon for unit tests.

    Provides common attributes that most tests need:
    - base_url, model_name, max_context_tokens
    - nesting_count
    - context (TauContext)

    NOTE: Uses MagicMock() (not spec=TauErgon) because tests set arbitrary
    attributes not on the real class. Tradeoff: typos in attribute names
    won't be caught. Integration tests (tau-sanity.py) cover real access.

    Usage:
        def test_something(mock_agent):
            mock_agent.base_url = "http://test:8000/v1"
            # ... test code ...
    """
    from agent_context import TauContext

    agent = MagicMock()
    agent.base_url = "http://test:8000/v1"
    agent.model_name = "test-model"
    agent.max_context_tokens = 200000
    agent.nesting_stack = ""
    agent.nesting_count = 0  # Derived from len(nesting_stack)
    agent.current_group_name = "default"
    agent.context = TauContext(
        [
            {"role": "system", "content": "You are helpful"},
        ]
    )
    return agent


@pytest.fixture
def mock_llm_client():
    """Create a mock OpenAI-compatible LLM client.

    Returns a Mock with pre-configured response structure:
    - chat.completions.create() returns a valid response
    - response.usage has prompt_tokens, completion_tokens, total_tokens

    Usage:
        def test_something(mock_llm_client):
            mock_llm_client.chat.completions.create.return_value = ...
    """
    mock = MagicMock()
    mock_response = Mock()
    mock_response.choices = [
        Mock(message=Mock(content="Test response", tool_calls=None))
    ]
    mock_response.usage = Mock(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        prompt_tokens_details={"cached_tokens": 0},
    )
    mock.chat.completions.create.return_value = mock_response
    return mock


@pytest.fixture
def mock_tool_result():
    """Create a standard tool result structure.

    Returns a dict with:
    - content: The tool output
    - tool_call_id: The associated tool call ID

    Usage:
        def test_something(mock_tool_result):
            mock_tool_result["content"] = "new content"
    """
    return {
        "content": "Tool result",
        "tool_call_id": "test-call-id",
    }


@pytest.fixture
def sample_context():
    """Create a sample TauContext with a basic conversation.

    Returns a TauContext with:
    - system message
    - user message
    - assistant message

    Usage:
        def test_something(sample_context):
            assert len(sample_context) == 3
    """
    from agent_context import TauContext

    return TauContext(
        [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
    )


@pytest.fixture
def batched_tool_context():
    """Create a sample context with batched tool calls.

    Returns a TauContext with:
    - user message
    - assistant with 2 tool calls
    - 2 tool results
    - assistant response

    Usage:
        def test_something(batched_tool_context):
            assert not batched_tool_context.is_tool_pending()
    """
    from agent_context import TauContext

    return TauContext(
        [
            {"role": "user", "content": "Get weather and temp"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "t1", "function": {"name": "get_weather"}},
                    {"id": "t2", "function": {"name": "get_temperature"}},
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "sunny"},
            {"role": "tool", "tool_call_id": "t2", "content": "25c"},
            {"role": "assistant", "content": "It's sunny and 25c"},
        ]
    )


@pytest.fixture(autouse=True)
def clear_tool_env_vars():
    """Automatically clear session-file env vars before each test and restore after.

    Clears TOOL_*_FILE, TAU_AUDIT_LOG_FILE, TAU_PARENT_AUDIT_FILE, and
    TAU_FORK_NESTING to prevent environment leakage between tests.
    Also resets the global SESSION_PREFIX so each test gets a fresh session.
    """
    import os


    _SESSION_KEYS = {
        "TAU_AUDIT_LOG_FILE",
        "TOOL_CONTEXT_FILE",
        "TAU_PARENT_AUDIT_FILE",
        "TAU_FORK_NESTING",
    }

    cleared = {}
    for key in list(os.environ):
        if key in _SESSION_KEYS or (key.startswith("TOOL_") and key.endswith("_FILE")):
            cleared[key] = os.environ.pop(key)

    # Reset global session prefix so each test gets a fresh prefix.
    import agent_session
    agent_session.SESSION_PREFIX = None

    yield

    # Restore after test
    os.environ.update(cleared)


@pytest.fixture
def patch_tau_bot_invoke():
    """Context manager to patch TauErgon.invoke_with_tools.

    Usage:
        def test_something(patch_tau_bot_invoke):
            with patch_tau_bot_invoke(return_value="Test response"):
                # code that calls invoke_with_tools
    """

    def _patch(return_value="Test response"):
        return patch.object(
            __import__("agent_core", fromlist=["TauErgon"]).TauErgon,
            "invoke_with_tools",
            return_value=return_value,
        )

    return _patch


@pytest.fixture(autouse=True)
def _no_prod_telemetry(monkeypatch):
    """No test may write the production fence_segmenter.jsonl (telemetry is
    live forensics). File-level stubs miss modules that reach the segmenter
    via repair_response; conftest autouse covers every test under src/tests."""
    try:
        import agent_fence_segmenter as _seg
        monkeypatch.setattr(_seg, "default_telemetry_writer", lambda rec: None)
    except Exception:
        pass
