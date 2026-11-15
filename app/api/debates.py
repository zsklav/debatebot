"""HTTP contracts for persona debates and argument analysis."""

import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import require_model_client
from app.core.cache import AnalysisCache
from app.core.model_config import get_model
from app.debate.engine import Debate
from app.debate.find_hole import find_hole
from app.debate.personas import PERSONAS
from app.debate.scorer import score_argument


class CreateDebateRequest(BaseModel):
    persona_key: str


class CreateDebateResponse(BaseModel):
    session_id: str
    persona_name: str


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class TurnResponse(BaseModel):
    reply: str
    turn_count: int
    total_tokens: int


class StandaloneFindholeRequest(BaseModel):
    argument: str = Field(min_length=1, max_length=4000)


def build_router(repository, client, require_user):
    routes = APIRouter(tags=["debates"])
    analysis_cache = AnalysisCache()

    @routes.get("/api/personas")
    def list_personas():
        return {"personas": [
            {"key": key, "name": persona["name"], "tagline": persona["tagline"]}
            for key, persona in PERSONAS.items()
        ]}


    @routes.post("/api/debate", response_model=CreateDebateResponse)
    def create_debate(req: CreateDebateRequest, owner: str = Depends(require_user)) -> CreateDebateResponse:
        if req.persona_key not in PERSONAS:
            raise HTTPException(400, f"Unknown persona '{req.persona_key}'")
        session_id = str(uuid4())
        repository.create_session(session_id, Debate(require_model_client(client), req.persona_key), owner_id=owner)
        return CreateDebateResponse(
            session_id=session_id,
            persona_name=PERSONAS[req.persona_key]["name"],
        )


    def _get(sid: str, owner: str) -> Debate:
        stored = repository.load_session(sid, client, owner_id=owner)
        if not stored:
            raise HTTPException(404, "Session not found")
        return stored[0]


    @routes.post("/api/debate/{sid}/turn", response_model=TurnResponse)
    def turn(sid: str, req: TurnRequest, owner: str = Depends(require_user)) -> TurnResponse:
        stored = repository.load_session(sid, require_model_client(client), owner_id=owner)
        if not stored:
            raise HTTPException(404, "Session not found")
        debate, revision = stored
        if not req.message.strip():
            raise HTTPException(400, "Empty message")
        reply = debate.respond(req.message)
        repository.update_session(sid, debate, revision, owner_id=owner)
        return TurnResponse(
            reply=reply,
            turn_count=debate.turn_count(),
            total_tokens=debate.total_tokens,
        )


    @routes.get("/api/debate/{sid}")
    def get_debate(sid: str, owner: str = Depends(require_user)) -> dict:
        debate = _get(sid, owner)
        return {
            "persona_key": debate.persona_key,
            "persona_name": debate.persona["name"],
            "transcript": debate.transcript(),
            "turn_count": debate.turn_count(),
            "total_tokens": debate.total_tokens,
            "messages": debate.messages[1:],
        }


    @routes.post("/api/debate/{sid}/score")
    def score(sid: str, owner: str = Depends(require_user)) -> dict:
        debate = _get(sid, owner)
        if debate.turn_count() == 0:
            raise HTTPException(400, "No debate yet")
        transcript = debate.transcript()
        return cached_analysis(owner, "scorer", transcript,
                               lambda: score_argument(require_model_client(client), transcript).model_dump())


    @routes.post("/api/debate/{sid}/findhole")
    def hole_in_debate(sid: str, owner: str = Depends(require_user)) -> dict:
        debate = _get(sid, owner)
        if debate.turn_count() == 0:
            raise HTTPException(400, "No debate yet")
        transcript = debate.transcript()
        return cached_analysis(owner, "findhole", transcript,
                               lambda: find_hole(require_model_client(client), transcript).model_dump())


    @routes.post("/api/findhole")
    def hole_standalone(req: StandaloneFindholeRequest, owner: str = Depends(require_user)) -> dict:
        if not req.argument.strip():
            raise HTTPException(400, "Empty argument")
        return cached_analysis(owner, "findhole", req.argument,
                               lambda: find_hole(require_model_client(client), req.argument).model_dump())


    def cached_analysis(owner, role, text, compute):
        key = (owner, role, get_model(role), hashlib.sha256(text.encode()).hexdigest())
        return analysis_cache.run(key, compute)


    return routes
