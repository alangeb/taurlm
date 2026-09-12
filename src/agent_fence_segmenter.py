"""Tier-2 fence segmenter: LLM classifies ambiguous reply lines, we rebuild.

Design record: specs/llm-fence-segmenter.md  (REQ-LFS-006..REQ-LFS-021)

A SEPARATE tier behind the deterministic ladder (agent_fence_repair). The ladder
certains CLEAN/OK itself; when it can only say AMBIGUOUS, this module makes a
context-free, tool-free LLM call whose ONLY job is to partition the reply's
ORIGINAL lines into prose / code / drop ranges. We then rebuild the reply from
the ORIGINAL bytes plus active fence tokens (content-free inserted blank lines
excepted). The model never re-types code and never sees the agent conversation.

Everything above invoke_fn is pure (zero network), so the gates and the assembler
are unit-testable without a model. invoke_fn is the single injection seam;
production wiring lives in agent_pipeline.
"""
from __future__ import annotations

import ast
import hashlib
import re
import json
import secrets
import time

from agent_repl_parse import fence_tokens, _opener_of, _closer_of

NL = chr(10)

__all__ = [
    "DEFAULT_MAX_TOKENS", "DEFAULT_MAX_RETRIES", "MAX_LINES", "MAX_BYTES",
    "PROMPT_VERSION", "fence_like",
    "build_system_prompt", "build_user_message", "parse_fixer_answer",
    "gate_partition", "gate_drop_legality", "assemble", "gate_assembly_proof",
    "boundary_sanity", "segment_and_repair",
]

# Constants (REQ-LFS-006/007/008/020)
DEFAULT_MAX_TOKENS = 4096          # JSON classification output is tiny
DEFAULT_MAX_RETRIES = 3            # 1 initial + 3 feedback retries; no session cap
MAX_LINES = 400
MAX_BYTES = 60000
PROMPT_VERSION = "lfs-2"

_NONCE_TRIES = 5
_TELEMETRY_WARNED = False
# Continuation hints: after a SyntaxError, suggest a boundary was cut mid-construct
# (REQ-LFS-013). Pure heuristic; NEVER a retry trigger.
_HINT_TOKENS = {"except", "else", "finally", "elif", ")", "]", "}"}
_MD_HEAD = re.compile(r"\s*(?:[-*>#]|\d+[.)])\s")
_ALL_STYLES = ("std", "quad", "html", "at")


def _is_blank(line):
    return line.strip() == ""


def _proto_env(line):
    """Lazy ladder imports (avoid import cycle; REQ-LFS-021)."""
    from agent_fence_repair import _proto_kind, _env_open, _env_close
    if _proto_kind(line) is not None:
        return True
    if line and line[0] not in " \t" and (_env_open(line) or _env_close(line)):
        return True
    return False


def _line_fence_like(line):
    """THIS line is a fence/protocol tag; probes ALL four styles (REQ-LFS-003)."""
    for style in _ALL_STYLES:
        if _opener_of(line, style) is not None or _closer_of(line, style):
            return True
    return _proto_env(line)


def _col0_fence(line):
    """Fence-like AND strictly at column 0 (mirrors _opener_of strictness)."""
    if not line or line[0] in " \t":
        return False
    for style in _ALL_STYLES:
        if _opener_of(line, style) is not None or _closer_of(line, style):
            return True
    return _proto_env(line)


def fence_like(text):
    """Public predicate (REQ-LFS-003): any line is fence-like. ONE TRUTH:
    the ladder owns the implementation (it needs _proto_kind/_env_* anyway);
    this is a pure delegating alias so the two modules cannot drift (external
    review finding #1). No I/O: a lazy import is a link, not a request."""
    from agent_fence_repair import fence_like as _impl
    return _impl(text)


# ---------------------------------------------------------------------------
# Gate G1 -- exact partition (REQ-LFS-010)
# ---------------------------------------------------------------------------

