"""
POST /v1/generate — the entry point for every portfolio generation.

Flow:
  1. Parse + validate input (Pydantic)
  2. Sanitize free-text fields (prompt injection check)
  3. Rate limit check
  4. Create job (Redis + Postgres)
  5. Enqueue pipeline as background task
  6. Return job_id immediately (async pattern — never hold connection open)
"""
from __future__ import annotations
import re
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse

from schemas.input import InputRequest, ProfessionType, ResumeFile, ProfileSources, GitHubSource
from infra.job_queue import create_job, update_job_running, update_job_failed
from infra.redis import check_and_increment_rate_limit
from infra.config import settings

router = APIRouter(tags=["generation"])

# ── Prompt injection patterns ─────────────────────────────────────────────
# Simple heuristic — catches the most common injection attempts
# without over-blocking legitimate prompts.
INJECTION_PATTERNS = [
    r"ignore (previous|all|above) instructions",
    r"you are now",
    r"disregard (your|the|all)",
    r"system prompt",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[INST\]",
    r"###\s*(instruction|system)",
]
INJECTION_REGEX = re.compile(
    "|".join(INJECTION_PATTERNS),
    re.IGNORECASE,
)


def sanitize_text(text: str | None) -> str | None:
    """Returns None if injection pattern detected, else the original text."""
    if text is None:
        return None
    if INJECTION_REGEX.search(text):
        return None
    return text


@router.post("/generate")
async def generate_portfolio(
    request: Request,
    background_tasks: BackgroundTasks,
    # Form fields — multipart because resume file upload
    profession_type_mode: str = Form(...),
    profession_type_value: str = Form(...),
    style_pattern: str = Form(...),
    prompt: str = Form(...),
    github_connected: bool = Form(default=False),
    github_username: str | None = Form(default=None),
    linkedin_text: str | None = Form(default=None),
    self_description: str | None = Form(default=None),
    resume_file: UploadFile | None = File(default=None),
    # User identity — in production comes from auth middleware
    user_id: str = Form(default="anonymous"),
):
    """
    Creates a portfolio generation job.
    Returns job_id immediately — client polls GET /v1/jobs/{job_id} for result.
    """

    # ── 1. Sanitize free-text inputs ──────────────────────────────────────
    clean_prompt = sanitize_text(prompt)
    clean_description = sanitize_text(self_description)
    clean_linkedin = sanitize_text(linkedin_text)

    if clean_prompt is None:
        raise HTTPException(status_code=400, detail="Invalid prompt content")

    # ── 2. Build input object ─────────────────────────────────────────────
    resume_file_schema = ResumeFile(present=False)
    s3_key = None

    if resume_file and resume_file.filename:
        # In production: upload to S3 here and store s3_key
        # For now: just record that a file was provided
        # TODO: implement S3 upload
        resume_file_schema = ResumeFile(
            present=True,
            filename=resume_file.filename,
            mime_type=resume_file.content_type,
            s3_key=f"resumes/{user_id}/{resume_file.filename}",
        )

    try:
        raw_input = InputRequest(
            resume_file=resume_file_schema,
            self_description=clean_description,
            profession_type=ProfessionType(
                mode=profession_type_mode,
                value=profession_type_value,
            ),
            style_pattern=style_pattern,
            prompt=clean_prompt,
            profile_sources=ProfileSources(
                github=GitHubSource(
                    connected=github_connected,
                    username=github_username if github_connected else None,
                ),
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    # ── 3. Rate limit check ───────────────────────────────────────────────
    client_ip = request.client.host if request.client else "unknown"
    allowed, reason = await check_and_increment_rate_limit(user_id, client_ip)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    # ── 4. Create job ─────────────────────────────────────────────────────
    job_id = await create_job(user_id, raw_input)

    # ── 5. Enqueue pipeline as background task ────────────────────────────
    background_tasks.add_task(run_pipeline, job_id, user_id, raw_input)

    # ── 6. Return immediately ─────────────────────────────────────────────
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "queued",
            "poll_url": f"/{settings.api_version}/jobs/{job_id}",
            "estimated_seconds": 15,
        },
    )


async def run_pipeline(job_id: str, user_id: str, raw_input: InputRequest) -> None:
    """
    Background task — runs the LangGraph pipeline for a job.
    Handles completion and failure, always updates job status.
    """
    from pipeline.graph import get_compiled_graph
    from pipeline.state import initial_state
    from infra.job_queue import update_job_complete, update_job_failed

    try:
        await update_job_running(job_id)

        app = await get_compiled_graph()
        state = initial_state(job_id, user_id, raw_input)

        config = {"configurable": {"thread_id": job_id}}
        final_state = await app.ainvoke(state, config=config)

        if final_state["status"] == "complete" and final_state["final_output"]:
            meta = final_state["final_output"].metadata
            await update_job_complete(
                job_id=job_id,
                final_output=final_state["final_output"],
                merged_profile=(
                    final_state["merged_profile"].model_dump(mode="json")
                    if final_state["merged_profile"] else None
                ),
                errors=final_state["errors"],
                retry_count=final_state["retry_count"],
                fallback_used=meta.fallback_used,
                sources_used=meta.sources_used,
                sources_missing=meta.sources_missing,
            )
        else:
            await update_job_failed(job_id, final_state["errors"])

    except Exception as e:
        await update_job_failed(job_id, [f"Pipeline error: {str(e)}"])