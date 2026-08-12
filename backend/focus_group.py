from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


PROMPT_VERSION = "aiwl-panel-v1"

PERSONA_LENSES = [
    ("trend", "Trend-aware and attentive to whether a look feels current without looking forced."),
    ("approachability", "Social and attentive to warmth, ease, and whether the wearer seems comfortable to approach."),
    ("polish", "Detail-oriented and attentive to coordination, fit cues, finish, and intentionality."),
    ("individuality", "Values confident personal style and notices generic or overly safe combinations."),
    ("practical", "Grounded and skeptical; notices when a look seems impractical, costume-like, or mismatched to the venue."),
    ("authenticity", "Attentive to authenticity and whether confidence appears natural rather than performative."),
]
SCREENING_LENSES = [
    (
        "balanced_screen",
        "Balanced first-pass reviewer weighing fashionability, confidence, approachability, audience fit, coordination, authenticity, and venue practicality together.",
    )
]
MAX_PANEL_SIZE = len(PERSONA_LENSES)
MAX_OUTPUT_TOKENS = 2500


class OutfitVerdict(BaseModel):
    outfit_id: str = Field(description="The visible label, such as OUTFIT 1")
    fashionable: int = Field(ge=1, le=10)
    confident: int = Field(ge=1, le=10)
    approachable: int = Field(ge=1, le=10)
    audience_fit: int = Field(ge=1, le=10)
    rationale: str
    concern: str


class PersonaResult(BaseModel):
    outfits: list[OutfitVerdict]
    persona_summary: str


async def _evaluate_persona(
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    audience: str,
    impression: str,
    image_data_url: str,
    labels: list[str],
    persona_key: str,
    persona_lens: str,
) -> tuple[str, PersonaResult, dict[str, int]]:
    system_prompt = (
        "You are one member of a synthetic fashion focus group. Give directional visual feedback, "
        "not claims about real people or guaranteed behavior. Judge only the clothing and styling visible "
        "in the labeled contact sheet. Do not score the wearer's face, body, attractiveness, race, or other "
        "sensitive attributes. Use the full 1–10 scale, compare every option, and return each supplied label exactly once."
    )
    user_prompt = (
        f"Target audience: {audience}\n"
        f"Desired impression: {impression}\n"
        f"Your evaluation lens: {persona_lens}\n"
        f"Required outfit labels: {', '.join(labels)}\n\n"
        "Rank how each complete outfit is likely to communicate the desired impression in this context. "
        "Keep each rationale and concern concrete and under 24 words."
    )
    async with semaphore:
        response = await client.responses.parse(
            model=model,
            reasoning={"effort": "low"},
            store=False,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                            "detail": "high",
                        },
                    ],
                },
            ],
            text_format=PersonaResult,
        )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError(f"The {persona_key} panel response was empty or refused.")

    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    token_usage = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cached_tokens": int(getattr(input_details, "cached_tokens", 0) or 0),
    }
    return persona_key, parsed, token_usage


async def run_panel(
    *,
    api_key: str,
    model: str,
    audience: str,
    impression: str,
    contact_sheet: Path,
    labels: list[str],
    panel_size: int,
    persona_lenses: list[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    available_lenses = persona_lenses or PERSONA_LENSES
    if not 1 <= panel_size <= len(available_lenses):
        raise ValueError(
            f"Audience size must be between 1 and {len(available_lenses)}."
        )
    encoded = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")
    image_data_url = f"data:image/jpeg;base64,{encoded}"
    client = AsyncOpenAI(api_key=api_key, timeout=120.0, max_retries=2)
    semaphore = asyncio.Semaphore(3)
    personas = available_lenses[:panel_size]
    tasks = [
        _evaluate_persona(
            client,
            semaphore,
            model,
            audience,
            impression,
            image_data_url,
            labels,
            key,
            lens,
        )
        for key, lens in personas
    ]
    try:
        responses = await asyncio.gather(*tasks)
    finally:
        await client.close()

    by_label: dict[str, list[OutfitVerdict]] = defaultdict(list)
    usage_rows: list[dict[str, Any]] = []
    for persona_key, result, usage in responses:
        seen: set[str] = set()
        for verdict in result.outfits:
            normalized = verdict.outfit_id.strip().upper()
            if normalized in labels and normalized not in seen:
                by_label[normalized].append(verdict)
                seen.add(normalized)
        usage_rows.append({"persona_key": persona_key, **usage})

    ranked: list[dict[str, Any]] = []
    for label in labels:
        verdicts = by_label.get(label, [])
        if not verdicts:
            raise RuntimeError(f"The audience panel omitted {label}; no partial ranking was saved.")
        fashionable = sum(item.fashionable for item in verdicts) / len(verdicts)
        confident = sum(item.confident for item in verdicts) / len(verdicts)
        approachable = sum(item.approachable for item in verdicts) / len(verdicts)
        audience_fit = sum(item.audience_fit for item in verdicts) / len(verdicts)
        overall = (
            fashionable * 0.25
            + confident * 0.20
            + approachable * 0.20
            + audience_fit * 0.35
        )
        ranked.append(
            {
                "panel_label": label,
                "fashionable": round(fashionable, 1),
                "confident": round(confident, 1),
                "approachable": round(approachable, 1),
                "audience_fit": round(audience_fit, 1),
                "overall": round(overall, 1),
                "consensus": verdicts[0].rationale,
                "rationales": [item.rationale for item in verdicts],
                "concerns": list(dict.fromkeys(item.concern for item in verdicts if item.concern))[:4],
                "responses": len(verdicts),
            }
        )
    ranked.sort(key=lambda item: (-item["overall"], item["panel_label"]))
    return ranked, usage_rows
