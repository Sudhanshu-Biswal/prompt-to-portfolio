"""
FastAPI application entry point.
Handles startup/shutdown lifecycle, middleware, and router registration.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from infra.config import settings
from infra.postgres import init_db, close_db
from infra.redis import get_redis, close_redis
from api.routes import generate, jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # Startup
    await init_db()
    await get_redis()       # warm connection
    yield
    # Shutdown
    await close_db()
    await close_redis()


app = FastAPI(
    title="Prompt-to-Portfolio Generator",
    description="Agentic system — prompt + profile data → deployable portfolio site",
    version=settings.api_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
)

# ── Middleware ────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else ["https://bold.pro"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────

app.include_router(generate.router, prefix=f"/{settings.api_version}")
app.include_router(jobs.router, prefix=f"/{settings.api_version}")


# ── Global exception handler ──────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


# ── Health check ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}