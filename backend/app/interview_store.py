from __future__ import annotations

import json
import sqlite3
from typing import Any

from .history_store import get_db_path

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS interview_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    session_owner_id TEXT NOT NULL DEFAULT 'anonymous',
    jd_text TEXT NOT NULL,
    resume_text TEXT NOT NULL,
    questions_json TEXT NOT NULL DEFAULT '[]',
    answers_json TEXT NOT NULL DEFAULT '[]',
    current_index INTEGER NOT NULL DEFAULT 0,
    feedback_json TEXT,
    final_score INTEGER,
    recommendations_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

CREATE_INDEX_SQLS = [
    """
    CREATE INDEX IF NOT EXISTS idx_interview_sessions_created
    ON interview_sessions (created_at DESC, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_interview_sessions_status
    ON interview_sessions (status);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_interview_sessions_owner
    ON interview_sessions (session_owner_id, id DESC);
    """,
]

OPTIONAL_COLUMNS: dict[str, str] = {
    "session_owner_id": "TEXT NOT NULL DEFAULT 'anonymous'",
    "final_score": "INTEGER",
    "recommendations_json": "TEXT NOT NULL DEFAULT '[]'",
}

ALLOWED_STATUS = {"active", "paused", "finished"}


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_optional_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(interview_sessions)").fetchall()
    existing = {str(row["name"]) for row in rows}
    for column, ddl in OPTIONAL_COLUMNS.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE interview_sessions ADD COLUMN {column} {ddl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    _ensure_optional_columns(conn)
    for sql in CREATE_INDEX_SQLS:
        conn.execute(sql)


def _json_loads(value: str, *, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _normalize_status(status: str | None, *, fallback: str) -> str:
    safe_status = (status or fallback).strip().lower()
    if safe_status not in ALLOWED_STATUS:
        return fallback
    return safe_status


def _scope_candidates(scope_id: str | None) -> list[str]:
    safe_scope = (scope_id or "").strip()
    if not safe_scope:
        return []

    result: list[str] = [safe_scope]
    if safe_scope.startswith("session:"):
        sid = safe_scope.split(":", 1)[1]
        if sid:
            result.append(f"anonymous:{sid}")
    elif safe_scope.startswith("anonymous:"):
        sid = safe_scope.split(":", 1)[1]
        if sid:
            result.append(f"session:{sid}")

    dedup: list[str] = []
    seen: set[str] = set()
    for item in result:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


def _owner_filter_sql(*, session_owner_id: str | None, include_all_sessions: bool) -> tuple[str, tuple[Any, ...]]:
    if include_all_sessions:
        return "", ()

    candidates = _scope_candidates(session_owner_id)
    if not candidates:
        return "", ()

    placeholders = ", ".join(["?"] * len(candidates))
    return f"AND session_owner_id IN ({placeholders})", tuple(candidates)


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "session_token": str(row["session_token"]),
        "status": str(row["status"]),
        "session_owner_id": str(row["session_owner_id"]),
        "jd_text": str(row["jd_text"]),
        "resume_text": str(row["resume_text"]),
        "questions": _json_loads(str(row["questions_json"]), fallback=[]),
        "answers": _json_loads(str(row["answers_json"]), fallback=[]),
        "current_index": int(row["current_index"]),
        "feedback": _json_loads(str(row["feedback_json"]), fallback={}) if row["feedback_json"] else None,
        "final_score": int(row["final_score"]) if row["final_score"] is not None else None,
        "recommendations": _json_loads(str(row["recommendations_json"]), fallback=[]),
        "metadata": _json_loads(str(row["metadata_json"]), fallback={}),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def create_interview_session(
    *,
    session_token: str,
    session_owner_id: str,
    jd_text: str,
    resume_text: str,
    questions: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> int:
    with _connect() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO interview_sessions (
                session_token,
                status,
                session_owner_id,
                jd_text,
                resume_text,
                questions_json,
                answers_json,
                current_index,
                metadata_json
            )
            VALUES (?, 'active', ?, ?, ?, ?, '[]', 0, ?)
            """,
            (
                session_token,
                session_owner_id,
                jd_text,
                resume_text,
                json.dumps(questions, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def fetch_interview_session(
    *,
    session_id: int,
    session_owner_id: str | None = None,
    include_all_sessions: bool = False,
) -> dict[str, Any] | None:
    with _connect() as conn:
        _ensure_schema(conn)

        scope_sql, scope_params = _owner_filter_sql(
            session_owner_id=session_owner_id,
            include_all_sessions=include_all_sessions,
        )
        query = f"""
            SELECT
                id,
                session_token,
                status,
                session_owner_id,
                jd_text,
                resume_text,
                questions_json,
                answers_json,
                current_index,
                feedback_json,
                final_score,
                recommendations_json,
                metadata_json,
                created_at,
                updated_at
            FROM interview_sessions
            WHERE id = ?
            {scope_sql}
            LIMIT 1
        """

        row = conn.execute(query, (session_id, *scope_params)).fetchone()

    if row is None:
        return None

    return _row_to_item(row)


def list_interview_sessions(
    *,
    limit: int = 20,
    status: str | None = None,
    session_owner_id: str | None = None,
    include_all_sessions: bool = False,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(100, int(limit)))
    safe_status = (status or "").strip().lower()

    where_clauses: list[str] = []
    params: list[Any] = []

    if safe_status:
        where_clauses.append("status = ?")
        params.append(_normalize_status(safe_status, fallback="active"))

    if not include_all_sessions:
        candidates = _scope_candidates(session_owner_id)
        if candidates:
            placeholders = ", ".join(["?"] * len(candidates))
            where_clauses.append(f"session_owner_id IN ({placeholders})")
            params.extend(candidates)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    with _connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT
                id,
                session_token,
                status,
                session_owner_id,
                jd_text,
                resume_text,
                questions_json,
                answers_json,
                current_index,
                feedback_json,
                final_score,
                recommendations_json,
                metadata_json,
                created_at,
                updated_at
            FROM interview_sessions
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()

    return [_row_to_item(row) for row in rows]


def list_interview_results(
    *,
    limit: int = 20,
    session_owner_id: str | None = None,
    include_all_sessions: bool = False,
) -> list[dict[str, Any]]:
    return list_interview_sessions(
        limit=limit,
        status="finished",
        session_owner_id=session_owner_id,
        include_all_sessions=include_all_sessions,
    )


def fetch_interview_result(
    *,
    session_id: int,
    session_owner_id: str | None = None,
    include_all_sessions: bool = False,
) -> dict[str, Any] | None:
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=session_owner_id,
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        return None
    if str(row.get("status", "")).lower() != "finished":
        return None
    return row


def update_interview_session(
    *,
    session_id: int,
    status: str | None = None,
    answers: list[dict[str, Any]] | None = None,
    current_index: int | None = None,
    feedback: dict[str, Any] | None = None,
    final_score: int | None = None,
    recommendations: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    session_owner_id: str | None = None,
    include_all_sessions: bool = False,
) -> dict[str, Any] | None:
    current = fetch_interview_session(
        session_id=session_id,
        session_owner_id=session_owner_id,
        include_all_sessions=include_all_sessions,
    )
    if current is None:
        return None

    safe_status = _normalize_status(status, fallback=str(current["status"]))
    safe_answers = answers if answers is not None else current["answers"]
    safe_current_index = int(current_index if current_index is not None else current["current_index"])
    safe_feedback = feedback if feedback is not None else current["feedback"]
    safe_final_score = int(final_score) if final_score is not None else current.get("final_score")
    safe_recommendations = recommendations if recommendations is not None else current.get("recommendations", [])
    if not isinstance(safe_recommendations, list):
        safe_recommendations = []
    safe_recommendations = [str(item).strip() for item in safe_recommendations if str(item).strip()][:8]
    safe_metadata = metadata if metadata is not None else current["metadata"]

    with _connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE interview_sessions
            SET status = ?,
                answers_json = ?,
                current_index = ?,
                feedback_json = ?,
                final_score = ?,
                recommendations_json = ?,
                metadata_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                safe_status,
                json.dumps(safe_answers, ensure_ascii=False),
                safe_current_index,
                json.dumps(safe_feedback, ensure_ascii=False) if safe_feedback is not None else None,
                safe_final_score,
                json.dumps(safe_recommendations, ensure_ascii=False),
                json.dumps(safe_metadata or {}, ensure_ascii=False),
                session_id,
            ),
        )
        conn.commit()

    return fetch_interview_session(
        session_id=session_id,
        session_owner_id=session_owner_id,
        include_all_sessions=include_all_sessions,
    )
