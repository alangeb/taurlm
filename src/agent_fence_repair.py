"""In-place fence/protocol repair for assistant output (v2 ladder design).

Design record: specs/fence-pipeline-v2.md

Contract:
  - Runs on the ASSISTANT MESSAGE ONLY (never reasoning).
  - Rewrites the text BEFORE it is appended to context, so the model never
    re-sees its own bad pattern.
  - Three stages, first stage with >=1 ACCEPTED block wins. Only the winning
    stage may modify the text.
      1. active fence style   (counter pass)
      2. other styles, quad -> html -> std (triple-tick deliberately LAST)
      3. protocol tags (tool-call artifacts), extensible registry
  - Counter: depth +1 on an opener (token ALONE, strictly column 0),
    -1 on a closer (token at column 0, trailing content allowed, but a
    token-boundary match only -- never a substring match).
  - Only the LAST block may extend to end-of-text (auto-close). That falls out
    of the counter: depth > 0 at EOF can only be the in-progress last block.
  - Empty / whitespace-only body is clearly wrong -> REJECT the block.
  - No syntax checking here. Accept and let the REPL error teach the model.
  - Never leave empty content behind: if a rewrite would blank out non-empty
    input, the rewrite is refused (that would be an error).
  - Idempotent on text: repair(repair(x)) == repair(x). The REPORT is not
    idempotent by design (run 2 has nothing to fix).

No literal fence tokens appear in this source; all tokens are derived at
runtime from agent_repl_parse, which also OWNS the line predicates and
the depth counter (scan_style) - imported below, never duplicated, so repair
and the strict extractor can never disagree.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from agent_repl_parse import (
    fence_tokens,
    scan_style,
    extract_code_blocks,
    _tokens,
    _opener_of,
    _closer_of,
)

__all__ = ["repair_response", "RepairReport", "PROTOCOL_SPECS", "ProtocolSpec"]

#: Stage-2 probe order. Triple-tick ("std") is deliberately LAST: it appears too
#: frequently inside real prose content to be trusted early.
_OTHER_ORDER = ("quad", "html", "at", "std")
_BASH_NAMES = {"bash", "shell", "sh", "terminal"}

#: Newline used to re-join repaired lines.
NL = "\n"

_LT = chr(60)
_SLT = _LT + chr(47)
_GT = chr(62)


# ---------------------------------------------------------------------------
# Human-readable step identity
# ---------------------------------------------------------------------------

STEP_MESSAGES = {
    "glue-opener-split": (
        "One of your code fences was fused onto the end of a tool-call tag on the "
        "same line, so I could not see it as a block opener and your code would "
        "have been silently dropped. I split the tag off; your code is unchanged. "
        "Put the fence token alone on its own line."
    ),
    "protocol-envelope-stripped": (
        "Your code was wrapped in tool-call tags around a valid code fence. "
        "I stripped the tags and left your code unchanged. Do not emit tool-call "
        "tags in RLM mode - use the active code fence directly."
    ),
    "active-enclosed": "",
    "half-open-active": (
        "Your code block was opened but never closed. I assumed it ran to the end "
        "and closed it for you."
    ),
    "other-style-enclosed": (
        "Your code block used a different fence style than the one configured. "
        "I converted it to the current style."
    ),
    "half-open-other-style": (
        "Your code block used a different fence style and was never closed. "
        "I converted it and closed it for you."
    ),
    "protocol-tags": (
        "Your code was wrapped in tool-call tags instead of a code fence. "
        "I converted it to the current style."
    ),
    "orphan-close-dropped": (
        "I removed a stray closing fence that had no matching opener."
    ),
    "unbalanced-block": (
        "Your code block contains the fence token alone at column 0 INSIDE the "
        "block, so its real extent is ambiguous (unbalanced inner fence) and I "
        "did NOT run it. Do not put the fence token alone at column 0 inside "
        "code - indent it, or build it from parts (e.g. chr(64)+'PY') if you "
        "must print it."
    ),
    "llm-repair-applied": (
        "Your reply had ambiguous code fencing that the deterministic ladder "
        "could not resolve with certainty. I re-segmented it mechanically from "
        "YOUR OWN lines (no code was rewritten) so it now parses. Put each fence "
        "token alone at column 0 so I never have to guess again."
    ),
    "llm-repair-failed": (
        "Your reply had ambiguous code fencing I could not repair safely, so it "
        "was left unchanged and may not have run. Put each fence token alone on "
        "its own line at column 0 (nothing else on the line) and re-emit the code."
    ),
    "closer-inside-block-suspect": (
        "Your code contained a closing fence token alone on a line, so the block was "
        "cut short there and everything after it was not executed. Do not put the fence token "
        "alone at column 0 inside code - indent it, or build it from parts "
        "(e.g. chr(64)+chr(47)+'PY') if you must print it."
    ),
}


@dataclass
class Entry:
    step: str
    lang: str
    block: list
    note: str = ""   # optional dynamic per-entry note (tier-2 banner detail)

    @property
    def message(self) -> str:
        """Human-readable explanation for this repair step."""
        return STEP_MESSAGES.get(self.step, "")


@dataclass
class RepairReport(list):
    """List of Entry objects plus helpers.

    Iterating yields Entry objects (coherent with ``rep[i]`` which also yields
    an Entry). ``__contains__`` is overridden so ``"step-id" in report`` still
    reads as membership by step id, preserving the old back-compat idiom.
    """

    #: Tier-2 verdict (REQ-LFS-001): "clean" | "ok" | "ambiguous". Default ok.
    verdict: str = "ok"

    @property
    def entries(self):
        return list(list.__iter__(self))

    def __bool__(self):
        return len(self.entries) > 0

    def __contains__(self, step):
        """Membership is by step id, so old `if "style-normalized" in notes` still reads."""
        return any(e.step == step for e in self.entries)

    def summary(self) -> str:
        seen = []
        for e in self.entries:
            if e.step not in seen:
                seen.append(e.step)
        return ", ".join(seen)

    @property
    def message(self) -> str:
        """Concatenated human-readable messages, de-duplicated, in step order."""
        seen = []
        for e in self.entries:
            if e.message and e.message not in seen:
                seen.append(e.message)
        return " ".join(seen)


# ---------------------------------------------------------------------------
# Token predicates
# ---------------------------------------------------------------------------

def _accepted(body):
    return bool("".join(body).strip())


def _inner_fences(body, style):
    """Active-style fence lines that sit INSIDE a block body.

    The depth counter closes a block at the closer that returns depth to 0, but
    the strict extractor is NON-GREEDY and stops at the FIRST closer. The two
    therefore disagree as soon as a body contains any active-style fence line:
    the block cannot be honoured (and no appended closer can rescue it - the pad
    would be executed). Such a stage must be refused, not patched.
    """
    return [ln for ln in body
            if _opener_of(ln, style) or _closer_of(ln, style)]


# ---------------------------------------------------------------------------
# Stage 3: protocol tags (extensible registry)
# ---------------------------------------------------------------------------

@dataclass
class ProtocolSpec:
    name: str
    open_res: list
    close_res: list
    bash_hint: str = ""


PROTOCOL_SPECS = [
    ProtocolSpec("tool_call", [_LT + "tool_call" + _GT], [_SLT + "tool_call" + _GT]),
    ProtocolSpec(
        "function",
        [_LT + "function=(\\w+)" + _GT],
        [_SLT + "function" + _GT],
    ),
    ProtocolSpec("parameter", [_LT + "parameter=\\w+" + _GT], [_SLT + "parameter" + _GT]),
]


def _proto_kind(line):
    """Return (kind, name, lang) for a lone protocol tag line, else None."""
    s = line.strip()
    if line != s or not s:
        return None
    for spec in PROTOCOL_SPECS:
        for rx in spec.open_res:
            m = re.match("^" + rx + "$", s)
            if m:
                name = m.group(1).lower() if m.groups() else ""
                lang = "bash" if name in _BASH_NAMES else "python"
                return ("open", spec.name, lang)
        for rx in spec.close_res:
            if re.match("^" + rx + "$", s):
                return ("close", spec.name, None)
    return None


def scan_proto(lines, active_style):
    """Opener GROUP -> body -> first allowed closer. No depth counter needed.

    The opener is a RUN of consecutive lone protocol-tag lines, counted as ONE
    opener. The body ends at the first lone protocol CLOSE tag, at the active
    style's closing fence, at the start of the next opener group, or at EOF
    (auto-close, only valid because it is then the last block).
    """
    cl_active = [v[1] for v in _tokens(active_style).values()]
    blocks = []
    i = 0
    n = len(lines)
    last_lang = None          # language established by the enclosing function tag
    while i < n:
        k = _proto_kind(lines[i])
        if not k or k[0] != "open":
            i += 1
            continue
        # consume the opener group
        g = i
        k0 = k                                  # kind of the group HEAD (k is reused)
        lang = k[2]
        saw_function = k[1] == "function"
        while g + 1 < n:
            nk = _proto_kind(lines[g + 1])
            if nk and nk[0] == "open":
                g += 1
                if nk[1] == "function":
                    saw_function = True
                if nk[2] == "bash":
                    lang = "bash"
                continue
            break
        # A parameter-only group is nested inside a function: inherit its language
        # rather than defaulting to python (which would run a shell command as Python).
        if not saw_function and last_lang is not None:
            lang = last_lang
        if saw_function:
            last_lang = lang
        # scan the body
        b = g + 1
        end = n
        kind = "eof"
        while b < n:
            nk = _proto_kind(lines[b])
            if nk and nk[0] == "close":
                end, kind = b, "close"
                break
            if nk and nk[0] == "open":
                end, kind = b, "group"
                break
            if lines[b] and lines[b][0] != " ":
                for cl in cl_active:
                    if lines[b].startswith(cl) and (
                        len(lines[b]) == len(cl) or lines[b][len(cl)] in " \t"
                    ):
                        end, kind = b, "close"
                        break
                if kind == "close":
                    break
            b += 1
        body = lines[g + 1:end]
        consume = kind == "close"
        last = end
        if kind == "close":
            # a run of consecutive lone protocol close tags is ONE closer
            while last + 1 < n and _proto_kind(lines[last + 1]) and \
                    _proto_kind(lines[last + 1])[0] == "close":
                last += 1
            k = _proto_kind(lines[last])
            if k and k[1] in ("function", "tool_call"):
                last_lang = None
        if kind != "eof":
            blocks.append((i, last, lang, body, False, consume))
        elif g == i and k0[1] == "tool_call" and "".join(body).strip():
            # H1b: a truncated reply that ends with a LONE <tool_call> (no
            # function/parameter envelope, nothing after it) is a pure
            # truncation artifact - the code that followed was dropped on the
            # floor. Auto-close it. This is the ONE exception to the shipped
            # "protocol tags never auto-close" rule (specs/fence-pipeline-v2.md,
            # "Step 3 closer rule"): an envelope group (function/parameter) is
            # still refused, because there the trailing prose really is prose.
            # `kind == "eof"` already implies this is the LAST group - a later
            # opener would have ended the body with kind == "group" - so the
            # false-positive guard holds by construction.
            blocks.append((i, n, lang, body, True, False))
        i = last if last > i else i + 1
    return blocks


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render(lines, blocks, active_style):
    """Rebuild the text from the winning stage's accepted blocks.

    Everything outside a block is copied verbatim; orphan-closer removal is
    owned solely by _finalize, which runs on the rendered result.
    """
    aft = fence_tokens(active_style)
    opens = {"python": aft["py_open"], "bash": aft["sh_open"]}
    closes = {"python": aft["py_close"], "bash": aft["sh_close"]}
    out: list = []

    def _copy(lo: int, hi: int) -> None:
        for idx in range(max(lo, 0), min(hi, len(lines))):
            out.append(lines[idx])

    cursor = 0
    for op_i, cl_i, lang, body, _auto, consume in blocks:
        _copy(cursor, op_i)
        out.append(opens[lang])
        out.extend(body)
        out.append(closes[lang])
        # NOTE: no "net-pad" here. A body with a net-positive run of active-style
        # openers CANNOT be made extractable by appending closers: the appended
        # closers land inside the executed body (the strict extractor's non-greedy
        # match runs to the LAST valid closer), so the model receives a
        # SyntaxError for code it never wrote. Such a stage is refused outright
        # (see _net_unbalanced in repair_response) and diagnosed instead.
        cursor = cl_i + (1 if consume else 0)
    _copy(cursor, len(lines))
    return out


def _finalize(new_lines, active_style):
    """Drop ACTIVE-style orphan closers that the rewrite left behind.

    A converting stage (and the protocol stage) copies non-block text verbatim,
    so inert closer lines from another dialect survive into the output. Once
    that output is re-read in the ACTIVE style they are orphans, and a second
    pass would strip them -- which would break repair(repair(x)) == repair(x).
    Removing them here keeps the invariant true and is harmless: an orphan
    closer carries no content.
    """
    post = scan_style(new_lines, active_style)
    if not post.orphans:
        return new_lines, set()
    drop = set(post.orphans)
    return [ln for idx, ln in enumerate(new_lines) if idx not in drop], drop


def _renders_extractable(new_lines, want_blocks, active_style):
    """Prove the rendered text re-scans to exactly the blocks we intended.

    This is the contract that makes 'the extractor is strict because repair
    guarantees well-formedness' true rather than aspirational: repair may only
    commit a rewrite whose output the shared counter reads back as the same
    blocks. Anything else falls through to the next stage, so we can never
    silently drop code by emitting text the extractor discards.
    """
    chk = scan_style(new_lines, active_style)
    if chk.unclosed:
        return False
    got = [b for b in chk.blocks if _accepted(b[3])]
    if len(got) != len(want_blocks):
        return False
    for want, have in zip(want_blocks, got):
        w, g = want[3], have[3]
        # Exact body match: no pad tolerance. A net-open body is refused before
        # it ever reaches here, so the rendered body must be the intended body.
        if g != w:
            return False
    return True


def _preview(block, n=2):
    if len(block) <= n * 2:
        return list(block)
    return list(block[:n]) + ["... +%d lines ..." % (len(block) - 2 * n)] + list(block[-n:])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_ok(body_lines) -> bool:
    """True iff the python body compiles (REQ-LFS-002 self-containment probe)."""
    try:
        ast.parse(NL.join(body_lines))
        return True
    except (SyntaxError, ValueError):
        return False


def _foreign_col0_fence(line, active_style) -> bool:
    """A strictly-col-0 opener/closer of a style OTHER than active (REQ-LFS-002
    mixed-style mega-merge detector)."""
    if not line or line[0] in " \t":
        return False
    for s in ("std", "quad", "html", "at"):
        if s == active_style:
            continue
        if _opener_of(line, s) is not None or _closer_of(line, s):
            return True
    return False


def _bare_statement_line(line):
    """True iff the line parses ONLY as a bare-Name expression statement.

    The auto-close veto (REQ-LFS-022a, narrowed after external review): a
    prose tail like "result"/"Done" parses as a bare-Name no-op, so a clean
    parse does not certify an auto-close boundary. Constants are NOT vetoed
    (a trailing 42/None/"ok" is a harmless no-op a model may genuinely want);
    only bare Name leaks are the prose-execution threat, and prose words are
    Names."""
    txt = line.strip()
    if not txt or len(txt.split()) > 4:
        return False
    try:
        tree = ast.parse(txt)
    except (SyntaxError, ValueError):
        return False
    if len(tree.body) != 1:
        return False
    node = tree.body[0]
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Name)

def _auto_close_ambiguous(b, style, active_style) -> bool:
    """An auto-closed accepted block is ambiguous UNLESS it is an active-style
    python block that parses cleanly (nothing followed it at EOF -> lost nothing).
    A converting stage's auto-close is never certifiable; bash has no parser."""
    if style != active_style:          # converting stage
        return True
    if b[2] != "python":               # bash: no parser available
        return True
    if not _parse_ok(b[3]):            # python: ambiguous iff it fails to parse
        return True
    # REQ-LFS-022a: a clean parse is NOT sufficient certification. A prose tail
    # like "after"/"Done" parses as a bare-Name no-op statement, so an
    # auto-closed block whose body contains one has an unprovable boundary
    # (executing prose silently). Refuse; let tier-2 segment it.
    return any(_bare_statement_line(ln) for ln in b[3])


