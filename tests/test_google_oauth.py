"""Real OAuth request construction with a fake Google transport; no live credentials."""

import logging
from unittest import TestCase
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx

from app.auth import COOKIE, token_hash
from app.integrations.google_config import CALLBACK, GoogleConfig, GoogleError, SCOPE
from app.integrations.google_oauth import CallbackLogFilter, GoogleOAuth
from app.integrations.meet_import import MeetImports
from app.integrations.routes import build_router
from app.storage import MemoryRepository


def config():
    return GoogleConfig("offline-test.apps.googleusercontent.com", "offline-server-secret",
                        "http://127.0.0.1:8000" + CALLBACK, Fernet.generate_key().decode())


def authorize(repository, settings, handler, clock=None, owner="alice"):
    clock = clock or Mock(return_value=1000)
    repository.create_user(owner, owner, "unused-test-hash")
    repository.save_auth(token_hash("session-" + owner), owner, 100000)
    oauth = GoogleOAuth(repository, config=lambda:settings, transport=httpx.MockTransport(handler), clock=clock)
    url, binding = oauth.begin(owner, token_hash("session-" + owner))
    state = parse_qs(urlsplit(url).query)["state"][0]
    oauth.finish(state, binding, "offline-code")
    return oauth


def tokens(**overrides):
    return {"access_token":"private-test-access", "refresh_token":"private-test-refresh", "scope":SCOPE,
            "expires_in":3600, "token_type":"Bearer", **overrides}


class OAuthTests(TestCase):
    def test_pkce_scopes_state_replay_and_encrypted_owner_binding(self):
        repository, settings, seen = MemoryRepository(), config(), []
        def transport(request):
            seen.append(request)
            body = parse_qs(request.content.decode())
            self.assertEqual(body["code"], ["offline-code"])
            self.assertGreater(len(body["code_verifier"][0]), 40)
            return httpx.Response(200, json=tokens())
        oauth = authorize(repository, settings, transport)
        row = repository.get_google_connection("alice")
        self.assertNotIn("private-test-refresh", row["encrypted_tokens"])
        self.assertNotIn("private-test-access", row["encrypted_tokens"])
        self.assertNotIn(settings.client_secret, repr(settings))
        self.assertEqual(oauth.access_token("alice"), "private-test-access")
        repository.save_google_connection("bob", row["encrypted_tokens"], row["generation"], row["scopes"])
        with self.assertRaises(GoogleError):
            oauth.access_token("bob")
        self.assertEqual(len(seen), 1)

    def test_bad_binding_cannot_consume_flow_and_completed_state_cannot_replay(self):
        repository, settings, calls = MemoryRepository(), config(), []
        repository.create_user("alice", "alice", "unused")
        repository.save_auth("hash", "alice", 10000)
        oauth = GoogleOAuth(repository, config=lambda:settings, clock=lambda:1000,
                            transport=httpx.MockTransport(lambda request:(calls.append(request) or httpx.Response(200,json=tokens()))))
        url, binding = oauth.begin("alice", "hash")
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["scope"], [SCOPE])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        state = params["state"][0]
        with self.assertRaises(GoogleError):
            oauth.finish(state, "another-browser", "code")
        oauth.finish(state, binding, "code")
        with self.assertRaises(GoogleError):
            oauth.finish(state, binding, "code")
        self.assertEqual(len(calls), 1)

    def test_logout_and_expiry_invalidate_pending_authorization(self):
        for expired in (False, True):
            repository, settings, clock = MemoryRepository(), config(), Mock(return_value=1000)
            repository.create_user("alice", "alice", "unused")
            repository.save_auth("hash", "alice", 10000)
            http = Mock()
            oauth = GoogleOAuth(repository, config=lambda:settings, clock=clock)
            oauth.http_json = http
            url, binding = oauth.begin("alice", "hash")
            if expired:
                clock.return_value = 1601
            else:
                repository.revoke_auth("hash")
            with self.assertRaises(GoogleError):
                oauth.finish(parse_qs(urlsplit(url).query)["state"][0], binding, "code")
            http.assert_not_called()

    def test_refresh_preserves_refresh_token_and_revocation_removes_connection(self):
        repository, settings, clock, calls = MemoryRepository(), config(), Mock(return_value=1000), []
        def transport(request):
            calls.append(parse_qs(request.content.decode()))
            if len(calls) == 1:
                return httpx.Response(200, json=tokens())
            if len(calls) == 2:
                return httpx.Response(200,json={"access_token":"refreshed-access","expires_in":3600,"token_type":"Bearer"})
            return httpx.Response(400,json={"error":"invalid_grant"})
        oauth = authorize(repository, settings, transport, clock)
        clock.return_value = 4700
        self.assertEqual(oauth.access_token("alice"), "refreshed-access")
        clock.return_value = 9000
        with self.assertRaises(GoogleError):
            oauth.access_token("alice")
        self.assertEqual(calls[2]["refresh_token"], ["private-test-refresh"])
        self.assertIsNone(repository.get_google_connection("alice"))

    def test_insufficient_scope_and_tampered_cipher_never_produce_access(self):
        with self.assertRaises(GoogleError):
            authorize(MemoryRepository(), config(), lambda request:httpx.Response(200,json=tokens(scope="openid")))
        repository, settings = MemoryRepository(), config()
        oauth = authorize(repository, settings, lambda request:httpx.Response(200,json=tokens()))
        row = repository.get_google_connection("alice")
        repository.save_google_connection("alice", "tampered", row["generation"], row["scopes"])
        with self.assertRaises(GoogleError):
            oauth.access_token("alice")

    def test_callback_redacts_query_from_access_log(self):
        record = logging.LogRecord("uvicorn.access", 20, "", 1, '%s %s %s %s %s',
            ("address", "GET", CALLBACK + "?code=private-authorization-code&state=secret", "1.1", 303), None)
        CallbackLogFilter().filter(record)
        self.assertNotIn("private-authorization-code", record.getMessage())
        self.assertEqual(record.args[2], CALLBACK)


class OAuthRouteTests(TestCase):
    def test_lax_browser_binding_allows_callback_without_strict_login_cookie(self):
        repository, settings = MemoryRepository(), config()
        repository.create_user("alice", "alice", "unused")
        repository.save_auth(token_hash("session"), "alice", 100000)
        oauth = GoogleOAuth(repository, config=lambda:settings, clock=lambda:1000,
                            transport=httpx.MockTransport(lambda request:httpx.Response(200,json=tokens())))
        def owner(request: Request):
            if request.cookies.get(COOKIE) != "session":
                raise HTTPException(401, "Sign in")
            return "alice"
        app = FastAPI()
        app.include_router(build_router(repository, owner, oauth, MeetImports(repository, oauth)))
        with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as client:
            client.cookies.set(COOKIE, "session")
            response = client.post("/api/integrations/google/connect", json={})
            self.assertEqual(response.status_code, 200)
            self.assertIn("SameSite=lax", response.headers["set-cookie"])
            self.assertIn("HttpOnly", response.headers["set-cookie"])
            state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
            client.cookies.delete(COOKIE)
            callback = client.get(CALLBACK, params={"state":state,"code":"offline-code"})
            self.assertEqual(callback.status_code, 303)
            self.assertEqual(callback.headers["location"], "/notebook?google=connected")
            self.assertEqual(callback.headers["referrer-policy"], "no-referrer")
            self.assertIsNotNone(repository.get_google_connection("alice"))
