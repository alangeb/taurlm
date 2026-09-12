"""Prefix cache tracker for LLM requests.

Estimates expected prefix cache hit rate by comparing consecutive request bodies.
Extracted from agent_llm.py.
"""

from __future__ import annotations

import json


class PrefixCacheTracker:
    """Track expected vs actual prefix cache hits by comparing request bodies.

    Warnings are emitted via ``should_warn()``, which deduplicates identical
    warning keys so a persistent condition does not flood the log.

    - Gap: expected - actual >= 20pp
    - Params: model or tools changed
    - Low expected: expected hit < 25%
    - Low actual: actual hit < 25%
    """

    _DIVERGENCE_CONTEXT_LEN = 40  # chars on each side of divergence point

    def __init__(self) -> None:
        self._last_request_body: bytes | None = None
        self._last_params_key: bytes | None = None
        self._prev_request_body: bytes | None = None
        self._prev_params_key: bytes | None = None
        self._warned_keys: set[str] = set()

    def compute_expected_hit(self, body_bytes: bytes) -> tuple[float, str]:
        """Compute expected prefix cache hit rate from request body bytes.

        Compares current request body with the previous one to estimate
        what fraction of the context can be served from the prefix cache.

        Saves the previous body to ``_prev_request_body`` so ``diagnose_miss``
        can compare the current body against the one that was actually sent.

        Returns:
            (expected_hit_rate 0.0-1.0, reason_string)
        """
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 0.0, "unparseable body"

        params_key = self._extract_params_key(body)

        if self._last_params_key is not None and params_key != self._last_params_key:
            changed = self._find_param_changes(self._last_params_key, params_key)
            self._prev_request_body = self._last_request_body
            self._prev_params_key = self._last_params_key
            self._last_params_key = params_key
            self._last_request_body = body_bytes
            return 0.0, f"params changed: {changed}"

        if self._last_request_body is None:
            # First call — no previous body to compare.
            # Store current body as _prev_request_body so diagnose_miss can use it
            # on the next call.
            self._prev_request_body = body_bytes
            self._prev_params_key = params_key
            self._last_params_key = params_key
            self._last_request_body = body_bytes
            return 0.0, "first call (no previous context)"

        # Save previous body and params for diagnosis BEFORE updating
        self._prev_request_body = self._last_request_body
        self._prev_params_key = self._last_params_key

        # KV cache is built on the message sequence (via chat template),
        # not on generation params. Compare messages only when params
        # (model + tools) are unchanged, so param changes like preserve_thinking
        # or temperature don't falsely report a low expected cache hit.
        prev_cmp = self._get_compare_bytes(self._last_request_body)
        curr_cmp = self._get_compare_bytes(body_bytes)
        common = self._longest_common_prefix(prev_cmp, curr_cmp)
        total = len(curr_cmp)
        expected = common / total if total > 0 else 0.0
        if expected < 0.25 and common < len(prev_cmp):
            reason = f"prefix match: {common}/{total} bytes ({expected:.1%}) — diverged@{common}"
        else:
            reason = f"prefix match: {common}/{total} bytes ({expected:.1%})"

        self._last_params_key = params_key
        self._last_request_body = body_bytes
        return expected, reason

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnose_miss(self, current_body_bytes: bytes) -> str:
        """Diagnose why a prefix cache miss may have occurred.

        Compares *current_body_bytes* against ``_prev_request_body`` (the body
        from the previous request that was actually sent to the LLM).

        Checks params stability, prefix match percentage, and body size delta.

        Args:
            current_body_bytes: The current request body bytes.

        Returns:
            Human-readable diagnosis string.
        """
        reasons: list[str] = []

        if self._prev_request_body is None:
            return "no previous request to compare"

        # Check params
        try:
            current_body = json.loads(current_body_bytes.decode("utf-8"))
            current_params = self._extract_params_key(current_body)
            if self._prev_params_key is not None and current_params != self._prev_params_key:
                changed = self._find_param_changes(self._prev_params_key, current_params)
                reasons.append(f"params changed: {changed}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            reasons.append("unparseable current body")

        # Prefix match — compare messages (KV cache is built on messages,
        # not on generation params like temperature or preserve_thinking)
        prev_cmp = self._get_compare_bytes(self._prev_request_body)
        curr_cmp = self._get_compare_bytes(current_body_bytes)
        common = self._longest_common_prefix(prev_cmp, curr_cmp)
        total = len(curr_cmp)
        prev_total = len(prev_cmp)
        match_pct = (common / total * 100) if total > 0 else 0
        reasons.append(f"prefix match: {common}/{total} bytes ({match_pct:.1f}%)")

        size_delta = total - prev_total
        if size_delta != 0:
            sign = "+" if size_delta > 0 else ""
            reasons.append(f"body size delta: {sign}{size_delta} bytes ({prev_total} -> {total})")

        if common < min(prev_total, total):
            reasons.append(f"diverged@{common}")
            div_ctx = self._format_divergence(prev_cmp, curr_cmp, common)
            reasons.append(div_ctx)

        return "; ".join(reasons)

    def format_divergence_lines(self, current_body_bytes: bytes) -> str | None:
        """Return multi-line OLD/NEW divergence context, or None if not available.

        Compares *current_body_bytes* against ``_prev_request_body`` (the body
        from the previous request that was actually sent to the LLM).
        Returns a 2-line string:
          :: OLD: <40 matched><40 differing>
          :: NEW: <40 matched><40 differing>
        """
        if self._prev_request_body is None:
            return None
        prev_cmp = self._get_compare_bytes(self._prev_request_body)
        curr_cmp = self._get_compare_bytes(current_body_bytes)
        common = self._longest_common_prefix(prev_cmp, curr_cmp)
        if common >= min(len(prev_cmp), len(curr_cmp)):
            return None
        return self._format_divergence(prev_cmp, curr_cmp, common)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_divergence(self, old_body: bytes, new_body: bytes, divergence_pos: int) -> str:
        """Show 40 chars of matched context and 40 chars of divergence on each side.

        Returns a multi-line string:
          :: OLD: <40 matched chars><40 differing chars>
          :: NEW: <40 matched chars><40 differing chars>
        """
        ctx = self._DIVERGENCE_CONTEXT_LEN
        old_snippet = old_body[max(0, divergence_pos - ctx):divergence_pos + ctx].decode("utf-8", errors="replace")
        new_snippet = new_body[max(0, divergence_pos - ctx):divergence_pos + ctx].decode("utf-8", errors="replace")
        return f":: OLD: {old_snippet}\n:: NEW: {new_snippet}"

    def _extract_params_key(self, body: dict) -> bytes:
        """Extract model+tools as a deterministic cache-invalidation key.

        Only 'model' and 'tools' invalidate prefix cache in major backends.
        Generation params (temperature, top_p, etc.) do not affect prefill KV cache.
        """
        params = {}
        if "model" in body:
            params["model"] = body["model"]
        if "tools" in body:
            params["tools"] = body["tools"]
        return json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _find_param_changes(self, old_params_key: bytes, new_params_key: bytes) -> str:
        """Find which params changed between two parameter sets.

        Args:
            old_params_key: JSON-encoded params from the previous request.
            new_params_key: JSON-encoded params from the current request.
        """
        try:
            old = json.loads(old_params_key.decode("utf-8"))
            new = json.loads(new_params_key.decode("utf-8"))
            changed = [k for k in sorted(set(old.keys()) | set(new.keys())) if old.get(k) != new.get(k)]
            return ", ".join(changed) if changed else "unknown"
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "unparseable"

    @staticmethod
    def _longest_common_prefix(a: bytes, b: bytes) -> int:
        """Return length of longest common byte prefix."""
        import os.path as _os_path
        return len(_os_path.commonprefix([a, b]))

    @staticmethod
    def _extract_messages_bytes(body_bytes: bytes, body_dict: dict | None = None) -> bytes | None:
        """Extract and serialize the messages array from a request body.

        KV cache is built on the message sequence (via chat template),
        not on generation params. This extracts just the messages for
        accurate prefix cache comparison.

        Args:
            body_bytes: Raw request body bytes.
            body_dict: Pre-parsed body dict (avoids redundant json.loads).
        """
        try:
            if body_dict is None:
                body_dict = json.loads(body_bytes.decode("utf-8"))
            messages = body_dict.get("messages", [])
            return json.dumps(messages, sort_keys=True).encode("utf-8")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _get_compare_bytes(self, body_bytes: bytes) -> bytes:
        """Extract bytes for prefix comparison: messages-only when possible, full body otherwise.

        KV cache is built on the message sequence (via chat template),
        not on generation params. Extract just the messages for accurate
        prefix cache comparison. Falls back to full body if parsing fails.
        """
        msgs = self._extract_messages_bytes(body_bytes)
        return msgs if msgs is not None else body_bytes

    def should_warn(self, key: str) -> bool:
        """Return ``True`` only when *key* has not been seen before.

        Deduplicates cache warnings so that a persistent condition
        (e.g. consistently 0% actual hit rate) does not flood the console/audit
        log with the same message on every LLM call.  Each unique *key* is
        emitted exactly once; subsequent identical keys are suppressed.

        The caller controls granularity by including rounded values in the
        key (e.g. ``"low_act:0%"``).  When conditions change (e.g. actual hit
        rate moves from 0% to 50% and a *different* warning is needed), the
        new key is emitted.  ``reset()`` clears the seen-set, allowing
        re-emission after a session boundary or context reset.
        """
        if key in self._warned_keys:
            return False
        self._warned_keys.add(key)
        return True

    def reset(self) -> None:
        """Clear all stored state."""
        self._last_request_body = None
        self._last_params_key = None
        self._prev_request_body = None
        self._prev_params_key = None
        self._warned_keys.clear()


__all__ = [
    "PrefixCacheTracker",
]