def gate_partition(segments, n_lines):
    """Segments tile [0, n_lines) as ascending, non-overlapping half-open ranges."""
    cursor = 0
    for seg in segments:
        lo = seg.get("from")
        hi = seg.get("to")
        if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int) or not isinstance(hi, int):
            return False, "non-integer range bound"
        if lo >= hi:
            return False, "empty range from=%s to=%s" % (lo, hi)
        if lo < 0 or hi > n_lines:
            return False, "range out of bounds from=%s to=%s (n=%s)" % (lo, hi, n_lines)
        if lo > cursor:
            return False, "gap at line %s" % cursor
        if lo < cursor:
            return False, "overlap at line %s" % lo
        cursor = hi
    if cursor != n_lines:
        return False, "gap at line %s" % cursor
    return True, ""


def _line_active_sh(line, style):
    """Line is an ACTIVE-style SH fence token at column 0 (bash opener only).
    Used as the sole evidence that bash execution was intended in the input."""
    if style is None or not line or line[0] in " \t":
        return False
    return _opener_of(line, style) == "bash"


def _sh_witness(lines, style):
    """SH opener licenses bash ONLY as a block skeleton: a col-0 opener
    closed later by a col-0 SH closer, or the last fence token in the input
    (legit half-open-last-block). A stray opener followed only by unrelated
    fences is NOT intent evidence - ladder would execute nothing from it.
    (Fresh-spawn finding: stray @SH opener + unrelated fences fabricated
    bash out of prose.)"""
    ft = fence_tokens(style)
    cl = ft["sh_close"]
    n = len(lines)
    def _cl(i):
        ln = lines[i]
        return ln.startswith(cl) and (ln == cl or ln[len(cl)] in " \\t")
    for i in range(n):
        if not _line_active_sh(lines[i], style):
            continue
        if any(_cl(j) for j in range(i + 1, n)):
            return True
        if not any(_line_fence_like(lines[j]) for j in range(i + 1, n)):
            return True
    return False

def gate_bash_witness(segments, lines, active_style):
    """Deterministic ladder-parity gate (REQ-LFS-011): a code segment typed
    lang=bash is legal ONLY if the INPUT contains an active-style SH fence OPENER (a stray closer is not intent evidence - the ladder only orphan-drops closers).
    The deterministic ladder can never turn SH-less prose into shell, so neither
    may tier-2 -- otherwise a garbled reply (or instructions injected in an
    untrusted tool result) could fabricate bash execution. Absent that witness the
    segmentation is rejected -> status quo, never coerced. Flag-only? No: HARD."""
    if active_style is None:
        return True, ""
    if not any(s.get("type") == "code" and s.get("lang") == "bash" for s in segments):
        return True, ""
    if _sh_witness(lines, active_style):
        return True, ""
    return False, "bash code segment without a credentialed SH opener"


# ---------------------------------------------------------------------------
# Gate G2 -- drop legality (REQ-LFS-011)
# ---------------------------------------------------------------------------

def _col0_active_fence(line, style):
    """Strictly-col-0 opener/closer of the ACTIVE style (the token that, if
    it sat inside a code range, would split the block and drop its tail)."""
    if style is None or not line or line[0] in " \t":
        return False
    return _opener_of(line, style) is not None or _closer_of(line, style)


def gate_drop_legality(segments, lines, active_style=None):
    """drop: fence-like-or-blank only; prose: no col-0 fence; code: NO
    active-style fence line at col 0 (it would split the block and silently
    drop the tail -- the unbalanced-block the ladder refuses). Zero-code
    ranges are NOT a G2 failure (REQ-LFS-016)."""
    for seg in segments:
        t = seg["type"]
        for idx in range(seg["from"], seg["to"]):
            line = lines[idx]
            if t == "drop":
                if not (_is_blank(line) or _line_fence_like(line)):
                    return False, "illegal drop at line %s: %r" % (idx, line[:40])
            elif t == "prose" and _col0_fence(line):
                return False, "fence line in prose at line %s: %r" % (idx, line[:40])
            elif t == "code" and _col0_active_fence(line, active_style):
                return False, "active fence line in code at line %s: %r" % (idx, line[:40])
    return True, ""


