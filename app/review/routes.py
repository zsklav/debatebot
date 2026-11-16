"""Account-scoped review endpoints. Demo outputs never masquerade as live models."""

import hashlib
import json
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


def build_router(repository, require_user):
    routes = APIRouter(prefix="/api/reviews", tags=["post-interview review"])
    cache = AnalysisCache(max_entries=16, ttl=600)

    @routes.get("/configuration")
    def settings(owner=Depends(require_user)):
        try:
            models = [r.public() for r in configuration()]
            return {"configured":True, "models":models, "max_model_requests":3}
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
        except ReviewConfigurationError as error:
            raise HTTPException(503, str(error))
        identity = json.dumps({"payload":payload.model_dump(), "models":[s.public() for s in specs] if specs else "scripted-v1"}, sort_keys=True)
        key = (owner, hashlib.sha256(identity.encode()).hexdigest())
        def compute():
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