def fence_like(text) -> bool:
    """Public style-agnostic predicate (REQ-LFS-003). True iff any line is an
    opener/closer of ANY style at column 0, a lone protocol tag, or a col-0
    envelope tag. NOT _ANY_FENCE_RE (that never matches a lone opener/closer).
    Pure, no I/O. _proto_kind/_env_* are defined below in this module."""
    if not isinstance(text, str) or not text:
        return False
    for ln in text.split(NL):
        for s in ("std", "quad", "html", "at"):
            if _opener_of(ln, s) is not None or _closer_of(ln, s):
                return True
        if _proto_kind(ln) is not None:
            return True
        if ln and ln[0] not in " \t" and (_env_open(ln) or _env_close(ln)):
            return True
    return False


def _repair_core(text, active_style):
    # REQ-LFS-002: the tier-2 verdict is computed HERE, in flight, where the scan
    # tuples carry their auto_closed flags and the accepted-block list is in hand.
    # It is stored on the returned RepairReport at EVERY return path and must not
    # be re-derived from the final report alone (a later stage discards earlier
    # unbalanced notes). Rules live in specs/llm-fence-segmenter.md REQ-LFS-002.
    if not isinstance(text, str) or not text:
        rep = RepairReport()
        rep.verdict = "clean"                      # empty / non-string
        return text, rep

    if "\r\n" in text:                       # CRLF would defeat every line match
        text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    order = [active_style] + [s for s in _OTHER_ORDER
                              if s != active_style
                              and not (s == "std" and active_style != "std")]
    # std under a non-std style is NEVER auto-converted (even std-only replies):
    # a fenced code EXAMPLE in prose would silently start executing. Code-vs-
    # prose intent is SEMANTIC -> tier-2 territory (REQ-LFS-022); the ladder
    # only says ambiguous there, and tier-2 now survives tiling pedantry.

    unbalanced: list = []

    def _note_unbalanced(bad):
        """Remember blocks we cannot honour, deduped by body (4 stages see them all)."""
        for b in bad:
            if not any(e.block == list(b[3]) for e in unbalanced):
                unbalanced.append(Entry("unbalanced-block", b[2], list(b[3])))

    for stage, style in enumerate(order):
        sc = scan_style(lines, style)
        good = [b for b in sc.blocks if _accepted(b[3])]
        if not good:
            continue
        bad = [b for b in good if _inner_fences(b[3], active_style)]
        if bad:
            _note_unbalanced([b for b in bad
                              if any(_opener_of(ln, active_style) for ln in b[3])])
            continue
        half = any(b[4] for b in good)
        conv = style != active_style
        if conv and half:
            step = "half-open-other-style"
        elif conv:
            step = "other-style-enclosed"
        elif half:
            step = "half-open-active"
        else:
            step = "active-enclosed"
        if step == "active-enclosed" and not sc.orphans:
            # Already-valid fast path (empty report). Verdict ok, UNLESS a body
            # hides a col-0 FOREIGN fence token AND fails to parse -- the
            # mixed-style mega-merge whose certainty is false (REQ-LFS-002).
            mixed = any(
                b[2] == "python"                              # bash: no parse check
                and any(_foreign_col0_fence(ln, active_style) for ln in b[3])
                and not _parse_ok(b[3])
                for b in good
            )
            rep = RepairReport()
            rep.verdict = "ambiguous" if mixed else "ok"
            return text, rep
        new_lines, dropped = _finalize(_render(lines, good, active_style), active_style)
        if not _renders_extractable(new_lines, good, active_style):
            continue                          # stage cannot be honoured -> fall through
        rep = RepairReport()
        for b in good:
            if b[4] or style != active_style:
                rep.append(Entry(step, b[2], list(b[3])))
        if dropped:
            def _odd_triple(body_lines):
                joined = NL.join(body_lines)
                dq = chr(34) * 3
                sq = chr(39) * 3
                return joined.count(dq) % 2 == 1 or joined.count(sq) % 2 == 1
            stranded = any(_odd_triple(b[3]) for b in good)
            step = "closer-inside-block-suspect" if stranded else "orphan-close-dropped"
            rep.append(Entry(step, "python", []))
        # Verdict: a refusal step present, or an auto-closed block we cannot
        # prove self-contained -> ambiguous; otherwise the ladder committed a
        # proven rewrite -> ok.
        refusal = any(e.step in _REFUSAL_STEPS for e in rep.entries)
        ac_amb = any(b[4] and _auto_close_ambiguous(b, style, active_style) for b in good)
        # REQ-LFS-022a: the fast path has a `mixed` veto (foreign col-0 fence +
        # unparsable body = false certainty); the committed path needs the SAME
        # veto — protocol/other-style renders can smuggle fence lines into an
        # accepted body while claiming ok.
        foreign_bad = any(
            b[2] == "python"
            and any(_foreign_col0_fence(ln, active_style) for ln in b[3])
            and not _parse_ok(b[3])
            for b in good
        )
        rep.verdict = "ambiguous" if (refusal or ac_amb or foreign_bad) else "ok"
        return NL.join(new_lines), rep

    pb = scan_proto(lines, active_style)
    good = [b for b in pb if _accepted(b[3])]
    bad = [b for b in good if _inner_fences(b[3], active_style)]
    if bad:
        _note_unbalanced([b for b in bad
                          if any(_opener_of(ln, active_style) for ln in b[3])])
        good = []                               # never render some and drop others
    if good:
        new_lines, _dropped = _finalize(_render(lines, good, active_style), active_style)
        if not _renders_extractable(new_lines, good, active_style):
            rep = RepairReport()
            rep.verdict = "clean" if not fence_like(text) else "ambiguous"
            return text, rep
        rep = RepairReport()
        for b in good:
            rep.append(Entry("protocol-tags", b[2], list(b[3])))
        # An H1b protocol auto-close (b[4]) is never certifiable -> ambiguous.
        # Foreign-style col-0 fences smuggled into a python body render as
        # broken code (```python inside @PY) -> same veto as the style branch.
        foreign_bad = any(
            b[2] == "python"
            and any(_foreign_col0_fence(ln, active_style) for ln in b[3])
            and not _parse_ok(b[3])
            for b in good
        )
        rep.verdict = "ambiguous" if (any(b[4] for b in good) or foreign_bad) else "ok"
        return NL.join(new_lines), rep

    if unbalanced:
        rep = RepairReport()
        rep.extend(unbalanced)
        rep.verdict = "ambiguous"
        return text, rep

    # Nothing accepted. Pure prose is clean; fence-like-but-unaccepted is
    # ambiguous (zero accepted blocks AND fence_like -> REQ-LFS-002).
    rep = RepairReport()
    rep.verdict = "clean" if not fence_like(text) else "ambiguous"
    return text, rep