# ---------------------------------------------------------------------------
# Assembly + Gate G3 -- re-scan / content-preservation proof (REQ-LFS-012)
# ---------------------------------------------------------------------------

def assemble(segments, lines, active_style):
    """Rebuild text from ORIGINAL lines + active fence tokens.

    Returns assembled_text ONLY. An earlier version also returned per-block
    body spans; G3 deliberately re-derives expected bodies from segments +
    the ORIGINAL lines (proof must not trust the assembler's own memory -
    test_gate_assembly_proof_detects_body_drift), so the spans were dead
    weight (external review finding #4).
    """
    ft = fence_tokens(active_style)
    out = []
    prev_type = None

    def sep():
        if out and out[-1] != "":
            out.append("")

    for seg in segments:
        t = seg["type"]
        lo, hi = seg["from"], seg["to"]
        if t == "drop":
            continue
        if t == "prose":
            if prev_type == "code":
                sep()
            out.extend(lines[lo:hi])
            prev_type = "prose"
        else:  # code
            sep()
            is_bash = seg.get("lang") == "bash"
            op = ft["sh_open"] if is_bash else ft["py_open"]
            cl = ft["sh_close"] if is_bash else ft["py_close"]
            out.append(op)
            body = list(lines[lo:hi])
            out.extend(body)
            out.append(cl)
            prev_type = "code"
    return NL.join(out)


def gate_assembly_proof(assembled, segments, lines, active_style):
    """Prove well-formedness AND byte-identical bodies (REQ-LFS-012).

    The comparison is INDEPENDENT of the assembler's own bookkeeping: we scan
    the assembled text, take the ACCEPTED blocks in order, and require each
    block's body to equal the ORIGINAL lines[lo:hi] of the matching code segment.
    An assembler off-by-one (dropping a trailing body line, e.g. answer ready)
    therefore fails here rather than being waved through by a self-referential
    check. Verdict proof runs strip_envelopes then the PURE ladder _repair_core
    (never repair_response -- recursion ban, REQ-LFS-019).
    """
    from agent_fence_repair import _repair_core, strip_envelopes, _accepted
    from agent_repl_parse import scan_style

    code_segs = [s for s in segments if s["type"] == "code"]
    stripped, _env = strip_envelopes(assembled, active_style)
    chk = scan_style(stripped.split(NL), active_style)
    accepted = [b for b in chk.blocks if _accepted(b[3])]
    if not accepted:
        return False, "no accepted block after proof"
    if len(accepted) != len(code_segs):
        return False, "block count %s != code segments %s (a range collapsed)" % (
            len(accepted), len(code_segs))
    if chk.unclosed:
        return False, "re-scan unclosed"

    # In-order, verbatim content-preservation: original bytes, not assembler memory.
    for i, seg in enumerate(code_segs):
        expected = list(lines[seg["from"]:seg["to"]])
        got_body = list(accepted[i][3])
        if got_body != expected:
            return False, "body drift at segment %s (lines %s-%s)" % (
                i, seg["from"], seg["to"])
        if seg.get("lang") not in (accepted[i][2], None):
            return False, "lang drift at segment %s" % i

    _assembled_again, rep = _repair_core(stripped, active_style)
    if getattr(rep, "verdict", "ambiguous") == "ambiguous":
        return False, "re-scan ambiguous"
    return True, ""


# ---------------------------------------------------------------------------
# Gate G4 -- boundary sanity (REQ-LFS-013, flag only, NEVER retry)
# ---------------------------------------------------------------------------

def _indent_of(line):
    """Leading-whitespace width (tabs expanded to 8) of a line."""
    stripped = line.expandtabs(8).lstrip()
    return len(line.expandtabs(8)) - len(stripped)


