"""Build local extractive drafts; human notes never replace source evidence."""

from copy import deepcopy
from hashlib import sha256
from html import escape
import re

from app.core.transcripts import ROLES, canonical_text, parse_transcript
from app.review.evidence import prepare_transcript


def preview(payload):
    turns = parse_transcript(payload.content, payload.format)
    speakers = list(dict.fromkeys(turn.speaker for turn in turns))
    if set(payload.speaker_roles) - set(speakers):
        raise ValueError("Speaker assignments must refer to labels in this transcript")
    roles = {name: ROLES[name.casefold()] for name in speakers if name.casefold() in ROLES}
    roles.update(payload.speaker_roles)
    problems = []
    if any(name not in roles for name in speakers):
        problems.append("Assign a role to every speaker before saving.")
    if sum(role == "Candidate" for role in roles.values()) != 1:
        problems.append("Assign exactly one candidate speaker; other speakers can be Interviewer or Exclude.")
    return {"speakers": speakers, "speaker_roles": roles, "turns": [turn.public() for turn in turns],
            "problems": problems, "ready": not problems, "model_requests": 0}


def build(payload):
    checked = preview(payload)
    if not checked["ready"]:
        raise ValueError(" ".join(checked["problems"]))
    turns = parse_transcript(payload.content, payload.format)
    transcript = canonical_text(turns, checked["speaker_roles"])
    sources = [source.model_dump() for source in prepare_transcript(transcript)]
    timeline, current = [], {"question_source_ids": [], "answer_source_ids": []}
    for source in sources:
        if source["speaker"] == "Interviewer" and current["answer_source_ids"]:
            timeline.append(current)
            current = {"question_source_ids": [], "answer_source_ids": []}
        key = "question_source_ids" if source["speaker"] == "Interviewer" else "answer_source_ids"
        current[key].append(source["id"])
    if current["question_source_ids"] or current["answer_source_ids"]:
        timeline.append(current)
    by_id = {source["id"]: source for source in sources}
    notes = []
    for number, group in enumerate(timeline, 1):
        identifier = f"note-{number:03}"
        group["note_id"] = identifier
        answer = "\n\n".join(by_id[key]["text"] for key in group["answer_source_ids"])
        excerpt = answer[:600]
        notes.append({"id": identifier, "body": excerpt, "excerpt": excerpt, "kind": "source_excerpt",
                      "source_ids": group["question_source_ids"] + group["answer_source_ids"]})
    return {"title": payload.title, "raw_content": payload.content, "format": payload.format,
            "speaker_roles": checked["speaker_roles"], "transcript": transcript,
            "source_hash": sha256(transcript.encode()).hexdigest(), "sources": sources,
            "timeline": timeline, "notes": notes,
            "draft_method": "Exact answer excerpts, capped at 600 characters per question group; no AI summary or model calls."}


def apply_edit(document, edit):
    expected = {note["id"] for note in document["notes"]}
    received = [note.id for note in edit.notes]
    if set(received) != expected or len(received) != len(expected):
        raise ValueError("Save every notebook note exactly once; source links cannot be replaced")
    updated, changes = deepcopy(document), []
    bodies = {note.id: note.body for note in edit.notes}
    if edit.title != document["title"]:
        changes.append({"field": "title", "before": document["title"], "after": edit.title})
        updated["title"] = edit.title
    for note in updated["notes"]:
        body = bodies[note["id"]]
        if body != note["body"]:
            changes.append({"field": note["id"], "before": note["body"], "after": body})
            note["body"] = body
            note["kind"] = "source_excerpt" if body == note["excerpt"] else "human_note"
    return updated, changes


def markdown(record):
    document = record["document"]

    def plain(value):
        return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", escape(str(value)))

    lines = [f"# {plain(document['title'])}", "", f"Notebook {record['id']} · revision {record['revision']}", "",
             "Notes are extractive drafts or human edits. Review assessments use the original normalized transcript.", ""]
    if document.get("ai_digest"):
        lines.extend(["## AI digest · human review required", ""])
        lines.extend([plain(document["ai_digest"]["scope"]), "", "Model: " + plain(document["ai_digest"]["model"]), ""])
        for point in document["ai_digest"]["points"]:
            lines.extend([plain(point["text"]), "", f"Source: {plain(point['source_id'])}", "> " + plain(point["quote"]), ""])
    sources = {source["id"]: source for source in document["sources"]}
    for number, note in enumerate(document["notes"], 1):
        lines.extend([f"## Note {number} · {plain(note['kind'])}", "", plain(note["body"]) or "_(Empty note)_", ""])
        for identifier in note["source_ids"]:
            source = sources[identifier]
            stamp = source.get("start_time") or "time unavailable"
            lines.extend([f"**{identifier} · {source['speaker']} · {plain(stamp)}**", "",
                          "> " + plain(source["text"]).replace("\n", "\n> "), ""])
    return "\n".join(lines)
