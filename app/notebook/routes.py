"""Private notebook operations and an explicitly authorized, bounded AI digest."""

from uuid import UUID, uuid4
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.core.paths import ROOT
from app.storage import SessionConflict
from app.core.cache import AnalysisCache
from .models import DigestRequest, NotebookEdit, NotebookInput
from . import service
from . import digest


def build_router(repository, require_user, client=None):
    routes = APIRouter(prefix="/api/notebooks", tags=["interview notebooks"])
    digest_cache = AnalysisCache(max_entries=16, ttl=600)

    def owned(identifier, owner):
        record = repository.get_notebook(str(identifier), owner_id=owner)
        if record is None:
            raise HTTPException(404, "Notebook not found")
        return record

    @routes.get("")
    def recent(owner=Depends(require_user)):
        return {"notebooks": repository.list_notebooks(owner_id=owner), "limit": 20}

    @routes.get("/sample")
    def sample(owner=Depends(require_user)):
        return {"title": "Synthetic caching interview", "format": "vtt",
                "content": (ROOT / "examples/interview_sample.vtt").read_text(encoding="utf-8")}

    @routes.get("/capabilities")
    def capabilities(owner=Depends(require_user)):
        try:
            return {"ai_digest_configured":client is not None, "provider":"groq", "model":digest.model(), "max_model_requests":1}
        except ValueError:
            return {"ai_digest_configured":False, "detail":"AI model configuration is invalid", "max_model_requests":1}

    @routes.post("/preview")
    def preview(payload: NotebookInput, owner=Depends(require_user)):
        try:
            return service.preview(payload)
        except ValueError as error:
            raise HTTPException(422, str(error))

    @routes.post("")
    def create(payload: NotebookInput, owner=Depends(require_user)):
        try:
            document = service.build(payload)
        except ValueError as error:
            raise HTTPException(422, str(error))
        identifier = str(uuid4())
        repository.create_notebook(identifier, document, owner_id=owner)
        return owned(identifier, owner)

    @routes.get("/{identifier}")
    def get(identifier: UUID, owner=Depends(require_user)):
        return owned(identifier, owner)

    @routes.patch("/{identifier}")
    def edit(identifier: UUID, payload: NotebookEdit, owner=Depends(require_user)):
        current = owned(identifier, owner)
        if current["revision"] != payload.revision:
            raise HTTPException(409, "Notebook changed. Reopen it before saving your edits")
        try:
            document, changes = service.apply_edit(current["document"], payload)
            if changes:
                repository.update_notebook(str(identifier), document, payload.revision, changes, owner_id=owner)
        except ValueError as error:
            raise HTTPException(422, str(error))
        except SessionConflict:
            raise HTTPException(409, "Notebook changed. Reopen it before saving your edits")
        return owned(identifier, owner)

    @routes.get("/{identifier}/history")
    def history(identifier: UUID, owner=Depends(require_user)):
        owned(identifier, owner)
        return {"revisions": repository.notebook_history(str(identifier), owner_id=owner), "limit": 20}

    @routes.post("/{identifier}/digest")
    def generate_digest(identifier: UUID, payload: DigestRequest, owner=Depends(require_user)):
        current = owned(identifier, owner)
        if not payload.consent_to_external_processing:
            raise HTTPException(400, "Authorize sending the original transcript to the configured model before generating AI notes")
        if client is None:
            raise HTTPException(503, "Configure GROQ_API_KEY before generating AI notes")
        document = current["document"]
        try:
            chosen = digest.model()
        except ValueError:
            raise HTTPException(503, "AI model configuration is invalid")
        existing = document.get("ai_digest")
        if (existing and existing["source_hash"] == document["source_hash"] and existing["model"] == chosen
                and existing.get("policy_version") == digest.policy_version()):
            return {**current, "digest_reused":True}
        if current["revision"] != payload.revision:
            raise HTTPException(409, "Notebook changed. Reopen it before generating AI notes")
        try:
            result = digest_cache.run((owner, document["source_hash"], chosen, digest.POLICY), lambda: digest.generate(client, document))
        except Exception:
            raise HTTPException(502, "AI notes request or exact-source validation failed. No new digest was saved; no automatic retry was made") from None
        updated = deepcopy(document)
        updated["ai_digest"] = result
        try:
            repository.update_notebook(str(identifier), updated, payload.revision, [{"field":"ai_digest", "before":"",
                "after":"\n".join(point["text"] for point in result["points"])}], owner_id=owner)
        except SessionConflict:
            raise HTTPException(409, "Notebook changed while AI notes were generated. Reload and retry; the completed digest is cached for ten minutes")
        return {**owned(identifier, owner), "digest_reused":False}

    @routes.get("/{identifier}/export")
    def export(identifier: UUID, owner=Depends(require_user)):
        record = owned(identifier, owner)
        return Response(service.markdown(record), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="interview-notebook-{identifier}.md"'})

    return routes