def boundary_sanity(segments, lines):
    """Flag python code ranges whose body fails ast.parse AND whose immediately
    following prose segment looks like a continuation of the cut block. Two hints
    (REQ-LFS-013): (a) first token in {except, else, finally, elif} or a bare
    closing bracket, or (b) greater indentation than the block's minimum body
    indent (an un-indented tail stranded as prose). Accepted cuts that leave valid
    syntax pass ast cleanly and cannot be flagged -- by design. FLAG-ONLY."""
    flags = []
    for seg in segments:
        if seg["type"] != "code" or seg.get("lang") == "bash":
            continue
        lo, hi = seg["from"], seg["to"]
        body = lines[lo:hi]
        try:
            ast.parse(NL.join(body))
            continue
        except (SyntaxError, ValueError):
            pass
        # Block minimum indent over non-blank body lines (0 if empty/all-blank).
        body_indents = [_indent_of(ln) for ln in body if ln.strip()]
        min_indent = min(body_indents) if body_indents else 0
        nxt = None
        for cand in segments:
            if cand["type"] == "prose" and cand["from"] == hi:
                nxt = cand
                break
        hint = False
        if nxt is not None:
            for j in range(nxt["from"], min(nxt["to"], nxt["from"] + 3)):
                raw = lines[j]
                head = raw.strip()
                if not head:
                    continue
                if head.split(" ", 1)[0].rstrip(":") in _HINT_TOKENS or head[:1] in {")", "]", "}"}:
                    hint = True
                    break
                # (b) dangling continuation: prose line more-indented than
                # the block min indent, but NOT a markdown/quote head (a bulleted/
                # quoted tail is legit prose, not a stranded body line).
                if _indent_of(raw) > min_indent and not _MD_HEAD.match(raw):
                    hint = True
                    break
        if hint:
            flags.append("boundary uncertain at line %s" % seg["from"])
    return flags


# ---------------------------------------------------------------------------
# Prompt construction (REQ-LFS-008/009)
# ---------------------------------------------------------------------------

_PROMPT_CACHE = {}


def build_system_prompt(active_style):
    """Static per-style system prompt (cached). Active fence tokens appear only
    inside quoted reprs -- never at column 0 (fence-pipeline-v2 discipline)."""
    if active_style in _PROMPT_CACHE:
        return _PROMPT_CACHE[active_style]
    ft = fence_tokens(active_style)
    tok_repr = (
        "python open=%r close=%r ; bash open=%r close=%r"
        % (ft["py_open"], ft["py_close"], ft["sh_open"], ft["sh_close"])
    )
    lines = [
        "You are a mechanical fence segmenter for a code REPL. The reply below",
        "has AMBIGUOUS code fencing. Classify every input line into non-overlapping,",
        "ascending, half-open ranges [from,to) that tile the input EXACTLY, typed",
        "prose, code, or drop. You never rewrite, reorder, or retype content --",
        "you only emit line-index ranges.",
        "",
        "ACTIVE fence tokens (quoted for recognition only; do not emit them): " + tok_repr + ".",
        "",
        "Rules:",
        "- code ranges require lang: python, unless a dropped fence line in that",
        "  range is SH-style or the original protocol tag named a bash tool, then bash.",
        "- drop ranges are ONLY for fence-like or protocol-tag lines (stray openers/",
        "  closers/tool-call tags). Never drop real content lines.",
        "- keep prose content as prose. A lone fence/tag line at column 0 must be",
        "  typed drop, never prose.",
        "- When unsure, choose PROSE. Mislabeling prose as drop is the worst"
        "  possible error - content must never vanish.",
        "- Lines are shown NUMBERED; from/to are exactly those numbers and"
        "  must cover 0..N-1 exactly (no gaps, no overlaps). Never count the"
        "  delimiter or header lines in a range.",
        "",
        "Example N=4 (0:hello 1:<python opener> 2:print(1) 3:bye):",
        '{"segments":[{"type":"prose","from":0,"to":1},{"type":"drop","from":1,"to":2},{"type":"code","from":2,"to":3,"lang":"python"},{"type":"prose","from":3,"to":4}]}',
        "- Output ONLY this JSON object, no prose, no fences:",
        '{"segments": [{"type": "prose|code|drop", "from": 0, "to": 3, "lang": "python"}]}',
        "from/to are 0-based half-open; lang present iff type==code.",
    ]
    prompt = NL.join(lines)
    _PROMPT_CACHE[active_style] = prompt
    return prompt


