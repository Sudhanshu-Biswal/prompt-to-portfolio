"""
GitHub fetch output schema — shape of data returned by the GitHub MCP server.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, HttpUrl


class GitHubProfile(BaseModel):
    username: str
    name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    profile_url: str
    followers: int = 0
    public_repos: int = 0


class GitHubRepo(BaseModel):
    name: str
    description: str | None = None
    url: str
    stars: int = 0
    forks: int = 0
    primary_language: str | None = None
    topics: list[str] = Field(default_factory=list)
    thumbnail_url: str | None = None   # OpenGraph/social preview image
    is_pinned: bool = False


class GitHubFetchOutput(BaseModel):
    source: str = "github"
    username: str
    profile: GitHubProfile
    pinned_repos: list[GitHubRepo] = Field(default_factory=list)
    top_languages: dict[str, int] = Field(
        default_factory=dict,
        description="Language name → percentage of total code"
    )
    rate_limit_remaining: int | None = None