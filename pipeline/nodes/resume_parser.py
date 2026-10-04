"""
Resume Parser Node.
Reads raw_input from state, decides parse vs generate mode,
calls the resume-parser MCP server, writes resume_output to state.

Failure handling:
  - MCP server error → writes to errors, sets resume_output=None
  - Low confidence → writes warning to errors, still passes result forward
  - Pipeline always continues — resume is optional if GitHub is connected
"""
from __future__ import annotations
import asyncio
from pipeline.state import LangGraphState
from mcp_clients import resume_parser_client
from infra.config import settings


async def resume_parser_node(state: LangGraphState) -> dict:
    try:
        raw = state["raw_input"]

        if raw.resume_file.present and raw.resume_file.s3_key:
            # Parse mode — file was uploaded
            # In production: download from S3 using s3_key
            # For now: stub download (replace with real S3 fetch)
            file_bytes = await _fetch_resume_from_s3(raw.resume_file.s3_key)
            result = await asyncio.wait_for(
                resume_parser_client.parse_resume(
                    file_bytes=file_bytes,
                    mime_type=raw.resume_file.mime_type,
                ),
                timeout=settings.timeout_resume_parser,
            )
        else:
            # Generate mode — self-description text
            result = await asyncio.wait_for(
                resume_parser_client.generate_profile(
                    description=raw.self_description,
                    guardrail_level="strict",
                ),
                timeout=settings.timeout_resume_parser,
            )

        # Warn on low confidence but don't fail
        errors = list(state["errors"])
        if result.confidence < 0.7:
            errors.append(
                f"Resume parsing confidence low ({result.confidence:.2f}) — "
                "some fields may be missing or inaccurate"
            )

        return {
            "resume_output": result,
            "resume_mode": result.source_mode,
            "errors": errors,
        }

    except asyncio.TimeoutError:
        return {
            "resume_output": None,
            "resume_mode": None,
            "errors": state["errors"] + [
                f"Resume parser timed out after {settings.timeout_resume_parser}s — "
                "proceeding with GitHub data only"
            ],
        }
    except Exception as e:
        return {
            "resume_output": None,
            "resume_mode": None,
            "errors": state["errors"] + [
                f"Resume parser failed: {type(e).__name__}: {str(e)} — "
                "proceeding with GitHub data only"
            ],
        }


async def _fetch_resume_from_s3(s3_key: str) -> bytes:
    """
    Downloads resume file from S3.
    TODO: implement real S3 download using boto3/aiobotocore.
    Stub returns empty bytes for now — replace before production.
    """
    # from infra.s3 import download_file
    # return await download_file(s3_key)
    raise NotImplementedError(
        "S3 download not implemented yet. "
        "Implement infra/s3.py and replace this stub."
    )