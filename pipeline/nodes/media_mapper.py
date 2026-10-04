"""
Media Mapper Node.
Resolves component media slots to actual URLs from MergedProfile.
Deterministic — no LLM, no external calls.
Unresolved slots use a placeholder — never fails the pipeline.
"""
from __future__ import annotations
from pipeline.state import LangGraphState
from schemas.media import ResolvedMedia

INTERNAL_CDN = "https://cdn.internal"
ICON_MAP = {
    "icons.github": f"{INTERNAL_CDN}/icons/github.svg",
    "icons.linkedin": f"{INTERNAL_CDN}/icons/linkedin.svg",
    "icons.email": f"{INTERNAL_CDN}/icons/email.svg",
    "icons.award": f"{INTERNAL_CDN}/icons/award.svg",
}


def media_mapper_node(state: LangGraphState) -> dict:
    """Synchronous — pure URL resolution, no I/O."""
    profile = state["merged_profile"]
    components = state["selected_components"]

    if not profile or not components:
        return {
            "resolved_media": ResolvedMedia(),
        }

    resolved: dict[str, str] = {}
    unresolved: list[str] = []

    # Collect all media slots across selected components
    all_slots: set[str] = set()
    for component in components.items:
        for slot in component.data_slots:
            if _is_media_slot(slot):
                all_slots.add(slot)

    for slot in all_slots:
        url = _resolve_slot(slot, profile)
        if url:
            resolved[slot] = url
        else:
            # Check icon map
            icon_url = ICON_MAP.get(slot)
            if icon_url:
                resolved[slot] = icon_url
            else:
                unresolved.append(slot)

    return {
        "resolved_media": ResolvedMedia(
            resolved_media=resolved,
            unresolved=unresolved,
        )
    }


def _is_media_slot(slot: str) -> bool:
    """Returns True if slot references a URL (image/icon), not text data."""
    return any(keyword in slot for keyword in [
        "avatar_url", "thumbnail_url", "image_url", "icons.", "logo_url"
    ])


def _resolve_slot(slot: str, profile) -> str | None:
    """Resolves a slot path to a URL from the profile."""
    slot_map = {
        "identity.avatar_url": profile.identity.avatar_url,
    }

    # Handle project thumbnail slots — projects[].thumbnail_url
    if "projects[" in slot and "thumbnail_url" in slot:
        for project in profile.projects:
            if project.thumbnail_url:
                return project.thumbnail_url
        return None

    return slot_map.get(slot)