"""Regression tests for the "_1 prefix leak" in the session-log prefix mechanism.

Bug (pre-fix): ``read_system_prompt()`` called ``_get_log_filename_prefix()``
directly, which CLAIMS a prefix by atomically creating a 0-byte
``{prefix}.context`` file. Because ``TauErgon._init_subsystems`` read the
system prompt BEFORE initializing the session, every real run

  1. leaked a stray empty ``_1.context`` file, and
  2. interpolated the WRONG ``{prefix}.audit/.context`` paths into
     AGENT_RLM.md (the throwaway claim) while the session used the next
     prefix — the agent was TOLD wrong log paths in its own prompt.

Fix:
  * Part A: ``read_system_prompt`` prefers the live ``SESSION_PREFIX`` and,
    only when none exists, falls back to the non-creating
    ``_peek_log_filename_prefix()``. It never claims/creates.
  * Part B: ``_init_subsystems`` reads the system prompt AFTER
    ``init_subsystems`` so the real session prefix exists first.

LOG_DIR is always redirected to tmp_path — tests are hermetic.
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure src/ is importable (mirrors the other test modules).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The real AGENT_RLM.md carries no {audit_file}/{context_file} placeholders
# today, so interpolation tests inject a template via the module __file__
# seam (read_system_prompt reads Path(__file__).parent / "AGENT_RLM.md").
FAKE_TEMPLATE = (
    "audit={audit_file}\n"
    "context={context_file}\n"
    "log={log_file}\n"
)

SESSION_SHAPE = re.compile(r"^(\d+)_(\d{14})_(\d+)$")


def _context_files(log_dir: Path) -> list:
    return sorted(p.name for p in log_dir.glob("*.context"))


def _point_log_dir_at(monkeypatch, tmp_path: Path) -> Path:
    """Redirect agent_session's LOG_DIR to tmp_path and reset SESSION_PREFIX."""
    import agent_session

    log_dir = tmp_path / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_session, "LOG_DIR", log_dir)
    monkeypatch.setattr(agent_session, "SESSION_PREFIX", None)
    return log_dir


