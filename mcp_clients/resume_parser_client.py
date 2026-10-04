"""
Resume Parser MCP Client.
Used by the Resume Parser node to call the resume-parser MCP server.
"""
from __future__ import annotations
import base64

import httpx
from schemas.resume import ResumeParserOutput
from infra.config import settings


async def parse_resume(
    file_bytes: bytes,
    mime_type: str,
) -> ResumeParserOutput:
    """
    Calls parse_resume tool on the resume-parser MCP server.
    Encodes file bytes as base64 before sending.
    """
    file_b64 = base64.b64encode(file_bytes).decode("utf-8")

    async with httpx.AsyncClient(timeout=settings.timeout_resume_parser) as client:
        response = await client.post(
            f"{settings.resume_parser_mcp_url}/tools/parse_resume",
            json={
                "file_bytes_b64": file_b64,
                "mime_type": mime_type,
                "confidence_threshold": 0.6,
            },
        )
        response.raise_for_status()

    return ResumeParserOutput.model_validate(response.json())


async def generate_profile(
    description: str,
    guardrail_level: str = "strict",
) -> ResumeParserOutput:
    """
    Calls generate_profile tool on the resume-parser MCP server.
    """
    async with httpx.AsyncClient(timeout=settings.timeout_resume_parser) as client:
        response = await client.post(
            f"{settings.resume_parser_mcp_url}/tools/generate_profile",
            json={
                "description": description,
                "guardrail_level": guardrail_level,
            },
        )
        response.raise_for_status()

    return ResumeParserOutput.model_validate(response.json())