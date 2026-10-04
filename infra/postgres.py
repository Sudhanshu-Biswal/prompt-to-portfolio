"""
Postgres — async SQLAlchemy engine, session factory, and ORM models.

Tables:
  jobs             — every generation job, status, inputs, outputs
  eval_runs        — eval harness results per release
  catalog_versions — component catalog version history
"""
from __future__ import annotations
from datetime import datetime
from typing import AsyncGenerator
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, Text, JSON, Index
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase
from infra.config import settings


# ── Engine + session factory ──────────────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,      # log SQL in dev only
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,                # reconnect on stale connections
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, commits on success, rolls back on error."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── ORM models ────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class JobRecord(Base):
    """
    One row per generation job.
    raw_input and final_output stored as JSONB for flexibility.
    merged_profile stored separately — useful for debugging bad outputs.
    """
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True)           # UUID as string
    user_id = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="queued")

    # Input — stored for replay and debugging
    raw_input = Column(JSON, nullable=False)

    # Pipeline outputs — set on completion
    merged_profile = Column(JSON, nullable=True)
    final_output = Column(JSON, nullable=True)

    # Non-fatal errors accumulated during pipeline
    errors = Column(JSON, nullable=False, default=list)

    # Metadata
    retry_count = Column(Integer, default=0)
    fallback_used = Column(Boolean, default=False)
    sources_used = Column(JSON, default=list)           # ["resume", "github"]
    sources_missing = Column(JSON, default=list)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_jobs_user_created", "user_id", "created_at"),
        Index("ix_jobs_status_created", "status", "created_at"),
    )


class EvalRun(Base):
    """
    One row per eval harness run — tracks scores over time.
    A drop in accuracy_score between runs signals a prompt regression.
    """
    __tablename__ = "eval_runs"

    id = Column(String(36), primary_key=True)
    run_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    pipeline_version = Column(String(20), nullable=False)
    prompt_version = Column(String(20), nullable=False)
    golden_set_size = Column(Integer, nullable=False)

    # Hard metric
    schema_pass_rate = Column(Float, nullable=False)

    # LLM-judge scores (1–5)
    accuracy_score = Column(Float, nullable=True)
    coherence_score = Column(Float, nullable=True)
    tone_score = Column(Float, nullable=True)
    completeness_score = Column(Float, nullable=True)

    # Cases that failed — stored for analysis
    failed_cases = Column(JSON, default=list)


class CatalogVersion(Base):
    """Tracks component catalog versions — useful when debugging selector behavior."""
    __tablename__ = "catalog_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(20), nullable=False)
    deployed_at = Column(DateTime, default=datetime.utcnow)
    component_count = Column(Integer, nullable=False)
    changelog = Column(Text, nullable=True)


# ── DB initialization ─────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Creates all tables if they don't exist.
    Called once at application startup.
    In production use Alembic migrations instead.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()