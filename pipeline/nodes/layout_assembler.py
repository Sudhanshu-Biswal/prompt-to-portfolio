"""
Layout Assembler + Validator Node.
Two logical steps in one node — they always run together.

Step 1 — Assembler: builds FinalOutput JSON from MergedProfile + CopyOutput + SelectedComponents.
          Deterministic. No LLM. Pure data assembly.

Step 2 — Validator: checks FinalOutput against schema + field constraints.
          On failure: builds structured retry_prompt_addition for Copy Generation.
          On max retries: writes safe fallback layout to final_output.

The retry router edge reads validation_result after this node to decide next step.
"""
from __future__ import annotations
from datetime import datetime
from pipeline.state import LangGraphState
from schemas.output import (
    FinalOutput, OutputMetadata, Page, Section,
    ValidationResult, ValidationError, CopyGenerationOutput
)
from infra.config import settings


def assemble_validate_node(state: LangGraphState) -> dict:
    """Synchronous — pure data assembly and validation."""
    profile = state["merged_profile"]
    components = state["selected_components"]
    copy = state["copy_output"]
    media = state["resolved_media"]

    # If we have nothing to work with — hard fail
    if not profile:
        return {
            "status": "failed",
            "errors": state["errors"] + ["Cannot assemble: no merged profile"],
            "validation_result": ValidationResult(passed=False),
        }

    # ── Step 1: Assemble ──────────────────────────────────────────────────
    sections = _build_sections(profile, components, copy, media)

    meta = state["merged_profile"].meta
    candidate = FinalOutput(
        job_id=state["job_id"],
        layout=profile.style.pattern,
        color_scheme=profile.style.color_preference,
        font=_pick_font(profile.style.pattern),
        metadata=OutputMetadata(
            generated_at=datetime.utcnow(),
            profession_type=profile.identity.profession_type,
            sources_used=meta.sources_present,
            sources_missing=meta.sources_missing,
            fallback_used=False,
            retry_count=state["retry_count"],
        ),
        pages=[Page(page="home", sections=sections)],
        warnings=[e for e in state["errors"] if e],
    )

    # ── Step 2: Validate ──────────────────────────────────────────────────
    errors = _validate(candidate, copy)

    if not errors:
        return {
            "final_output": candidate,
            "validation_result": ValidationResult(passed=True),
            "status": "complete",
        }

    # Validation failed
    retry_count = state["retry_count"] + 1

    if retry_count > settings.max_copy_retries:
        # Max retries hit — use safe fallback layout
        fallback = _build_safe_fallback(state, candidate)
        return {
            "final_output": fallback,
            "validation_result": ValidationResult(
                passed=False,
                retry_count=retry_count,
                errors=errors,
            ),
            "retry_count": retry_count,
            "status": "complete",
            "errors": state["errors"] + [
                f"Validation failed after {retry_count} retries — using safe fallback layout"
            ],
        }

    # Build structured retry feedback for Copy Generation
    retry_feedback = _build_retry_feedback(errors)

    return {
        "final_output": None,
        "validation_result": ValidationResult(
            passed=False,
            retry_count=retry_count,
            errors=errors,
            retry_prompt_addition=retry_feedback,
        ),
        "retry_count": retry_count,
    }


def _build_sections(profile, components, copy: CopyGenerationOutput | None, media) -> list[Section]:
    """Assembles Section objects from profile data + copy."""
    sections = []

    if not components:
        return _minimal_sections(profile, copy)

    copy_map = _copy_to_map(copy) if copy else {}

    for component in components.items:
        section = _build_section(component, profile, copy_map, media)
        if section:
            sections.append(section)

    return sections


def _build_section(component, profile, copy_map: dict, media) -> Section | None:
    """Builds one Section from a component + profile data."""
    props: dict = {}
    ctype = component.type

    if ctype == "hero":
        hero_copy = copy_map.get("hero", {})
        props = {
            "name": profile.identity.name,
            "headline": hero_copy.get("headline", profile.identity.name or ""),
            "subheadline": hero_copy.get("subheadline", profile.identity.tagline or ""),
            "avatar_url": profile.identity.avatar_url,
            "cta": {"label": hero_copy.get("cta_label", "See my work"), "anchor": "#projects"},
            "links": {
                "github": profile.links.github_url,
                "linkedin": profile.links.linkedin_url,
                "email": profile.links.email,
            },
        }

    elif ctype == "skills":
        skills_copy = copy_map.get("skills", {})
        # Group by category
        grouped: dict[str, list[str]] = {}
        for skill in profile.skills:
            grouped.setdefault(skill.category, []).append(skill.name)
        props = {
            "section_title": skills_copy.get("section_title", "Skills"),
            "groups": [
                {"category": cat, "items": items}
                for cat, items in grouped.items()
            ],
        }

    elif ctype == "projects":
        projects_copy = copy_map.get("projects", {})
        overrides = {
            o["project_name"]: o["tagline"]
            for o in projects_copy.get("project_overrides", [])
        }
        props = {
            "section_title": projects_copy.get("section_title", "Projects"),
            "intro": projects_copy.get("intro"),
            "projects": [
                {
                    "name": p.name,
                    "tagline": overrides.get(p.name, p.description or ""),
                    "description": p.description,
                    "github_url": p.github_url,
                    "thumbnail_url": p.thumbnail_url,
                    "stars": p.stars,
                    "topics": p.topics[:5],
                }
                for p in profile.projects
            ],
        }

    elif ctype == "experience":
        exp_copy = copy_map.get("experience", {})
        props = {
            "section_title": exp_copy.get("section_title", "Experience"),
            "items": [
                {
                    "role": e.role,
                    "company": e.company,
                    "dates": e.dates,
                    "highlights": e.highlights,
                }
                for e in profile.experience
            ],
        }

    elif ctype == "awards":
        awards_copy = copy_map.get("awards", {})
        props = {
            "section_title": awards_copy.get("section_title", "Recognition"),
            "items": [
                {"title": a.title, "detail": a.detail}
                for a in profile.awards
            ] + [
                {"title": f"Patent: {p.title}", "detail": p.status}
                for p in profile.patents
            ],
        }

    elif ctype == "contact":
        contact_copy = copy_map.get("contact", {})
        props = {
            "section_title": contact_copy.get("section_title", "Get in touch"),
            "body": contact_copy.get("body", ""),
            "cta": {"label": contact_copy.get("cta_label", "Send a message")},
            "links": {
                "email": profile.links.email,
                "github": profile.links.github_url,
                "linkedin": profile.links.linkedin_url,
            },
        }

    else:
        return None

    return Section(
        type=ctype,
        component_id=component.component_id,
        props=props,
    )