# ---------------------------------------------------------------------------
# Envelope strip (P1): tool-call tags wrapped AROUND a valid active fence.
# The style ladder returns early on an active-enclosed block, so the protocol
# stage never sees these and the tag lines leak into context. This pass runs on
# the core output and removes ONLY lone tag lines that sit OUTSIDE an accepted
# block (above its opener / below its closer). It never touches a block body or
# the fence lines, so the extracted code is byte-identical -> cannot change what
# executes, only what is stored. Idempotent: a clean reply has no anchored tags.
# ---------------------------------------------------------------------------

_ENV_NAME = r"(?:tool_call|function|parameter|invoke|antml:[a-z_]+)"
_ENV_OPEN = re.compile(r"^\s*<" + _ENV_NAME + r"(?:[\s=][^>]*)?>\s*$")
_ENV_CLOSE = re.compile(r"^\s*</" + _ENV_NAME + r">\s*$")


def _env_open(line):
    return bool(_ENV_OPEN.match(line))


def _env_close(line):
    return bool(_ENV_CLOSE.match(line))


# ---------------------------------------------------------------------------
# Glue-opener pre-pass (P1b): a tool-call OPEN tag FUSED onto a fence-open token
# on the same line (e.g.  <parameter name="code">@PY ). The opener predicate
# requires the token ALONE at column 0, so such a line is invisible as a block
# opener and the whole block is silently dropped - the code never runs and no
# note is emitted. This pre-pass splits the tag off, leaving the fence token
# alone (indent preserved) so the normal ladder can see it.
#
# The whole-line anchor is the safety property: a line carrying ANY other
# content (a print statement, prose, a string literal) cannot match, so real
# code is never rewritten. Idempotent: the output line has no tag left.
# ---------------------------------------------------------------------------

