"""HTTP contracts for evidence analysis, bounded research and saved runs."""

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from groq import APIError
from pydantic import BaseModel, Field

from app.api.dependencies import require_model_client
from app.evidence.assessment import analyze_argument
from app.evidence.research import research_claim
from app.evidence.runtime import EvidenceUnavailable, open_evidence_index
from app.evidence.web_retrieval import WebEvidence, WebUnavailable
from app.storage import StorageUnavailable


class EvidenceFindholeRequest(BaseModel):
    argument: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=3, ge=1, le=5)


class ResearchRequest(BaseModel):
    claim: str = Field(min_length=1, max_length=4000)
    source: Literal["local", "web"] = "local"


def build_router(repository, client, require_user):
    routes = APIRouter(tags=["evidence"])


    @routes.post("/api/research")
    def research(req: ResearchRequest, owner: str = Depends(require_user)) -> dict:
        if not req.claim.strip():
            raise HTTPException(400, "Empty claim")
        try:
            if req.source == "web":
                web = WebEvidence()
                result = research_claim(require_model_client(client), web, req.claim, source_mode="web")
                result["search_provider"] = web.provider
            else:
                with open_evidence_index() as index:
                    result = research_claim(require_model_client(client), index, req.claim)
            return _save_run("research", req.claim, result, owner)
        except WebUnavailable:
            return JSONResponse(status_code=503, content={"detail": "Web search unavailable; check provider setup", "code": "web_unavailable"})
        except EvidenceUnavailable:
            raise HTTPException(503, "Evidence index unavailable; run python -m app.evidence.retrieval index")
        except StorageUnavailable:
            raise
        except (RuntimeError, APIError):
            raise HTTPException(502, "Research model request or output validation failed")
        except ValueError:
            raise HTTPException(422, "Research input exceeds supported limits")


    @routes.post("/api/findhole/evidence")
    def hole_with_evidence(req: EvidenceFindholeRequest, owner: str = Depends(require_user)) -> dict:
        if not req.argument.strip():
            raise HTTPException(400, "Empty argument")
        try:
            with open_evidence_index() as index:
                result = analyze_argument(require_model_client(client), index, req.argument, limit=req.limit)
            return _save_run("evidence", req.argument, result, owner)
        except EvidenceUnavailable:
            raise HTTPException(503, "Evidence index unavailable; install retrieval dependencies and run python -m app.evidence.retrieval index")
        except StorageUnavailable:
            raise
        except (RuntimeError, APIError):
            raise HTTPException(502, "Model request or output validation failed; retry the analysis")
        except ValueError:
            raise HTTPException(422, "Argument or retrieved context exceeds evidence-analysis limits")


    def _save_run(kind, input_text, result, owner):
        run_id = str(uuid4())
        response = {**result, "run_id": run_id, "storage": repository.mode}
        repository.save_run(run_id, kind, input_text, response, owner_id=owner)
        return response


    @routes.get("/api/runs/{run_id}")
    def get_run(run_id: str, owner: str = Depends(require_user)):
        saved = repository.get_run(run_id, owner_id=owner)
        if saved is None:
            raise HTTPException(404, "Run not found")
        return saved

    return routes
