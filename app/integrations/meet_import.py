"""Durable bounded polling and atomic transcript-to-notebook finalization."""

import asyncio
import logging
import time
from uuid import uuid4

from app.core.persistence import SessionConflict, StorageUnavailable
from app.notebook.models import NotebookInput
from app.notebook import service as notebook_service
from .google_config import GoogleError
from .meet_client import DemoMeetClient, MeetClient, NotReady, record_name

logger = logging.getLogger(__name__)


def public_job(row):
    return {key: row.get(key) for key in ("id", "provider", "conference_record", "status", "message", "attempts",
            "google_requests", "next_poll", "deadline", "notebook_id", "created_at", "updated_at")}


class MeetImports:
    def __init__(self, repository, oauth, *, clock=time.time, gateway=None):
        self.repository, self.oauth, self.clock = repository, oauth, clock
        self.gateway = gateway or (lambda: MeetClient(oauth))
        self.running = False
        self.worker_error = None

    def create(self, owner, conference=None, *, demo=False):
        if demo:
            conference = "conferenceRecords/synthetic-demo"
        else:
            record_name(conference)
            self.oauth.config()
            if self.repository.get_google_connection(owner) is None:
                raise GoogleError("reconnect", "Connect Google before importing a real meeting")
        return self.repository.create_meet_import(str(uuid4()), owner, "demo" if demo else "google", conference, self.clock())

    def owned(self, identifier, owner):
        return self.repository.get_meet_import(identifier, owner)

    def tick(self):
        row = self.repository.claim_meet_import(self.clock())
        if row is None:
            return False
        gateway = DemoMeetClient() if row["provider"] == "demo" else self.gateway()
        status, payload, message = "failed", None, "Import failed without creating a partial notebook"
        try:
            if self.clock() >= row["deadline"] or row["attempts"] > 30:
                status, message = "expired", "Stopped waiting for a transcript. Check transcription availability, then retry or import a file"
            else:
                if row["provider"] == "demo" and row["attempts"] == 1:
                    raise NotReady("Demo: simulating transcript preparation; no Google requests")
                payload = gateway.fetch(row["owner_id"], row["conference_record"])
                notebook_service.preview(NotebookInput.model_validate(payload["notebook_input"]))
                status, message = "needs_speakers", "Transcript ready. Confirm speakers to create the notebook"
        except GoogleError as error:
            status = "waiting" if error.retryable else "failed"
            message = str(error)
        except (ValueError, TypeError, KeyError):
            message = "The transcript could not be imported completely. Check its size, speakers and timestamps"
        except Exception as error:
            logger.error("Meet import failed (%s)", type(error).__name__)
        self.repository.finish_meet_import(row["id"], row["lease_token"], status, now=self.clock(), message=message,
            payload=payload if status == "needs_speakers" else None, reads=gateway.requests,
            next_poll=self.clock() + (1 if row["provider"] == "demo" else min(120, 30 * max(1, row["attempts"] // 5))))
        return True

    async def run(self):
        self.running = True
        try:
            while True:
                try:
                    await asyncio.to_thread(self.tick)
                    self.worker_error = None
                except StorageUnavailable:
                    self.worker_error = "Import storage unavailable; apply migrations and check PostgreSQL"
                await asyncio.sleep(2 if not self.worker_error else 10)
        finally:
            self.running = False

    def complete(self, identifier, owner, title, roles):
        row = self.owned(identifier, owner)
        if row is None:
            return None
        if row["notebook_id"]:
            return self.repository.get_notebook(row["notebook_id"], owner_id=owner), True
        if row["status"] != "needs_speakers":
            raise SessionConflict("Transcript is not ready for speaker confirmation")
        payload = row["payload"]
        source = NotebookInput.model_validate({**payload["notebook_input"], "title":title, "speaker_roles":roles})
        document = notebook_service.build(source)
        document["import_provenance"] = {**payload["provenance"], "job_id":identifier}
        notebook_id, reused = self.repository.complete_meet_import(identifier, owner, document)
        return self.repository.get_notebook(notebook_id, owner_id=owner), reused