def _glue_res(active_style):
    """[(lang, compiled whole-line glue regex)] for the active style's openers."""
    ft = fence_tokens(active_style)
    out = []
    for lang, key in (("python", "py_open"), ("bash", "sh_open")):
        tok = re.escape(ft[key])
        out.append((lang, re.compile(
            r"^([ \t]*)<" + _ENV_NAME + r"(?:[ \t=][^>\n]*)?>[ \t\r]*("
            + tok + r")[ \t\r]*$", re.MULTILINE)))
        # REQ-LFS-022b: bare glue — "@PY <code>" fused on one line. Split is
        # safe ONLY if the opener's block closes later (opener on its own line
        # + identical closer exist in the untouched text); the honest per-block
        # gate in repair_response still has to see an accepted opener.
        out.append((lang, re.compile(
            r"^([ \t]*)(" + tok + r")[ \t]+(\S.*)$")))
    return out


def _protected_line_indices(text, active_style):
    """Line indices that must NOT be glue-rewritten: any line inside an accepted
    active-style block (opener..closer inclusive). Code inside an accepted block
    is executed as-is; rewriting it would change executed bytes."""
    lines = text.split("\n")
    sc = scan_style(lines, active_style)
    prot = set()
    for op_i, cl_i, _lang, _body, _auto, _consume in sc.blocks:
        if _accepted(_body):
            prot.update(range(op_i, (cl_i if cl_i is not None else len(lines)) + 1))
    return prot


