from fastapi import APIRouter
from app.services.session_logger import get_all_sessions, get_session_turns, export_csv, init_db, DB_PATH
from fastapi.responses import FileResponse
import tempfile, sqlite3, json
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

router = APIRouter()


class QuestionnairePayload(BaseModel):
    session_id: str
    answers: Dict[str, Any]
    task_success: Optional[bool] = None


class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class ConversationPayload(BaseModel):
    session_id: str
    messages: List[ConversationMessage]


def ensure_tables():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS questionnaires (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            answers      TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL UNIQUE,
            messages     TEXT NOT NULL,
            saved_at     TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


@router.get("/")
async def list_sessions():
    return {"sessions": get_all_sessions()}


@router.get("/{session_id}/turns")
async def get_session_turns_endpoint(session_id: str):
    return {"turns": get_session_turns(session_id)}


@router.post("/questionnaire")
async def save_questionnaire(data: QuestionnairePayload):
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO questionnaires (session_id, answers, submitted_at) VALUES (?,?,?)",
        (data.session_id, json.dumps(data.answers), datetime.utcnow().isoformat()),
    )
    if data.task_success is not None:
        conn.execute(
            "UPDATE sessions SET task_success=?, ended_at=? WHERE session_id=?",
            (int(data.task_success), datetime.utcnow().isoformat(), data.session_id),
        )
    conn.commit()
    conn.close()
    return {"status": "saved"}


@router.post("/conversation")
async def save_conversation(data: ConversationPayload):
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    messages_json = json.dumps([
        {"role": m.role, "content": m.content, "timestamp": m.timestamp}
        for m in data.messages
    ])
    conn.execute(
        "INSERT OR REPLACE INTO conversations (session_id, messages, saved_at) VALUES (?,?,?)",
        (data.session_id, messages_json, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"status": "saved", "message_count": len(data.messages)}


@router.get("/export/sessions-csv")
async def export_sessions_csv():
    tmp = tempfile.mktemp(suffix=".csv")
    export_csv(tmp)
    return FileResponse(tmp, media_type="text/csv", filename="sessions.csv")


@router.get("/export/questionnaires-csv")
async def export_questionnaires_csv():
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT q.session_id, s.system_type, s.llm_model,
               s.started_at, s.ended_at, s.task_success,
               q.submitted_at, q.answers
        FROM questionnaires q
        LEFT JOIN sessions s ON q.session_id = s.session_id
        ORDER BY q.submitted_at DESC
    """).fetchall()
    conn.close()

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "session_id", "system_type", "llm_model",
        "started_at", "ended_at", "task_success", "submitted_at",
        "a1_task_completed", "a2_satisfaction", "b1_engagement",
        "b2_usability", "b3_reward", "b4_features_met", "b5_ease_of_communication",
    ])
    for row in rows:
        session_id, system_type, llm_model, started_at, ended_at, task_success, submitted_at, answers_json = row
        try:
            a = json.loads(answers_json)
        except Exception:
            a = {}
        writer.writerow([
            session_id, system_type, llm_model, started_at, ended_at, task_success, submitted_at,
            a.get("a1",""), a.get("a2",""), a.get("b1",""),
            a.get("b2",""), a.get("b3",""), a.get("b4",""), a.get("b5",""),
        ])
    output.seek(0)
    tmp = tempfile.mktemp(suffix=".csv")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.write(output.getvalue())
    return FileResponse(tmp, media_type="text/csv", filename="questionnaires.csv")


@router.get("/export/conversations-csv")
async def export_conversations_csv():
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT c.session_id, s.system_type, s.llm_model, s.started_at, c.messages
        FROM conversations c
        LEFT JOIN sessions s ON c.session_id = s.session_id
        ORDER BY s.started_at DESC
    """).fetchall()
    conn.close()

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "session_id", "system_type", "llm_model", "session_started_at",
        "turn_number", "role", "content", "timestamp"
    ])
    for session_id, system_type, llm_model, started_at, messages_json in rows:
        try:
            messages = json.loads(messages_json)
        except Exception:
            continue
        for i, msg in enumerate(messages, 1):
            writer.writerow([
                session_id, system_type, llm_model, started_at,
                i, msg.get("role",""), msg.get("content",""), msg.get("timestamp",""),
            ])
    output.seek(0)
    tmp = tempfile.mktemp(suffix=".csv")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.write(output.getvalue())
    return FileResponse(tmp, media_type="text/csv", filename="conversations.csv")


@router.get("/export/combined-csv")
async def export_combined_csv():
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT
            s.session_id, s.system_type, s.llm_model,
            s.started_at, s.ended_at, s.task_success, s.final_slots,
            COUNT(t.turn_id) as total_turns,
            SUM(CASE WHEN t.is_repair = 1 THEN 1 ELSE 0 END) as repair_turns,
            q.answers, q.submitted_at
        FROM sessions s
        LEFT JOIN turns t ON s.session_id = t.session_id
        LEFT JOIN questionnaires q ON s.session_id = q.session_id
        GROUP BY s.session_id
        ORDER BY s.started_at DESC
    """).fetchall()
    conn.close()

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "session_id", "system_type", "llm_model",
        "started_at", "ended_at", "task_success",
        "total_turns", "repair_turns", "session_duration_seconds",
        "a1_task_completed", "a2_satisfaction", "b1_engagement",
        "b2_usability", "b3_reward", "b4_features_met", "b5_ease_of_communication",
        "questionnaire_submitted",
    ])
    for row in rows:
        (session_id, system_type, llm_model, started_at, ended_at,
         task_success, final_slots, total_turns, repair_turns,
         answers_json, questionnaire_submitted) = row

        duration = ""
        if started_at and ended_at:
            try:
                start = datetime.fromisoformat(started_at)
                end = datetime.fromisoformat(ended_at)
                duration = str(int((end - start).total_seconds()))
            except Exception:
                pass

        try:
            a = json.loads(answers_json) if answers_json else {}
        except Exception:
            a = {}

        writer.writerow([
            session_id, system_type, llm_model, started_at, ended_at, task_success,
            total_turns, repair_turns, duration,
            a.get("a1",""), a.get("a2",""), a.get("b1",""),
            a.get("b2",""), a.get("b3",""), a.get("b4",""), a.get("b5",""),
            questionnaire_submitted,
        ])
    output.seek(0)
    tmp = tempfile.mktemp(suffix=".csv")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.write(output.getvalue())
    return FileResponse(tmp, media_type="text/csv", filename="thesis_data.csv")