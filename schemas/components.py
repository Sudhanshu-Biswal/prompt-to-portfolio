"""
Component schemas — the portfolio section template catalog.
Served by the component-library MCP server.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class MediaRequirement(BaseModel):
    type: Literal["image", "video", "icon", "none"] = "none"
    slots: list[str] = Field(
        default_factory=list,
        description="State path references, e.g. 'identity.avatar_url', 'projects[].thumbnail_url'"
    )
    count: int = 0


class Component(BaseModel):
    component_id: str
    type: Literal[
        "hero", "about", "skills", "projects",
        "experience", "education", "awards",
        "contact", "testimonials", "blog", "custom"
    ]
    display_name: str
    description: str
    suitable_professions: list[str] = Field(
        ...,
        description="Profession types this component works well for"
    )
    suitable_styles: list[Literal["minimal", "modern", "classic", "premium"]]
    layout_priority: Literal["high", "medium", "low"]
    required_for_professions: list[str] = Field(default_factory=list)
    data_slots: list[str] = Field(
        ...,
        description="MergedProfile paths this component needs, e.g. 'identity.name'"
    )
    media_requirements: MediaRequirement = Field(default_factory=MediaRequirement)
    default_props: dict = Field(default_factory=dict)
    style_variant: str | None = None


class SelectedComponents(BaseModel):
    items: list[Component]
    fallback_used: bool = False             # True if defaults.json was loaded
    selector_version: str = "1.0.0"