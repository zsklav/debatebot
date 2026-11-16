"""Google authorization, read-only meeting selection and private import jobs."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from app.auth import COOKIE, token_hash
from app.core.persistence import SessionConflict
from app.review.models import StrictModel
from .google_config import CALLBACK, GoogleError, configuration_status
from .google_oauth import BIND_COOKIE
from .meet_client import MeetClient
from .meet_import import public_job


class ImportRequest(StrictModel):
    conference_record: str = Field(min_length=1, max_length=240)


class ImportAction(StrictModel):
    action: Literal["retry", "cancel"]


class ConfirmImport(StrictModel):
    title: str = Field(min_length=2, max_length=100)
    speaker_roles: dict[str, Literal["Candidate", "Interviewer", "Exclude"]] = Field(max_length=12)


def build_router(repository, require_user, oauth, imports):
    routes = APIRouter(prefix="/api/integrations/google", tags=["Google Meet import"])

    def owned(identifier, owner):
        row = imports.owned(str(identifier), owner)
        if row is None:
            raise HTTPException(404, "Import not found")
        return row

    def failure(error):
        code = 503 if error.code == "setup" else 502 if error.retryable else 400
        raise HTTPException(code, str(error))

    @routes.get("/status")
    def status(owner=Depends(require_user)):
        connection = repository.get_google_connection(owner)
        return {**configuration_status(), "connected":connection is not None,
                "worker_running":imports.running, "worker_error":imports.worker_error,
                "storage":repository.mode, "demo_available":True}

    @routes.post("/connect")
    def connect(request: Request, response: Response, owner=Depends(require_user)):
        try:
            config = oauth.config()
            if str(request.base_url).rstrip("/") != config.origin:
                raise GoogleError("origin", "Open the app at the configured Google callback origin before connecting")
            url, binding = oauth.begin(owner, token_hash(request.cookies.get(COOKIE, "")))
            response.set_cookie(BIND_COOKIE, binding, max_age=600, httponly=True, secure=config.redirect_uri.startswith("https:"),
                                samesite="lax", path=CALLBACK)
            return {"authorization_url":url}
        except GoogleError as error:
            failure(error)

    @routes.get("/callback")
    def callback(request: Request, state: str = Query(default="", max_length=200),
                 code: str = Query(default="", max_length=4096), error: str = Query(default="", max_length=100)):
        outcome = "connected"
        try:
            oauth.finish(state, request.cookies.get(BIND_COOKIE, ""), code, denied=bool(error))
        except GoogleError as failure:
            outcome = "denied" if failure.code == "denied" else "connection_failed"
        response = RedirectResponse("/notebook?google=" + outcome, status_code=303)
        response.delete_cookie(BIND_COOKIE, path=CALLBACK)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @routes.post("/disconnect")
    def disconnect(owner=Depends(require_user)):
        try:
            oauth.disconnect(owner)
            return {"connected":False}
        except GoogleError as error:
            failure(error)

    @routes.get("/meetings")
    def meetings(code: str = Query(default="", max_length=200), owner=Depends(require_user)):
        try:
            return MeetClient(oauth).recent(owner, code)
        except GoogleError as error:
            failure(error)
        except ValueError as error:
            raise HTTPException(422, str(error))

    @routes.get("/jobs")
    def jobs(owner=Depends(require_user)):
        return {"jobs":[public_job(row) for row in repository.list_meet_imports(owner)], "limit":10}

    @routes.post("/jobs")
    def create(payload: ImportRequest, owner=Depends(require_user)):
        try:
            return public_job(imports.create(owner, payload.conference_record))
        except GoogleError as error:
            failure(error)
        except ValueError as error:
            raise HTTPException(422, str(error))

    @routes.post("/demo")
    def demo(owner=Depends(require_user)):
        return public_job(imports.create(owner, demo=True))

    @routes.get("/jobs/{identifier}")
    def get(identifier: UUID, owner=Depends(require_user)):
        return public_job(owned(identifier, owner))

    @routes.get("/jobs/{identifier}/prepare")
    def prepare(identifier: UUID, owner=Depends(require_user)):
        row = owned(identifier, owner)
        if row["status"] != "needs_speakers":
            raise HTTPException(409, "Transcript is not ready for speaker confirmation")
        return {"job_id":str(identifier), **row["payload"]}

    @routes.post("/jobs/{identifier}/complete")
    def complete(identifier: UUID, payload: ConfirmImport, owner=Depends(require_user)):
        owned(identifier, owner)
        try:
            result = imports.complete(str(identifier), owner, payload.title, payload.speaker_roles)
            notebook, reused = result
            return {**notebook, "import_reused":reused}
        except SessionConflict as error:
            raise HTTPException(409, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))

    @routes.post("/jobs/{identifier}/action")
    def action(identifier: UUID, payload: ImportAction, owner=Depends(require_user)):
        owned(identifier, owner)
        try:
            return public_job(repository.action_meet_import(str(identifier), owner, payload.action, imports.clock()))
        except SessionConflict as error:
            raise HTTPException(409, str(error))

    return routes
