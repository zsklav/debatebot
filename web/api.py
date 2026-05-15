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

Session storage: in-memory dict keyed by UUID. This is fine for a single-
process demo; production would use Redis (so sessions survive restarts and
multi-worker scaling). LEARNINGS.md covers this tradeoff.
"""

import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from debate import Debate
from find_hole import find_hole
from personas import PERSONAS
from scorer import score_argument

load_dotenv()

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    raise RuntimeError("GROQ_API_KEY not set; see .env.example")

client = Groq(api_key=api_key)

# Session store. In-memory; survives only as long as the process. Production
# would back this with Redis. The dict is keyed by session_id (UUID string).
sessions: dict[str, Debate] = {}

app = FastAPI(title="DebateBot", version="1.0")

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
def create_debate(req: CreateDebateRequest) -> CreateDebateResponse:
    if req.persona_key not in PERSONAS:
        raise HTTPException(400, f"Unknown persona '{req.persona_key}'")
    session_id = str(uuid4())
    sessions[session_id] = Debate(client, req.persona_key)
    return CreateDebateResponse(
        session_id=session_id,
        persona_name=PERSONAS[req.persona_key]["name"],
    )


def _get(sid: str) -> Debate:
    debate = sessions.get(sid)
    if not debate:
        raise HTTPException(404, "Session not found (it may have expired on restart)")
    return debate


class TurnRequest(BaseModel):
    message: str


class TurnResponse(BaseModel):
    reply: str
    turn_count: int
    total_tokens: int


@app.post("/api/debate/{sid}/turn", response_model=TurnResponse)
def turn(sid: str, req: TurnRequest) -> TurnResponse:
    debate = _get(sid)
    if not req.message.strip():
        raise HTTPException(400, "Empty message")
    reply = debate.respond(req.message)
    return TurnResponse(
        reply=reply,
        turn_count=debate.turn_count(),
        total_tokens=debate.total_tokens,
    )


@app.get("/api/debate/{sid}")
def get_debate(sid: str) -> dict:
    debate = _get(sid)
    return {
        "persona_key": debate.persona_key,
        "persona_name": debate.persona["name"],
        "transcript": debate.transcript(),
        "turn_count": debate.turn_count(),
        "total_tokens": debate.total_tokens,
    }


@app.post("/api/debate/{sid}/score")
def score(sid: str) -> dict:
    debate = _get(sid)
    if debate.turn_count() == 0:
        raise HTTPException(400, "No debate yet")
    result = score_argument(client, debate.transcript())
    return result.model_dump()


@app.post("/api/debate/{sid}/findhole")
def hole_in_debate(sid: str) -> dict:
    debate = _get(sid)
    if debate.turn_count() == 0:
        raise HTTPException(400, "No debate yet")
    weakness = find_hole(client, debate.transcript())
    return weakness.model_dump()


class StandaloneFindholeRequest(BaseModel):
    argument: str


@app.post("/api/findhole")
def hole_standalone(req: StandaloneFindholeRequest) -> dict:
    if not req.argument.strip():
        raise HTTPException(400, "Empty argument")
    weakness = find_hole(client, req.argument)
    return weakness.model_dump()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "active_sessions": len(sessions)}
