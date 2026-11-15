"""Application composition works without generation keys or a particular CWD."""

from contextlib import chdir
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.review.demo import SAMPLE
from app.storage import MemoryRepository


class ApplicationTests(unittest.TestCase):
    def test_keyless_source_preview_and_assets_work_from_another_directory(self):
        with patch.dict(os.environ, {"DEBATEBOT_AUTH_REQUIRED": "0"}, clear=True), \
                tempfile.TemporaryDirectory() as folder, chdir(folder):
            app = create_app(repository=MemoryRepository(), load_environment=False)
            with TestClient(app) as client:
                for path in ("/", "/review", "/static/post-review.js", "/static/evidence-lab.css"):
                    self.assertEqual(client.get(path).status_code, 200, path)
                preview = client.post("/api/reviews/prepare", json=SAMPLE)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.json()["model_requests"], 0)
                self.assertEqual(client.post("/api/debate", json={"persona_key": "socratic"}).status_code, 503)
                self.assertFalse(client.get("/api/reviews/configuration").json()["configured"])

    def test_factory_instances_do_not_share_private_sessions(self):
        with patch.dict(os.environ, {"DEBATEBOT_AUTH_REQUIRED": "0"}, clear=True):
            first = create_app(repository=MemoryRepository(), client=Mock(), load_environment=False)
            second = create_app(repository=MemoryRepository(), client=Mock(), load_environment=False)
            with TestClient(first) as a, TestClient(second) as b:
                response = a.post("/api/debate", json={"persona_key": "socratic"})
                self.assertEqual(response.status_code, 200)
                path = "/api/debate/" + response.json()["session_id"]
                self.assertEqual(a.get(path).status_code, 200)
                self.assertEqual(b.get(path).status_code, 404)
