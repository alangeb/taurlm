"""Python Code Extraction from LLM Responses

This module extracts Python code blocks from LLM responses. The model
generates responses with embedded Python code in markdown code fences,
and this module extracts them for execution in the REPL kernel.

Architecture
------------
STRICT extraction (specs/fence-pipeline-v2.md). agent_fence_repair has already
normalized the assistant message before this runs, so this module accepts only
perfectly-formed active-style blocks and never guesses:

1. Split the response into lines
2. One linear depth-counter pass (shared with agent_fence_repair via scan_style)
3. Emit a block only when depth returns to 0 (fully enclosed)
4. Drop empty bodies and the unclosed trailing block

Key Functions
-------------
extract_code_blocks : Strictly extract fully-enclosed active-style blocks
fence_tokens        : Human-readable fence delimiters for a style
normalize_fences    : Rewrite fences in docs/prompts to a target style
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "CodeBlock",
    "extract_code_blocks",
    "FENCE_STYLES",
    "FENCE_OPENERS",
    "fence_tokens",
    "normalize_fences",
]

# ---------------------------------------------------------------------------
# Fence style definitions
# ---------------------------------------------------------------------------

#: Valid fence style names
FENCE_STYLES = ("std", "quad", "html", "at")

#: Opening fence tokens per style (used for EOT multi-block detection)
FENCE_OPENERS: dict[str, set[str]] = {
    "std": {"```python", "```bash"},
    "quad": {"``````python", "``````bash"},
    "html": {"<py>", "<sh>"},
    "at": {"@PY", "@SH"},
}

#: Human-readable fence delimiters per style
_FENCE_TOKENS: dict[str, dict[str, str]] = {
    "std": {"py_open": "```python", "py_close": "```", "sh_open": "```bash", "sh_close": "```", "py_label": "```python", "sh_label": "```bash"},
    "quad": {"py_open": "``````python", "py_close": "``````", "sh_open": "``````bash", "sh_close": "``````", "py_label": "``````python", "sh_label": "``````bash"},
    "html": {"py_open": "<py>", "py_close": "</py>", "sh_open": "<sh>", "sh_close": "</sh>", "py_label": "<py>", "sh_label": "<sh>"},
    "at": {"py_open": "@PY", "py_close": "@/PY", "sh_open": "@SH", "sh_close": "@/SH", "py_label": "@PY", "sh_label": "@SH"},
}


def fence_tokens(style: str) -> dict[str, str]:
    """Return human-readable fence delimiters for the given style.

    Args:
        style: One of "std", "quad", "html", "at"

    Returns:
        Dict with keys: py_open, py_close, sh_open, sh_close, py_label, sh_label
    """
    return _FENCE_TOKENS.get(style, _FENCE_TOKENS["std"])


# ---------------------------------------------------------------------------
# Shared line predicates + depth counter.
# Used by BOTH the strict extractor below and agent_fence_repair, so repair
# and extraction can never disagree about what a block is.
# Design record: specs/fence-pipeline-v2.md
# ---------------------------------------------------------------------------

def _tokens(style):
    """Return {lang: (opener, closer)} for one fence style."""
    ft = fence_tokens(style)
    return {
        "python": (ft["py_open"], ft["py_close"]),
        "bash": (ft["sh_open"], ft["sh_close"]),
    }


def _opener_of(line, style):
    """Lang if line is an opener ALONE at column 0 (no leading whitespace), else None."""
    if not line or line[0] == " ":
        return None
    for lang, (op, _cl) in _tokens(style).items():
        if line.rstrip(" \t") == op:
            return lang
    return None


def _closer_of(line, style):
    """True if line starts a closer at column 0. Token-boundary, never substring."""
    if not line or line[0] == " ":
        return False
    for _lang, (_op, cl) in _tokens(style).items():
        if line.startswith(cl):
            rest = line[len(cl):]
            if rest == "" or rest[0] in " \t":
                return True
    return False


@dataclass
class Scan:
    """Result of one linear pass over the lines of a response.

    blocks: (opener_idx, end_idx, lang, body_lines, auto_closed, consume_end)
    orphans: closer line indices seen while at depth 0
    unclosed: True if a block was still open at end of text
    """

    blocks: list = field(default_factory=list)
    orphans: list = field(default_factory=list)
    unclosed: bool = False


def scan_style(lines, style):
    """Single linear pass with ONE depth counter (deliberately not a stack).

    open open close -> depth 1,2,1 -> EOF -> ONE block, inner opener kept as
    literal code. open close close -> block, then the extra closer is an orphan.
    Only the last block can ever be auto_closed, because depth > 0 at EOF can
    only describe the in-progress final block.
    """
    s = Scan()
    depth = 0
    start = None
    lang = None
    for i, line in enumerate(lines):
        op = _opener_of(line, style)
        if op is not None:
            if depth == 0:
                start = i
                lang = op
            depth += 1
            continue
        if _closer_of(line, style):
            if depth > 0:
                depth -= 1
                if depth == 0:
                    s.blocks.append((start, i, lang, lines[start + 1:i], False, True))
                    start = None
                    lang = None
            else:
                s.orphans.append(i)
            continue
    if depth > 0 and start is not None:
        s.blocks.append((start, len(lines), lang, lines[start + 1:], True, False))
        s.unclosed = True
    return s


# Each style maps to a list of (compiled_regex, language) pairs.
# The regex captures code in group(1). Delimiters must be ALONE on a line.
_FLAGS = re.DOTALL | re.MULTILINE

_FENCE_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    "std": [
        (re.compile(r"^```python\n(.*?)^```\s*$", _FLAGS), "python"),
        (re.compile(r"^```bash\n(.*?)^```\s*$", _FLAGS), "bash"),
    ],
    "quad": [
        (re.compile(r"^``````python\n(.*?)^``````\s*$", _FLAGS), "python"),
        (re.compile(r"^``````bash\n(.*?)^``````\s*$", _FLAGS), "bash"),
    ],
    "html": [
        (re.compile(r"^<py>\n(.*?)^</py>\s*$", _FLAGS), "python"),
        (re.compile(r"^<sh>\n(.*?)^</sh>\s*$", _FLAGS), "bash"),
    ],
    "at": [
        (re.compile(r"^@PY\n(.*?)^@/PY\s*$", _FLAGS), "python"),
        (re.compile(r"^@SH\n(.*?)^@/SH\s*$", _FLAGS), "bash"),
    ],
}

@dataclass
class CodeBlock:
    """Represents an extracted code block.

    Attributes:
        language: Language tag (python, bash, etc.)
        code: The code content
        start_pos: Starting position in original text
        end_pos: Ending position in original text
    """
    language: str
    code: str
    start_pos: int = 0
    end_pos: int = 0

    def __bool__(self) -> bool:
        """Return True if code is non-empty."""
        return bool(self.code.strip())


def extract_code_blocks(text: str, fence_style: str = "std") -> list[CodeBlock]:
    """Strict extraction: active fence style only, fully enclosed blocks only.

    There is deliberately NO fallback here. agent_fence_repair has already
    normalized the assistant text (style conversion, auto-close of a trailing
    half-open block, protocol-tag conversion) before this runs, and it shares
    the same scan_style() counter, so repair and extraction cannot disagree.
    Anything still malformed after repair is simply not a block.

    Rules (specs/fence-pipeline-v2.md):
      - opener must be the active style's token, ALONE, at column 0
      - closer must start at column 0; trailing content is allowed, but it is a
        token-boundary match, never a substring match
      - an empty / whitespace-only body is clearly wrong and is dropped
      - an unclosed trailing block is NOT extracted (repair owns that case)

    Args:
        text: LLM response text (already repaired)
        fence_style: One of "std", "quad", "html", "at"

    Returns:
        List of CodeBlock objects in order of appearance
    """
    if fence_style not in FENCE_STYLES:
        from agent_console import display_warn
        display_warn(f"Invalid fence_style {fence_style!r}, falling back to 'std'")
        fence_style = "std"

    lines = text.split("\n")
    # char offset of each line start, for start_pos/end_pos bookkeeping
    offs = []
    acc = 0
    for ln in lines:
        offs.append(acc)
        acc += len(ln) + 1

    scan = scan_style(lines, fence_style)
    blocks: list[CodeBlock] = []
    for op_i, cl_i, lang, body, auto_closed, _consume in scan.blocks:
        if auto_closed:
            continue                      # not fully enclosed -> not a block
        if not "".join(body).strip():
            continue                      # empty body is clearly wrong
        code = "\n".join(body).rstrip()
        start_pos = offs[op_i]
        end_pos = offs[cl_i] + len(lines[cl_i]) if cl_i < len(lines) else len(text)
        blocks.append(CodeBlock(language=lang, code=code, start_pos=start_pos, end_pos=end_pos))
    return blocks


# ---------------------------------------------------------------------------
# Fence normalization (for system prompts and skill docs)
# ---------------------------------------------------------------------------

# Regex to find fenced code blocks in ANY of the 4 styles.
# Matches: opening_fence\n...code...\nclosing_fence
# The opening must be alone on its line (start of line).
_ANY_FENCE_RE = re.compile(
    r"^(```python|```bash|``````python|``````bash|<py>|<sh>|@PY|@SH)\n"
    r"(.*?)"
    r"^(```|``````|</py>|</sh>|@/PY|@/SH)\s*$",
    re.DOTALL | re.MULTILINE,
)

# Map: opening token -> (language, closing token)
_FENCE_MAP: dict[str, tuple[str, str]] = {
    "```python": ("python", "```"),
    "```bash": ("bash", "```"),
    "``````python": ("python", "``````"),
    "``````bash": ("bash", "``````"),
    "<py>": ("python", "</py>"),
    "<sh>": ("bash", "</sh>"),
    "@PY": ("python", "@/PY"),
    "@SH": ("bash", "@/SH"),
}


def normalize_fences(text: str, target_style: str, skip_sections: list[str] | None = None) -> str:
    """Normalize all fenced code blocks in text to use the target fence style.

    Finds code blocks in ANY of the 4 supported styles and rewrites the
    fence tokens to match target_style. Code content is left untouched.

    Args:
        text: Markdown text containing fenced code blocks
        target_style: One of "std", "quad", "html", "at"
        skip_sections: List of section heading substrings to skip.
            Sections whose heading contains any of these substrings
            will NOT have their fences replaced (e.g., reference tables).

    Returns:
        The text with fence tokens normalized to target_style.
    """
    if target_style == "std":
        # std is the default — still normalize non-std blocks to std
        pass

    ft = fence_tokens(target_style)
    py_open = ft["py_open"]
    py_close = ft["py_close"]
    sh_open = ft["sh_open"]
    sh_close = ft["sh_close"]

    # Split into sections by ## or ### headings
    lines = text.split("\n")
    sections: list[tuple[str, list[str]]] = []  # (heading, lines)
    current_heading = ""
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{2,3}\s", line):
            if current_lines or current_heading:
                sections.append((current_heading, current_lines))
            current_heading = line
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines or current_heading:
        sections.append((current_heading, current_lines))

    # Determine which sections to skip
    skip = set()
    if skip_sections:
        for s in skip_sections:
            skip.add(s.lower())

    # Process each section
    result_parts: list[str] = []
    for heading, sec_lines in sections:
        sec_text = "\n".join(sec_lines)

        # Check if this section should be skipped
        heading_lower = heading.lower()
        should_skip = any(s in heading_lower for s in skip)

        if should_skip:
            result_parts.append(heading)
            result_parts.append(sec_text)
            continue

        # Normalize fences in this section
        def _replace_fences(m: re.Match) -> str:
            opener = m.group(1)
            code = m.group(2)
            lang, _ = _FENCE_MAP.get(opener, ("python", "```"))
            if lang == "python":
                return f"{py_open}\n{code}{py_close}"
            else:
                return f"{sh_open}\n{code}{sh_close}"

        sec_text = _ANY_FENCE_RE.sub(_replace_fences, sec_text)

        result_parts.append(heading)
        result_parts.append(sec_text)

    return "\n".join(result_parts)
