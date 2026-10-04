"""
Media mapper output schema — resolved URLs for every media slot
in the selected components.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class ResolvedMedia(BaseModel):
    """
    Maps state path references → actual URLs.
    e.g. "identity.avatar_url" → "https://avatars.githubusercontent.com/..."
    """
    resolved_media: dict[str, str] = Field(
        default_factory=dict,
        description="slot_path → resolved URL"
    )
    unresolved: list[str] = Field(
        default_factory=list,
        description=(
            "Slots that couldn't be resolved — Layout Assembler uses "
            "CDN placeholder for these. Never causes a pipeline failure."
        )
    )
    placeholder_url: str = "https://cdn.internal/placeholders/default-thumbnail.png"