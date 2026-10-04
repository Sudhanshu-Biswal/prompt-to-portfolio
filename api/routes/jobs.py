"""
GET /v1/jobs/{job_id} — client polls this until status = "complete" or "failed".

Response shape by status:
  queued/running → { job_id, status }
  complete       → { job_id, status, result: FinalOutput, warnings }
  failed         → { job_id, status, errors }
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from infra.job_queue import get_job

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """
    Returns current job status and result (if complete).
    Read path: Redis first → Postgres fallback.
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job {job_id} not found",
        )

    return job