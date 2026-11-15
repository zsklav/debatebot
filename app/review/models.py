"""Input and evidence contracts shared by all model providers."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Criterion(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    label: str = Field(min_length=2, max_length=70)
    description: str = Field(min_length=10, max_length=300)


class ReviewInput(StrictModel):
    transcript: str = Field(min_length=30, max_length=14000)
    job_description: str = Field(min_length=20, max_length=3000)
    resume: str = Field(default="", max_length=4000)
    criteria: list[Criterion] = Field(min_length=1, max_length=3)
    consent_to_external_processing: bool = False
    evidence_confirmed: bool = False

    @model_validator(mode="after")
    def validate_criteria(self):
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("Criterion IDs must be unique")
        excluded = {"age", "gender", "race", "religion", "caste", "ethnicity", "nationality",
                    "disability", "marital status", "personality", "honesty", "intelligence"}
        if any(c.label.casefold() in excluded for c in self.criteria):
            raise ValueError("Use job-specific technical/competency criteria")
        return self


class Source(StrictModel):
    id: str
    kind: Literal["transcript", "resume"]
    speaker: str
    start_line: int
    end_line: int
    text: str


class Citation(StrictModel):
    source_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=600)


class Finding(StrictModel):
    criterion_id: str
    status: Literal["demonstrated", "needs_verification", "not_assessed"]
    explanation: str = Field(min_length=10, max_length=650)
    evidence: list[Citation] = Field(max_length=3)


class ReviewerOutput(StrictModel):
    findings: list[Finding] = Field(min_length=1, max_length=3)


class AdjudicatedFinding(Finding):
    resolution: Literal["agreed", "proposed_resolution", "unresolved"]


class CouncilOutput(StrictModel):
    summary: str = Field(min_length=20, max_length=1000)
    findings: list[AdjudicatedFinding] = Field(min_length=1, max_length=3)


def validate_findings(result, criteria, allowed_sources):
    """Validate coverage and exact quotes, not correctness or entailment."""
    expected = {c.id for c in criteria}
    received = [finding.criterion_id for finding in result.findings]
    if set(received) != expected or len(received) != len(expected):
        raise ValueError("Output must assess every declared criterion exactly once")
    for finding in result.findings:
        if finding.status != "not_assessed" and not finding.evidence:
            raise ValueError("An affirmative finding needs interview evidence")
        seen = set()
        for reference in finding.evidence:
            source = allowed_sources.get(reference.source_id)
            if (source is None or source.kind != "transcript" or source.speaker != "Candidate" or
                    not reference.quote or reference.quote not in source.text):
                raise ValueError("Findings must quote supplied candidate answers exactly")
            key = (reference.source_id, reference.quote)
            if key in seen:
                raise ValueError("Duplicate evidence reference")
            seen.add(key)


def validate_adjudication(result, reviews):
    """Keep a judge's resolution consistent with the underlying reviewer statuses."""
    statuses = [
        {finding["criterion_id"]: finding["status"] for finding in review["findings"]}
        for review in reviews
    ]
    for finding in result.findings:
        if finding.resolution == "agreed" and any(
            review[finding.criterion_id] != finding.status for review in statuses
        ):
            raise ValueError("Unresolved disagreement cannot be labelled agreement")
        if finding.resolution == "unresolved" and finding.status == "demonstrated":
            raise ValueError("Unresolved findings cannot establish demonstration")
