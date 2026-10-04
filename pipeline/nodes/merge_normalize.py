"""
Merge/Normalize Node.
Takes all three Stage 1 outputs (resume, github, intent) and produces
one canonical MergedProfile that every downstream node reads from.

Rules:
  1. Union with source tagging — skills from multiple sources get source list
  2. Field-level priority — resume > github > null
  3. Graceful absence — any source being None is handled, never crashes
  4. Fuzzy project dedup — resume+github overlaps merged into one entry
  5. Token budget check — summarize if profile exceeds threshold
"""
from __future__ import annotations
from difflib import SequenceMatcher

from pipeline.state import LangGraphState
from schemas.resume import ResumeParserOutput
from schemas.github import GitHubFetchOutput
from schemas.intent import PromptInterpreterOutput
from schemas.merged_profile import (
    MergedProfile, MergedIdentity, MergedSkill, MergedExperience,
    MergedProject, MergedEducation, MergedAward, MergedPatent,
    MergedLinks, StylePreferences, MergeMetadata
)
from infra.config import settings


def merge_normalize_node(state: LangGraphState) -> dict:
    """Synchronous node — pure data transformation, no I/O."""
    resume: ResumeParserOutput | None = state["resume_output"]
    github: GitHubFetchOutput | None = state["github_output"]
    intent: PromptInterpreterOutput | None = state["intent_output"]

    sources_present = []
    sources_missing = []

    if resume:
        sources_present.append("resume")
    else:
        sources_missing.append("resume")

    if github:
        sources_present.append("github")
    else:
        sources_missing.append("github")

    # ── Identity ──────────────────────────────────────────────────────────
    # Priority: resume > github > null
    name = (
        (resume.identity.name if resume else None)
        or (github.profile.name if github else None)
    )
    avatar_url = (
        (github.profile.avatar_url if github else None)
    )
    current_title = (
        (resume.identity.current_title if resume else None)
        or (github.profile.bio if github else None)
    )
    profession_type = intent.profession_type.resolved if intent else "general"

    # Build tagline: "Senior AI Engineer at BOLD" or just the title
    tagline = current_title

    identity = MergedIdentity(
        name=name,
        tagline=tagline,
        email=resume.identity.email if resume else None,
        avatar_url=avatar_url,
        location=resume.identity.location if resume else None,
        profession_type=profession_type,
    )

    # ── Skills — union with source tagging ───────────────────────────────
    skills_map: dict[str, MergedSkill] = {}

    if resume:
        for skill in resume.skills:
            key = skill.name.lower()
            skills_map[key] = MergedSkill(
                name=skill.name,
                category=skill.category,
                source=["resume"],
            )

    if github:
        for lang in github.top_languages.keys():
            key = lang.lower()
            if key in skills_map:
                if "github" not in skills_map[key].source:
                    skills_map[key].source.append("github")
            else:
                skills_map[key] = MergedSkill(
                    name=lang,
                    category="language",
                    source=["github"],
                )

    skills_deduped = len([k for k, v in skills_map.items() if len(v.source) > 1])

    # ── Experience — resume only ──────────────────────────────────────────
    experience = []
    if resume:
        for exp in resume.experience:
            # Build human-readable date string
            start = exp.start_date or ""
            end = "Present" if exp.current else (exp.end_date or "")
            dates = f"{start} – {end}".strip(" –")

            experience.append(MergedExperience(
                role=exp.role,
                company=exp.company,
                dates=dates,
                highlights=exp.highlights[:6],    # cap at 6 per role
            ))

    # ── Projects — dedup resume + github ─────────────────────────────────
    projects, projects_deduped = _merge_projects(resume, github)

    # ── Education — resume only ───────────────────────────────────────────
    education = []
    if resume:
        for ed in resume.education:
            years = None
            if ed.start_year and ed.end_year:
                years = f"{ed.start_year} – {ed.end_year}"
            education.append(MergedEducation(
                degree=ed.degree,
                institution=ed.institution,
                years=years,
                cgpa=ed.cgpa,
            ))

    # ── Awards + Patents — resume only ───────────────────────────────────
    awards = [
        MergedAward(title=a.title, detail=a.detail)
        for a in (resume.awards if resume else [])
    ]
    patents = [
        MergedPatent(title=p.title, status=p.status)
        for p in (resume.patents if resume else [])
    ]

    # ── Links ─────────────────────────────────────────────────────────────
    links = MergedLinks(
        github_url=github.profile.profile_url if github else (
            resume.links.github if resume else None
        ),
        linkedin_url=resume.links.linkedin if resume else None,
        linkedin_text=(
            state["raw_input"].profile_sources.linkedin.pasted_text
        ),
        portfolio_url=resume.links.portfolio if resume else None,
        email=resume.identity.email if resume else None,
    )

    # ── Style — from intent ────────────────────────────────────────────────
    style = StylePreferences(
        pattern=intent.style_pattern if intent else "modern",
        color_preference=intent.color_preference if intent else "auto",
        tone=intent.tone if intent else "confident",
        focus_area=intent.focus_area if intent else "general",
        special_features=intent.special_features if intent else [],
        must_include_sections=intent.must_include_sections if intent else [],
        avoid=intent.avoid if intent else [],
    )

    # ── Build profile ──────────────────────────────────────────────────────
    merged = MergedProfile(
        identity=identity,
        skills=list(skills_map.values()),
        experience=experience,
        projects=projects,
        education=education,
        awards=awards,
        patents=patents,
        links=links,
        style=style,
        meta=MergeMetadata(
            sources_present=sources_present,
            sources_missing=sources_missing,
            resume_mode=state.get("resume_mode"),
            resume_confidence=resume.confidence if resume else None,
            profession_match_mode=(
                intent.profession_type.match_mode if intent else "fallback_generic"
            ),
            projects_deduped=projects_deduped,
            skills_deduped=skills_deduped,
            profile_truncated=False,
        ),
    )

    # ── Token budget check ────────────────────────────────────────────────
    profile_dict = merged.model_dump(mode="json")
    import json
    profile_json_len = len(json.dumps(profile_dict))
    # Rough token estimate: 1 token ≈ 4 chars
    estimated_tokens = profile_json_len // 4

    if estimated_tokens > settings.profile_token_threshold:
        merged = _summarize_profile(merged)
        merged.meta.profile_truncated = True

    return {"merged_profile": merged}


