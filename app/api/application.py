"""Application composition. Feature routers receive their resources explicitly."""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq

from app.api.debates import build_router as debate_router
from app.api.evidence import build_router as evidence_router
from app.auth import build_auth
from app.core.paths import FRONTEND, ROOT
from app.notebook.routes import build_router as notebook_router
from app.integrations.google_oauth import CallbackLogFilter, GoogleOAuth
from app.integrations.meet_import import MeetImports
from app.integrations.routes import build_router as google_router
from app.review.routes import build_router as review_router
from app.storage import SessionConflict, StorageUnavailable, repository_from_env


def create_app(*, repository=None, client=None, load_environment=True, start_workers=True):
    """Build isolated application state; imports alone do not connect to storage."""
    if load_environment:
        load_dotenv(ROOT / ".env")
    if repository is None:
        repository = repository_from_env()
    owns_client = False
    if client is None:
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if key and key != "your_groq_api_key_here":
            client = Groq(api_key=key, max_retries=0, timeout=30)
            owns_client = True

    google = GoogleOAuth(repository)
    imports = MeetImports(repository, google)
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, CallbackLogFilter) for item in access_logger.filters):
        access_logger.addFilter(CallbackLogFilter())

    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(imports.run()) if start_workers else None
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            if owns_client:
                client.close()

    app = FastAPI(title="DebateBot · Review Council", version="2.0", lifespan=lifespan)
    app.state.repository = repository
    app.state.google_oauth = google
    app.state.meet_imports = imports
    auth_router, require_user = build_auth(repository)
    app.include_router(auth_router)
    app.include_router(debate_router(repository, client, require_user))
    app.include_router(evidence_router(repository, client, require_user))
    app.include_router(review_router(repository, require_user))
    app.include_router(notebook_router(repository, require_user, client))
    app.include_router(google_router(repository, require_user, google, imports))
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Validation errors must not echo transcripts or credentials.
        detail = [
            {key: item[key] for key in ("loc", "msg", "type") if key in item}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(StorageUnavailable)
    async def storage_error(request, error):
        return JSONResponse(status_code=503, content={
            "detail": "Application storage unavailable; check database setup and migrations",
            "code": "storage_unavailable",
        })

    @app.exception_handler(SessionConflict)
    async def session_conflict(request, error):
        return JSONResponse(status_code=409, content={
            "detail": "Session changed during generation; reload before retrying",
        })

    @app.middleware("http")
    async def private_response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    def workspace():
        return FileResponse(FRONTEND / "index.html")

    @app.get("/review", include_in_schema=False)
    def review_workspace():
        return FileResponse(FRONTEND / "post-review.html")

    @app.get("/notebook", include_in_schema=False)
    def interview_notebook():
        return FileResponse(FRONTEND / "notebook.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "storage": repository.mode}

    return app
