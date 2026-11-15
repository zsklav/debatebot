"""Account/session contracts and cross-user API isolation; no model calls."""

from contextlib import nullcontext
import os
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api import evidence as evidence_routes
from app.api.application import create_app
from app.storage import MemoryRepository

from app.auth import COOKIE, password_hash, token_hash, verify_password

PASSWORD = "Testing-password-42"


class PasswordTests(unittest.TestCase):
    def test_salted_password_hashes_and_wrong_passwords(self):
        first, second = password_hash(PASSWORD), password_hash(PASSWORD)
        self.assertNotEqual(first, second)
        self.assertNotIn(PASSWORD, first)
        self.assertTrue(verify_password(PASSWORD, first))
        self.assertFalse(verify_password("incorrect-password", first))
        self.assertFalse(verify_password(PASSWORD, "malformed"))


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.env = patch.dict(os.environ, {"DEBATEBOT_AUTH_REQUIRED": "1"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repository = MemoryRepository()
        self.routes = evidence_routes
        app = create_app(repository=self.repository, client=self.provider, load_environment=False)
        self.a = TestClient(app)
        self.b = TestClient(app)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def register(self, client, username):
        response = client.post("/api/auth/register", json={"username": username, "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        return response

    def test_auth_is_required_before_model_or_private_record_access(self):
        for path, payload in (("/api/debate", {"persona_key": "socratic"}),
                              ("/api/research", {"claim": "A claim"}),
                              ("/api/findhole", {"argument": "A claim"}),
                              ("/api/findhole/evidence", {"argument": "A claim"})):
            self.assertEqual(self.a.post(path, json=payload).status_code, 401)
        self.assertEqual(self.a.get("/api/debate/missing").status_code, 401)
        self.assertEqual(self.a.get("/api/runs/missing").status_code, 401)
        self.provider.chat.completions.create.assert_not_called()

    def test_cookie_flags_hash_only_storage_and_logout_revocation(self):
        registered = self.register(self.a, "alice")
        cookie = registered.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie); self.assertIn("samesite=strict", cookie)
        raw = self.a.cookies.get(COOKIE)
        self.assertNotIn(raw, self.repository.tokens)
        self.assertIn(token_hash(raw), self.repository.tokens)
        self.assertTrue(self.a.get("/api/auth/me").json()["authenticated"])
        self.assertEqual(self.a.post("/api/auth/logout", json={}).status_code, 200)
        self.a.cookies.set(COOKIE, raw)
        self.assertFalse(self.a.get("/api/auth/me").json()["authenticated"])

    def test_login_rotation_and_wrong_credentials(self):
        self.register(self.a, "alice")
        old = self.a.cookies.get(COOKIE)
        self.assertEqual(self.b.post("/api/auth/login", json={"username": "alice", "password": "wrong-password-42"}).status_code, 401)
        self.assertEqual(self.a.post("/api/auth/login", json={"username": "ALICE", "password": PASSWORD}).status_code, 200)
        self.assertNotEqual(old, self.a.cookies.get(COOKIE))
        self.assertNotIn(token_hash(old), self.repository.tokens)

    def test_other_user_cannot_read_or_mutate_debate_or_run(self):
        self.register(self.a, "alice"); self.register(self.b, "bob")
        sid = self.a.post("/api/debate", json={"persona_key": "socratic"}).json()["session_id"]
        self.assertEqual(self.b.get(f"/api/debate/{sid}").status_code, 404)
        for suffix, payload in (("turn", {"message": "Intrusion"}), ("score", {}), ("findhole", {})):
            self.assertEqual(self.b.post(f"/api/debate/{sid}/{suffix}", json=payload).status_code, 404)
        with patch.object(self.routes, "open_evidence_index", return_value=nullcontext(Mock())), patch.object(
                self.routes, "research_claim", return_value={"status": "partial", "assessment": None}):
            run = self.a.post("/api/research", json={"claim": "Private claim"}).json()["run_id"]
        self.assertEqual(self.a.get(f"/api/runs/{run}").status_code, 200)
        self.assertEqual(self.b.get(f"/api/runs/{run}").status_code, 404)
        self.provider.chat.completions.create.assert_not_called()

    def test_expired_and_forged_tokens_are_rejected(self):
        self.register(self.a, "alice")
        key = token_hash(self.a.cookies.get(COOKIE))
        self.repository.tokens[key]["expires_at"] = int(time.time()) - 1
        self.assertFalse(self.a.get("/api/auth/me").json()["authenticated"])
        self.b.cookies.set(COOKIE, "forged-token")
        self.assertEqual(self.b.get("/api/runs/any").status_code, 401)

    def test_cross_origin_writes_and_form_posts_are_rejected(self):
        payload = {"username": "alice", "password": PASSWORD}
        self.assertEqual(self.a.post("/api/auth/register", json=payload, headers={"Origin": "https://evil.example"}).status_code, 403)
        # FastAPI may reject non-JSON body parsing before the endpoint's 415 guard.
        self.assertIn(self.a.post("/api/auth/login", content="{}", headers={"Content-Type": "text/plain"}).status_code, {415, 422})
        self.register(self.a, "alice")
        self.assertEqual(self.a.post("/api/debate", json={"persona_key": "socratic"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.a.post("/api/auth/logout", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_throttling_stops_repeated_login_attempts(self):
        self.register(self.a, "alice")
        statuses = [self.b.post("/api/auth/login", json={"username": "alice", "password": "wrong-password-42"}).status_code for _ in range(10)]
        self.assertEqual(statuses[-1], 429)

    def test_account_validation_and_duplicate_names(self):
        invalid = self.a.post("/api/auth/register", json={"username": "alice", "password": "short"})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn('"input"', invalid.text)
        self.assertEqual(self.a.post("/api/auth/register", json={"username": "bad name", "password": PASSWORD}).status_code, 422)
        self.register(self.a, "alice")
        self.assertEqual(self.b.post("/api/auth/register", json={"username": "ALICE", "password": PASSWORD}).status_code, 409)

    def test_https_cookie_is_secure_and_private_responses_are_not_cached(self):
        from fastapi import FastAPI
        from app.auth import build_auth
        from app.storage import MemoryRepository
        with patch.dict(os.environ, {"DEBATEBOT_PUBLIC_ORIGIN": "https://testserver"}):
            app = FastAPI()
            app.include_router(build_auth(MemoryRepository())[0])
        with TestClient(app, base_url="https://testserver") as api:
            response = api.post("/api/auth/register", json={"username": "secure-user", "password": PASSWORD})
            self.assertIn("secure", response.headers["set-cookie"].lower())
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.a.get("/api/auth/me").headers["cache-control"], "no-store")