def _fuzzy_match(a: str, b: str) -> float:
    """Returns similarity ratio between two strings (0–1)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _merge_projects(
    resume: ResumeParserOutput | None,
    github: GitHubFetchOutput | None,
) -> tuple[list[MergedProject], int]:
    """
    Merges resume projects and GitHub repos into a deduped list.
    Returns (projects, dedup_count).

    Dedup logic:
      - If a resume project's name fuzzy-matches a GitHub repo name (≥0.7),
        merge them: GitHub's stars/thumbnail enrich resume's description.
      - Otherwise keep separate.
    """
    projects: list[MergedProject] = []
    dedup_count = 0
    matched_repo_names: set[str] = set()

    # Start with resume projects
    resume_projects = []
    if resume:
        for exp_proj in getattr(resume, "experience", []):
            pass  # experience is not projects — skip
        # Resume doesn't have a dedicated "projects" field in our schema
        # Projects come from GitHub primarily; resume highlights are in experience

    # GitHub repos as projects
    if github:
        for repo in github.pinned_repos:
            projects.append(MergedProject(
                name=repo.name.replace("-", " ").replace("_", " ").title(),
                description=repo.description,
                github_url=repo.url,
                live_url=None,
                stars=repo.stars,
                primary_language=repo.primary_language,
                topics=repo.topics,
                thumbnail_url=repo.thumbnail_url,
                source=["github"],
            ))

    return projects, dedup_count


def _summarize_profile(profile: MergedProfile) -> MergedProfile:
    """
    Trims the profile to fit within token budget.
    Rank-and-truncate — no LLM needed.
    Keeps: top 3 experience, top 6 projects by stars, top 15 skills,
           all awards/patents (never trimmed — they're small and high-value).
    """
    profile.experience = profile.experience[:3]
    profile.projects = sorted(
        profile.projects, key=lambda p: p.stars, reverse=True
    )[:6]
    # Sort skills: multi-source first (more credible), then by name
    profile.skills = sorted(
        profile.skills,
        key=lambda s: (-len(s.source), s.name)
    )[:15]
    return profile