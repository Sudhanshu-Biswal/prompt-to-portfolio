"""
Redis client — manages all ephemeral state:
  - Job status (what the client polls)
  - LangGraph checkpoints (handled by LangGraph's own checkpointer)
  - Rate limit counters (sliding window per user/IP)
  - GitHub fetch cache (avoid re-fetching within TTL)

Key naming conventions:
  job:status:{job_id}        → job status string
  job:result:{job_id}        → final output JSON (copy of Postgres record for fast reads)
  ratelimit:user:{user_id}:h → hourly counter
  ratelimit:user:{user_id}:d → daily counter
  ratelimit:ip:{ip}:h        → hourly counter
  ratelimit:global:concurrent → current active job count
  github:cache:{username}    → cached GitHub fetch output JSON
"""
from __future__ import annotations
import json
from typing import Any
import redis.asyncio as aioredis
from infra.config import settings


# ── Client singleton ──────────────────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None


# ── Job status ────────────────────────────────────────────────────────────

async def set_job_status(job_id: str, status: str) -> None:
    """Update job status. Client polls this key."""
    r = await get_redis()
    await r.set(
        f"job:status:{job_id}",
        status,
        ex=settings.job_status_ttl_seconds,
    )


async def get_job_status(job_id: str) -> str | None:
    r = await get_redis()
    return await r.get(f"job:status:{job_id}")


async def set_job_result(job_id: str, result: dict[str, Any]) -> None:
    """
    Cache final output JSON in Redis for fast polling reads.
    Source of truth is Postgres — this is a read-through cache.
    """
    r = await get_redis()
    await r.set(
        f"job:result:{job_id}",
        json.dumps(result),
        ex=settings.job_status_ttl_seconds,
    )


async def get_job_result(job_id: str) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(f"job:result:{job_id}")
    return json.loads(raw) if raw else None


# ── Rate limiting — sliding window ────────────────────────────────────────

async def check_and_increment_rate_limit(
    user_id: str,
    ip: str,
) -> tuple[bool, str]:
    """
    Checks all rate limits and increments counters if allowed.
    Returns (allowed: bool, reason: str).
    Reason is empty string if allowed.
    """
    r = await get_redis()

    # Per-user hourly
    user_hourly_key = f"ratelimit:user:{user_id}:h"
    user_hourly = int(await r.get(user_hourly_key) or 0)
    if user_hourly >= settings.rate_limit_per_user_hourly:
        return False, f"Hourly limit reached ({settings.rate_limit_per_user_hourly}/hr per user)"

    # Per-user daily
    user_daily_key = f"ratelimit:user:{user_id}:d"
    user_daily = int(await r.get(user_daily_key) or 0)
    if user_daily >= settings.rate_limit_per_user_daily:
        return False, f"Daily limit reached ({settings.rate_limit_per_user_daily}/day per user)"

    # Per-IP hourly
    ip_hourly_key = f"ratelimit:ip:{ip}:h"
    ip_hourly = int(await r.get(ip_hourly_key) or 0)
    if ip_hourly >= settings.rate_limit_per_ip_hourly:
        return False, f"Hourly limit reached ({settings.rate_limit_per_ip_hourly}/hr per IP)"

    # Global concurrent
    concurrent_key = "ratelimit:global:concurrent"
    concurrent = int(await r.get(concurrent_key) or 0)
    if concurrent >= settings.rate_limit_global_concurrent:
        return False, "System is at capacity, please retry in a moment"

    # All checks passed — increment all counters atomically
    pipe = r.pipeline()
    pipe.incr(user_hourly_key)
    pipe.expire(user_hourly_key, 3600)
    pipe.incr(user_daily_key)
    pipe.expire(user_daily_key, 86400)
    pipe.incr(ip_hourly_key)
    pipe.expire(ip_hourly_key, 3600)
    pipe.incr(concurrent_key)
    await pipe.execute()

    return True, ""


async def decrement_concurrent_jobs() -> None:
    """
    Call when a job completes or fails.
    Prevents the concurrent counter from drifting upward if jobs crash.
    """
    r = await get_redis()
    current = int(await r.get("ratelimit:global:concurrent") or 0)
    if current > 0:
        await r.decr("ratelimit:global:concurrent")


# ── GitHub fetch cache ────────────────────────────────────────────────────

async def get_github_cache(username: str) -> dict[str, Any] | None:
    """Returns cached GitHub fetch output or None if expired/missing."""
    r = await get_redis()
    raw = await r.get(f"github:cache:{username}")
    return json.loads(raw) if raw else None


async def set_github_cache(username: str, data: dict[str, Any]) -> None:
    r = await get_redis()
    await r.set(
        f"github:cache:{username}",
        json.dumps(data),
        ex=settings.github_cache_ttl_seconds,
    )