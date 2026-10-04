"""
Component Library MCP Server.
Built with FastMCP — exposes the portfolio section template catalog
as MCP tools so any agent or pipeline node can fetch components
through a standardized interface.

Tools:
  list_components(profession_type, style_pattern, special_features)
      → ranked list of matching components, defaults if no match

  get_component(component_id)
      → single component by ID

  list_all_professions()
      → all known profession types in the catalog

Run locally:
  python mcp_servers/component_library/server.py

In production: runs as a persistent SSE service on port 8001.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from schemas.components import Component, SelectedComponents

# ── Catalog loading ───────────────────────────────────────────────────────

CATALOG_DIR = Path(__file__).parent / "catalog"


def load_catalog() -> list[Component]:
    """Loads and parses components.json. Called once at startup."""
    with open(CATALOG_DIR / "components.json") as f:
        raw = json.load(f)
    return [Component(**item) for item in raw]


def load_default_ids() -> list[str]:
    """Loads component IDs from defaults.json."""
    with open(CATALOG_DIR / "defaults.json") as f:
        return json.load(f)


# Cache at module level — catalog is static, no need to re-read per request
_catalog: list[Component] = []
_defaults: list[str] = []

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# ── MCP Server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="component-library",
    description=(
        "Serves portfolio section template components filtered by "
        "profession type and style pattern."
    ),
)


@mcp.tool()
async def list_components(
    profession_type: str,
    style_pattern: Literal["minimal", "modern", "classic", "premium"],
    special_features: list[str] = [],
    max_results: int = 8,
) -> dict:
    """
    Returns a ranked list of portfolio components matching the given
    profession_type and style_pattern.

    Falls back to the default component set if nothing matches.
    Injects components for special_features regardless of rank.

    Args:
        profession_type: resolved profession (e.g. "developer", "designer")
        style_pattern: visual style ("minimal", "modern", "classic", "premium")
        special_features: explicitly requested sections (e.g. ["contact_section"])
        max_results: cap on returned components (default 8)

    Returns:
        dict with keys: items (list of components), fallback_used (bool)
    """
    global _catalog, _defaults
    if not _catalog:
        _catalog = load_catalog()
        _defaults = load_default_ids()

    # Filter by profession and style
    matched = [
        c for c in _catalog
        if profession_type in c.suitable_professions
        and style_pattern in c.suitable_styles
    ]

    fallback_used = False
    if not matched:
        # No match — load defaults
        matched = [c for c in _catalog if c.component_id in _defaults]
        fallback_used = True

    # Inject special feature components (e.g. "contact_section" → contact component)
    FEATURE_MAP = {
        "contact_section": ["contact"],
        "blog_links": ["blog"],
        "awards_section": ["awards"],
    }
    for feature in special_features:
        section_types = FEATURE_MAP.get(feature, [])
        for section_type in section_types:
            # Add if not already in matched
            feature_components = [
                c for c in _catalog
                if c.type == section_type
                and style_pattern in c.suitable_styles
                and c not in matched
            ]
            if feature_components:
                matched.append(feature_components[0])

    # Rank by layout_priority
    matched.sort(key=lambda c: PRIORITY_ORDER.get(c.layout_priority, 99))

    # Deduplicate by type — keep highest priority per section type
    seen_types: set[str] = set()
    deduped = []
    for c in matched:
        if c.type not in seen_types:
            deduped.append(c)
            seen_types.add(c.type)

    result = deduped[:max_results]

    return {
        "items": [c.model_dump() for c in result],
        "fallback_used": fallback_used,
        "selector_version": "1.0.0",
    }


@mcp.tool()
async def get_component(component_id: str) -> dict:
    """
    Returns the full schema for a specific component by ID.

    Args:
        component_id: e.g. "hero_developer_modern_01"

    Returns:
        Component dict or raises error if not found.
    """
    global _catalog
    if not _catalog:
        _catalog = load_catalog()

    component = next((c for c in _catalog if c.component_id == component_id), None)
    if component is None:
        raise ValueError(f"Component '{component_id}' not found in catalog")

    return component.model_dump()


@mcp.tool()
async def list_all_professions() -> list[str]:
    """
    Returns all profession types present in the component catalog.
    Used by the Interpreter's embedding match to know the target set.
    """
    global _catalog
    if not _catalog:
        _catalog = load_catalog()

    professions: set[str] = set()
    for c in _catalog:
        professions.update(c.suitable_professions)

    return sorted(professions)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("COMPONENT_LIBRARY_PORT", "8001"))
    mcp.run(transport="sse", host="0.0.0.0", port=port)