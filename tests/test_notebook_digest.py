"""Verify the one-call budget, source boundaries, validation and reuse of AI notes."""

import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.notebook import digest
from app.notebook.routes import build_router
from app.storage import MemoryRepository
from test_notebook import VTT


class DigestTests(TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.model = Mock()
        self.model.with_options.return_value = self.model
        app = FastAPI()
        def owner(request: Request):
            return request.headers.get("x-test-owner", "alice")
        app.include_router(build_router(self.repository, owner, self.model))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.record = self.client.post("/api/notebooks", json={"title":"Synthetic interview", "content":VTT, "format":"vtt"}).json()
        self.path = "/api/notebooks/" + self.record["id"]
        self.point = {"text":"The candidate described a 60-second cache TTL.", "source_id":self.record["document"]["sources"][1]["id"],
                      "quote":"60-second TTL"}
        self.reply()

    def reply(self, point=None, finish="stop"):
        self.model.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish, message=SimpleNamespace(content=json.dumps({"points":[point or self.point]})))],
            usage=SimpleNamespace(total_tokens=123))

    def generate(self, revision=0, **kwargs):
        return self.client.post(self.path + "/digest", json={"revision":revision, "consent_to_external_processing":True}, **kwargs)

    def test_consent_ownership_and_revision_are_checked_before_any_model_call(self):
        self.assertEqual(self.client.post(self.path+"/digest", json={"revision":0}).status_code, 400)
        self.assertEqual(self.generate(headers={"x-test-owner":"bob"}).status_code, 404)
        self.assertEqual(self.generate(revision=99).status_code, 409)
        self.model.chat.completions.create.assert_not_called()

    def test_human_notes_are_excluded_and_note_edits_reuse_original_source_digest(self):
        notes = [{"id":note["id"], "body":note["body"]} for note in self.record["document"]["notes"]]
        notes[0]["body"] = "PRIVATE HUMAN INTERPRETATION: invented outcome"
        edited = self.client.patch(self.path, json={"revision":0, "title":"Synthetic interview", "notes":notes}).json()
        response = self.generate(revision=edited["revision"])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertFalse(result["digest_reused"])
        self.assertEqual(result["document"]["sources"], self.record["document"]["sources"])
        self.assertEqual(result["document"]["ai_digest"]["reported_tokens"], 123)
        options = self.model.with_options.call_args.kwargs
        self.assertEqual(options["max_retries"], 0)
        sent = self.model.chat.completions.create.call_args.kwargs
        self.assertEqual(sent["max_tokens"], 1400)
        self.assertNotIn("PRIVATE HUMAN INTERPRETATION", json.dumps(sent["messages"]))
        notes[0]["body"] = "Revised private interpretation."
        updated = self.client.patch(self.path, json={"revision":result["revision"], "title":"Updated title", "notes":notes}).json()
        again = self.generate(revision=updated["revision"])
        self.assertTrue(again.json()["digest_reused"])
        self.model.chat.completions.create.assert_called_once()
        self.assertIn("AI digest", self.client.get(self.path+"/export").text)
        self.assertIn("Human review required", self.client.get(self.path+"/export").text)

    def test_unknown_quote_blank_quote_and_truncation_never_save_or_retry(self):
        for point, finish in [({**self.point, "quote":"This never appeared in the transcript."}, "stop"),
                              ({**self.point, "quote":" "}, "stop"),
                              ({**self.point, "source_id":"invented-source"}, "stop"),
                              (self.point, "length")]:
            with self.subTest(point=point, finish=finish):
                self.model.reset_mock()
                self.reply(point, finish)
                result = self.generate()
                self.assertEqual(result.status_code, 502, result.text)
                self.model.chat.completions.create.assert_called_once()
                saved = self.client.get(self.path).json()
                self.assertEqual(saved["revision"], 0)
                self.assertNotIn("ai_digest", saved["document"])

    def test_policy_change_invalidates_saved_digest(self):
        self.assertEqual(self.generate().status_code, 200)
        with patch.object(digest, "POLICY", digest.POLICY + "\nUpdated summary policy."):
            result = self.generate(revision=1)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()["digest_reused"])
        self.assertEqual(self.model.chat.completions.create.call_count, 2)

    def test_completed_model_result_is_reused_after_an_optimistic_save_conflict(self):
        from app.storage import SessionConflict
        with patch.object(self.repository, "update_notebook", side_effect=SessionConflict("simulated concurrent save")):
            self.assertEqual(self.generate().status_code, 409)
        self.assertEqual(self.generate().status_code, 200)
        self.model.chat.completions.create.assert_called_once()
