"""Import provenance, attribution, private notes, lost-update protection and review links."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.core.paths import ROOT
from app.core.transcripts import canonical_text, parse_transcript
from app.notebook.models import NotebookInput
from app.notebook.routes import build_router
from app.notebook.service import build, preview
from app.review import demo
from app.review.evidence import prepare_transcript
from app.review.providers import ModelRoute
from app.review.routes import build_router as review_router
from app.storage import MemoryRepository

VTT = (ROOT / "examples/interview_sample.vtt").read_text(encoding="utf-8")


class TranscriptTests(unittest.TestCase):
    def test_bom_imports_work_but_invalid_unicode_and_reordered_times_fail(self):
        turns = parse_transcript("\ufeff[00:12] Candidate: I described the caching mechanism.")
        self.assertEqual(turns[0].speaker, "Candidate")
        for content in ("Candidate: Invalid Unicode \ud800.",
                        "[00:30] Interviewer: A later question.\n[00:10] Candidate: An earlier answer.",
                        "[00:30] Interviewer: An elapsed clock.\n[2026-11-15T10:00:00Z] Candidate: An absolute clock."):
            with self.assertRaises(ValueError):
                parse_transcript(content)

    def test_vtt_keeps_speakers_clock_ranges_and_actual_answers(self):
        turns = parse_transcript(VTT, "vtt")
        self.assertEqual(len(turns), 5)
        self.assertEqual(turns[1].start_time, "00:00:19.000")
        self.assertEqual(turns[1].end_time, "00:00:31.000")
        self.assertEqual(turns[1].speaker, "Candidate")
        self.assertIn("60-second TTL", turns[1].text)
        self.assertGreater(turns[1].start_line, 1)

    def test_missing_timestamps_stay_missing_and_named_speakers_need_assignment(self):
        payload = NotebookInput(content="Asha: Describe caching in your project.\nRavi: I used Redis for product lookups.")
        checked = preview(payload)
        self.assertFalse(checked["ready"])
        self.assertIsNone(checked["turns"][0]["start_time"])
        with self.assertRaises(ValueError):
            build(payload)
        payload.speaker_roles = {"Asha": "Interviewer", "Ravi": "Candidate"}
        document = build(payload)
        self.assertEqual(document["sources"][1]["speaker"], "Candidate")
        self.assertIsNone(document["sources"][1]["start_time"])

    def test_meet_entries_preserve_absolute_times_and_require_complete_pages(self):
        exported = {"participants": {"participants/a": "Asha", "participants/b": "Ravi"}, "transcriptEntries": [
            {"participant": "participants/a", "text": "Explain the cache.", "startTime": "2026-11-15T10:00:00Z", "endTime": "2026-11-15T10:00:05Z"},
            {"participant": "participants/b", "text": "I used Redis for product lookups.", "startTime": "2026-11-15T10:00:06Z", "endTime": "2026-11-15T10:00:15Z"}]}
        turns = parse_transcript(json.dumps(exported), "meet_json")
        self.assertEqual(turns[1].speaker, "Ravi")
        self.assertEqual(turns[1].start_time, "2026-11-15T10:00:06Z")
        exported["nextPageToken"] = "more-records"
        with self.assertRaisesRegex(ValueError, "paginated"):
            parse_transcript(json.dumps(exported), "meet_json")

    def test_bad_or_reversed_timestamps_and_mixed_voice_cues_fail(self):
        for content, format in [
            ("[00:70] Candidate: An invalid clock should not become a source.", "text"),
            ("[00:30 --> 00:10] Candidate: An impossible time range.", "text"),
            ("WEBVTT\n\n00:01.000 -->\nCandidate: Answer.", "vtt"),
            ("WEBVTT\n\n00:01.000 --> 00:02.000\n<v Alice>One.</v><v Bob>Two.</v>", "vtt"),
            ("WEBVTT\n\n00:01.000 --> 00:02.000\nAn answer without an identifiable speaker.", "vtt"),
        ]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_transcript(content, format)

    def test_multiline_colons_survive_normalization_and_chunking(self):
        turns = parse_transcript("WEBVTT\n\n00:01.000 --> 00:05.000\n<v Candidate>I built a cache.\nTrade-offs: stale reads.</v>\n", "vtt")
        normalized = canonical_text(turns, {"Candidate": "Candidate"})
        sources = prepare_transcript(normalized)
        self.assertEqual(len(sources), 1)
        self.assertIn("\nTrade-offs: stale reads.", sources[0].text)
        self.assertEqual(sources[0].start_time, "00:01.000")

    def test_two_candidates_unknown_assignments_and_oversized_imports_fail(self):
        payload = NotebookInput(content="Alice: I built the frontend application.\nBob: I built a separate backend.", speaker_roles={"Alice":"Candidate", "Bob":"Candidate"})
        with self.assertRaises(ValueError):
            build(payload)
        payload.speaker_roles = {"Invisible speaker":"Candidate"}
        with self.assertRaises(ValueError):
            preview(payload)
        with self.assertRaises(ValueError):
            parse_transcript("Candidate: " + "a" * 60000)

    def test_drafts_are_extracts_and_unanswered_prompts_are_preserved(self):
        document = build(NotebookInput(content=VTT, format="vtt"))
        self.assertEqual(len(document["timeline"]), 3)
        self.assertFalse(document["timeline"][-1]["answer_source_ids"])
        self.assertEqual(document["notes"][-1]["body"], "")
        for note in document["notes"]:
            self.assertEqual(note["kind"], "source_excerpt")
            self.assertEqual(note["body"], note["excerpt"])


class NotebookRouteTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        def owner(request: Request):
            value = request.headers.get("x-test-owner")
            if not value:
                raise HTTPException(401, "Sign in")
            return value
        app = FastAPI()
        app.include_router(build_router(self.repository, owner))
        app.include_router(review_router(self.repository, owner))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"x-test-owner":"alice"}

    def create(self):
        response = self.client.post("/api/notebooks", json={"title":"Caching interview", "content":VTT, "format":"vtt"}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def edit(self, record, text):
        notes = [{"id":note["id"], "body":note["body"]} for note in record["document"]["notes"]]
        notes[0]["body"] = text
        return {"revision":record["revision"], "title":record["document"]["title"], "notes":notes}

    def test_import_and_preview_require_auth_and_make_no_model_request(self):
        self.assertEqual(self.client.get("/api/notebooks/sample").status_code, 401)
        self.assertEqual(self.client.post("/api/notebooks/preview", json={"content":VTT,"format":"vtt"}).status_code, 401)
        with patch("app.review.routes.configuration") as configuration:
            response = self.client.post("/api/notebooks/preview", json={"content":VTT,"format":"vtt"}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["model_requests"], 0)
            self.create()
        configuration.assert_not_called()

    def test_edits_keep_sources_immutable_and_store_version_history(self):
        record = self.create()
        response = self.client.patch("/api/notebooks/" + record["id"], json=self.edit(record, "Human observation: verify invalidation."), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        edited = response.json()
        self.assertEqual(edited["revision"], 1)
        for field in ("sources", "transcript", "source_hash", "raw_content"):
            self.assertEqual(edited["document"][field], record["document"][field])
        self.assertEqual(edited["document"]["notes"][0]["kind"], "human_note")
        history = self.client.get("/api/notebooks/" + record["id"] + "/history", headers=self.headers).json()["revisions"]
        self.assertEqual([row["revision"] for row in history], [1, 0])
        self.assertEqual(history[0]["changes"][0]["after"], "Human observation: verify invalidation.")
        self.assertEqual(len(self.client.get("/api/notebooks", headers=self.headers).json()["notebooks"]), 1)

    def test_stale_and_duplicate_note_saves_cannot_overwrite_notes(self):
        record = self.create()
        path = "/api/notebooks/" + record["id"]
        body = self.edit(record, "First revision.")
        self.assertEqual(self.client.patch(path, json=body, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.patch(path, json=self.edit(record, "Stale revision."), headers=self.headers).status_code, 409)
        latest = self.client.get(path, headers=self.headers).json()
        invalid = self.edit(latest, "Duplicate note.")
        invalid["notes"][1] = invalid["notes"][0]
        self.assertEqual(self.client.patch(path, json=invalid, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get(path, headers=self.headers).json()["document"]["notes"][0]["body"], "First revision.")

    def test_other_owner_cannot_read_edit_export_or_view_history(self):
        record = self.create()
        path, other = "/api/notebooks/" + record["id"], {"x-test-owner":"bob"}
        for suffix in ("", "/history", "/export"):
            self.assertEqual(self.client.get(path + suffix, headers=other).status_code, 404)
        self.assertEqual(self.client.patch(path, json=self.edit(record, "Stolen edit"), headers=other).status_code, 404)
        self.assertEqual(self.client.get("/api/notebooks", headers=other).json()["notebooks"], [])

    def test_export_is_a_safe_download_with_exact_sources(self):
        record = self.create()
        path = "/api/notebooks/" + record["id"]
        self.client.patch(path, json=self.edit(record, '<img src=x onerror=alert(1)> **not source evidence**'), headers=self.headers)
        response = self.client.get(path + "/export", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertNotIn("<img", response.text)
        self.assertIn("&lt;img", response.text)
        self.assertIn("60\\-second TTL", response.text)
        self.assertIn("00:00:19", response.text)

    def test_linked_review_uses_original_answers_and_reuses_result_after_note_edits(self):
        record = self.create()
        body = {**deepcopy(demo.SAMPLE), "transcript":record["document"]["transcript"], "notebook_id":record["id"],
                "evidence_confirmed":True, "consent_to_external_processing":True}
        specs = [ModelRoute(role,"groq",role,role,"offline-key") for role in ("a","b","judge")]
        with patch("app.review.routes.configuration", return_value=specs), patch("app.review.routes.evaluate", return_value={"status":"complete"}) as running:
            first = self.client.post("/api/reviews/run", json=body, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.client.patch("/api/notebooks/" + record["id"], json=self.edit(record, "A human opinion, not transcript evidence."), headers=self.headers)
            second = self.client.post("/api/reviews/run", json=body, headers=self.headers)
            self.assertEqual(first.json()["run_id"], second.json()["run_id"])
            self.assertEqual(running.call_count, 1)
            self.assertNotIn("human opinion", running.call_args[0][0].transcript)
            self.assertEqual(first.json()["notebook_source_hash"], record["document"]["source_hash"])
        with patch("app.review.routes.configuration") as configuring:
            self.assertEqual(self.client.post("/api/reviews/run", json={**body,"transcript":"Candidate: Changed evidence is not the saved original."}, headers=self.headers).status_code, 422)
            self.assertEqual(self.client.post("/api/reviews/run", json=body, headers={"x-test-owner":"bob"}).status_code, 404)
        configuring.assert_not_called()
