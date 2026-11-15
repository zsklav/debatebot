"""Local accounts with slow salted password hashes and revocable opaque cookies."""

from collections import deque
import hashlib
import hmac
import os
import re
import secrets
from threading import RLock
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

COOKIE = "debatebot_session"
ITERATIONS = 600_000
SESSION_SECONDS = 7 * 24 * 3600


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=12, max_length=128)


def build_auth(repository):
    router = APIRouter(prefix="/api/auth")
    required = os.environ.get("DEBATEBOT_AUTH_REQUIRED", "1") != "0"
    public_origin = os.environ.get("DEBATEBOT_PUBLIC_ORIGIN", "").rstrip("/")
    secure = os.environ.get("DEBATEBOT_COOKIE_SECURE", "1" if public_origin.startswith("https:") else "0") == "1"
    attempts, lock = {}, RLock()
    dummy = password_hash("dummy-password-never-used")

    def check_origin(request):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        origin = request.headers.get("origin")
        expected = public_origin or str(request.base_url).rstrip("/")
        if (origin and origin.rstrip("/") != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-origin write rejected")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Use application/json for write requests")

    def throttle(request):
        # Per-process bound, not a distributed rate limiter. Do not trust forwarded IPs.
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with lock:
            if address not in attempts and len(attempts) >= 4096:
                attempts.pop(next(iter(attempts)))
            bucket = attempts.setdefault(address, deque())
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= 10:
                raise HTTPException(429, "Too many authentication attempts; retry later", headers={"Retry-After": "60"})
            bucket.append(now)

    def user_from_cookie(request):
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 256:
            return None
        return repository.auth_user(token_hash(token), int(time.time()))

    def require_user(request: Request):
        if not required:
            return "local-demo"
        check_origin(request)
        user = user_from_cookie(request)
        if not user:
            raise HTTPException(401, "Sign in to continue")
        return user["id"]

    def login_cookie(request, response, user_id):
        old = request.cookies.get(COOKIE)
        if old:
            repository.revoke_auth(token_hash(old))
        token = secrets.token_urlsafe(32)
        repository.save_auth(token_hash(token), user_id, int(time.time()) + SESSION_SECONDS)
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=secure, samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"

    @router.post("/register")
    def register(data: Credentials, request: Request, response: Response):
        check_origin(request)
        throttle(request)
        username = data.username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{3,40}", username):
            raise HTTPException(422, "Username must contain 3-40 letters, digits, underscores or hyphens")
        user_id = str(uuid4())
        if not repository.create_user(user_id, username, password_hash(data.password)):
            raise HTTPException(409, "Username unavailable")
        login_cookie(request, response, user_id)
        return {"id": user_id, "username": username}

    @router.post("/login")
    def login(data: Credentials, request: Request, response: Response):
        check_origin(request)
        throttle(request)
        user = repository.user_by_name(data.username.strip().lower())
        valid = verify_password(data.password, user["password_hash"] if user else dummy)
        if not valid or user is None:
            raise HTTPException(401, "Invalid username or password")
        login_cookie(request, response, user["id"])
        return {"id": user["id"], "username": user["username"]}

    @router.post("/logout")
    def logout(request: Request, response: Response):
        check_origin(request)
        token = request.cookies.get(COOKIE)
        if token:
            repository.revoke_auth(token_hash(token))
        response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return {"signed_out": True}

    @router.get("/me")
    def me(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        user = user_from_cookie(request)
        return {"authenticated": bool(user) or not required, "required": required,
                "user": user if required else {"username": "local-demo", "id": "local-demo"}}

    return router, require_user
