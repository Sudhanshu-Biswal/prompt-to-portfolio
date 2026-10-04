"""
LangGraphState — the single shared state object that flows through every node.

Rules:
  1. Inputs (job_id, user_id, raw_input) are set once and never mutated.
  2. Each node writes only the keys it owns — never touches another node's keys.
  3. errors accumulates across nodes — never replaced, always appended.
  4. Control flow keys (retry_count, validation_result, status) are written
     by routing logic, not by processing nodes.
  5. All keys declared upfront — no dynamic keys added at runtime.

A node that returns {"merged_profile": result} leaves all other keys
exactly as they were. LangGraph merges the partial dict back into state.
"""
from __future__ import annotations
from typing import Literal
from typing_extensions import TypedDict

from schemas.input import InputRequest
from schemas.resume import ResumeParserOutput
from schemas.github import GitHubFetchOutput
from schemas.intent import PromptInterpreterOutput
from schemas.merged_profile import MergedProfile
from schemas.components import SelectedComponents
from schemas.media import ResolvedMedia
from schemas.output import CopyGenerationOutput, ValidationResult, FinalOutput


class LangGraphState(TypedDict):
    # ── set once at job creation, never mutated by any node ───────────────
    job_id: str
    user_id: str
    raw_input: InputRequest

    # ── stage 1: parallel fetch outputs ───────────────────────────────────
    # All three set concurrently — None if that source failed or wasn't connected.
    # Merge node reads all three and handles any combination of None values.
    resume_output: ResumeParserOutput | None
    github_output: GitHubFetchOutput | None
    intent_output: PromptInterpreterOutput | None

    # ── stage 2: sequential processing outputs ─────────────────────────────
    # Set in order. Each node reads upstream keys, writes its own.
    merged_profile: MergedProfile | None
    selected_components: SelectedComponents | None
    resolved_media: ResolvedMedia | None
    copy_output: CopyGenerationOutput | None
    final_output: FinalOutput | None

    # ── control flow ───────────────────────────────────────────────────────
    # These exist so graph edges can route on them — not for nodes to branch on.
    retry_count: int
    validation_result: ValidationResult | None
    resume_mode: Literal["parse", "generate"] | None   # set by resume_mode_router edge

    # ── error accumulation ─────────────────────────────────────────────────
    # Non-fatal errors — pipeline continues. Node appends, never replaces.
    # Pattern: return {"errors": state["errors"] + ["new error"]}
    errors: list[str]

    # ── job status ─────────────────────────────────────────────────────────
    # Written to Redis on every transition. Client polls this.
    status: Literal["queued", "running", "complete", "failed"]


def initial_state(job_id: str, user_id: str, raw_input: InputRequest) -> LangGraphState:
    """
    Factory for the starting state of a new job.
    All output keys start as None. Control flow keys at safe defaults.
    """
    return LangGraphState(
        job_id=job_id,
        user_id=user_id,
        raw_input=raw_input,
        resume_output=None,
        github_output=None,
        intent_output=None,
        merged_profile=None,
        selected_components=None,
        resolved_media=None,
        copy_output=None,
        final_output=None,
        retry_count=0,
        validation_result=None,
        resume_mode=None,
        errors=[],
        status="queued",
    )