def _copy_to_map(copy: CopyGenerationOutput) -> dict:
    """Converts CopyGenerationOutput to a simple dict for section building."""
    result = {}
    if copy.hero:
        result["hero"] = copy.hero.model_dump()
    if copy.skills:
        result["skills"] = copy.skills.model_dump()
    if copy.projects:
        result["projects"] = copy.projects.model_dump()
    if copy.experience:
        result["experience"] = copy.experience.model_dump()
    if copy.awards:
        result["awards"] = copy.awards.model_dump()
    if copy.contact:
        result["contact"] = copy.contact.model_dump()
    return result


def _validate(output: FinalOutput, copy: CopyGenerationOutput | None) -> list[ValidationError]:
    """
    Validates FinalOutput against field constraints.
    Returns list of ValidationErrors — empty means passed.
    """
    errors = []

    if not output.pages or not output.pages[0].sections:
        errors.append(ValidationError(
            section="output",
            field="pages",
            issue="No sections assembled",
            constraint="min_sections: 1",
        ))
        return errors

    # Validate copy character limits directly
    if copy:
        if copy.hero and len(copy.hero.headline) > 60:
            errors.append(ValidationError(
                section="hero",
                field="headline",
                issue=f"Exceeds 60 character limit ({len(copy.hero.headline)} chars)",
                current_value=copy.hero.headline,
                constraint="max_length: 60",
            ))
        if copy.hero and len(copy.hero.subheadline) > 200:
            errors.append(ValidationError(
                section="hero",
                field="subheadline",
                issue=f"Exceeds 200 character limit ({len(copy.hero.subheadline)} chars)",
                current_value=copy.hero.subheadline,
                constraint="max_length: 200",
            ))
        if copy.contact and len(copy.contact.body) > 300:
            errors.append(ValidationError(
                section="contact",
                field="body",
                issue=f"Exceeds 300 character limit ({len(copy.contact.body)} chars)",
                current_value=copy.contact.body[:50] + "...",
                constraint="max_length: 300",
            ))
        if copy.projects:
            for override in copy.projects.project_overrides:
                if len(override.tagline) > 120:
                    errors.append(ValidationError(
                        section="projects",
                        field=f"tagline for '{override.project_name}'",
                        issue=f"Exceeds 120 character limit ({len(override.tagline)} chars)",
                        current_value=override.tagline[:50] + "...",
                        constraint="max_length: 120",
                    ))

    return errors


def _build_retry_feedback(errors: list[ValidationError]) -> str:
    """
    Builds a specific, actionable retry instruction from validation errors.
    This goes into the Copy Generation prompt on retry.
    """
    lines = ["VALIDATION ERRORS TO FIX IN YOUR NEXT RESPONSE:"]
    for err in errors:
        lines.append(
            f"- {err.section}.{err.field}: {err.issue}. "
            f"Constraint: {err.constraint}."
        )
    lines.append(
        "\nRewrite only the fields listed above. "
        "Preserve the tone, facts, and all other content exactly."
    )
    return "\n".join(lines)


def _build_safe_fallback(state: LangGraphState, candidate: FinalOutput) -> FinalOutput:
    """
    Builds a minimal but always-valid layout from profile data only.
    Used when max retries are exhausted.
    No LLM copy — plain factual text only.
    """
    profile = state["merged_profile"]
    candidate.metadata.fallback_used = True

    # Simplify hero to just name + tagline (always within limits)
    for section in candidate.pages[0].sections:
        if section.type == "hero":
            name = profile.identity.name or "Portfolio"
            section.props["headline"] = name[:60]
            section.props["subheadline"] = (
                profile.identity.tagline or ""
            )[:200]

    return candidate


def _minimal_sections(profile, copy) -> list[Section]:
    """Minimal section list when component selector failed."""
    copy_map = _copy_to_map(copy) if copy else {}
    hero_copy = copy_map.get("hero", {})
    return [
        Section(
            type="hero",
            component_id="hero_developer_minimal_01",
            props={
                "name": profile.identity.name,
                "headline": hero_copy.get("headline", profile.identity.name or "")[:60],
                "subheadline": hero_copy.get("subheadline", "")[:200],
                "links": {"email": profile.links.email},
            },
        )
    ]


def _pick_font(style_pattern: str) -> str:
    return {
        "modern": "inter",
        "minimal": "inter",
        "classic": "georgia",
        "premium": "playfair",
    }.get(style_pattern, "inter")