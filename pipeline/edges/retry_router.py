"""
Retry router — conditional edge after assemble_validate node.

Reads validation_result and retry_count from state.
Returns the next node name — the graph uses this string to route.

Three outcomes:
  "done"           — validation passed, graph ends normally
  "copy_generation" — validation failed, retry budget remaining,
                      loop back with structured feedback in state
  "fallback"       — max retries hit, safe fallback layout already
                     written to state by assemble_validate node

This is a pure function. No side effects, no state mutation.
Independently testable with just a state dict.
"""
from __future__ import annotations
import os
from pipeline.state import LangGraphState

MAX_RETRIES = int(os.getenv("MAX_COPY_RETRIES", "2"))


def retry_router(state: LangGraphState) -> str:
    """
    Pure routing function — no state mutation, no side effects.
    Called by LangGraph after assemble_validate completes.
    """
    result = state["validation_result"]

    # Should never be None here — assemble_validate always sets it
    # Defensive check: treat as passed to avoid infinite loops
    if result is None:
        return "done"

    if result.passed:
        return "done"

    if state["retry_count"] < MAX_RETRIES:
        # structured feedback is already in state["validation_result"].retry_prompt_addition
        # copy_generation node will read it on next run
        return "copy_generation"

    # Max retries exhausted — assemble_validate has already written
    # the safe fallback layout to state["final_output"]
    return "fallback"