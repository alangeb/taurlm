"""Regression lock: LLM-invoke split patch seams.

After the agent_llm_invoke.py decomposition, recovery helpers live in llm_recovery
and guard helpers in llm_presend_guard. Internal sibling calls resolve via THOSE
modules' __dict__, so patches must target the new module paths — patching the
facade would be silently inert. These tests lock that contract.
"""
import types
from unittest.mock import patch

import agent_llm_invoke as facade
import llm_presend_guard
import llm_recovery


def test_recovery_seam_lives_in_llm_recovery_not_facade():
    f = llm_recovery._try_context_compress
    assert callable(f)
    # Facade re-export is the SAME function object: no local copy exists, so the
    # seam cannot silently point at a dead facade-local duplicate.
    assert facade._try_context_compress is f
    with patch("llm_recovery._try_context_compress") as spy:
        assert llm_recovery._try_context_compress is spy


def test_guard_seam_lives_in_llm_presend_guard_not_facade():
    f = llm_presend_guard._truncate_largest_user
    assert callable(f)
    assert facade._truncate_largest_user is f
    with patch("llm_presend_guard._truncate_largest_user") as spy:
        assert llm_presend_guard._truncate_largest_user is spy


def test_handle_overflow_internal_call_resolves_to_new_module():
    # attempt>=3 forces _handle_context_overflow to call _try_context_compress,
    # exercising the internal sibling call through llm_recovery.__dict__.
    fake = [{"role": "user", "content": "compressed"}]
    cfg = types.SimpleNamespace(
        compress_client=object(), compress_model="m", compress_extra_kwargs={},
        log_file=None, compress_audit_writer=None, max_context_tokens=1000,
        max_output_tokens=100,
    )
    with patch("llm_recovery._try_context_compress", return_value=fake) as spy:
        out = llm_recovery._handle_context_overflow(
            [{"role": "user", "content": "y"}], cfg, 3, "boom 999 tokens"
        )
        assert spy.called
        assert out == fake

