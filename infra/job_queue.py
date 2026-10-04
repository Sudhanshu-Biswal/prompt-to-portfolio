"""
Job queue — the bridge between the API layer and the pipeline.

Responsibilities:
  - Create a job record (Redis + Postgres)
  - Update job status on every state transition
  - Persist final output on completion
  - Fetch job status and result for polling

Flow:
  POST /generate
      → create_job()          creates Redis status + Postgres record
      → enqueue_pipeline()    kicks off LangGraph in background
      → returns job_id

  GET /jobs/{job_id}
      → get_job_status()      reads Redis (fast)
      → get_job_result()      reads Redis cache → Postgres fallback
"""
from __future__ import annotations
import uuid
import json
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.input import InputRequest
from schemas.output import FinalOutput
from infra.redis import (
    set_job_status,
    get_job_status,
    set_job_result,
    get_job_result as get_redis_result,
    decrement_concurrent_jobs,
)
from infra.postgres import JobRecord, AsyncSessionFactory


# ── Job creation ──────────────────────────────────────────────────────────

async def create_job(user_id: str, raw_input: InputRequest) -> str:
    """
    Creates a new job in both Redis (status) and Postgres (record).
    Returns the job_id.
    """
    job_id = str(uuid.uuid4())

    # Redis — status key (what client polls)
    await set_job_status(job_id, "queued")

    # Postgres — durable record
    async with AsyncSessionFactory() as session:
        record = JobRecord(
            id=job_id,
            user_id=user_id,
            status="queued",
            raw_input=raw_input.model_dump(mode="json"),
            errors=[],
        )
        session.add(record)
        await session.commit()

    return job_id


# ── Status updates ────────────────────────────────────────────────────────

async def update_job_running(job_id: str) -> None:
    await set_job_status(job_id, "running")
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id)
            .values(status="running")
        )
        await session.commit()


async def update_job_complete(
    job_id: str,
    final_output: FinalOutput,
    merged_profile: dict | None,
    errors: list[str],
    retry_count: int,
    fallback_used: bool,
    sources_used: list[str],
    sources_missing: list[str],
) -> None:
    """
    Persists completed job. Writes to both Redis (cache) and Postgres (durable).
    """
    output_dict = final_output.model_dump(mode="json")

    # Redis cache — fast reads for polling
    await set_job_result(job_id, output_dict)
    await set_job_status(job_id, "complete")

    # Postgres — durable record
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id)
            .values(
                status="complete",
                final_output=output_dict,
                merged_profile=merged_profile,
                errors=errors,
                retry_count=retry_count,
                fallback_used=fallback_used,
                sources_used=sources_used,
                sources_missing=sources_missing,
                completed_at=datetime.utcnow(),
            )
        )
        await session.commit()

    # Release concurrent slot
    await decrement_concurrent_jobs()


async def update_job_failed(
    job_id: str,
    errors: list[str],
) -> None:
    await set_job_status(job_id, "failed")
    async with AsyncSessionFactory() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id)
            .values(
                status="failed",
                errors=errors,
                completed_at=datetime.utcnow(),
            )
        )
        await session.commit()

    await decrement_concurrent_jobs()


# ── Fetch for polling ─────────────────────────────────────────────────────

async def get_job(job_id: str) -> dict[str, Any] | None:
    """
    Returns job status + result for GET /jobs/{job_id}.
    Read path: Redis first (fast) → Postgres fallback (if Redis expired).
    """
    status = await get_job_status(job_id)
    if status is None:
        # Redis expired or job doesn't exist — check Postgres
        async with AsyncSessionFactory() as session:
            record = await session.get(JobRecord, job_id)
            if record is None:
                return None
            status = record.status

    result: dict[str, Any] = {"job_id": job_id, "status": status}

    if status == "complete":
        # Try Redis cache first
        output = await get_redis_result(job_id)
        if output is None:
            # Cache miss — read from Postgres
            async with AsyncSessionFactory() as session:
                record = await session.get(JobRecord, job_id)
                output = record.final_output if record else None
        result["result"] = output

    return result