def fix_glue_openers(text, active_style):
    """Split fused tool-call-tag + fence-open lines, but NEVER inside an accepted
    block. Returns (new_text, [Entry])."""
    if not isinstance(text, str) or not text:
        return text, []
    lines = text.split("\n")
    prot = _protected_line_indices(text, active_style)
    entries: list = []
    changed = False
    for idx, line in enumerate(lines):
        if idx in prot:
            continue
        for lang, rx in _glue_res(active_style):
            m = rx.match(line)
            if m:
                if m.lastindex == 3:  # bare glue: opener fused with code
                    indent, tok, rest = m.group(1), m.group(2), m.group(3)
                    closer = fence_tokens(active_style)[
                        "py_close" if lang == "python" else "sh_close"]
                    # Same-lang closer must exist LATER in the text: an
                    # earlier or other-language token may not vouch for
                    # a split (would strand the glued code line).
                    if (line.strip() != tok and closer
                            and any(j > idx and l.rstrip() == closer
                                    for j, l in enumerate(lines))):
                        lines[idx] = indent + tok + "\n" + indent + rest
                        entries.append(Entry("glue-opener-split", lang, []))
                        changed = True
                        break
                    continue
                lines[idx] = m.group(1) + m.group(2)
                entries.append(Entry("glue-opener-split", lang, []))
                changed = True
                break
    if not changed:
        return text, []
    return "\n".join(lines), entries


