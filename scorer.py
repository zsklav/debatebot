"""
Day 4: LLM-as-judge scorer.

Uses a SEPARATE LLM call with a strict rubric to grade the user's argument
across 4 axes. Output is structured JSON, validated by Pydantic, with one
retry if validation fails.

This is "LLM-as-judge" — one of the most important industry patterns:
using an LLM to evaluate another LLM's output (or in our case, the user's
argument). It's how production teams measure prompt changes, run
regressions, and pick best-of-N candidate responses at scale.

Key design choices:
  - Separate API call (not part of the debate). Different system prompt.
  - Low temperature (0.2) so scoring is calibrated and stable across runs.
  - response_format={"type": "json_object"} — Groq forces JSON-shaped output.
  - Pydantic validation on top — even valid JSON can be wrong shape.
  - One retry on validation failure (poor man's structured-output guarantee).
"""

import json
from typing import Optional

from groq import Groq
from pydantic import BaseModel, Field, ValidationError

from model_config import get_model


class AxisScore(BaseModel):
    """One axis of the rubric. Score is bounded; Pydantic enforces 1-10."""

    score: int = Field(ge=1, le=10)
    rationale: str


class ArgumentScore(BaseModel):
    """The full report on the user's argument."""

    logical_coherence: AxisScore
    evidence_quality: AxisScore
    opposition_handling: AxisScore
    clarity: AxisScore
    overall: int = Field(ge=1, le=10)
    summary: str


SCORER_SYSTEM_PROMPT = """You are a rigorous, calibrated argument evaluator. The user will paste a debate transcript. The transcript contains:
- The user's claim(s) — lines starting with "YOU:"
- The bot's pushback — lines starting with the bot's name in caps

Your job: score the USER's argument across 4 axes. Use the bot's pushback as context — it shows what the user *should have* addressed. Be honest, not flattering. Most arguments fall in the 4-7 range; do not grade-inflate.

CALIBRATION:
- 1-3: weak, riddled with logical errors, no evidence, ignores counter-views
- 4-6: passable, has merit but with real gaps
- 7-8: strong, well-reasoned, mostly addresses counter-arguments
- 9-10: exceptional, near-bulletproof reasoning. Reserve for top 5% of arguments.

You MUST respond with VALID JSON matching this EXACT schema (no extra keys, no missing keys):
{
  "logical_coherence": {"score": <1-10>, "rationale": "<one sentence>"},
  "evidence_quality": {"score": <1-10>, "rationale": "<one sentence>"},
  "opposition_handling": {"score": <1-10>, "rationale": "<one sentence>"},
  "clarity": {"score": <1-10>, "rationale": "<one sentence>"},
  "overall": <1-10>,
  "summary": "<2-3 sentence verdict — the headline of where this argument stands>"
}

THE AXES (what each one measures):
- logical_coherence: do conclusions follow from premises? are there fallacies (hasty generalization, false dichotomy, slippery slope, ad hominem)?
- evidence_quality: are claims backed by specifics, data, examples, mechanisms? or are they bare assertions and appeals to authority?
- opposition_handling: did the user grapple with the strongest counter-view? or did they only fight strawmen (or ignore the opposition entirely)?
- clarity: is the argument crisp and unambiguous? or full of weasel words ("many people", "studies show", "everyone knows") and undefined terms?

Return ONLY the JSON. No commentary before or after. No code fences."""


def score_argument(
    client: Groq,
    transcript: str,
    max_retries: int = 1,
    *,
    model: str | None = None,
) -> ArgumentScore:
    """Send a debate transcript to the scorer LLM. Parse and validate JSON.

    Args:
        client: Groq client.
        transcript: human-readable debate transcript (use Debate.transcript()).
        max_retries: how many times to retry if the LLM returns invalid JSON
            or wrong shape. Default 1 retry (2 attempts total).
        model: optional explicit Groq model ID; otherwise resolved from settings.

    Returns:
        A validated ArgumentScore.

    Raises:
        RuntimeError: if the scorer fails to produce valid output after all
            retries. In production you'd log this and fall back gracefully.
    """
    selected_model = get_model("scorer", model)
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        response = client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": SCORER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"DEBATE TRANSCRIPT:\n\n{transcript}\n\n"
                        f"Score the user's argument now. Return only the JSON."
                    ),
                },
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content

        try:
            data = json.loads(raw)
            return ArgumentScore(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            continue

    raise RuntimeError(
        f"Scorer failed after {max_retries + 1} attempt(s). Last error: {last_error}"
    )


def format_score_report(score: ArgumentScore) -> str:
    """Human-readable rendering of an ArgumentScore for CLI output."""
    return "\n".join(
        [
            "",
            "=" * 70,
            "  ARGUMENT SCORE REPORT",
            "=" * 70,
            "",
            f"  OVERALL: {score.overall}/10",
            "",
            f"  Logical coherence    {score.logical_coherence.score:>2}/10",
            f"     {score.logical_coherence.rationale}",
            "",
            f"  Evidence quality     {score.evidence_quality.score:>2}/10",
            f"     {score.evidence_quality.rationale}",
            "",
            f"  Opposition handling  {score.opposition_handling.score:>2}/10",
            f"     {score.opposition_handling.rationale}",
            "",
            f"  Clarity              {score.clarity.score:>2}/10",
            f"     {score.clarity.rationale}",
            "",
            "  VERDICT:",
            f"     {score.summary}",
            "",
            "=" * 70,
        ]
    )