def _install_fake_template(monkeypatch, log_dir: Path) -> None:
    """Make read_system_prompt read a template containing the path
    placeholders, next to a fake agent_subsystems __file__ in log_dir's
    parent. Keeps interpolation assertions exact and hermetic."""
    import agent_subsystems

    fake_md = log_dir / "AGENT_RLM.md"
    fake_md.write_text(FAKE_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(
        agent_subsystems,
        "__file__",
        str(log_dir / "agent_subsystems.py"),
    )


# ── Part A: read_system_prompt must never claim/create ─────────────────────


class TestReadSystemPromptNoLeak:
    def test_set_prefix_interpolates_and_creates_nothing(self, monkeypatch, tmp_path):
        """SESSION_PREFIX set -> prompt carries THAT value's audit/context
        paths; ZERO new files; no stray _1 anywhere."""
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        _install_fake_template(monkeypatch, log_dir)
        import agent_session
        from agent_subsystems import read_system_prompt

        agent_session.SESSION_PREFIX = "999_20200101000000_7"
        before = _context_files(log_dir)
        assert before == []

        prompt = read_system_prompt()

        after = _context_files(log_dir)
        assert after == before, "read_system_prompt must not create .context files"
        assert list(log_dir.glob("*.audit")) == []
        assert str(log_dir / "999_20200101000000_7.audit") in prompt
        assert str(log_dir / "999_20200101000000_7.context") in prompt
        other = [
            tok for tok in re.findall(r"/\S+\.(?:audit|context)", prompt)
            if "999_20200101000000_7." not in tok
        ]
        assert other == [], f"prompt referenced non-session paths: {other}"


    def test_none_prefix_creates_no_file_but_returns_prompt(self, monkeypatch, tmp_path):
        """SESSION_PREFIX None -> peek path: usable prompt, zero files created."""
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        import agent_session
        from agent_subsystems import read_system_prompt

        assert agent_session.SESSION_PREFIX is None
        before = _context_files(log_dir)
        assert before == []

        prompt = read_system_prompt()

        after = _context_files(log_dir)
        assert after == before, "peek path must not create any .context file"
        assert isinstance(prompt, str) and len(prompt) > 100, "usable prompt required"


# ── _peek_log_filename_prefix: right shape, zero side effects ──────────────


class TestPeekLogFilenamePrefix:
    def test_shape_and_no_file(self, monkeypatch, tmp_path):
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        import os
        from agent_session import _peek_log_filename_prefix

        p = _peek_log_filename_prefix()
        m = SESSION_SHAPE.match(p)
        assert m, f"bad prefix shape: {p!r}"
        assert m.group(1) == str(os.getppid())
        assert len(m.group(2)) == 14
        assert m.group(3) == "1"
        assert list(log_dir.glob("*")) == [], "peek must not touch the dir at all"

    def test_skips_claimed_numbers_without_creating(self, monkeypatch, tmp_path):
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        import os
        from agent_session import _get_log_filename_prefix, _peek_log_filename_prefix

        claimed = _get_log_filename_prefix()  # real claim: creates 0-byte file
        assert _context_files(log_dir) == [f"{claimed}.context"]

        peeked = _peek_log_filename_prefix()
        assert peeked != claimed
        assert peeked.startswith(f"{os.getppid()}_")
        # Exactly one file still — the peek added nothing.
        assert _context_files(log_dir) == [f"{claimed}.context"]

    def test_get_prefix_still_claims_atomically(self, monkeypatch, tmp_path):
        """Part A must not regress the real claimer."""
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        from agent_session import _get_log_filename_prefix

        p1 = _get_log_filename_prefix()
        p2 = _get_log_filename_prefix()
        assert p1 != p2
        assert (log_dir / f"{p1}.context").exists()
        assert (log_dir / f"{p2}.context").exists()
        assert (log_dir / f"{p1}.context").stat().st_size == 0

    def test_exported_in_all(self):
        import agent_session
        assert "_peek_log_filename_prefix" in agent_session.__all__


# ── Part B: ordering — the real session's prefix reaches the prompt ────────


def _make_init_config():
    from agent_init import AgentInitConfig

    return AgentInitConfig(
        agent_name="test-agent",
        llm_groups={},
        current_group_name="test",
        model_override=None,
        base_url_override=None,
        max_context_tokens_override=None,
        model_name="test-model",
        base_url="http://test",
        api_key="test-key",
        max_context_tokens=10000,
        max_tokens=None,
        timeout=180,
        max_silent_retries=3,
        max_enhanced_retries=3,
        max_explicit_retries=3,
        inference_params=None,
        loop_detection_window_size=5,
        loop_detection_repeat_threshold=3,
        heartbeat_enabled=False,
        heartbeat_interval=None,
    )


class TestSessionPrefixReachesPrompt:
    def test_after_session_init_prompt_matches_session_prefix(self, monkeypatch, tmp_path):
        """A real AgentSessionManager(setup_files=True) sets SESSION_PREFIX;
        read_system_prompt then reports that SAME prefix and leaves exactly
        ONE .context file (the real session) in LOG_DIR."""
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        _install_fake_template(monkeypatch, log_dir)
        import agent_session
        from agent_session import AgentSessionManager
        from agent_subsystems import read_system_prompt

        session = AgentSessionManager(setup_files=True)
        prefix = agent_session.SESSION_PREFIX
        assert prefix is not None and SESSION_SHAPE.match(prefix)
        assert session.context_file == log_dir / f"{prefix}.context"
        assert session.audit_file == log_dir / f"{prefix}.audit"
        assert _context_files(log_dir) == [f"{prefix}.context"]

        prompt = read_system_prompt()

        assert _context_files(log_dir) == [f"{prefix}.context"]
        assert str(log_dir / f"{prefix}.audit") in prompt
        assert str(log_dir / f"{prefix}.context") in prompt
        # Every path token the prompt advertises must be the REAL session's.
        # (The prefix legitimately ends "_1" here — so match on full paths,
        # not on the "_1" substring, which would false-positive.)
        # {log_file} interpolates the audit path too -> audit appears twice.
        assert prompt.count(f"{prefix}.audit") == 2   # audit= and log= lines
        assert prompt.count(f"{prefix}.context") == 1
        other = [
            tok for tok in re.findall(r"/\S+\.(?:audit|context)", prompt)
            if f"{prefix}." not in tok
        ]
        assert other == [], f"prompt leaked non-session paths: {other}"

    def test_new_order_single_context_file(self, monkeypatch, tmp_path):
        """Empirical core of Part B: init_subsystems (session claims prefix)
        then read_system_prompt => exactly ONE .context file in LOG_DIR
        (the OLD order produced TWO: stray _1 + real _2)."""
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        from agent_subsystems import init_subsystems, read_system_prompt

        init = _make_init_config()
        agent = type("A", (), {"config": None})()

        bundle = init_subsystems(agent, init)
        mid = _context_files(log_dir)
        assert len(mid) == 1

        prompt = read_system_prompt(fence_style="std")

        after = _context_files(log_dir)
        assert after == mid, (
            f"read_system_prompt leaked files after session init: "
            f"before={mid} after={after}"
        )
        assert len(after) == 1, f"expected exactly ONE .context, got {after}"
        assert bundle.session.context_file.name == after[0]


# ── Full-agent integration: TauErgon() leaves exactly one 0-byte .context ──


class TestTauErgonNoLeak:
    def test_taurgon_init_exactly_one_context_file(self, monkeypatch, tmp_path):
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        from unittest.mock import patch as _patch
        from agent_core import TauErgon
        from agent_config import Config
        import agent_core

        with _patch.object(
            agent_core, "resolve_agent_init", return_value=_make_init_config()
        ):
            agent = TauErgon(Config())

        contexts = sorted(p.name for p in log_dir.glob("*.context"))
        assert len(contexts) == 1, (
            f"expected exactly one .context (the real session), got {contexts}"
        )
        assert contexts[0] == f"{agent._session._prefix}.context"
        assert (log_dir / contexts[0]).stat().st_size == 0

    def test_taurgon_prompt_uses_real_session_prefix(self, monkeypatch, tmp_path):
        """The prompt set on the agent's context must name the session's OWN
        prefix paths — pre-fix it named the throwaway _1 paths."""
        log_dir = _point_log_dir_at(monkeypatch, tmp_path)
        _install_fake_template(monkeypatch, log_dir)
        from unittest.mock import patch as _patch
        from agent_core import TauErgon
        from agent_config import Config
        import agent_core

        with _patch.object(
            agent_core, "resolve_agent_init", return_value=_make_init_config()
        ):
            agent = TauErgon(Config())

        prefix = agent._session._prefix
        system = agent.context.get_system() or ""
        assert str(log_dir / f"{prefix}.audit") in system
        assert str(log_dir / f"{prefix}.context") in system
