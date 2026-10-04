"""
Resume Parser MCP Server.
Thin wrapper around the internal BOLD resume-parsing API.
Exposes two tools:
  parse_resume    — file upload → structured JSON (parse mode)
  generate_profile — self-description text → structured JSON (generate mode)

The internal API does the actual LLM work.
This server normalizes the response into our ResumeParserOutput schema
and applies the no-invention guardrail in generate mode.

Run locally:
  python mcp_servers/resume_parser/server.py
"""
from __future__ import annotations
import base64
import os
from typing import Literal

import httpx
from fastmcp import FastMCP

from schemas.resume import (
    ResumeParserOutput, ResumeIdentity, ResumeLinks,
    Skill, Experience, Education, Award, Patent
)
from infra.config import settings

mcp = FastMCP(
    name="resume-parser",
    description=(
        "Parses resume files or synthesizes structured profiles from "
        "free-text self-descriptions via the internal BOLD resume API."
    ),
)

# ── Internal API client ───────────────────────────────────────────────────

async def _call_internal_api(payload: dict) -> dict:
    """
    Calls the internal resume parsing API.
    Raises httpx.HTTPError on failure — caller handles retry/fallback.
    """
    async with httpx.AsyncClient(
        timeout=settings.timeout_resume_parser,
        headers={
            "Authorization": f"Bearer {settings.internal_resume_api_key}",
            "Content-Type": "application/json",
        }
    ) as client:
        response = await client.post(
            settings.internal_resume_api_url,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def _normalize_response(raw: dict, mode: str) -> ResumeParserOutput:
    """
    Normalizes internal API response into ResumeParserOutput schema.
    Internal API may use different field names — all mapping lives here.
    If the internal API schema changes, fix this function only.
    """
    identity_raw = raw.get("identity") or raw.get("personal_info") or {}
    skills_raw = raw.get("skills") or []
    experience_raw = raw.get("experience") or raw.get("work_experience") or []
    education_raw = raw.get("education") or []
    awards_raw = raw.get("awards") or raw.get("achievements") or []
    patents_raw = raw.get("patents") or []
    links_raw = raw.get("links") or raw.get("urls") or {}

    return ResumeParserOutput(
        source_mode=mode,
        confidence=float(raw.get("confidence", 0.85)),
        identity=ResumeIdentity(
            name=identity_raw.get("name") or identity_raw.get("full_name"),
            current_title=identity_raw.get("current_title") or identity_raw.get("title"),
            email=identity_raw.get("email"),
            phone=identity_raw.get("phone"),
            location=identity_raw.get("location"),
            summary=raw.get("summary") or identity_raw.get("summary"),
        ),
        skills=[
            Skill(
                name=s if isinstance(s, str) else s.get("name", ""),
                category=s.get("category", "other") if isinstance(s, dict) else "other",
            )
            for s in skills_raw if s
        ],
        experience=[
            Experience(
                role=e.get("role") or e.get("title") or "",
                company=e.get("company") or e.get("organization") or "",
                location=e.get("location"),
                start_date=e.get("start_date") or e.get("from"),
                end_date=e.get("end_date") or e.get("to"),
                current=e.get("current", False),
                highlights=e.get("highlights") or e.get("responsibilities") or [],
            )
            for e in experience_raw if e
        ],
        education=[
            Education(
                degree=ed.get("degree") or ed.get("qualification") or "",
                institution=ed.get("institution") or ed.get("university") or "",
                start_year=ed.get("start_year"),
                end_year=ed.get("end_year") or ed.get("year"),
                cgpa=str(ed["cgpa"]) if ed.get("cgpa") else None,
                field_of_study=ed.get("field_of_study") or ed.get("major"),
            )
            for ed in education_raw if ed
        ],
        awards=[
            Award(
                title=a.get("title") or a.get("name") or "",
                detail=a.get("detail") or a.get("description"),
                year=a.get("year"),
            )
            for a in awards_raw if a
        ],
        patents=[
            Patent(
                title=p.get("title") or "",
                status=p.get("status", "pending"),
                year=p.get("year"),
            )
            for p in patents_raw if p
        ],
        links=ResumeLinks(
            github=links_raw.get("github"),
            linkedin=links_raw.get("linkedin"),
            portfolio=links_raw.get("portfolio") or links_raw.get("website"),
        ),
        invention_guardrail_applied=(mode == "generate"),
    )


# ── MCP Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def parse_resume(
    file_bytes_b64: str,
    mime_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ],
    confidence_threshold: float = 0.6,
) -> dict:
    """
    Parses a resume file into structured JSON.

    Args:
        file_bytes_b64: base64-encoded file content
        mime_type: PDF or DOCX only
        confidence_threshold: minimum confidence to accept result

    Returns:
        ResumeParserOutput as dict. Raises on failure.
    """
    raw = await _call_internal_api({
        "file": file_bytes_b64,
        "mime_type": mime_type,
        "mode": "json",
        "output_format": "structured",
    })

    result = _normalize_response(raw, mode="parse")

    if result.confidence < confidence_threshold:
        raise ValueError(
            f"Resume parsing confidence {result.confidence:.2f} "
            f"below threshold {confidence_threshold}. "
            "Consider using self-description instead."
        )

    return result.model_dump()


@mcp.tool()
async def generate_profile(
    description: str,
    guardrail_level: Literal["strict", "moderate"] = "strict",
) -> dict:
    """
    Synthesizes a structured profile from a free-text self-description.

    strict guardrail: only structures explicitly stated facts.
                      No invented company names, dates, metrics, or degrees.
    moderate:         allows reasonable inferences
                      (e.g. infers seniority level from years mentioned).

    Args:
        description: user's self-description (50–2000 chars)
        guardrail_level: how strictly to avoid invention

    Returns:
        ResumeParserOutput as dict with invention_guardrail_applied=True.
    """
    if len(description.strip()) < 20:
        raise ValueError("Description too short to generate a meaningful profile.")

    guardrail_instruction = (
        "STRICT: Only extract information explicitly stated by the user. "
        "Do NOT infer, assume, or invent any company names, job titles, dates, "
        "GPAs, metrics, or awards not directly mentioned. "
        "Leave fields null if the information was not provided."
        if guardrail_level == "strict"
        else
        "MODERATE: Extract stated information. You may make reasonable inferences "
        "about seniority level from years of experience mentioned, "
        "but do not invent specific company names, metrics, or credentials."
    )

    raw = await _call_internal_api({
        "text": description,
        "mode": "generate",
        "output_format": "structured",
        "guardrail": guardrail_instruction,
    })

    result = _normalize_response(raw, mode="generate")
    return result.model_dump()


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("RESUME_PARSER_PORT", "8002"))
    mcp.run(transport="sse", host="0.0.0.0", port=port)