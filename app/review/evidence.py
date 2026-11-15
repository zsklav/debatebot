"""Source-addressable transcript parsing and isolated, ephemeral Qdrant RAG."""

from functools import lru_cache
import os
from pathlib import Path
import re
from threading import RLock

from langchain_core.documents import Document
from .models import ReviewInput, Source

from app.core.paths import ROOT
_lock = RLock()


def prepare(payload: ReviewInput):
    sources = []
    speaker, start, buffer = None, 1, []
    def flush(end):
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        if not text:
            return
        # Preserve source locations and exact substrings while bounding chunks.
        for offset in range(0, len(text), 1200):
            sources.append(Source(id=f"transcript-{len(sources) + 1:03}", kind="transcript",
                                  speaker=speaker or "Unknown", start_line=start, end_line=end,
                                  text=text[offset:offset + 1200]))
    lines = payload.transcript.splitlines()
    for number, line in enumerate(lines, 1):
        match = re.match(r"^\s*(Candidate|Applicant|Interviewee|Interviewer)\s*:\s*(.*)$", line, re.I)
        if match:
            flush(number - 1)
            speaker = "Interviewer" if match[1].casefold() == "interviewer" else "Candidate"
            start, buffer = number, [match[2]]
        elif speaker:
            buffer.append(line)
        elif line.strip():
            raise ValueError("Label transcript turns as Interviewer: or Candidate:")
    flush(len(lines))
    if not any(source.speaker == "Candidate" for source in sources):
        raise ValueError("At least one Candidate: answer is required")
    if payload.resume:
        for offset in range(0, len(payload.resume), 1000):
            sources.append(Source(id=f"resume-{offset // 1000 + 1:03}", kind="resume", speaker="Resume claim",
                                  start_line=payload.resume[:offset].count("\n") + 1,
                                  end_line=payload.resume[:offset + 1000].count("\n") + 1,
                                  text=payload.resume[offset:offset + 1000]))
    if len(sources) > 64:
        raise ValueError("Use at most 64 transcript/resume segments in the first version")
    return sources


@lru_cache(maxsize=1)
def embedder():
    from app.evidence.retrieval import LocalEmbedder
    return LocalEmbedder(Path(os.environ.get("DEBATEBOT_EMBEDDING_CACHE", str(ROOT / ".data/models"))), offline=True)


def retrieve(payload, sources, encoder=None):
    """A new in-memory collection per invocation: no other account's vectors."""
    from qdrant_client import QdrantClient, models
    # Encoder reuse is serialized, like the existing local-evidence baseline.
    with _lock:
        encoder = encoder or embedder()
        # Only candidate answers are searched. Questions and resume context are
        # attached below, so embedding/indexing them would do unnecessary work.
        documents = [
            Document(page_content=source.text, metadata=source.model_dump(exclude={"text"}))
            for source in sources
            if source.kind == "transcript" and source.speaker == "Candidate"
        ]
        vectors = encoder.passages([d.page_content for d in documents])
        client = QdrantClient(":memory:")
        try:
            client.create_collection("review", vectors_config=models.VectorParams(size=encoder.dimension, distance=models.Distance.COSINE))
            client.upsert("review", points=[
                models.PointStruct(id=index, vector=vector, payload=document.metadata)
                for index, (document, vector) in enumerate(zip(documents, vectors))
            ], wait=True)
            selected = {}
            by_id = {source.id:source for source in sources}
            for criterion in payload.criteria:
                vector = encoder.query(f"{criterion.label}: {criterion.description}")
                hits = client.query_points("review", query=vector, limit=3, with_payload=True).points
                for hit in hits:
                    selected[hit.payload["id"]] = by_id[hit.payload["id"]]
            # A terse answer needs its question for interpretation. Questions
            # provide context but cannot themselves prove candidate competency.
            candidates = list(selected.values())
            positions = {source.id:i for i, source in enumerate(sources)}
            for candidate in candidates:
                for previous in reversed(sources[:positions[candidate.id]]):
                    if previous.speaker == "Interviewer":
                        if len(selected) < 12:
                            selected[previous.id] = previous
                        break
            # Resume assertions remain explicitly separate, not demonstration.
            for source in sources:
                if source.kind == "resume" and len(selected) < 13:
                    selected[source.id] = source
            return list(selected.values())[:13]
        finally:
            client.close()
