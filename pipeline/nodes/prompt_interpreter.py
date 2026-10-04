"""
Prompt Interpreter Node.
Uses GPT-4o-mini to extract structured intent from the user's prompt.
Handles custom profession types via embedding similarity match.

This is the only Stage 1 node that calls an LLM directly.
Everything it produces goes into intent_output on state.
"""
from __future__ import annotations
import asyncio
import json
import numpy as np
from openai import AsyncOpenAI
from pipeline.state import LangGraphState
from schemas.intent import PromptInterpreterOutput, ResolvedProfession
from infra.config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)

# ── Known professions — pre-computed embeddings cached at module level ────
# Populated on first call to avoid startup cost
_KNOWN_PROFESSIONS: list[str] = []
_PROFESSION_EMBEDDINGS: dict[str, list[float]] = {}

KNOWN_PROFESSION_LABELS = [
    "developer", "designer", "writer",
    "researcher", "data_scientist", "product_manager", "general"
]


async def _get_profession_embeddings() -> dict[str, list[float]]:
    """
    Returns embedding vectors for all known profession labels.
    Computed once per process startup, cached in module memory.
    """
    global _PROFESSION_EMBEDDINGS
    if _PROFESSION_EMBEDDINGS:
        return _PROFESSION_EMBEDDINGS

    response = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=KNOWN_PROFESSION_LABELS,
    )
    _PROFESSION_EMBEDDINGS = {
        label: response.data[i].embedding
        for i, label in enumerate(KNOWN_PROFESSION_LABELS)
    }
    return _PROFESSION_EMBEDDINGS


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / norm) if norm > 0 else 0.0


async def _resolve_profession(raw_value: str, mode: str) -> ResolvedProfession:
    """
    Resolves profession_type to a known catalog label.
    - preset_direct: user picked a known value → no embedding needed
    - embedding_match: custom input above similarity threshold
    - fallback_generic: below threshold, use "general"
    """
    if mode == "preset" and raw_value in KNOWN_PROFESSION_LABELS:
        return ResolvedProfession(
            raw_input=raw_value,
            resolved=raw_value,
            match_mode="preset_direct",
            similarity_score=None,
            fallback_used=False,
        )

    # Custom or unknown preset — embed and match
    custom_response = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=[raw_value],
    )
    custom_vector = custom_response.data[0].embedding

    profession_embeddings = await _get_profession_embeddings()
    similarities = {
        profession: _cosine_similarity(custom_vector, vec)
        for profession, vec in profession_embeddings.items()
    }
    best_match = max(similarities, key=lambda k: similarities[k])
    best_score = similarities[best_match]

    if best_score >= settings.profession_match_threshold:
        return ResolvedProfession(
            raw_input=raw_value,
            resolved=best_match,
            match_mode="embedding_match",
            similarity_score=round(best_score, 3),
            fallback_used=False,
        )
    else:
        return ResolvedProfession(
            raw_input=raw_value,
            resolved="general",
            match_mode="fallback_generic",
            similarity_score=round(best_score, 3),
            fallback_used=True,
        )


INTERPRETER_SYSTEM_PROMPT = """
You are a structured intent extractor for a portfolio generator.
Extract the user's preferences from their prompt and return ONLY valid JSON.
No preamble, no explanation, no markdown — raw JSON only.

JSON schema to return:
{
  "style_pattern": "minimal|modern|classic|premium",
  "color_preference": "light|dark|auto",
  "tone": "confident|technical|creative|academic|friendly",
  "focus_area": "production_impact|research_and_systems|design_and_craft|writing_and_content|general",
  "special_features": ["contact_section", "blog_links", "awards_section"],
  "must_include_sections": [],
  "avoid": []
}

Rules:
- special_features: only include if explicitly mentioned by user
- avoid: extract things user says they don't want
- If not mentioned, use sensible defaults
- Do NOT add fields not in the schema
"""


async def prompt_interpreter_node(state: LangGraphState) -> dict:
    try:
        raw = state["raw_input"]

        # Resolve profession type (may involve embedding call)
        profession = await asyncio.wait_for(
            _resolve_profession(
                raw.profession_type.value,
                raw.profession_type.mode,
            ),
            timeout=settings.timeout_prompt_interpreter,
        )

        # GPT-4o-mini call for structured intent extraction
        user_message = f"""
User's prompt: {raw.prompt}
Selected style: {raw.style_pattern}
Profession type: {raw.profession_type.value}
"""
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_interpreter_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": INTERPRETER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,        # low temp — this is extraction not generation
                max_tokens=400,
            ),
            timeout=settings.timeout_prompt_interpreter,
        )

        extracted = json.loads(response.choices[0].message.content)

        intent = PromptInterpreterOutput(
            profession_type=profession,
            style_pattern=extracted.get("style_pattern", raw.style_pattern),
            color_preference=extracted.get("color_preference", "auto"),
            tone=extracted.get("tone", "confident"),
            focus_area=extracted.get("focus_area", "general"),
            special_features=extracted.get("special_features", []),
            must_include_sections=extracted.get("must_include_sections", []),
            avoid=extracted.get("avoid", []),
            original_prompt=raw.prompt,
        )

        return {"intent_output": intent}

    except asyncio.TimeoutError:
        # Fallback: use raw input values without LLM extraction
        return {
            "intent_output": _fallback_intent(state),
            "errors": state["errors"] + [
                "Prompt interpreter timed out — using default intent values"
            ],
        }
    except Exception as e:
        return {
            "intent_output": _fallback_intent(state),
            "errors": state["errors"] + [
                f"Prompt interpreter failed: {type(e).__name__}: {str(e)} — "
                "using default intent values"
            ],
        }


def _fallback_intent(state: LangGraphState) -> PromptInterpreterOutput:
    """
    Safe fallback when LLM call fails.
    Uses raw input values directly — no extraction, no embedding match.
    """
    raw = state["raw_input"]
    return PromptInterpreterOutput(
        profession_type=ResolvedProfession(
            raw_input=raw.profession_type.value,
            resolved=raw.profession_type.value
                if raw.profession_type.value in KNOWN_PROFESSION_LABELS
                else "general",
            match_mode="preset_direct",
            fallback_used=raw.profession_type.value not in KNOWN_PROFESSION_LABELS,
        ),
        style_pattern=raw.style_pattern,
        color_preference="auto",
        tone="confident",
        focus_area="general",
        special_features=[],
        must_include_sections=[],
        avoid=[],
        original_prompt=raw.prompt,
    )