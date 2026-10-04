"""
Prompt interpreter output schema — structured intent extracted from user input.
Output of the GPT-4o-mini Interpreter node.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ResolvedProfession(BaseModel):
    raw_input: str                          # exactly what the user typed
    resolved: str                           # the profession we'll use downstream
    match_mode: Literal[
        "preset_direct",                    # user picked a known preset
        "embedding_match",                  # custom input matched above threshold
        "fallback_generic"                  # below threshold, using "general"
    ]
    similarity_score: float | None = None   # None for preset_direct
    fallback_used: bool = False


class PromptInterpreterOutput(BaseModel):
    """
    All fields the Interpreter extracts from the user's input.
    Everything downstream uses this — never the raw prompt directly.
    """
    profession_type: ResolvedProfession
    style_pattern: Literal["minimal", "modern", "classic", "premium"]
    color_preference: Literal["light", "dark", "auto"] = "auto"
    tone: Literal["confident", "technical", "creative", "academic", "friendly"] = "confident"
    focus_area: Literal[
        "production_impact",
        "research_and_systems",
        "design_and_craft",
        "writing_and_content",
        "general"
    ] = "general"
    special_features: list[str] = Field(
        default_factory=list,
        description="Sections explicitly requested, e.g. ['contact_section', 'blog_links']"
    )
    must_include_sections: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(
        default_factory=list,
        description="Things to avoid in copy, e.g. ['salesy language', 'fluff metrics']"
    )
    original_prompt: str                    # preserved verbatim for Copy Generation context