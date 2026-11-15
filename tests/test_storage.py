"""Shared memory/PostgreSQL persistence contract; SQL integration is opt-in."""

import os
import importlib
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext
from uuid import uuid4

from debate import Debate
from storage import MemoryRepository, PostgresRepository, SessionConflict, StorageUnavailable
from test_baseline import completion


class StorageContract:
    def test_ownership_filters_reads_updates_and_runs(self):
        self.repository.create_session(self.sid, self.debate, owner_id="alice")
        self.assertIsNone(self.repository.load_session(self.sid, self.client, owner_id="bob"))
        self.assertIsNotNone(self.repository.load_session(self.sid, self.client, owner_id="alice"))
        with self.assertRaises(SessionConflict):
            self.repository.update_session(self.sid, self.debate, 0, owner_id="bob")
        self.repository.save_run(self.run_id, "research", "private", {"status": "partial"}, owner_id="alice")
        self.assertIsNone(self.repository.get_run(self.run_id, owner_id="bob"))
        self.assertIsNotNone(self.repository.get_run(self.run_id, owner_id="alice"))

    def setUp(self):
        self.repository = self.make_repository()
        self.client = Mock()
        self.client.chat.completions.create.return_value = completion("Why?")
        self.sid = "storage-test-" + str(uuid4())
        self.run_id = "storage-test-" + str(uuid4())
        self.debate = Debate(self.client, "socratic", model="stored-model")

    def test_round_trip_preserves_prompt_model_messages_and_tokens(self):
        self.repository.create_session(self.sid, self.debate)
        debate, revision = self.repository.load_session(self.sid, self.client)
        debate.respond("A claim")
        self.repository.update_session(self.sid, debate, revision)
        loaded, revision = self.reopened().load_session(self.sid, self.client)
        self.assertEqual(loaded.model, "stored-model")
        self.assertEqual(loaded.messages[0], self.debate.messages[0])
        self.assertEqual(loaded.turn_count(), 1)
        self.assertEqual(loaded.total_tokens, 12)
        self.assertEqual(revision, 1)

    def test_stale_writer_cannot_overwrite_completed_turn(self):
        self.repository.create_session(self.sid, self.debate)
        first, revision = self.repository.load_session(self.sid, self.client)
        second, stale = self.repository.load_session(self.sid, self.client)
        first.respond("First update")
        self.repository.update_session(self.sid, first, revision)
        second.respond("Stale update")
        with self.assertRaises(SessionConflict):
            self.repository.update_session(self.sid, second, stale)
        loaded, _ = self.repository.load_session(self.sid, self.client)
        self.assertIn("First update", loaded.transcript())
        self.assertNotIn("Stale update", loaded.transcript())

    def test_mutating_loaded_session_does_not_change_saved_snapshot(self):
        self.repository.create_session(self.sid, self.debate)
        loaded, _ = self.repository.load_session(self.sid, self.client)
        loaded.messages.append({"role": "user", "content": "Not committed"})
        saved, _ = self.repository.load_session(self.sid, self.client)
        self.assertEqual(saved.turn_count(), 0)
        self.assertNotIn("Not committed", saved.transcript())

    def test_failed_generation_leaves_saved_session_unchanged(self):
        self.repository.create_session(self.sid, self.debate)
        loaded, _ = self.repository.load_session(self.sid, self.client)
        self.client.chat.completions.create.side_effect = TimeoutError("fake timeout")
        with self.assertRaises(TimeoutError):
            loaded.respond("A claim")
        self.assertEqual(self.repository.load_session(self.sid, self.client)[0].turn_count(), 0)

    def test_run_roundtrip_and_missing_records(self):
        result = {"status": "partial", "events": [{"tool": "search_evidence"}], "run_id": self.run_id}
        self.repository.save_run(self.run_id, "research", "Unicode \u0939\u093f\u0902\u0926\u0940 and SQL ' quotes", result)
        stored = self.reopened().get_run(self.run_id)
        self.assertEqual(stored["result"], result)
        self.assertEqual(stored["kind"], "research")
        self.assertIsNone(self.repository.get_run("missing-" + str(uuid4())))
        self.assertIsNone(self.repository.load_session("missing-" + str(uuid4()), self.client))


