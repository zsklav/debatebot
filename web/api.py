"""
Day 7: FastAPI web layer around DebateBot.

Endpoints:
  GET  /                              -> serves the chat UI
  GET  /api/personas                  -> list available personas
  POST /api/debate                    -> create new debate session
  POST /api/debate/{sid}/turn         -> send user turn, get bot reply
  GET  /api/debate/{sid}              -> get transcript + token count
  POST /api/debate/{sid}/score        -> 4-axis argument score
  POST /api/debate/{sid}/findhole     -> single weakest claim analysis
  POST /api/findhole                  -> standalone (no session needed)

Session/run storage: PostgreSQL when DATABASE_URL is configured, otherwise explicit
in-memory demo mode. Apply migrations before starting the persistent app.
"""

import os
import hashlib
import sys
from pathlib import Path
from uuid import uuid4
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq, APIError
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from debate import Debate
from find_hole import find_hole
from personas import PERSONAS
from scorer import score_argument
from evidence import analyze_argument
from research import research_claim
from evidence_runtime import EvidenceUnavailable, open_evidence_index
from storage import repository_from_env, StorageUnavailable, SessionConflict
from auth import build_auth
from web_retrieval import WebEvidence, WebUnavailable
from analysis_cache import AnalysisCache
from model_config import get_model

load_dotenv()

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    raise RuntimeError("GROQ_API_KEY not set; see .env.example")

# Keep network retries explicit: one user action must not silently become three
# HTTP attempts. Structured-output repair still has its own bounded retry.
client = Groq(api_key=api_key, max_retries=0, timeout=30)
analysis_cache = AnalysisCache()

# Configured database failures never fall back silently to volatile memory.
repository = repository_from_env()

app = FastAPI(title="DebateBot", version="1.0")
auth_router, require_user = build_auth(repository)
app.include_router(auth_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # FastAPI normally echoes invalid input; never echo credentials in errors.
    return JSONResponse(status_code=422, content={"detail": [
        {key: item[key] for key in ("loc", "msg", "type") if key in item}
        for item in error.errors()]})


@app.middleware("http")
async def private_response_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StorageUnavailable)
async def storage_error(request, error):
    return JSONResponse(status_code=503, content={"detail": "Application storage unavailable; check database setup and migrations", "code": "storage_unavailable"})


@app.exception_handler(SessionConflict)
async def session_conflict(request, error):
    return JSONResponse(status_code=409, content={"detail": "Session changed during generation; reload before retrying"})

WEB_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(WEB_DIR / "static" / "index.html")


@app.get("/api/personas")
def list_personas() -> dict:
    return {
        "personas": [
            {"key": key, "name": p["name"], "tagline": p["tagline"]}
            for key, p in PERSONAS.items()
        ]
    }


class CreateDebateRequest(BaseModel):
    persona_key: str


class CreateDebateResponse(BaseModel):
    session_id: str
    persona_name: str


@app.post("/api/debate", response_model=CreateDebateResponse)
def create_debate(req: CreateDebateRequest, owner: str = Depends(require_user)) -> CreateDebateResponse:
    if req.persona_key not in PERSONAS:
        raise HTTPException(400, f"Unknown persona '{req.persona_key}'")
    session_id = str(uuid4())
    repository.create_session(session_id, Debate(client, req.persona_key), owner_id=owner)
    return CreateDebateResponse(
        session_id=session_id,
        persona_name=PERSONAS[req.persona_key]["name"],
    )


def _get(sid: str, owner: str) -> Debate:
    stored = repository.load_session(sid, client, owner_id=owner)
    if not stored:
        raise HTTPException(404, "Session not found")
    return stored[0]


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class TurnResponse(BaseModel):
    reply: str
    turn_count: int
    total_tokens: int


@app.post("/api/debate/{sid}/turn", response_model=TurnResponse)
def turn(sid: str, req: TurnRequest, owner: str = Depends(require_user)) -> TurnResponse:
    stored = repository.load_session(sid, client, owner_id=owner)
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


@app.get("/api/debate/{sid}")
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


@app.post("/api/debate/{sid}/score")
def score(sid: str, owner: str = Depends(require_user)) -> dict:
    debate = _get(sid, owner)
    if debate.turn_count() == 0:
        raise HTTPException(400, "No debate yet")
    transcript = debate.transcript()
    return cached_analysis(owner, "scorer", transcript,
                           lambda: score_argument(client, transcript).model_dump())


@app.post("/api/debate/{sid}/findhole")
def hole_in_debate(sid: str, owner: str = Depends(require_user)) -> dict:
    debate = _get(sid, owner)
    if debate.turn_count() == 0:
        raise HTTPException(400, "No debate yet")
    transcript = debate.transcript()
    return cached_analysis(owner, "findhole", transcript,
                           lambda: find_hole(client, transcript).model_dump())


class StandaloneFindholeRequest(BaseModel):
    argument: str = Field(min_length=1, max_length=4000)


@app.post("/api/findhole")
def hole_standalone(req: StandaloneFindholeRequest, owner: str = Depends(require_user)) -> dict:
    if not req.argument.strip():
        raise HTTPException(400, "Empty argument")
    return cached_analysis(owner, "findhole", req.argument,
                           lambda: find_hole(client, req.argument).model_dump())


def cached_analysis(owner, role, text, compute):
    key = (owner, role, get_model(role), hashlib.sha256(text.encode()).hexdigest())
    return analysis_cache.run(key, compute)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "storage": repository.mode}


class EvidenceFindholeRequest(BaseModel):
    argument: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=3, ge=1, le=5)


class ResearchRequest(BaseModel):
    claim: str = Field(min_length=1, max_length=4000)
    source: Literal["local", "web"] = "local"


@app.post("/api/research")
def research(req: ResearchRequest, owner: str = Depends(require_user)) -> dict:
    if not req.claim.strip():
        raise HTTPException(400, "Empty claim")
    try:
        if req.source == "web":
            web = WebEvidence()
            result = research_claim(client, web, req.claim, source_mode="web")
            result["search_provider"] = web.provider
        else:
            with open_evidence_index() as index:
                result = research_claim(client, index, req.claim)
        return _save_run("research", req.claim, result, owner)
    except WebUnavailable:
        return JSONResponse(status_code=503, content={"detail": "Web search unavailable; check provider setup", "code": "web_unavailable"})
    except EvidenceUnavailable:
        raise HTTPException(503, "Evidence index unavailable; run retrieval.py index")
    except StorageUnavailable:
        raise
    except (RuntimeError, APIError):
        raise HTTPException(502, "Research model request or output validation failed")
    except ValueError:
        raise HTTPException(422, "Research input exceeds supported limits")


@app.post("/api/findhole/evidence")
def hole_with_evidence(req: EvidenceFindholeRequest, owner: str = Depends(require_user)) -> dict:
    if not req.argument.strip():
        raise HTTPException(400, "Empty argument")
    try:
        with open_evidence_index() as index:
            result = analyze_argument(client, index, req.argument, limit=req.limit)
        return _save_run("evidence", req.argument, result, owner)
    except EvidenceUnavailable:
        raise HTTPException(503, "Evidence index unavailable; install retrieval dependencies and run retrieval.py index")
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


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, owner: str = Depends(require_user)):
    saved = repository.get_run(run_id, owner_id=owner)
    if saved is None:
        raise HTTPException(404, "Run not found")
    return saved