def strip_envelopes(text, active_style):
    """Remove lone tool-call tag lines adjacent to accepted active-style blocks.

    Returns (new_text, [Entry]). Each side is stripped independently (no matched
    pair required) so open-only / close-only / mismatched envelopes all work.
    Blank lines may sit between a tag and the fence and are bridged but kept.
    """
    if not isinstance(text, str) or not text:
        return text, []
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    sc = scan_style(lines, active_style)
    blocks = [b for b in sc.blocks if _accepted(b[3])]
    drop = set()
    entries = []
    for op_i, cl_i, lang, body, _auto, _consume in blocks:
        touched = False
        j = op_i - 1
        while j >= 0:
            ln = lines[j]
            if _env_open(ln):
                drop.add(j); touched = True; j -= 1
            elif ln.strip() == "":
                j -= 1
            else:
                break
        j = cl_i + 1
        while j < len(lines):
            ln = lines[j]
            if _env_close(ln):
                drop.add(j); touched = True; j += 1
            elif ln.strip() == "":
                j += 1
            else:
                break
        if touched:
            entries.append(Entry("protocol-envelope-stripped", lang, list(body)))
    if not drop:
        return text, []
    new_lines = [ln for i, ln in enumerate(lines) if i not in drop]
    # Same contract the converting stages honour: never commit a rewrite whose
    # output no longer re-scans to the SAME accepted blocks. A degenerate input
    # where a tag line was load-bearing for the block structure is refused
    # (return byte-for-byte), so strip can never drop extractable code.
    chk = scan_style(new_lines, active_style)
    if [b[3] for b in chk.blocks if _accepted(b[3])] != [b[3] for b in blocks]:
        return text, []
    return "\n".join(new_lines), entries


