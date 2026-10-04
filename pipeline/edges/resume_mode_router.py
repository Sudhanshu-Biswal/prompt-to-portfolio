"""
Resume mode router.
Decides whether the Resume Parser node should run in parse mode
(resume file present) or generate mode (self-description text).

This is a pure function — takes state, returns a string.
No side effects. Independently testable.
"""
from __future__ import annotations
from pipeline.state import LangGraphState


def resume_mode_router(state: LangGraphState) -> str:
    """
    Returns "parse" if a resume file was uploaded, "generate" otherwise.
    The Resume Parser node reads state["resume_mode"] to know which
    MCP tool to call.
    """
    if state["raw_input"].resume_file.present:
        return "parse"
    return "generate"