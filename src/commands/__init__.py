"""RLM Commands Package

Commands for the RLM agent: /goal, /status, /agents, /refine, /heartbeat,
/autonomous, /continue. Plus .md command files from this directory.
"""

from pathlib import Path

from . import goal, refine, heartbeat, autonomous, ctx, status, agent, wiki, llm, tweak, skills
# 'continue' is a Python keyword, so we import the module differently
import importlib
_continue_mod = importlib.import_module(".continue", package=__name__)

__all__ = [
    "goal",
    "refine",
    "heartbeat",
    "autonomous",
    "ctx",
    "status",
    "agent",
    "wiki",
    "llm",
    "tweak",
    "skills",
    "continue",
    "COMMANDS",
    "MD_COMMANDS",
]

# Command registry: name -> module
COMMANDS = {
    "goal": goal,
    "refine": refine,
    "heartbeat": heartbeat,
    "autonomous": autonomous,
    "ctx": ctx,
    "status": status,
    "agent": agent,
    "wiki": wiki,
    "llm": llm,
    "tweak": tweak,
    "skills": skills,
    "continue": _continue_mod,
}

# Markdown command registry: name -> Path
# Discovered from *.md files in this directory.
_COMMANDS_DIR = Path(__file__).parent
MD_COMMANDS: dict[str, Path] = {}
for _md_file in sorted(_COMMANDS_DIR.glob("*.md")):
    MD_COMMANDS[_md_file.stem] = _md_file
try:
    del _md_file
except NameError:
    pass
del _COMMANDS_DIR
