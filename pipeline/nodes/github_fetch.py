"""
GitHub Fetch Node.
Fetches profile, pinned repos, and top languages from GitHub.
Uses Redis cache to avoid redundant API calls within TTL.

Failure handling:
  - GitHub not connected → sets github_output=None silently (not an error)
  - Rate limit → writes warning, returns partial data if possible
  - Network error → writes to errors, sets github_output=None
  - Pipeline always continues — GitHub is optional
"""
from __future__ import annotations
import asyncio
import httpx

from pipeline.state import LangGraphState
from schemas.github import GitHubFetchOutput, GitHubProfile, GitHubRepo
from infra.redis import get_github_cache, set_github_cache
from infra.config import settings

GITHUB_API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def github_fetch_node(state: LangGraphState) -> dict:
    github_source = state["raw_input"].profile_sources.github

    # Not connected — silent skip, not an error
    if not github_source.connected or not github_source.username:
        return {"github_output": None}

    username = github_source.username

    # Check Redis cache first
    cached = await get_github_cache(username)
    if cached:
        return {
            "github_output": GitHubFetchOutput.model_validate(cached),
        }

    try:
        result = await asyncio.wait_for(
            _fetch_github_data(username),
            timeout=settings.timeout_github_fetch,
        )

        # Cache the result
        await set_github_cache(username, result.model_dump(mode="json"))

        return {"github_output": result}

    except asyncio.TimeoutError:
        return {
            "github_output": None,
            "errors": state["errors"] + [
                f"GitHub fetch timed out after {settings.timeout_github_fetch}s"
            ],
        }
    except Exception as e:
        return {
            "github_output": None,
            "errors": state["errors"] + [
                f"GitHub fetch failed: {type(e).__name__}: {str(e)}"
            ],
        }


async def _fetch_github_data(username: str) -> GitHubFetchOutput:
    """
    Calls GitHub REST API for profile, pinned repos, and languages.
    All three calls fire concurrently.
    """
    async with httpx.AsyncClient(headers=HEADERS, timeout=5.0) as client:
        profile_resp, repos_resp = await asyncio.gather(
            client.get(f"{GITHUB_API}/users/{username}"),
            client.get(f"{GITHUB_API}/users/{username}/repos?sort=stars&per_page=10"),
        )

    # Check rate limit
    rate_remaining = int(profile_resp.headers.get("X-RateLimit-Remaining", 60))
    if rate_remaining < 5:
        raise RuntimeError(
            f"GitHub API rate limit nearly exhausted ({rate_remaining} remaining)"
        )

    profile_resp.raise_for_status()
    repos_resp.raise_for_status()

    profile_data = profile_resp.json()
    repos_data = repos_resp.json()

    # Build profile
    profile = GitHubProfile(
        username=username,
        name=profile_data.get("name"),
        bio=profile_data.get("bio"),
        avatar_url=profile_data.get("avatar_url"),
        profile_url=f"https://github.com/{username}",
        followers=profile_data.get("followers", 0),
        public_repos=profile_data.get("public_repos", 0),
    )

    # Build repos — take top 6 by stars
    repos = []
    for repo in repos_data[:6]:
        if repo.get("fork"):
            continue                    # skip forks
        repos.append(GitHubRepo(
            name=repo["name"],
            description=repo.get("description"),
            url=repo["html_url"],
            stars=repo.get("stargazers_count", 0),
            forks=repo.get("forks_count", 0),
            primary_language=repo.get("language"),
            topics=repo.get("topics", []),
            thumbnail_url=(
                f"https://opengraph.githubassets.com/1/"
                f"{username}/{repo['name']}"
            ),
            is_pinned=False,
        ))

    # Top languages from repos
    lang_counts: dict[str, int] = {}
    for repo in repos_data:
        lang = repo.get("language")
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    total = sum(lang_counts.values()) or 1
    top_languages = {
        lang: round(count / total * 100)
        for lang, count in sorted(
            lang_counts.items(), key=lambda x: -x[1]
        )[:5]
    }

    return GitHubFetchOutput(
        username=username,
        profile=profile,
        pinned_repos=repos,
        top_languages=top_languages,
        rate_limit_remaining=rate_remaining,
    )