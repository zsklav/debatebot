"""Local transcript import. Speaker attribution and timestamps are never inferred."""

from dataclasses import asdict, dataclass
from datetime import datetime
from html import unescape
import json
import re

MAX_TRANSCRIPT_CHARS = 60000
MAX_TURNS = 240
ROLES = {"candidate": "Candidate", "applicant": "Candidate", "interviewee": "Candidate",
         "interviewer": "Interviewer"}
HEADER = re.compile(r"^\s*(?:\[([^\]]+)\]\s*)?([^:\n<>\[\]]{1,100}):\s*(.*)$")


@dataclass(frozen=True)
class Turn:
    speaker: str
    text: str
    start_line: int
    end_line: int
    start_time: str | None = None
    end_time: str | None = None

    def public(self):
        return asdict(self)


def time_value(value):
    """Comparable timestamp value, with its coordinate system kept explicit."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Use an elapsed HH:MM:SS timestamp or an ISO timestamp with timezone")
    match = re.fullmatch(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})(?:[.,](\d{1,3}))?", value)
    if match:
        hours, minutes, seconds, fraction = match.groups()
        if int(minutes) >= 60 or int(seconds) >= 60:
            raise ValueError("Timestamp minutes and seconds must be below 60")
        return "elapsed", ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int((fraction or "0").ljust(3, "0"))
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Absolute timestamps need a timezone")
        return "absolute", int(stamp.timestamp() * 1000)
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("Invalid transcript timestamp") from error


def checked_turn(speaker, text, start_line, end_line, start_time=None, end_time=None):
    if not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 100:
        raise ValueError("Every turn needs a speaker label of at most 100 characters")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Every transcript turn needs nonblank text")
    if any(char in speaker for char in "\n\r:[]<>"):
        raise ValueError("Speaker labels cannot contain line breaks, colons or markup")
    if end_time and not start_time:
        raise ValueError("An end timestamp needs a start timestamp")
    if start_time:
        start = time_value(start_time)
        if end_time:
            end = time_value(end_time)
            if end[0] != start[0] or end[1] < start[1]:
                raise ValueError("A transcript turn cannot end before it starts")
    return Turn(speaker.strip(), text.strip(), start_line, end_line, start_time, end_time)


def parse_text(content):
    turns, buffer = [], []
    speaker, start, times = None, 1, (None, None)

    def flush(end):
        text = "\n".join(buffer).strip()
        if text:
            turns.append(checked_turn(speaker, text, start, end, *times))

    lines = content.splitlines()
    for number, line in enumerate(lines, 1):
        if speaker and line.startswith("\t"):
            buffer.append(line[1:])
            continue
        match = HEADER.match(line)
        if match:
            flush(number - 1)
            stamp, speaker, text = match.groups()
            times = (None, None)
            if stamp:
                parts = re.split(r"\s*-->\s*", stamp)
                if len(parts) > 2:
                    raise ValueError("Use [start --> end] for a timestamp range")
                times = (parts[0], parts[1] if len(parts) == 2 else None)
            start, buffer = number, [text]
        elif speaker:
            buffer.append(line)
        elif line.strip():
            raise ValueError("Label transcript turns as Interviewer: or Candidate:, or map named speakers in the notebook")
    flush(len(lines))
    return turns


def parse_vtt(content):
    if not content.lstrip("\ufeff").startswith("WEBVTT"):
        raise ValueError("A WebVTT transcript must start with WEBVTT")
    turns, number, lines = [], 0, content.lstrip("\ufeff").splitlines()
    while number < len(lines):
        line = lines[number].strip()
        if re.match(r"^(?:NOTE(?:\s|$)|STYLE$|REGION$)", line):
            while number < len(lines) and lines[number].strip():
                number += 1
            continue
        if "-->" not in line:
            number += 1
            continue
        timing_line = number + 1
        times = line.split("-->")
        if len(times) != 2 or not times[1].strip():
            raise ValueError("Invalid WebVTT cue timing")
        start_time, end_time = times[0].strip(), times[1].strip().split()[0]
        number += 1
        body = []
        while number < len(lines) and lines[number].strip():
            body.append(lines[number])
            number += 1
        raw = "\n".join(body)
        voices = re.findall(r"<v(?:\.[^ >]+)?\s+([^>]+)>", raw)
        if len(set(voices)) > 1:
            raise ValueError("Split WebVTT cues containing multiple speakers before importing")
        text = unescape(re.sub(r"<[^>]*>", "", raw)).strip()
        if voices:
            speaker = unescape(voices[0]).strip()
        else:
            labelled = HEADER.match(text)
            if not labelled:
                raise ValueError("WebVTT cues need voice tags or Speaker: labels")
            _, speaker, text = labelled.groups()
        turns.append(checked_turn(speaker, text, timing_line, number, start_time, end_time))
    return turns


def parse_meet_json(content):
    try:
        data = json.loads(content)
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid transcript JSON") from error
    labels = {}
    if isinstance(data, dict):
        if data.get("nextPageToken"):
            raise ValueError("This export is paginated. Combine all transcript entries before importing")
        labels = data.get("participants", {})
        entries = data.get("transcriptEntries")
    else:
        entries = data
    if not isinstance(entries, list) or not isinstance(labels, dict):
        raise ValueError("Use a transcriptEntries array and optional participants ID-to-name object")
    turns = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("participant"), str):
            raise ValueError("Each Meet entry needs participant and text fields")
        participant = entry["participant"]
        # Keep resource IDs usable as labels even without a separate name map.
        speaker = labels.get(participant, participant)
        turns.append(checked_turn(speaker, entry.get("text"), index, index,
                                  entry.get("startTime"), entry.get("endTime")))
    return turns


def parse_transcript(content, format="text"):
    if not isinstance(content, str) or not 1 <= len(content) <= MAX_TRANSCRIPT_CHARS:
        raise ValueError(f"Keep the transcript within {MAX_TRANSCRIPT_CHARS:,} characters")
    try:
        content.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Use a valid UTF-8 transcript") from error
    parser = {"text": parse_text, "vtt": parse_vtt, "meet_json": parse_meet_json}.get(format)
    if parser is None:
        raise ValueError("Supported formats: text, vtt, meet_json")
    turns = parser(content.lstrip("\ufeff"))
    if not turns or len(turns) > MAX_TURNS:
        raise ValueError(f"Use between 1 and {MAX_TURNS} nonblank transcript turns")
    if len({turn.speaker for turn in turns}) > 12:
        raise ValueError("Use at most 12 distinct speaker labels")
    previous = None
    for turn in turns:
        if turn.start_time:
            current = time_value(turn.start_time)
            if previous and (previous[0] != current[0] or current[1] < previous[1]):
                raise ValueError("Keep timestamped turns in chronological order using one clock system")
            previous = current
    return turns


def canonical_text(turns, roles):
    lines = []
    for turn in turns:
        role = roles[turn.speaker]
        if role == "Exclude":
            continue
        stamp = ""
        if turn.start_time:
            stamp = f"[{turn.start_time}"
            if turn.end_time:
                stamp += f" --> {turn.end_time}"
            stamp += "] "
        text = turn.text.replace("\n", "\n\t")
        lines.append(f"{stamp}{role}: {text}")
    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        raise ValueError("Normalized transcript exceeds the input limit; split the interview")
    return text
