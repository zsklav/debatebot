"""OAuth code+PKCE flow, browser/session binding, encrypted grants and refresh."""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import logging
import secrets
from threading import RLock
import time
from urllib.parse import urlencode
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
import httpx

from app.auth import token_hash
from .google_config import CALLBACK, GoogleConfig, GoogleError, SCOPE

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
BIND_COOKIE = "debatebot_google_flow"


class CallbackLogFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn otherwise logs the authorization code in the query string.
        if isinstance(record.args, tuple) and len(record.args) == 5 and str(record.args[2]).startswith(CALLBACK):
            args = list(record.args)
            args[2] = CALLBACK
            record.args = tuple(args)
        return True


@dataclass(repr=False)
class PendingAuthorization:
    owner: str
    session_hash: str
    binding_hash: str
    verifier: str = field(repr=False)
    expires_at: float


class GoogleOAuth:
    def __init__(self, repository, *, config=GoogleConfig.from_env, transport=None, clock=time.time):
        self.repository, self.config, self.transport, self.clock = repository, config, transport, clock
        self.pending, self.lock = {}, RLock()

    def http_json(self, method, url, *, deadline=None, **kwargs):
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=5), transport=self.transport, follow_redirects=False) as client:
                with client.stream(method, url, **kwargs) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        if deadline is not None and time.monotonic() >= deadline:
                            raise GoogleError("timeout", "The Meet check exceeded its time budget; it can be retried", retryable=True)
                        size += len(chunk)
                        if size > 2_000_000:
                            raise GoogleError("limit", "Google response exceeds the local import budget")
                        chunks.append(chunk)
                    try:
                        data = json.loads(b"".join(chunks))
                    except (ValueError, RecursionError):
                        raise GoogleError("response", "Google returned an unreadable response") from None
                    if not isinstance(data, dict):
                        raise GoogleError("response", "Google returned an unexpected response")
                    return response.status_code, data
        except httpx.HTTPError:
            raise GoogleError("network", "Could not reach Google. The import can be retried", retryable=True) from None

    def begin(self, owner, session_hash):
        config = self.config()
        user = self.repository.auth_user(session_hash, int(self.clock()))
        if owner is None or not user or user["id"] != owner:
            raise GoogleError("session", "Sign in with a local account before connecting Google")
        state, binding, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        with self.lock:
            self.pending = {key: value for key, value in self.pending.items() if value.expires_at > self.clock()}
            if len(self.pending) >= 256:
                raise GoogleError("busy", "Too many unfinished connection attempts; retry shortly")
            self.pending[token_hash(state)] = PendingAuthorization(owner, session_hash, token_hash(binding), verifier, self.clock() + 600)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = {"client_id": config.client_id, "redirect_uri": config.redirect_uri, "response_type": "code",
                 "scope": SCOPE, "access_type": "offline", "prompt": "consent select_account", "state": state,
                 "code_challenge": challenge, "code_challenge_method": "S256"}
        return AUTH_URL + "?" + urlencode(query), binding

    def finish(self, state, binding, code=None, denied=False):
        with self.lock:
            pending = self.pending.get(token_hash(state))
            if (not pending or pending.expires_at <= self.clock() or
                    not hmac.compare_digest(pending.binding_hash, token_hash(binding))):
                raise GoogleError("state", "Connection attempt expired or belongs to another browser")
            del self.pending[token_hash(state)]
            user = self.repository.auth_user(pending.session_hash, int(self.clock()))
            if not user or user["id"] != pending.owner:
                raise GoogleError("session", "Sign in again before connecting Google")
            if denied or not code:
                raise GoogleError("denied", "Google connection was not authorized")
            config = self.config()
            status, data = self.http_json("POST", TOKEN_URL, data={"client_id": config.client_id,
                "client_secret": config.client_secret, "redirect_uri": config.redirect_uri, "grant_type": "authorization_code",
                "code": code, "code_verifier": pending.verifier})
            if status != 200:
                raise GoogleError("authorization", "Google did not accept the authorization code; reconnect")
            grant = self._grant(data)
            grant["owner_id"] = pending.owner
            self.repository.save_google_connection(pending.owner, self._encrypt(grant), grant["generation"], grant["scope"])
            return pending.owner

    def _grant(self, data, previous=None):
        previous = previous or {}
        scope_value = data.get("scope", " ".join(previous.get("scope", [])))
        if not isinstance(scope_value, str):
            raise GoogleError("scope", "Google returned an invalid scope response")
        scopes = scope_value.split()
        if SCOPE not in scopes or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise GoogleError("scope", "Google Meet read access was not granted; reconnect and approve the requested permission")
        refresh = data.get("refresh_token") or previous.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise GoogleError("refresh", "Offline access was not returned. Reconnect with consent")
        try:
            expiry = int(data["expires_in"])
        except (KeyError, ValueError, TypeError):
            raise GoogleError("response", "Google did not return a valid token lifetime") from None
        if not 1 <= expiry <= 86400:
            raise GoogleError("response", "Google returned an unsupported token lifetime")
        return {"access_token": data["access_token"], "refresh_token": refresh,
                "expires_at": self.clock() + expiry, "scope": scopes,
                "generation": previous.get("generation") or str(uuid4()), "owner_id":previous.get("owner_id")}

    def _encrypt(self, grant):
        return Fernet(self.config().encryption_key.encode()).encrypt(json.dumps(grant).encode()).decode()

    def _decrypt(self, encrypted, owner):
        try:
            grant = json.loads(Fernet(self.config().encryption_key.encode()).decrypt(encrypted.encode()))
            if not isinstance(grant, dict) or grant.get("owner_id") != owner:
                raise ValueError("Connection owner mismatch")
            return grant
        except (InvalidToken, ValueError, TypeError):
            raise GoogleError("encryption", "The saved Google connection cannot be read. Restore the original encryption key or reconnect") from None

    def access_token(self, owner, *, force=False):
        with self.lock:
            row = self.repository.get_google_connection(owner)
            if row is None:
                raise GoogleError("reconnect", "Connect Google before importing a real meeting")
            grant = self._decrypt(row["encrypted_tokens"], owner)
            if not force and grant["expires_at"] > self.clock() + 60:
                return grant["access_token"]
            config = self.config()
            status, data = self.http_json("POST", TOKEN_URL, data={"client_id": config.client_id,
                "client_secret": config.client_secret, "grant_type": "refresh_token", "refresh_token": grant["refresh_token"]})
            if status == 400 and data.get("error") == "invalid_grant":
                self.repository.delete_google_connection(owner, grant["generation"])
                raise GoogleError("reconnect", "Google access expired or was revoked. Reconnect to continue")
            if status != 200:
                raise GoogleError("refresh", "Google token refresh failed; retry later", retryable=status == 429 or status >= 500)
            updated = self._grant(data, grant)
            if not self.repository.refresh_google_connection(owner, grant["generation"], self._encrypt(updated)):
                raise GoogleError("reconnect", "The Google connection changed; retry with the current connection")
            return updated["access_token"]

    def disconnect(self, owner):
        with self.lock:
            row = self.repository.get_google_connection(owner)
            if row is None:
                return
            grant = self._decrypt(row["encrypted_tokens"], owner)
            try:
                with httpx.Client(timeout=15, transport=self.transport, follow_redirects=False) as client:
                    response = client.post(REVOKE_URL, data={"token": grant["refresh_token"]})
                if response.status_code not in {200, 400}:
                    raise GoogleError("revoke", "Google could not confirm disconnection; retry later", retryable=True)
            except httpx.HTTPError:
                raise GoogleError("revoke", "Google could not confirm disconnection; retry later", retryable=True) from None
            self.repository.delete_google_connection(owner, grant["generation"])
