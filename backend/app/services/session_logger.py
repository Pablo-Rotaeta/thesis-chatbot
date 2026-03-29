"""
Session Logger
==============
Automatically logs every conversation turn to SQLite.
This gives you the raw data for metrics A and C in your thesis evaluation:
  - Task success rate
  - Number of turns
  - Repair turns
  - Error patterns
"""

import sqlite3, json, uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sessions.db"


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            system_type  TEXT NOT NULL,  -- 'unconstrained' or 'skill_based'
            llm_provider TEXT NOT NULL,
            llm_model    TEXT NOT NULL,
            started_at   TEXT NOT NULL,
            ended_at     TEXT,
            task_success INTEGER DEFAULT NULL,   -- 1=yes, 0=no, NULL=incomplete
            final_slots  TEXT,                   -- JSON of collected slots
            participant_id TEXT                  -- optional, for linking to questionnaire
        );

        CREATE TABLE IF NOT EXISTS turns (
            turn_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            turn_number  INTEGER NOT NULL,
            timestamp    TEXT NOT NULL,
            role         TEXT NOT NULL,          -- 'user' or 'assistant'
            content      TEXT NOT NULL,
            current_step TEXT,                   -- skill step id (null for unconstrained)
            slots_filled TEXT,                   -- JSON snapshot of slots at this turn
            is_repair    INTEGER DEFAULT 0,      -- 1 if this was a recovery/retry turn
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS errors (
            error_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            turn_number  INTEGER,
            error_type   TEXT,   -- slot_omission, incorrect_value, premature_closure, etc.
            slot_name    TEXT,
            details      TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
    """)
    conn.commit()
    conn.close()


class SessionLogger:

    def __init__(self, session_id: str, system_type: str, provider: str, model: str):
        self.session_id = session_id
        init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL)",
            (session_id, system_type, provider, model, datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()

    def log_turn(
        self,
        turn_number: int,
        role: str,
        content: str,
        current_step: Optional[str] = None,
        slots_filled: Optional[Dict] = None,
        is_repair: bool = False,
    ):
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO turns
               (session_id, turn_number, timestamp, role, content, current_step, slots_filled, is_repair)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                self.session_id,
                turn_number,
                datetime.utcnow().isoformat(),
                role,
                content,
                current_step,
                json.dumps(slots_filled or {}),
                int(is_repair),
            ),
        )
        conn.commit()
        conn.close()

    def log_error(self, turn_number: int, error_type: str, slot_name: str = "", details: str = ""):
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO errors (session_id, turn_number, error_type, slot_name, details) VALUES (?,?,?,?,?)",
            (self.session_id, turn_number, error_type, slot_name, details),
        )
        conn.commit()
        conn.close()

    def close_session(self, task_success: Optional[bool], final_slots: Dict):
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE sessions SET ended_at=?, task_success=?, final_slots=? WHERE session_id=?",
            (
                datetime.utcnow().isoformat(),
                None if task_success is None else int(task_success),
                json.dumps(final_slots),
                self.session_id,
            ),
        )
        conn.commit()
        conn.close()


def get_all_sessions() -> List[Dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session_turns(session_id: str) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM turns WHERE session_id=? ORDER BY turn_number", (session_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_csv(output_path: str):
    """Export all data to CSV for statistical analysis in R/Python."""
    import csv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sessions = conn.execute("SELECT * FROM sessions").fetchall()
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=sessions[0].keys() if sessions else [])
        writer.writeheader()
        writer.writerows([dict(r) for r in sessions])

    conn.close()