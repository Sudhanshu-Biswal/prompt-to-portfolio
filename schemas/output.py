"""
Three schemas in one file because they're tightly coupled:
  CopyGenerationOutput  — what GPT-4o writes
  ValidationResult      — what the Validator produces
  FinalOutput           — what the client receives
"""
from __future__ import annotations
from typing import Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


# ── Copy Generation ────────────────────────────────────────────────────────

class HeroCopy(BaseModel):
    headline: str = Field(..., max_length=60)
    subheadline: str = Field(..., max_length=200)
    cta_label: str = Field(default="See my work", max_length=30)


class ProjectCopyOverride(BaseModel):
    project_name: str
    tagline: str = Field(..., max_length=120)


class ProjectsSectionCopy(BaseModel):
    section_title: str = Field(default="Projects", max_length=50)
    intro: str | None = Field(default=None, max_length=200)
    project_overrides: list[ProjectCopyOverride] = Field(default_factory=list)


class GenericSectionCopy(BaseModel):
    section_title: str = Field(..., max_length=50)
    intro: str | None = Field(default=None, max_length=200)


class ContactCopy(BaseModel):
    section_title: str = Field(default="Get in touch", max_length=50)
    body: str = Field(..., max_length=300)
    cta_label: str = Field(default="Send a message", max_length=30)


class CopyGenerationOutput(BaseModel):
    """
    All text content produced by GPT-4o.
    Every field has a character limit — enforced here AND in the Validator.
    Two layers because one always gets bypassed.
    """
    hero: HeroCopy
    skills: GenericSectionCopy
    projects: ProjectsSectionCopy
    experience: GenericSectionCopy
    awards: GenericSectionCopy | None = None
    contact: ContactCopy | None = None


# ── Validation ────────────────────────────────────────────────────────────

class ValidationError(BaseModel):
    section: str
    field: str
    issue: str
    current_value: Any = None
    constraint: str | None = None


class ValidationResult(BaseModel):
    passed: bool
    retry_count: int = 0
    errors: list[ValidationError] = Field(default_factory=list)
    retry_prompt_addition: str | None = Field(
        default=None,
        description=(
            "Structured feedback injected into Copy Generation prompt on retry. "
            "Specific about what failed and what to preserve."
        )
    )


# ── Final Output ──────────────────────────────────────────────────────────

class SectionProps(BaseModel):
    """Flexible props bag — schema varies by section type."""
    model_config = {"extra": "allow"}


class Section(BaseModel):
    type: str
    component_id: str
    props: dict[str, Any] = Field(default_factory=dict)


class OutputMetadata(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0"
    profession_type: str
    sources_used: list[str]
    sources_missing: list[str] = Field(default_factory=list)
    fallback_used: bool = False         # True if safe fallback layout was used
    retry_count: int = 0
    generator_version: str = "0.1.0"


class Page(BaseModel):
    page: str = "home"
    sections: list[Section]


class FinalOutput(BaseModel):
    """
    The complete JSON handed to the Next.js render layer.
    Every fact in props came from MergedProfile.
    Every piece of prose came from CopyGenerationOutput.
    Layout Assembler just assembled them — no invention.
    """
    job_id: str
    layout: Literal["minimal", "modern", "classic", "premium"]
    color_scheme: Literal["light", "dark", "auto"]
    font: str = "inter"
    metadata: OutputMetadata
    pages: list[Page]
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues: missing sources, fallbacks used, etc."
    )

    @field_validator("pages")
    @classmethod
    def at_least_one_page(cls, v):
        if not v:
            raise ValueError("FinalOutput must have at least one page")
        return v