"""Resolve model names at call time, after entry points load their environment."""

import os
from typing import Literal

DEFAULT_MODEL = "openai/gpt-oss-20b"
ModelRole = Literal["debate", "findhole", "scorer", "judge", "evidence", "research"]
ROLE_ENV = {
    "debate": "DEBATEBOT_DEBATE_MODEL",
    "findhole": "DEBATEBOT_FINDHOLE_MODEL",
    "scorer": "DEBATEBOT_SCORER_MODEL",
    "judge": "DEBATEBOT_JUDGE_MODEL",
    "evidence": "DEBATEBOT_EVIDENCE_MODEL",
    "research": "DEBATEBOT_RESEARCH_MODEL",
}


def reasoning_options(model: str) -> dict:
    """Keep GPT-OSS reasoning bounded; omit unsupported options for other models."""
    return {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss-") else {}


def get_model(role: ModelRole, override: str | None = None) -> str:
    """Explicit override > role setting > shared setting > original default.

    Names configure the existing Groq client, not another provider. Blank values
    fail early rather than silently sending a bad model ID to the API.
    """
    if override is not None:
        value = override
    else:
        value = os.environ.get(
            ROLE_ENV[role], os.environ.get("DEBATEBOT_MODEL", DEFAULT_MODEL)
        )
    if not value.strip():
        raise ValueError(f"Model for {role} must not be blank")
    return value.strip()
