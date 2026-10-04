"""
MergedProfile — the canonical shape produced by the Merge/Normalize node.
Every downstream node reads from this. No node after Merge ever touches
resume_output or github_output directly.

This is the firewall between the messy source world and the clean pipeline.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class MergedIdentity(BaseModel):
    name: str | None = None
    tagline: str | None = None              # synthesized: title + company or top skill
    email: str | None = None
    avatar_url: str | None = None
    location: str | None = None
    profession_type: str                    # resolved value from Interpreter


class MergedSkill(BaseModel):
    name: str
    category: str = "other"
    source: list[Literal["resume", "github", "linkedin"]] = Field(
        ...,
        description="Which sources mentioned this skill — enables 'verified in production' copy"
    )


class MergedExperience(BaseModel):
    role: str
    company: str
    dates: str                              # human-readable, e.g. "Jan 2025 – Present"
    highlights: list[str] = Field(default_factory=list)
    source: Literal["resume"] = "resume"   # experience always from resume only


class MergedProject(BaseModel):
    name: str
    description: str | None = None
    github_url: str | None = None
    live_url: str | None = None
    stars: int = 0
    primary_language: str | None = None
    topics: list[str] = Field(default_factory=list)
    thumbnail_url: str | None = None
    source: list[Literal["resume", "github"]] = Field(
        ...,
        description="resume+github means deduped from both — richer data"
    )


class MergedEducation(BaseModel):
    degree: str
    institution: str
    years: str | None = None               # "2017 – 2021"
    cgpa: str | None = None
    source: Literal["resume"] = "resume"


class MergedAward(BaseModel):
    title: str
    detail: str | None = None
    source: Literal["resume"] = "resume"


class MergedPatent(BaseModel):
    title: str
    status: str
    source: Literal["resume"] = "resume"


class MergedLinks(BaseModel):
    github_url: str | None = None
    linkedin_url: str | None = None
    linkedin_text: str | None = None        # manual paste fallback
    portfolio_url: str | None = None
    email: str | None = None


class StylePreferences(BaseModel):
    pattern: Literal["minimal", "modern", "classic", "premium"]
    color_preference: Literal["light", "dark", "auto"]
    tone: str
    focus_area: str
    special_features: list[str] = Field(default_factory=list)
    must_include_sections: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class MergeMetadata(BaseModel):
    """
    Records exactly what happened during merge — essential for debugging.
    If a generation looks wrong, inspect _meta first.
    """
    sources_present: list[str]             # e.g. ["resume", "github"]
    sources_missing: list[str]             # e.g. ["linkedin"]
    resume_mode: Literal["parse", "generate"] | None = None
    resume_confidence: float | None = None
    profession_match_mode: str             # from Interpreter output
    projects_deduped: int = 0              # how many resume+github overlaps were merged
    skills_deduped: int = 0
    profile_truncated: bool = False        # True if token budget summarization ran


class MergedProfile(BaseModel):
    """
    Single canonical profile shape. Built once by Merge node.
    Read-only for all subsequent nodes.
    """
    identity: MergedIdentity
    skills: list[MergedSkill] = Field(default_factory=list)
    experience: list[MergedExperience] = Field(default_factory=list)
    projects: list[MergedProject] = Field(default_factory=list)
    education: list[MergedEducation] = Field(default_factory=list)
    awards: list[MergedAward] = Field(default_factory=list)
    patents: list[MergedPatent] = Field(default_factory=list)
    links: MergedLinks = Field(default_factory=MergedLinks)
    style: StylePreferences

    meta: MergeMetadata