def _delim_lines(nonce):
    return ("<<<SEGMENT-BEGIN-" + nonce + ">>>", "<<<SEGMENT-END-" + nonce + ">>>")


def _pick_nonce(lines):
    """A nonce whose delimiters do not appear as a payload line (<=5 tries)."""
    for _ in range(_NONCE_TRIES):
        nonce = secrets.token_hex(4)
        b, e = _delim_lines(nonce)
        if b not in lines and e not in lines:
            return nonce
    return None


def build_user_message(text, nonce):
    """Wrap text between nonce delimiters WITH EXPLICIT LINE NUMBERS.

    Live-run finding: the real fixer model counted the delimiter lines and
    shipped off-by-one/garbage ranges (G1 out-of-bounds, G2 prose-as-drop).
    Printing each payload line as "<idx>: <line>" and stating N turns
    counting into transcription; segments still index the RAW payload lines
    (0..N-1), so gates/assembly are unchanged. Content bytes are untouched:
    numbering is display-only in the prompt."""
    b, e = _delim_lines(nonce)
    rows = text.split(NL)
    numbered = NL.join("%d: %s" % (i, ln) for i, ln in enumerate(rows))
    return (b + NL + "N=%d lines, numbered 0..%d:" % (len(rows), len(rows) - 1)
            + NL + numbered + NL + e)


# ---------------------------------------------------------------------------
# Fixer answer parsing (REQ-LFS-009)
# ---------------------------------------------------------------------------

_TYPES = {"prose", "code", "drop"}
_LANGS = {"python", "bash"}
_KEYS = {"type", "from", "to", "lang"}


def parse_fixer_answer(raw, n_lines):
    """(segments, reason); segments=None on malformed input. JSON may be wrapped
    in prose/fences -- we take the outermost {...}. Unknown keys, missing lang,
    bad enums, non-integer bounds -> reason."""
    if not isinstance(raw, str):
        return None, "answer is not a string"
    s = raw.strip()
    # Strip a fence wrapper if the fixer ignored the no-fence instruction.
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        return None, "no JSON object in answer"
    try:
        obj = json.loads(s[a:b + 1])
    except Exception as exc:
        return None, "malformed JSON: %s" % (exc.__class__.__name__,)
    if not isinstance(obj, dict) or set(obj) != {"segments"}:
        return None, "top-level must be exactly {segments: ...}"
    segs = obj["segments"]
    if not isinstance(segs, list) or not segs:
        return None, "segments missing or empty"
    out = []
    for seg in segs:
        if not isinstance(seg, dict):
            return None, "segment is not an object"
        extra = set(seg) - _KEYS
        if extra:
            return None, "unknown keys: %s" % ",".join(sorted(extra))
        t = seg.get("type")
        if t not in _TYPES:
            return None, "bad type %r" % (t,)
        lo, hi = seg.get("from"), seg.get("to")
        if not isinstance(lo, int) or not isinstance(hi, int) or isinstance(lo, bool) or isinstance(hi, bool):
            return None, "non-integer from/to"
        if t == "code":
            lang = seg.get("lang")
            if lang not in _LANGS:
                return None, "missing/invalid lang for code segment"
            item = {"type": t, "from": lo, "to": hi, "lang": lang}
        else:
            if "lang" in seg:
                return None, "lang only allowed on code segments"
            item = {"type": t, "from": lo, "to": hi}
        out.append(item)
    return out, ""


# ---------------------------------------------------------------------------
# Telemetry (REQ-LFS-017)
# ---------------------------------------------------------------------------

