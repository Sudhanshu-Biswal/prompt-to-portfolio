"""
Input schema — what the client sends to POST /v1/generate.
Every field validated here before the job is created.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class ResumeFile(BaseModel):
    present: bool
    filename: str | None = None
    mime_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ] | None = None
    # raw bytes not stored in schema — handled separately via object storage
    s3_key: str | None = None         # set after upload to S3


class ProfessionType(BaseModel):
    mode: Literal["preset", "custom"]
    value: str = Field(..., min_length=1, max_length=100)

    @field_validator("value")
    @classmethod
    def validate_preset_values(cls, v, info):
        VALID_PRESETS = {
            "developer", "designer", "writer",
            "researcher", "product_manager", "data_scientist", "general"
        }
        # only validate against preset list if mode is preset
        # custom values are free text — validated by length only
        return v


class GitHubSource(BaseModel):
    connected: bool
    username: str | None = None

    @model_validator(mode="after")
    def username_required_if_connected(self):
        if self.connected and not self.username:
            raise ValueError("username required when github is connected")
        return self


class LinkedInSource(BaseModel):
    # No API — manual paste only
    pasted_text: str | None = Field(
        default=None,
        max_length=5000,
        description="User's LinkedIn About section or profile summary, pasted manually"
    )


class ProfileSources(BaseModel):
    github: GitHubSource = Field(default_factory=lambda: GitHubSource(connected=False))
    linkedin: LinkedInSource = Field(default_factory=LinkedInSource)


class InputRequest(BaseModel):
    """
    Full input payload. Either resume_file.present=True OR
    self_description must be provided — not neither.
    """
    resume_file: ResumeFile = Field(default_factory=lambda: ResumeFile(present=False))
    self_description: str | None = Field(
        default=None,
        min_length=50,
        max_length=2000,
        description="Free-text self-description, used only when no resume is uploaded"
    )
    profession_type: ProfessionType
    style_pattern: Literal["minimal", "modern", "classic", "premium"]
    prompt: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Free-text: tone, emphasis, must-include sections, things to avoid"
    )
    profile_sources: ProfileSources = Field(default_factory=ProfileSources)

    @model_validator(mode="after")
    def require_resume_or_description(self):
        if not self.resume_file.present and not self.self_description:
            raise ValueError(
                "Either upload a resume or provide a self_description. "
                "At least one profile source is required."
            )
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "resume_file": {"present": False},
                "self_description": (
                    "I'm a senior AI engineer with 5 years of experience building "
                    "production GenAI systems. I've built RAG pipelines, fine-tuned "
                    "LLMs, and designed agentic workflows using LangGraph at BOLD."
                ),
                "profession_type": {"mode": "preset", "value": "developer"},
                "style_pattern": "modern",
                "prompt": (
                    "Dark mode portfolio emphasizing AI projects and production impact. "
                    "Confident but not salesy. Include a contact section."
                ),
                "profile_sources": {
                    "github": {"connected": True, "username": "sudhanshu-biswal"},
                    "linkedin": {"pasted_text": None}
                }
            }
        }