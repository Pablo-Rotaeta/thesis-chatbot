"""
Chat API Router
===============
POST /api/chat/message   — send a message, get a reply
POST /api/chat/start     — create a new session
POST /api/chat/end       — close a session with task success flag
"""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional

from app.services.llm_adapters import get_adapter
from app.services.dialog_managers import UnconstrainedDialogManager, SkillBasedDialogManager
from app.services.session_logger import SessionLogger, get_session_turns

router = APIRouter()

# In-memory session store (use Redis in production)
_sessions: Dict[str, Dict] = {}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class StartSessionRequest(BaseModel):
    system_type: str       # "unconstrained" or "skill_based"
    llm_provider: str      # "ollama" | "gemini" | "openai" | "anthropic"
    llm_model: Optional[str] = None
    participant_id: Optional[str] = None


class MessageRequest(BaseModel):
    session_id: str
    message: str


class EndSessionRequest(BaseModel):
    session_id: str
    task_success: Optional[bool] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/start")
async def start_session(req: StartSessionRequest):
    session_id = str(uuid.uuid4())
    adapter = get_adapter(req.llm_provider, req.llm_model)

    _sessions[session_id] = {
        "system_type": req.system_type,
        "provider": req.llm_provider,
        "model": adapter.model_name,
        "adapter": adapter,
        "conversation_history": [],
        "skill_state": None,
        "turn_count": 0,
        "logger": SessionLogger(session_id, req.system_type, req.llm_provider, adapter.model_name),
    }

    # Get the opening message from the bot
    session = _sessions[session_id]
    dm = _get_dm(session)
    result = await dm.respond(
        conversation_history=[],
        user_message="",
        **({"state_dict": None} if req.system_type == "skill_based" else {}),
    )

    session["conversation_history"].append({"role": "assistant", "content": result["reply"]})
    session["skill_state"] = result.get("system_state")
    session["turn_count"] = 1
    session["logger"].log_turn(1, "assistant", result["reply"], result.get("current_step"))

    return {
        "session_id": session_id,
        "opening_message": result["reply"],
        "system_type": req.system_type,
        "provider": req.llm_provider,
        "model": adapter.model_name,
    }


@router.post("/message")
async def send_message(req: MessageRequest):
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session["turn_count"] += 1
    turn = session["turn_count"]
    logger: SessionLogger = session["logger"]

    # Log user turn
    logger.log_turn(turn, "user", req.message)
    session["conversation_history"].append({"role": "user", "content": req.message})

    # Get response from the appropriate dialog manager
    dm = _get_dm(session)

    kwargs = {"conversation_history": session["conversation_history"], "user_message": req.message}
    if session["system_type"] == "skill_based":
        kwargs["state_dict"] = session["skill_state"]

    result = await dm.respond(**kwargs)

    # Update session state
    session["conversation_history"].append({"role": "assistant", "content": result["reply"]})
    session["skill_state"] = result.get("system_state")

    # Detect repair turns (retry_count > 0 in state)
    is_repair = False
    if session["skill_state"] and session["skill_state"].get("retry_count", 0) > 0:
        is_repair = True
        logger.log_error(turn, "slot_extraction_failed",
                        details=f"step={result.get('current_step')}")

    session["turn_count"] += 1
    logger.log_turn(
        session["turn_count"], "assistant", result["reply"],
        result.get("current_step"),
        result.get("slots_filled"),
        is_repair,
    )

    # Use is_complete from the dialog manager directly.
    # Fallback to checking current_step for backwards compatibility.
    is_complete = result.get("is_complete", result.get("current_step") == "complete")

    return {
        "reply": result["reply"],
        "current_step": result.get("current_step"),
        "slots_filled": result.get("slots_filled", {}),
        "is_complete": is_complete,
    }


@router.post("/end")
async def end_session(req: EndSessionRequest):
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    slots = session["skill_state"].get("slots", {}) if session["skill_state"] else {}
    session["logger"].close_session(req.task_success, slots)

    # Keep session in memory for read, just mark it closed
    session["ended"] = True
    return {"status": "closed", "total_turns": session["turn_count"]}


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    turns = get_session_turns(session_id)
    return {"session_id": session_id, "turns": turns}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_dm(session: Dict):
    adapter = session["adapter"]
    if session["system_type"] == "unconstrained":
        return UnconstrainedDialogManager(adapter)
    elif session["system_type"] == "skill_based":
        return SkillBasedDialogManager(adapter)
    raise HTTPException(status_code=400, detail=f"Unknown system_type: {session['system_type']}")