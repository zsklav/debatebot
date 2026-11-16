"""Bounded read-only Meet API adapter; no calendar, recording or media access."""

from datetime import datetime, timezone
import json
import re
import time
from urllib.parse import urlsplit

from app.core.paths import ROOT
from app.core.transcripts import MAX_TRANSCRIPT_CHARS, MAX_TURNS, parse_transcript, time_value
from .google_config import GoogleError

MEET_BASE = "https://meet.googleapis.com/v2/"
RECORD_PATTERN = r"conferenceRecords/[A-Za-z0-9_-]{1,180}"


class NotReady(GoogleError):
    def __init__(self, message):
        super().__init__("waiting", message, retryable=True)


def record_name(value):
    if not isinstance(value, str) or not re.fullmatch(RECORD_PATTERN, value):
        raise ValueError("Select a valid Meet conference record")
    return value


def meeting_code(value):
    if not value:
        return None
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if parsed.hostname != "meet.google.com" or parsed.username or parsed.password:
            raise ValueError("Use a Google Meet link or a meeting code")
        value = parsed.path.strip("/")
    if not re.fullmatch(r"[a-z]{3}-[a-z]{4}-[a-z]{3}", value):
        raise ValueError("Use a meeting code such as abc-defg-hij")
    return value


class MeetClient:
    def __init__(self, oauth):
        self.oauth, self.requests = oauth, 0
        self.deadline = time.monotonic() + 150

    def get(self, owner, path, params=None):
        if not re.fullmatch(RECORD_PATTERN + r"(?:/transcripts(?:/[A-Za-z0-9_-]+/entries)?|/participants)?|conferenceRecords", path):
            raise GoogleError("resource", "Unsupported Meet resource")
        token = self.oauth.access_token(owner)
        for attempt in range(2):
            if time.monotonic() >= self.deadline:
                raise GoogleError("timeout", "The Meet check exceeded its time budget; it can be retried", retryable=True)
            if self.requests >= 30:
                raise GoogleError("limit", "Meet import exceeded its 30-read check budget")
            self.requests += 1
            status, data = self.oauth.http_json("GET", MEET_BASE + path, params=params,
                                              deadline=self.deadline,
                                              headers={"Authorization": "Bearer " + token})
            if status == 200:
                return data
            if status == 401 and attempt == 0:
                token = self.oauth.access_token(owner, force=True)
                continue
            if status in {401, 403, 404}:
                raise GoogleError("access", "Google did not allow access to this meeting. Check the connected account and meeting permissions")
            raise GoogleError("google", "Google could not complete the read request", retryable=status == 429 or status >= 500)
        raise GoogleError("access", "Reconnect Google to continue")

    def pages(self, owner, path, field):
        rows, token, seen = [], None, set()
        for _ in range(5):
            params = {"pageSize": 100}
            if token:
                params["pageToken"] = token
            data = self.get(owner, path, params)
            batch = data.get(field, [])
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise GoogleError("response", "Google returned invalid meeting data")
            rows.extend(batch)
            token = data.get("nextPageToken")
            if not token:
                return rows
            if not isinstance(token, str) or token in seen or len(token) > 2000:
                raise GoogleError("response", "Google returned invalid pagination")
            seen.add(token)
        raise GoogleError("limit", "The meeting exceeds the local pagination limit; no partial transcript was imported")

    def recent(self, owner, code=None):
        code = meeting_code(code)
        params = {"pageSize": 20}
        if code:
            params["filter"] = f'space.meeting_code = "{code}"'
        data = self.get(owner, "conferenceRecords", params)
        records = data.get("conferenceRecords", [])
        if not isinstance(records, list):
            raise GoogleError("response", "Google returned invalid conference records")
        return {"meetings": [{"name": record_name(row.get("name")), "start_time": row.get("startTime"),
                              "end_time": row.get("endTime")} for row in records],
                "has_more": bool(data.get("nextPageToken"))}

    def fetch(self, owner, conference):
        conference = record_name(conference)
        record = self.get(owner, conference)
        if not record.get("endTime"):
            raise NotReady("The selected meeting is still in progress")
        transcripts = self.pages(owner, conference + "/transcripts", "transcripts")
        if not transcripts:
            raise NotReady("No transcript is available yet. Transcription must have been enabled in Meet")
        if any(row.get("state") != "FILE_GENERATED" for row in transcripts):
            raise NotReady("Google is still preparing the transcript")
        participants = self.pages(owner, conference + "/participants", "participants")
        displays = {}
        for row in participants:
            name = row.get("name")
            if not isinstance(name, str) or not name.startswith(conference + "/participants/"):
                raise GoogleError("response", "Participant does not belong to the selected meeting")
            user = row.get("signedinUser") or row.get("anonymousUser") or row.get("phoneUser") or {}
            display = user.get("displayName")
            displays[name] = display[:200] if isinstance(display, str) else name
        entries, seen = [], {}
        for transcript in transcripts:
            name = transcript.get("name", "")
            if not re.fullmatch(re.escape(conference) + r"/transcripts/[A-Za-z0-9_-]+", name):
                raise GoogleError("response", "Transcript does not belong to the selected meeting")
            for entry in self.pages(owner, name + "/entries", "transcriptEntries"):
                identifier = entry.get("name")
                if not isinstance(identifier, str) or not identifier.startswith(name + "/entries/"):
                    raise GoogleError("response", "Transcript entry has an invalid source ID")
                if identifier in seen:
                    if seen[identifier] != entry:
                        raise GoogleError("response", "Google returned conflicting versions of one transcript entry")
                    continue
                if not isinstance(entry.get("participant"), str) or not entry["participant"].startswith(conference + "/participants/"):
                    raise GoogleError("response", "Transcript speaker does not belong to the selected meeting")
                if time_value(entry.get("startTime"))[0] != "absolute":
                    raise GoogleError("response", "Meet transcript entries need absolute timestamps")
                seen[identifier] = entry
                entries.append(entry)
                if len(entries) > MAX_TURNS:
                    raise GoogleError("limit", "This transcript exceeds 240 turns. No partial notebook was created")
        if not entries:
            raise NotReady("The generated transcript has no entries yet")
        entries.sort(key=lambda entry: (time_value(entry["startTime"])[1], entry["name"]))
        speakers = list(dict.fromkeys(entry["participant"] for entry in entries))
        aliases = {native: f"Speaker {number}" for number, native in enumerate(speakers, 1)}
        exported = {"participants": aliases, "transcriptEntries": [
            {key: entry[key] for key in ("participant", "text", "startTime", "endTime") if key in entry}
            for entry in entries]}
        content = json.dumps(exported, ensure_ascii=False)
        if len(content) > MAX_TRANSCRIPT_CHARS:
            raise GoogleError("limit", "This transcript exceeds 60,000 characters. No partial notebook was created")
        parse_transcript(content, "meet_json")
        provenance = {"provider": "google", "conference_record": conference,
            "transcript_names": [row["name"] for row in transcripts], "entry_names": [entry["name"] for entry in entries],
            "speaker_ids": {alias: native for native, alias in aliases.items()}, "entry_count": len(entries),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "scope": "Generated transcript sessions, not a guarantee the entire meeting was transcribed"}
        return {"notebook_input": {"title": "Meet interview · " + str(record.get("startTime", ""))[:16],
                                   "content": content, "format": "meet_json"},
                "display_names": {alias: displays.get(native, alias) for native, alias in aliases.items()},
                "provenance": provenance}


class DemoMeetClient:
    """Fixed synthetic capture; this never connects to Google or impersonates OAuth."""
    requests = 0

    def fetch(self, owner, conference):
        turns = parse_transcript((ROOT / "examples/interview_sample.vtt").read_text(encoding="utf-8"), "vtt")
        entries = [{"participant": "demo/" + turn.speaker.lower(), "text": turn.text,
                    "startTime": "2026-11-15T" + turn.start_time + "Z", "endTime": "2026-11-15T" + turn.end_time + "Z"} for turn in turns]
        return {"notebook_input": {"title": "Automation demo · synthetic interview", "format": "meet_json",
                "content": json.dumps({"participants": {"demo/interviewer":"Speaker 1", "demo/candidate":"Speaker 2"}, "transcriptEntries": entries})},
                "display_names": {"Speaker 1":"Interviewer (synthetic)", "Speaker 2":"Candidate (synthetic)"},
                "provenance": {"provider":"demo", "conference_record":conference, "entry_count":len(entries),
                               "scope":"Scripted import demonstration. No Google account or live meeting was accessed."}}
