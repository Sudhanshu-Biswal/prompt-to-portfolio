"""
Central config — reads all env vars via pydantic-settings.
Every other file imports from here. No file calls os.getenv directly.

Usage:
    from infra.config import settings
    print(settings.openai_api_key)
"""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ───────────────────────────────────────────────────
    openai_api_key: str
    openai_interpreter_model: str = "gpt-4o-mini"
    openai_copy_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # ── Internal resume API ───────────────────────────────────
    internal_resume_api_url: str = "http://localhost:9000/resume"
    internal_resume_api_key: str = ""

    # ── Redis ─────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    job_state_ttl_seconds: int = 86400       # 24h — checkpoint TTL
    job_status_ttl_seconds: int = 604800     # 7d  — status key TTL
    github_cache_ttl_seconds: int = 3600     # 1h  — GitHub fetch cache

    # ── Postgres ──────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/portfolio_gen"

    # ── Object storage ────────────────────────────────────────
    s3_bucket: str = "portfolio-gen-resumes"
    s3_region: str = "ap-south-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # ── MCP server URLs ───────────────────────────────────────
    component_library_mcp_url: str = "http://localhost:8001/sse"
    resume_parser_mcp_url: str = "http://localhost:8002/sse"
    github_mcp_url: str = "http://localhost:8003/sse"

    # ── Pipeline ──────────────────────────────────────────────
    profession_match_threshold: float = 0.75
    max_copy_retries: int = 2
    profile_token_threshold: int = 1500      # summarize above this
    max_components: int = 8

    # ── Node timeouts (seconds) ───────────────────────────────
    timeout_resume_parser: int = 10
    timeout_github_fetch: int = 5
    timeout_prompt_interpreter: int = 8
    timeout_copy_generation: int = 30
    timeout_component_selector: int = 3
    timeout_default: int = 2

    # ── Rate limiting ─────────────────────────────────────────
    rate_limit_per_user_hourly: int = 10
    rate_limit_per_user_daily: int = 50
    rate_limit_per_ip_hourly: int = 20
    rate_limit_global_concurrent: int = 500

    # ── Eval ──────────────────────────────────────────────────
    eval_accuracy_threshold: float = 4.0
    eval_schema_pass_threshold: float = 0.98

    # ── App ───────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    api_version: str = "v1"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    lru_cache ensures .env is read once at startup, not on every import.
    Call get_settings() everywhere — don't instantiate Settings() directly.
    """
    return Settings()


# Module-level singleton — import this directly
settings = get_settings()