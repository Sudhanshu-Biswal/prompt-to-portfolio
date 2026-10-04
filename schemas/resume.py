"""
Resume parser output schema.
Same shape whether parse_mode (file upload) or generate_mode
(self-description) produced it. Downstream nodes never care which.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ResumeIdentity(BaseModel):
    name: str | None = None
    current_title: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    summary: str | None = None


class Skill(BaseModel):
    name: str
    category: Literal[
        "language", "framework", "infrastructure",
        "technique", "evaluation", "tool", "other"
    ] = "other"


class ExperienceHighlight(BaseModel):
    text: str = Field(..., max_length=300)


class Experience(BaseModel):
    role: str
    company: str
    location: str | None = None
    start_date: str | None = None      # "YYYY-MM" format
    end_date: str | None = None        # None if current
    current: bool = False
    highlights: list[str] = Field(default_factory=list, max_length=8)


class Education(BaseModel):
    degree: str
    institution: str
    start_year: int | None = None
    end_year: int | None = None
    cgpa: str | None = None
    field_of_study: str | None = None


class Award(BaseModel):
    title: str
    detail: str | None = None
    year: int | None = None


class Patent(BaseModel):
    title: str
    status: Literal[
        "granted", "approved_for_filing", "pending", "provisional"
    ] = "pending"
    year: int | None = None


class ResumeLinks(BaseModel):
    github: str | None = None
    linkedin: str | None = None
    portfolio: str | None = None
    other: list[str] = Field(default_factory=list)


class ResumeParserOutput(BaseModel):
    """
    Structured output from the resume parser MCP server.
    All fields optional — graceful absence is expected for sparse inputs
    (especially in generate_mode with a thin self-description).
    """
    source_mode: Literal["parse", "generate"]
    confidence: float = Field(ge=0.0, le=1.0)

    identity: ResumeIdentity = Field(default_factory=ResumeIdentity)
    skills: list[Skill] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    awards: list[Award] = Field(default_factory=list)
    patents: list[Patent] = Field(default_factory=list)
    links: ResumeLinks = Field(default_factory=ResumeLinks)

    # generate mode guardrail flag — set by the MCP server
    # True = model confirmed it only structured what was explicitly stated
    invention_guardrail_applied: bool = False