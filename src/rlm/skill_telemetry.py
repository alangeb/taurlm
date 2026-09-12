"""Skill-activation telemetry (Tier 4).

Append-only JSONL recording of which skills were NUDGED per turn (Tier 1/2
match logic) and which skills the model actually LOADED. Enables the
"skill existed but wasn't loaded" under-activation signal for tuning Tiers 0-2.

Design: every public method swallows ALL exceptions (missing dir, bad JSON,
permission errors) — telemetry must NEVER break an agent turn. Lines are
appended and flushed immediately so a crash still preserves prior records.

Heuristic for ``nudged_not_loaded``: a nudge for skill S counts as
"not loaded" if no load record for S appears AFTER that nudge's timestamp in
the log. This is approximate (it does not track per-turn causality or whether
the model even saw the nudge) but is cheap and directionally correct.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _default_log_dir() -> Path:
    """Resolve the log dir using the codebase's own convention.

    Mirrors agent_session.LOG_DIR (TAU_LOG_DIR env or ~/.local/taurlm/log).
    Imported lazily and guarded so a telemetry failure never propagates.
    """
    try:
        from agent_session import LOG_DIR as _LOG_DIR
        return Path(_LOG_DIR)
    except Exception:
        return Path.home() / ".local" / "taurlm" / "log"


class SkillTelemetry:
    """Append-only JSONL recorder for skill nudge/load events.

    Args:
        path: Optional explicit jsonl path. Defaults to
            ``<LOG_DIR>/skill_telemetry.jsonl`` using the codebase's
            log-dir convention (see ``_default_log_dir``).
    """

    def __init__(self, path: Path | None = None):
        if path is None:
            path = _default_log_dir() / "skill_telemetry.jsonl"
        self.path = Path(path)
        self._turn_seq = 0

    def next_turn_id(self) -> int:
        """Monotonic per-loader turn counter (cheap, no pipeline plumbing)."""
        self._turn_seq += 1
        return self._turn_seq

    # --- recording -----------------------------------------------------

    def record_nudge(self, turn_id, query: str, matches: list) -> None:
        """Append one nudge event. ``matches`` is a list of (name, score)."""
        try:
            skills = [str(n) for n, _ in matches]
            top = max((int(s) for _, s in matches), default=0)
            self._write({
                "ts": time.time(),
                "event": "nudge",
                "turn_id": turn_id,
                "skills": skills,
                "top": top,
            })
        except Exception:
            pass  # telemetry must never break a turn

    def record_load(self, name: str) -> None:
        """Append one load event for skill *name*."""
        try:
            self._write({"ts": time.time(), "event": "load", "name": str(name)})
        except Exception:
            pass

    def record_spawn(self, status: str, spawn_id: str | None = None) -> None:
        """Append one spawn-outcome event (verification-cost metric).

        Records the FINAL terminal status of a spawn so we can measure
        the suspect-rate (how often a worker ends 'suspect' /
        'budget_exhausted' / 'error' vs a clean 'completed'). This is the
        OBSERVABLE proxy for worker trustworthiness — the manager's
        re-verification behaviour is not harness-observable, so we record
        only the spawn outcome. Fail-safe: never breaks a spawn.
        """
        try:
            self._write({
                "ts": time.time(),
                "event": "spawn",
                "status": str(status),
                "spawn_id": str(spawn_id) if spawn_id is not None else None,
            })
        except Exception:
            pass

    # --- reading -------------------------------------------------------

    def summary(self) -> dict:
        """Aggregate counts from the JSONL log.

        Returns:
            {"loads": {name: count}, "nudges": {name: count},
             "nudged_not_loaded": {name: count}}

        ``nudged_not_loaded`` counts, per skill, the number of nudges that
        were NOT followed by any load of that skill at a later timestamp.
        """
        loads: dict[str, int] = {}
        nudges: dict[str, int] = {}
        # name -> list of nudge timestamps
        nudge_ts: dict[str, list[float]] = {}
        load_ts: dict[str, list[float]] = {}
        try:
            if not self.path.exists():
                return {"loads": {}, "nudges": {}, "nudged_not_loaded": {}}
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts", 0.0)
                ev = rec.get("event")
                if ev == "load":
                    name = rec.get("name")
                    if name:
                        loads[name] = loads.get(name, 0) + 1
                        load_ts.setdefault(name, []).append(ts)
                elif ev == "nudge":
                    for name in rec.get("skills", []):
                        nudges[name] = nudges.get(name, 0) + 1
                        nudge_ts.setdefault(name, []).append(ts)
        except Exception:
            return {"loads": loads, "nudges": nudges, "nudged_not_loaded": {}}

        not_loaded: dict[str, int] = {}
        for name, ts_list in nudge_ts.items():
            later_loads = load_ts.get(name, [])
            cnt = sum(1 for nt in ts_list if not any(lt > nt for lt in later_loads))
            if cnt:
                not_loaded[name] = cnt
        return {"loads": loads, "nudges": nudges, "nudged_not_loaded": not_loaded}

    def spawn_summary(self) -> dict:
        """Count spawn outcomes by terminal status.

        Returns:
            {"spawns": {status: count}, "total": int}
        """
        counts: dict[str, int] = {}
        try:
            if not self.path.exists():
                return {"spawns": {}, "total": 0}
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("event") == "spawn":
                    st = str(rec.get("status", "?"))
                    counts[st] = counts.get(st, 0) + 1
        except Exception:
            pass
        return {"spawns": counts, "total": sum(counts.values())}

    # --- internals -----------------------------------------------------

    def _write(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()


__all__ = ["SkillTelemetry"]
