"""
Day 5: "Find the hole" — the killer differentiator.

Most AI feedback is generic mush ("consider adding more evidence",
"strengthen your reasoning"). This module does the opposite: it picks the
SINGLE most load-bearing weakness in the user's argument, quotes it
verbatim, and constructs the surgical attack that targets only it.

One weakness. One quote. One precise attack. One repair suggestion.

This is "structured output + LLM-as-judge" (Day 4) repurposed for
adversarial analysis instead of grading. The same 4-layer defense applies:
strict prompt schema + JSON mode + Pydantic validation + retry.
"""

from typing import Optional

from groq import Groq
from pydantic import BaseModel, Field, ValidationError

from model_config import get_model


class ArgumentWeakness(BaseModel):
    """The single load-bearing weakness in an argument."""

    weakest_claim: str  # verbatim quote from user's argument
    why_weak: str  # 1-2 sentence diagnosis
    the_attack: str  # 3-4 sentence surgical counter
    how_to_defend: str  # 1-2 sentence specific suggestion
    severity: int = Field(ge=1, le=10)


FINDHOLE_SYSTEM_PROMPT = """You are a surgical argument analyzer. Your specialty: finding the SINGLE most load-bearing weak point in an argument and attacking it with precision.

The user will paste an argument — it could be one claim, a multi-turn debate transcript, or a paragraph. Your job:

1. Read the entire input carefully.
2. Identify the ONE claim, sentence, or phrase that — if dismantled — brings down the most of the rest of the argument with it. Not the biggest of many weaknesses. THE single load-bearing one.
3. Quote that exact weakest claim VERBATIM from the user's text.
4. Diagnose precisely why it's weak. Consider THREE categories of weakness — not just content-level evidence problems:

   CONTENT-LEVEL: unsupported assertion (bare claim, no support), vague terms (undefined key words with multiple interpretations — distinct from being merely unsupported), overreaching claim
   STRUCTURAL/RHETORICAL: false dichotomy, slippery slope, strawman, cherry-picking
   REASONING: hasty generalization, appeal to authority, burden-of-proof reversal, circular reasoning

   A claim can have evidence and still fail structurally. Look at the SHAPE of the argument, not just the content of individual claims.

   WHICH SENTENCE TO QUOTE for each fallacy type:
   - SLIPPERY SLOPE: when you see "if X, then Y, then Z, then disaster" — quote the WHOLE chain phrase or the first link where the leap appears, NOT just the final extreme outcome. The weakness is the chain itself, not the conclusion.
   - STRAWMAN: when an argument opens by describing what opponents supposedly believe (e.g., "Critics of X think Y"), the strawman lives in THAT description, not in the rebuttal that follows. Quote the description of the opposing view.
   - CHERRY-PICKING: quote the phrase that generalizes from the limited evidence (the "therefore X is superior" / "this proves Y" type phrase), since that's where the leap happens.
   - VAGUE TERMS: vague is NOT the same as unsupported. Vague claims have undefined key words with multiple plausible meanings. Quote the sentence containing the undefined terms; in diagnosis, name the specific words that lack definition.
5. Construct the surgical attack: the most damaging counter-argument that targets ONLY this hole. 3-4 sentences. Make it specific to what the user actually said.
6. Suggest how the user could defend or shore up this specific weakness. 1-2 sentences. Concrete and actionable.
7. Rate the severity 1-10 — how load-bearing is this single weakness to the overall argument?

CRUCIAL RULES:
- ONE weakness only. Picking "the top three" is forbidden — that defeats the entire point.
- Your weakest_claim MUST be a nonempty, exact verbatim quote from the user's input. Do not paraphrase or change punctuation. If it isn't in the argument, the analysis fails.
- Surgical, not scattershot. Generic feedback like "you should add more evidence" or "consider counter-arguments" is FORBIDDEN. Your attack must be specific to the words the user wrote.
- The user wrote "YOU:" lines if this is a debate transcript. The bot's lines (in caps) are CONTEXT, not the user's argument — never quote them as the weakest claim.

SEVERITY CALIBRATION:
- 9-10: this weakness alone makes the argument collapse. Without fixing it, the whole thing fails.
- 7-8: serious; removes a major plank but other parts survive.
- 4-6: real weakness but the argument survives with revision.
- 1-3: minor; the argument is strong overall, this is polish.

Return ONLY this JSON. No commentary before or after. No code fences:
{
  "weakest_claim": "<exact verbatim quote from the user's argument>",
  "why_weak": "<1-2 sentence diagnosis — be precise about the failure mode>",
  "the_attack": "<3-4 sentence surgical counter that targets only this claim>",
  "how_to_defend": "<1-2 sentence concrete suggestion to fix this specific hole>",
  "severity": <integer 1-10>
}"""


def find_hole(
    client: Groq,
    argument: str,
    max_retries: int = 1,
    *,
    model: str | None = None,
) -> ArgumentWeakness:
    """Analyze an argument and return its single most load-bearing weakness.

    Args:
        client: Groq client.
        argument: the user's argument text. Can be a single claim, a debate
            transcript (with YOU:/BOT: lines), or a multi-paragraph essay.
        max_retries: retries on JSON/schema/quote validation failure. Default 1.
        model: optional explicit Groq model ID; otherwise resolved from settings.

    Returns:
        Validated ArgumentWeakness.

    Raises:
        RuntimeError on persistent validation failure.
    """
    if not argument.strip():
        raise ValueError("Argument must not be blank")
    if max_retries < 0:
        raise ValueError("max_retries must be nonnegative")
    selected_model = get_model("findhole", model)
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        response = client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": FINDHOLE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"ARGUMENT:\n\n{argument}\n\n"
                        f"Find the single load-bearing weakness now. Return only the JSON."
                    ),
                },
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content

        try:
            # Validate both the JSON schema and the quote's relationship to input.
            weakness = ArgumentWeakness.model_validate_json(raw)
            if not weakness.weakest_claim.strip() or weakness.weakest_claim not in argument:
                raise ValueError("weakest_claim must be an exact nonempty quote from the argument")
            return weakness
        except (ValidationError, ValueError) as e:
            last_error = e
            continue

    raise RuntimeError(
        f"find_hole failed after {max_retries + 1} attempt(s). Last error: {last_error}"
    )


def format_hole_report(weakness: ArgumentWeakness) -> str:
    """Human-readable rendering of an ArgumentWeakness for CLI output."""
    return "\n".join(
        [
            "",
            "=" * 70,
            f"  THE HOLE   (severity: {weakness.severity}/10)",
            "=" * 70,
            "",
            "  WEAKEST CLAIM:",
            f"     \"{weakness.weakest_claim}\"",
            "",
            "  WHY IT'S WEAK:",
            f"     {weakness.why_weak}",
            "",
            "  THE ATTACK:",
            f"     {weakness.the_attack}",
            "",
            "  HOW TO DEFEND IT:",
            f"     {weakness.how_to_defend}",
            "",
            "=" * 70,
        ]
    )
