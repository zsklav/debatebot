"""Account-scoped review endpoints. Demo outputs never masquerade as live models."""

from datetime import datetime, timezone
import hashlib
import json
import os
from threading import Lock
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException

from app.core.cache import AnalysisCache
from .models import ReviewInput
from .evidence import prepare
from .providers import configuration, ModelAdapter, ReviewConfigurationError
from .council import evaluate
from . import demo


class PartialReview(RuntimeError):
    def __init__(self, report):
        self.report = report


def daily_limit():
    """POST_REVIEW_DAILY_LIMIT: live reviews per UTC day for this process. 0 means no cap."""
    value = os.environ.get("POST_REVIEW_DAILY_LIMIT", "0").strip()
    if not value.isdigit() or int(value) > 1000:
        raise ReviewConfigurationError("POST_REVIEW_DAILY_LIMIT must be a whole number from 0 to 1000")
    return int(value)


class DailyBudget:
    """Counts live reviews per UTC day. Process memory only: a restart resets the count."""

    def __init__(self, clock=lambda: datetime.now(timezone.utc).date()):
        self.clock, self.lock = clock, Lock()
        self.day, self.used = None, 0

    def _roll(self):
        today = self.clock()
        if today != self.day:
            self.day, self.used = today, 0

    def remaining(self, limit):
        with self.lock:
            self._roll()
            return None if limit == 0 else max(0, limit - self.used)

    def take(self, limit):
        with self.lock:
            self._roll()
            if limit and self.used >= limit:
                return False
            self.used += 1
            return True


def build_router(repository, require_user):
    routes = APIRouter(prefix="/api/reviews", tags=["post-interview review"])
    cache = AnalysisCache(max_entries=16, ttl=600)
    budget = DailyBudget()

    @routes.get("/configuration")
    def settings(owner=Depends(require_user)):
        try:
            models = [r.public() for r in configuration()]
            limit = daily_limit()
            return {"configured":True, "models":models, "max_model_requests":3,
                    "daily_limit":limit, "daily_remaining":budget.remaining(limit)}
        except ReviewConfigurationError as error:
            return {"configured":False, "models":[], "detail":str(error), "max_model_requests":3}

    @routes.post("/prepare")
    def prepare_review(payload: ReviewInput, owner=Depends(require_user)):
        try:
            return {"sources":[source.model_dump() for source in prepare(payload)],
                    "model_requests":0, "scope":"Local normalization only; no assessment made."}
        except ValueError as error:
            raise HTTPException(422, str(error))

    def execute(payload, owner, scripted):
        if payload.notebook_id:
            notebook = repository.get_notebook(payload.notebook_id, owner_id=owner)
            if notebook is None:
                raise HTTPException(404, "Notebook not found")
            if payload.transcript != notebook["document"]["transcript"]:
                raise HTTPException(422, "Linked reviews must use the notebook's original normalized transcript")
        try:
            prepare(payload)  # Invalid transcripts fail before any model request.
        except ValueError as error:
            raise HTTPException(422, str(error))
        if not scripted and not payload.consent_to_external_processing:
            raise HTTPException(400, "Confirm authorization to process this transcript with the configured external model services")
        if not scripted and not payload.evidence_confirmed:
            raise HTTPException(400, "Review the normalized source preview before requesting model assessment")
        try:
            specs = None if scripted else configuration()
            limit = 0 if scripted else daily_limit()
        except ReviewConfigurationError as error:
            raise HTTPException(503, str(error))
        identity = json.dumps({"payload":payload.model_dump(), "models":[s.public() for s in specs] if specs else "scripted-v1"}, sort_keys=True)
        key = (owner, hashlib.sha256(identity.encode()).hexdigest())
        def compute():
            # Only new live runs spend the budget; cached repeats and the sample are free.
            if not scripted and not budget.take(limit):
                raise HTTPException(429, "Today's live review limit for this deployment is used up. Try the sample, or come back tomorrow.")
            adapters = [demo.ScriptedAdapter(role) for role in ("a", "b", "judge")] if scripted else [ModelAdapter(s) for s in specs]
            try:
                report = evaluate(payload, adapters, demo=scripted)
            except (OSError, RuntimeError, ValueError, ImportError):
                raise HTTPException(503, "Local review retrieval is unavailable. Install retrieval dependencies and cache the embedding model first.")
            run_id = str(uuid4())
            report.update(run_id=run_id, storage=repository.mode)
            if payload.notebook_id:
                report.update(notebook_id=payload.notebook_id, notebook_source_hash=notebook["document"]["source_hash"])
            # Only store the transcript/profile in the already owner-scoped run.
            repository.save_run(run_id, "post_interview_review", payload.transcript, report, owner_id=owner)
            if report["status"] == "partial":
                raise PartialReview(report)  # Saved, but not cached as a successful assessment.
            return report
        try:
            return cache.run(key, compute)
        except PartialReview as partial:
            return partial.report

    @routes.post("/run")
    def run_review(payload: ReviewInput, owner=Depends(require_user)):
        return execute(payload, owner, False)

    @routes.post("/demo")
    def sample_review(owner=Depends(require_user)):
        return execute(demo.payload(), owner, True)

    @routes.get("/{run_id}")
    def reopen(run_id: str, owner=Depends(require_user)):
        record = repository.get_run(run_id, owner_id=owner)
        if not record or record["kind"] != "post_interview_review":
            raise HTTPException(404, "Review not found")
        return record["result"]

    return routes