def default_telemetry_writer(record):
    """Append one JSONL line to LOG_DIR/fence_segmenter.jsonl. Swallow failures."""
    global _TELEMETRY_WARNED
    try:
        from agent_session import LOG_DIR
        with open(LOG_DIR / "fence_segmenter.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + NL)
    except Exception as exc:
        # Swallow, but ONCE loudly (external review finding #5): this log is
        # the only forensics on the tier-2 path; a silently dead log means
        # a whole subsystem goes unobservable. One stderr line, never a loop.
        if not _TELEMETRY_WARNED:
            _TELEMETRY_WARNED = True
            try:
                import sys
                print("[fence-segmenter] telemetry write failed: %s: %s"                      % (type(exc).__name__, exc), file=sys.stderr)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main driver (REQ-LFS-005/006/007/014/016/020)
# ---------------------------------------------------------------------------

def _bare_statement_line(line):
    """Delegating alias: canonical copy lives in agent_fence_repair (the
    veto site). Imported here for tests/back-compat; no second truth."""
    from agent_fence_repair import _bare_statement_line as _impl
    return _impl(line)


def normalize_segments(segments, lines, active_style):
    """Deterministic repairs for the segmenter's systematic tiling defects.

    Gates stay strict; this runs BEFORE them and fixes only provably-safe
    defects so a correct-intent segmentation survives fence-noise pedantry:
      1. G1 gap (leading/between/trailing): gap lines ALL blank or fence-like
         -> insert implicit drop. Real content in a gap stays fatal.
      2. Active fence line INSIDE a code segment -> replace the segment with
         code/drop/code pieces. Only lines whose stripped form is fence-like
         are ever dropped here, so real content can never vanish.
    Returns (segments, stats). Never raises.
    """
    stats = {"gap_fills": 0, "fence_splits": 0}

    def fence_noise(rng_lines):
        return bool(rng_lines) and all(_is_blank(x) or _line_fence_like(x) for x in rng_lines)

    out = []
    cursor = 0
    for seg in segments:
        lo, hi = seg.get("from"), seg.get("to")
        if not (isinstance(lo, int) and not isinstance(lo, bool)
                and isinstance(hi, int) and not isinstance(hi, bool)):
            out.append(seg)
            continue
        if lo > cursor and fence_noise(lines[cursor:lo]):
            out.append({"type": "drop", "from": cursor, "to": lo})
            stats["gap_fills"] += 1
            cursor = lo
        if seg.get("type") == "code" and 0 <= lo <= hi <= len(lines):
            body = lines[lo:hi]
            parses = True
            try:
                ast.parse(NL.join(body))
            except (SyntaxError, ValueError):
                parses = False
            if parses:
                # ``` inside a docstring is CONTENT; splitting would
                # delete a real line and break a body that was fine.
                out.append(seg)
                cursor = max(cursor, hi)
                continue
            cuts = [i for i in range(lo, hi)
                    if lines[i].strip() and _line_fence_like(lines[i].strip())]
            if cuts:
                stats["fence_splits"] += 1
                cur = lo
                for ci in cuts:
                    if ci > cur:
                        out.append({"type": "code", "from": cur, "to": ci,
                                    "lang": seg.get("lang", "python")})
                    out.append({"type": "drop", "from": ci, "to": ci + 1})
                    cur = ci + 1
                if cur < hi:
                    out.append({"type": "code", "from": cur, "to": hi,
                                "lang": seg.get("lang", "python")})
                cursor = max(cursor, hi)
                continue
        out.append(seg)
        cursor = max(cursor, hi)
    if cursor < len(lines) and fence_noise(lines[cursor:]):
        out.append({"type": "drop", "from": cursor, "to": len(lines)})
        stats["gap_fills"] += 1
    return out, stats


def segment_and_repair(text, active_style, *, invoke_fn, model,
                       max_retries=DEFAULT_MAX_RETRIES, telemetry_writer=None):
    """Returns (text, entries, verdict, telemetry). NEVER raises; on ANY failure
    returns the input unchanged with an llm-repair-failed entry (status quo).
    invoke_fn(messages, kwargs) -> (text, ok) is the sole transport seam."""
    from agent_fence_repair import Entry, strip_envelopes

    writer = telemetry_writer or default_telemetry_writer
    # D5: non-str guard BEFORE touching text (never-raise, REQ-LFS-005).
    def _telemetry(seed):
        base = {
            "ts": time.time(), "verdict_in": "ambiguous", "outcome": None,
            "retries_used": 0, "gate_failures": [], "dropped_count": 0,
            "dropped_inside_code": 0, "boundary_uncertain": [],
            "prompt_version": PROMPT_VERSION, "model": model,
        }
        base.update(seed)
        return base

    def _emit(t, entries, verdict, outcome, tele):
        tele["outcome"] = outcome
        try:
            writer(tele)
        except Exception:
            pass
        return t, entries, verdict, tele

    if not isinstance(active_style, str) or not active_style:
        # Style None/invalid would make assemble() fall back to std and
        # fabricate std fences from prose (fresh-spawn finding) -> fail
        # closed, status quo, zero calls.
        tele = _telemetry({"sha256": None, "snippet_start": "", "snippet_end": "",
                           "in_lines": 0, "in_bytes": 0})
        return _emit(text, [Entry("llm-repair-failed", "python", [])],
                     "ambiguous", "give-up", tele)
    if not isinstance(text, str) or not text.strip():
        # Empty/non-str: nothing to fence, never call the model (REQ-LFS-005/016).
        # Still emit a consistent-schema telemetry row (REQ-LFS-017).
        tele = _telemetry({"sha256": None, "snippet_start": "", "snippet_end": "",
                           "in_lines": 0, "in_bytes": 0})
        return _emit(text, [], "clean", "all-prose", tele)
    n_bytes = len(text.encode("utf-8", "surrogatepass"))
    n_lines_probe = len(text.replace("\r\n", "\n").split(NL))
    telemetry = _telemetry({
        "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
        "snippet_start": text[:200], "snippet_end": text[-200:],
        "in_lines": n_lines_probe, "in_bytes": n_bytes,
    })

    def _done(t, entries, verdict, outcome):
        return _emit(t, entries, verdict, outcome, telemetry)

    # REQ-LFS-020 size guard
    if n_lines_probe > MAX_LINES or n_bytes > MAX_BYTES:
        return _done(text, [Entry("llm-repair-failed", "python", [])], "ambiguous", "too-large")

    norm = text.replace("\r\n", "\n") if "\r\n" in text else text
    lines = norm.split(NL)
    n = len(lines)
    system = build_system_prompt(active_style)
    # Fresh dict PER CALL (REQ-LFS-021): never merged with group params.
    # enable_thinking=False is set exactly (groups set it true); dropping
    # preserve_thinking is intentional -- a 2-message classifier has no
    # reasoning to preserve. max_tokens rides here (OPENAI_BODY_PARAMS path).
    kwargs = {
        "temperature": 0,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    attempts = max(1, 1 + max_retries)
    last_reason = ""
    for attempt in range(attempts):
        telemetry["retries_used"] = attempt
        nonce = _pick_nonce(lines)
        if nonce is None:
            telemetry["gate_failures"].append("nonce collision")
            return _done(text, [Entry("llm-repair-failed", "python", [])], "ambiguous", "call-error")
        user = build_user_message(norm, nonce)
        if last_reason:
            user = user + NL + "Previous attempt failed: " + last_reason + ". Retry."
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        try:
            raw, ok = invoke_fn(messages, dict(kwargs))
        except Exception as exc:
            telemetry["gate_failures"].append("transport: %s" % exc.__class__.__name__)
            return _done(text, [Entry("llm-repair-failed", "python", [])], "ambiguous", "call-error")
        if not ok:
            telemetry["gate_failures"].append("invoke ok=False")
            return _done(text, [Entry("llm-repair-failed", "python", [])], "ambiguous", "call-error")

        segments, reason = parse_fixer_answer(raw, n)
        if segments is None:
            last_reason = reason or "unparseable answer"
            telemetry["gate_failures"].append("parse: " + last_reason)
            continue

        segments, _norm = normalize_segments(segments, lines, active_style)
        telemetry["norm_gap_fills"] = telemetry.get("norm_gap_fills", 0) + _norm["gap_fills"]
        telemetry["norm_fence_splits"] = telemetry.get("norm_fence_splits", 0) + _norm["fence_splits"]
        ok, reason = gate_partition(segments, n)
        if not ok:
            last_reason = reason or "partition"
            telemetry["gate_failures"].append("G1: " + last_reason)
            continue
        ok, reason = gate_drop_legality(segments, lines, active_style)
        if not ok:
            last_reason = reason or "drop legality"
            telemetry["gate_failures"].append("G2: " + last_reason)
            continue
        ok, reason = gate_bash_witness(segments, lines, active_style)
        if not ok:
            last_reason = reason or "bash witness"
            telemetry["gate_failures"].append("G2b: " + last_reason)
            continue

        # REQ-LFS-016: zero code AND zero drop -> all-prose, terminate immediately.
        if not any(s["type"] == "code" for s in segments) and not any(s["type"] == "drop" for s in segments):
            return _done(text, [], "clean", "all-prose")

        assembled = assemble(segments, lines, active_style)
        ok, reason = gate_assembly_proof(assembled, segments, lines, active_style)
        if not ok:
            last_reason = reason or "assembly proof"
            telemetry["gate_failures"].append("G3: " + last_reason)
            continue

        # ACCEPTED (REQ-LFS-014).
        boundary_flags = boundary_sanity(segments, lines)
        telemetry["boundary_uncertain"] = boundary_flags
        telemetry["dropped_count"] = sum(s["to"] - s["from"] for s in segments if s["type"] == "drop")
        # Asymmetric CODE-INTERNAL drop accounting (REQ-LFS-011).
        for s in segments:
            if s["type"] != "drop":
                continue
            prev_code = any(x["type"] == "code" and x["to"] == s["from"] for x in segments)
            next_code = any(x["type"] == "code" and x["from"] == s["to"] for x in segments)
            if prev_code and next_code:
                telemetry["dropped_inside_code"] += s["to"] - s["from"]

        spans = ["%s-%s:%s" % (s["from"], s["to"], s.get("lang", ""))
                 for s in segments if s["type"] == "code"]
        note = ("repaired segments " + ", ".join(spans)) if spans else "repaired"
        if telemetry["dropped_count"]:
            note += "; dropped %s fence/tag line(s)" % telemetry["dropped_count"]
        if telemetry["dropped_inside_code"]:
            note += " (%s of them inside code)" % telemetry["dropped_inside_code"]
        if boundary_flags:
            note += "; " + "; ".join(boundary_flags)
        entry = Entry("llm-repair-applied", "python", [], note=note)
        # D1: return the SAME form G3 proved (strip_envelopes only removes
        # envelope tags, never code bytes — REQ-LFS-012 byte-identity preserved).
        stripped_out, _ = strip_envelopes(assembled, active_style)
        return _done(stripped_out, [entry], "ok", "applied")

    # Gate failures exhausted all attempts -> status quo (REQ-LFS-005).
    # REQ-LFS-022c: give-up must never be SILENT. The dominant gate failures
    # carry line numbers; surfacing them in the lesson note turns "my block
    # vanished" into "the segmenter could not tile; these are your fence
    # problems" — the agent learns what the segmenter learned.
    gf = telemetry.get("gate_failures") or []
    dom = []
    for f in gf:
        key = f.split(":")[0] + (":" + f.split(" at line ")[1].split(":")[0] if " at line " in f else "")
        if key not in [d.split(" x")[0] for d in dom]:
            dom.append(key)
        else:  # collapse repeats: "G1 x3"
            i = [j for j, d in enumerate(dom) if d.startswith(key)][0]
            dom[i] = key + " x" + str(int(dom[i].split(" x")[1]) + 1) if " x" in dom[i] else key + " x2"
    note = "segmenter gate failures: " + ", ".join(dom[:4]) if dom else "segmenter gave up"
    note += " — re-emit with each fence token alone on its own line at column 0"
    return _done(text, [Entry("llm-repair-failed", "python", [], note=note)], "ambiguous", "give-up")