class MemoryTests(StorageContract, unittest.TestCase):
    def make_repository(self):
        return MemoryRepository()

    def reopened(self):
        return self.repository  # Memory mode is intentionally not restart durable.


@unittest.skipUnless(os.environ.get("RUN_POSTGRES_TESTS") == "1", "Opt in with RUN_POSTGRES_TESTS=1")
class PostgresTests(StorageContract, unittest.TestCase):
    def test_accounts_and_revocable_tokens_persist_in_postgres(self):
        user_id = "auth-test-" + str(uuid4())
        username = "auth-test-" + str(uuid4())
        token = "test-token-" + str(uuid4())
        try:
            self.assertTrue(self.repository.create_user(user_id, username, "test-hash"))
            self.assertFalse(self.repository.create_user("other", username, "other-hash"))
            self.repository.save_auth(token, user_id, 200)
            another = self.reopened()
            self.assertEqual(another.auth_user(token, 100)["id"], user_id)
            self.assertIsNone(another.auth_user(token, 200))
            another.revoke_auth(token)
            self.assertIsNone(self.repository.auth_user(token, 100))
        finally:
            self.repository._execute(lambda conn: conn.execute("DELETE FROM app_users WHERE id=%s", (user_id,)))

    def test_new_api_instance_reopens_saved_debate_and_run(self):
        from fastapi.testclient import TestClient
        previous = sys.modules.pop("web.api", None)
        try:
            with patch.dict(os.environ, {"DATABASE_URL": self.dsn, "GROQ_API_KEY": "offline-test-key", "DEBATEBOT_AUTH_REQUIRED": "0"}, clear=True), patch(
                    "dotenv.load_dotenv"), patch("groq.Groq", return_value=self.client):
                module = importlib.import_module("web.api")
                with TestClient(module.app) as api:
                    created = api.post("/api/debate", json={"persona_key": "socratic"})
                    self.sid = created.json()["session_id"]
                    self.assertEqual(api.post(f"/api/debate/{self.sid}/turn", json={"message": "Saved claim"}).status_code, 200)
                    with patch.object(module, "open_evidence_index", return_value=nullcontext(Mock())), patch.object(
                            module, "research_claim", return_value={"status": "partial", "assessment": None}):
                        run = api.post("/api/research", json={"claim": "Saved research claim"})
                    self.run_id = run.json()["run_id"]
                # New app/repository/client instance; no session cache is shared.
                sys.modules.pop("web.api", None)
                restarted = importlib.import_module("web.api")
                with TestClient(restarted.app) as api:
                    restored = api.get(f"/api/debate/{self.sid}")
                    self.assertEqual(restored.json()["turn_count"], 1)
                    self.assertIn("Saved claim", restored.json()["transcript"])
                    self.assertEqual(restored.json()["messages"][0]["role"], "user")
                    saved = api.get(f"/api/runs/{self.run_id}")
                    self.assertEqual(saved.json()["result"]["status"], "partial")
                    self.assertEqual(saved.json()["result"]["storage"], "postgresql")
        finally:
            sys.modules.pop("web.api", None)
            if previous is not None:
                sys.modules["web.api"] = previous

    def make_repository(self):
        from dotenv import dotenv_values
        self.dsn = os.environ.get("DEBATEBOT_TEST_DATABASE_URL") or dotenv_values(
            Path(__file__).resolve().parent.parent / ".env").get("DATABASE_URL")
        if not self.dsn:
            self.skipTest("No test PostgreSQL DSN configured")
        repository = PostgresRepository(self.dsn)
        repository.migrate()
        repository.migrate()  # Applying the same migration twice must be safe.
        return repository

    def reopened(self):
        return PostgresRepository(self.dsn)

    def tearDown(self):
        # Remove only records whose unique IDs this test created, never user data.
        self.repository._execute(lambda conn: conn.execute("DELETE FROM debate_sessions WHERE id=%s", (self.sid,)))
        self.repository._execute(lambda conn: conn.execute("DELETE FROM analysis_runs WHERE id=%s", (self.run_id,)))
