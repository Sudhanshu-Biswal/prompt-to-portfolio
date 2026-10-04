"""
Copy Generation Node.
GPT-4o writes all text content for the portfolio sections.
This is the most expensive node (~$0.016, ~8-12s).

On retry: validation_result.retry_prompt_addition is appended to the prompt
so the model has specific feedback about what failed and what to fix.
"""
from __future__ import annotations
import asyncio
import json
from openai import AsyncOpenAI
from pipeline.state import LangGraphState
from schemas.output import (
    CopyGenerationOutput, HeroCopy, GenericSectionCopy,
    ProjectsSectionCopy, ProjectCopyOverride, ContactCopy
)
from infra.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)

COPY_SYSTEM_PROMPT = """
You are a professional portfolio copywriter. Generate section copy for a
portfolio website based on the provided profile data and intent.

CRITICAL RULES:
1. Only use facts explicitly present in the profile_data. Never invent metrics,
   company names, dates, awards, or credentials not in the data.
2. Match the requested tone exactly.
3. Respect the avoid list — never include those elements.
4. Stay within character limits:
   - hero.headline: max 60 chars
   - hero.subheadline: max 200 chars
   - project taglines: max 120 chars
   - section titles: max 50 chars
   - contact.body: max 300 chars

Return ONLY valid JSON matching this schema. No markdown, no preamble:
{
  "hero": {
    "headline": "...",
    "subheadline": "...",
    "cta_label": "See my work"
  },
  "skills": {"section_title": "Technical Stack"},
  "projects": {
    "section_title": "Projects",
    "intro": "...",
    "project_overrides": [
      {"project_name": "...", "tagline": "..."}
    ]
  },
  "experience": {"section_title": "Experience"},
  "awards": {"section_title": "Recognition"},
  "contact": {
    "section_title": "Get in touch",
    "body": "...",
    "cta_label": "Send a message"
  }
}
"""


async def copy_generation_node(state: LangGraphState) -> dict:
    profile = state["merged_profile"]
    components = state["selected_components"]

    if not profile:
        return {
            "copy_output": None,
            "errors": state["errors"] + ["Copy generation: no merged profile"],
        }

    try:
        # Build context for the LLM
        context = {
            "profile_data": {
                "name": profile.identity.name,
                "tagline": profile.identity.tagline,
                "profession_type": profile.identity.profession_type,
                "skills": [s.name for s in profile.skills[:15]],
                "experience": [
                    {
                        "role": e.role,
                        "company": e.company,
                        "dates": e.dates,
                        "highlights": e.highlights[:4],
                    }
                    for e in profile.experience[:3]
                ],
                "projects": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "stars": p.stars,
                        "topics": p.topics[:5],
                    }
                    for p in profile.projects[:6]
                ],
                "awards": [a.title for a in profile.awards],
                "patents": [p.title for p in profile.patents],
            },
            "intent": {
                "tone": profile.style.tone,
                "focus_area": profile.style.focus_area,
                "avoid": profile.style.avoid,
                "original_prompt": state["raw_input"].prompt,
            },
        }

        user_message = f"Generate portfolio copy for this profile:\n{json.dumps(context, indent=2)}"

        # Append retry feedback if this is a retry
        if state["retry_count"] > 0 and state["validation_result"]:
            feedback = state["validation_result"].retry_prompt_addition
            if feedback:
                user_message += f"\n\nPREVIOUS ATTEMPT FAILED — FIX THESE ISSUES:\n{feedback}"

        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_copy_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": COPY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
                max_tokens=1200,
            ),
            timeout=settings.timeout_copy_generation,
        )

        raw = json.loads(response.choices[0].message.content)
        copy_output = _parse_copy_output(raw)

        return {
            "copy_output": copy_output,
            "retry_count": state["retry_count"],
        }

    except asyncio.TimeoutError:
        return {
            "copy_output": _safe_fallback_copy(state),
            "errors": state["errors"] + [
                f"Copy generation timed out after {settings.timeout_copy_generation}s "
                "— using fallback copy"
            ],
        }
    except Exception as e:
        return {
            "copy_output": _safe_fallback_copy(state),
            "errors": state["errors"] + [
                f"Copy generation failed: {type(e).__name__}: {str(e)} — "
                "using fallback copy"
            ],
        }


def _parse_copy_output(raw: dict) -> CopyGenerationOutput:
    """Parses raw LLM JSON output into typed CopyGenerationOutput."""
    hero_raw = raw.get("hero", {})
    return CopyGenerationOutput(
        hero=HeroCopy(
            headline=hero_raw.get("headline", "Portfolio")[:60],
            subheadline=hero_raw.get("subheadline", "")[:200],
            cta_label=hero_raw.get("cta_label", "See my work")[:30],
        ),
        skills=GenericSectionCopy(
            section_title=raw.get("skills", {}).get("section_title", "Skills")[:50],
        ),
        projects=ProjectsSectionCopy(
            section_title=raw.get("projects", {}).get("section_title", "Projects")[:50],
            intro=raw.get("projects", {}).get("intro"),
            project_overrides=[
                ProjectCopyOverride(
                    project_name=p["project_name"],
                    tagline=p["tagline"][:120],
                )
                for p in raw.get("projects", {}).get("project_overrides", [])
            ],
        ),
        experience=GenericSectionCopy(
            section_title=raw.get("experience", {}).get("section_title", "Experience")[:50],
        ),
        awards=GenericSectionCopy(
            section_title=raw.get("awards", {}).get("section_title", "Recognition")[:50],
        ),
        contact=ContactCopy(
            section_title=raw.get("contact", {}).get("section_title", "Get in touch")[:50],
            body=raw.get("contact", {}).get("body", "")[:300],
            cta_label=raw.get("contact", {}).get("cta_label", "Send a message")[:30],
        ),
    )


def _safe_fallback_copy(state: LangGraphState) -> CopyGenerationOutput:
    """Deterministic fallback copy — built from profile data, no LLM."""
    profile = state["merged_profile"]
    name = profile.identity.name or "Portfolio" if profile else "Portfolio"
    tagline = profile.identity.tagline or "" if profile else ""

    return CopyGenerationOutput(
        hero=HeroCopy(
            headline=name[:60],
            subheadline=tagline[:200],
            cta_label="See my work",
        ),
        skills=GenericSectionCopy(section_title="Skills"),
        projects=ProjectsSectionCopy(section_title="Projects"),
        experience=GenericSectionCopy(section_title="Experience"),
        awards=GenericSectionCopy(section_title="Recognition"),
        contact=ContactCopy(
            section_title="Get in touch",
            body="Open to new opportunities. Reach out via email.",
            cta_label="Send a message",
        ),
    )