# Steps where the ladder deliberately leaves the text byte-for-byte (the body
# holds a fence token, so no rewrite is safe). Envelope-stripping such a reply
# would edit text the ladder chose not to touch -> skip it entirely.
_REFUSAL_STEPS = {"unbalanced-block", "closer-inside-block-suspect"}


def _finish(text, active_style):
    """Run the core ladder + envelope strip; return (text, RepairReport)."""
    out, rep = _repair_core(text, active_style)
    if not any(e.step in _REFUSAL_STEPS for e in rep.entries):
        text2, env = strip_envelopes(out, active_style)
        if env:
            rep.extend(env)
            out = text2
    return out, rep


def _one_pass(text, active_style):
    # P1b first: a fence token fused onto a tool-call tag is invisible to every
    # stage below, so it must be split before anything looks at the text.
    glued, glue = fix_glue_openers(text, active_style)
    out, rep = _finish(glued, active_style)
    if glue:
        # Honest per-block gate: advertise the split ONLY for a glued opener
        # that actually became the opener of an accepted block in the final
        # text. A glued opener that stays dropped (e.g. an indented opener with
        # no matching closer sitting next to an unrelated valid block) must NOT
        # trigger the note, so we never over-promise on a block we did not fix.
        glued_lines = glued.split("\n")
        out_lines = out.split("\n")
        # Openers that the STRICT extractor accepted (what actually executes);
        # derived from the final text via block char-offsets, so index shifts
        # from earlier passes can't mislead us. Raw lines (no strip) so an
        # indented glued opener never collides with a column-0 accepted opener.
        acc_openers = set()
        for b in extract_code_blocks(out, fence_style=active_style):
            # start_pos points AT the block's opener line in the final text.
            li = out.count("\n", 0, b.start_pos)
            if 0 <= li < len(out_lines):
                acc_openers.add(out_lines[li])
        # glue is a 1:1 line rewrite of `text`; the lines glue rewrote are the
        # ones that now differ (a lone active-style opener token).
        produced = {b for a, b in zip(text.split("\n"), glued_lines) if a != b}
        if produced & acc_openers:
            head = RepairReport()
            head.extend(glue)
            head.extend(rep.entries)
            head.verdict = rep.verdict   # REQ-LFS-002: a glued turn keeps its verdict
            rep = head
    return out, rep, (out != text)


