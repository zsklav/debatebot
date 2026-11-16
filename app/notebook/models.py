from typing import Literal

from pydantic import Field

from app.core.transcripts import MAX_TRANSCRIPT_CHARS, MAX_TURNS
from app.review.models import StrictModel


class NotebookInput(StrictModel):
    title: str = Field(default="Interview notebook", min_length=2, max_length=100)
    content: str = Field(min_length=30, max_length=MAX_TRANSCRIPT_CHARS)
    format: Literal["text", "vtt", "meet_json"] = "text"
    speaker_roles: dict[str, Literal["Candidate", "Interviewer", "Exclude"]] = Field(default_factory=dict, max_length=12)


class NoteEdit(StrictModel):
    id: str = Field(pattern=r"^note-\d{3}$")
    body: str = Field(max_length=1200)


class NotebookEdit(StrictModel):
    revision: int = Field(ge=0)
    title: str = Field(min_length=2, max_length=100)
    notes: list[NoteEdit] = Field(min_length=1, max_length=MAX_TURNS)


class DigestRequest(StrictModel):
    revision: int = Field(ge=0)
    consent_to_external_processing: bool = False
