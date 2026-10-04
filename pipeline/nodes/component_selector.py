"""
Component Selector Node.
Calls component-library MCP server, filters by profession+style,
injects special feature components, deduplicates by section type.
"""
from __future__ import annotations
import asyncio
from pipeline.state import LangGraphState
from mcp_clients import component_library_client
from infra.config import settings


async def component_selector_node(state: LangGraphState) -> dict:
    profile = state["merged_profile"]
    if not profile:
        return {
            "selected_components": None,
            "errors": state["errors"] + ["Component selector: no merged profile available"],
        }

    try:
        result = await asyncio.wait_for(
            component_library_client.list_components(
                profession_type=profile.identity.profession_type,
                style_pattern=profile.style.pattern,
                special_features=profile.style.special_features,
            ),
            timeout=settings.timeout_component_selector,
        )
        return {"selected_components": result}

    except asyncio.TimeoutError:
        return {
            "selected_components": None,
            "errors": state["errors"] + ["Component selector timed out"],
        }
    except Exception as e:
        return {
            "selected_components": None,
            "errors": state["errors"] + [
                f"Component selector failed: {type(e).__name__}: {str(e)}"
            ],
        }