def repair_response(text, active_style):
    """Full repair, run to a FIXED POINT: an ok verdict certifies the TEXT.

    Deep fuzz found ok->ambiguous flips: pass 1 auto-closes a half-open
    block, the appended closer then licenses a glue-split on pass 2 that
    breaks the structure - pass 1 promised ok for text it no longer
    certifies. So we iterate passes until the text stops moving (bounded),
    accumulate the honest per-pass lessons, and the returned verdict is the
    WORST verdict seen along the chain (clean < ok < ambiguous): tier-2
    still gets any turn that ever looked doubtful. If no fixed point is
    reached within the cap, return the ORIGINAL text (status quo) with an
    ambiguous verdict - we never ship text that no pass certified."""
    _WORSE = {"clean": 0, "ok": 1, "ambiguous": 2}
    cur = text
    merged_entries: list = []
    worst = "clean"
    last_verdict = "clean"
    for _ in range(4):
        out, rep, changed = _one_pass(cur, active_style)
        last_verdict = rep.verdict
        if not changed:
            merged_entries.extend(rep.entries)
            if _WORSE.get(last_verdict, 2) > _WORSE[worst]:
                worst = last_verdict
            merged = RepairReport()
            merged.extend(merged_entries)
            merged.verdict = worst
            return out, merged
        cur = out
        merged_entries.extend(rep.entries)
        if _WORSE.get(rep.verdict, 2) > _WORSE[worst]:
            worst = rep.verdict
    # No fixed point within the cap: fail closed to status quo. Keep the
    # already-diagnosed per-pass lessons (verdict is unconditionally ambiguous
    # here, so surfacing them adds no false certainty); the generic summary
    # entry stays LAST so it reads as the conclusion, not the only note.
    failed = RepairReport()
    failed.extend(merged_entries)
    failed.append(Entry("unbalanced-block", "python", []))
    failed.verdict = "ambiguous"
    return text, failed
