from fastapi import APIRouter
from app.services.session_logger import get_all_sessions, get_session_turns, export_csv
import tempfile, os
from fastapi.responses import FileResponse

router = APIRouter()

@router.get("/")
async def list_sessions():
    return {"sessions": get_all_sessions()}

@router.get("/{session_id}")
async def get_session(session_id: str):
    return {"turns": get_session_turns(session_id)}

@router.get("/export/csv")
async def export_sessions_csv():
    tmp = tempfile.mktemp(suffix=".csv")
    export_csv(tmp)
    return FileResponse(tmp, media_type="text/csv", filename="sessions.csv")