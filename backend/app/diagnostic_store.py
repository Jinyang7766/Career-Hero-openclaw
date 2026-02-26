from __future__ import annotations

import json
import sqlite3
from typing import Any

from .history_store import get_db_path

DIAGNOSTIC_STATES: tuple[str, ...] = (
    "jd_input",
    "analyzing",
    "report",
    "micro",
    "chat",
    "final_report",
)

DEFAULT_DIAGNOSTIC_STATE = "jd_input"

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "jd_input": {"jd_input", "analyzing"},
    "analyzing": {"analyzing", "report", "jd_input"},
    "report": {"report", "analyzing", "micro", "chat", "final_report", "jd_input"},
    "micro": {"micro", "chat", "final_report", "report", "analyzing"},
    "chat": {"chat", "micro", "final_report", "report", "analyzing"},
    "final_report": {"final_report", "report", "chat", "micro", "analyzing"},
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS diagnostic_flow_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_scope_id TEXT NOT NULL,
    resume_id INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'jd_input',
    last_event TEXT NOT NULL DEFAULT 'init',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(owner_scope_id, resume_id)
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_diagnostic_flow_states_owner_resume
ON diagnostic_flow_states (owner_scope_id, resume_id, updated_at DESC, id DESC);
"""


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_SQL)


def _safe_owner_scope(owner_scope_id: str | None) -> str:
    value = (owner_scope_id or "").strip()
    return value or "anonymous"


def _safe_resume_id(resume_id: int | None) -> int:
    if resume_id is None:
        return 0
    try:
        value = int(resume_id)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)


def _safe_status(status: str | None) -> str:
    value = (status or "").strip().lower()
    if value in DIAGNOSTIC_STATES:
        return value
    return DEFAULT_DIAGNOSTIC_STATE


def _safe_event(event: str | None) -> str:
    value = (event or "").strip()
    return value[:80] if value else "manual"


def _json_loads(value: str, *, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _allowed_next(status: str) -> list[str]:
    return sorted(_ALLOWED_TRANSITIONS.get(status, {status}))


def _row_to_state(row: sqlite3.Row) -> dict[str, Any]:
    status = _safe_status(str(row["status"]))
    return {
        "id": int(row["id"]),
        "owner_scope_id": str(row["owner_scope_id"]),
        "resume_id": int(row["resume_id"]),
        "status": status,
        "last_event": str(row["last_event"]),
        "metadata": _json_loads(str(row["metadata_json"]), fallback={}),
        "allowed_next": _allowed_next(status),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def get_diagnostic_state(*, owner_scope_id: str | None, resume_id: int | None = 0) -> dict[str, Any]:
    safe_owner = _safe_owner_scope(owner_scope_id)
    safe_resume = _safe_resume_id(resume_id)

    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT
                id,
                owner_scope_id,
                resume_id,
                status,
                last_event,
                metadata_json,
                created_at,
                updated_at
            FROM diagnostic_flow_states
            WHERE owner_scope_id = ? AND resume_id = ?
            LIMIT 1
            """,
            (safe_owner, safe_resume),
        ).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO diagnostic_flow_states (owner_scope_id, resume_id, status, last_event, metadata_json)
                VALUES (?, ?, ?, 'init', '{}')
                """,
                (safe_owner, safe_resume, DEFAULT_DIAGNOSTIC_STATE),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT
                    id,
                    owner_scope_id,
                    resume_id,
                    status,
                    last_event,
                    metadata_json,
                    created_at,
                    updated_at
                FROM diagnostic_flow_states
                WHERE owner_scope_id = ? AND resume_id = ?
                LIMIT 1
                """,
                (safe_owner, safe_resume),
            ).fetchone()

    if row is None:
        raise RuntimeError("failed to initialize diagnostic flow state")

    return _row_to_state(row)


def can_transition_diagnostic_state(*, from_status: str, to_status: str) -> bool:
    current = _safe_status(from_status)
    target = _safe_status(to_status)
    allowed = _ALLOWED_TRANSITIONS.get(current, {current})
    return target in allowed


def transition_diagnostic_state(
    *,
    owner_scope_id: str | None,
    resume_id: int | None = 0,
    to_status: str,
    event: str | None = None,
    metadata: dict[str, Any] | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    safe_owner = _safe_owner_scope(owner_scope_id)
    safe_resume = _safe_resume_id(resume_id)
    target = _safe_status(to_status)
    safe_event = _safe_event(event)
    safe_metadata = metadata if isinstance(metadata, dict) else {}

    with _connect() as conn:
        _ensure_schema(conn)

        row = conn.execute(
            """
            SELECT
                id,
                owner_scope_id,
                resume_id,
                status,
                last_event,
                metadata_json,
                created_at,
                updated_at
            FROM diagnostic_flow_states
            WHERE owner_scope_id = ? AND resume_id = ?
            LIMIT 1
            """,
            (safe_owner, safe_resume),
        ).fetchone()

        if row is None:
            conn.execute(
                """
                INSERT INTO diagnostic_flow_states (owner_scope_id, resume_id, status, last_event, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    safe_owner,
                    safe_resume,
                    DEFAULT_DIAGNOSTIC_STATE,
                    "init",
                    json.dumps({}, ensure_ascii=False),
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT
                    id,
                    owner_scope_id,
                    resume_id,
                    status,
                    last_event,
                    metadata_json,
                    created_at,
                    updated_at
                FROM diagnostic_flow_states
                WHERE owner_scope_id = ? AND resume_id = ?
                LIMIT 1
                """,
                (safe_owner, safe_resume),
            ).fetchone()

        if row is None:
            raise RuntimeError("failed to initialize diagnostic flow state")

        current = _row_to_state(row)
        current_status = _safe_status(str(current["status"]))

        if strict and not can_transition_diagnostic_state(from_status=current_status, to_status=target):
            raise ValueError(f"invalid diagnostic transition: {current_status} -> {target}")

        merged_metadata = current["metadata"] if isinstance(current["metadata"], dict) else {}
        if safe_metadata:
            merged_metadata = {**merged_metadata, **safe_metadata}

        conn.execute(
            """
            UPDATE diagnostic_flow_states
            SET status = ?,
                last_event = ?,
                metadata_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                target,
                safe_event,
                json.dumps(merged_metadata, ensure_ascii=False),
                int(current["id"]),
            ),
        )
        conn.commit()

        updated = conn.execute(
            """
            SELECT
                id,
                owner_scope_id,
                resume_id,
                status,
                last_event,
                metadata_json,
                created_at,
                updated_at
            FROM diagnostic_flow_states
            WHERE id = ?
            LIMIT 1
            """,
            (int(current["id"]),),
        ).fetchone()

    if updated is None:
        raise RuntimeError("diagnostic flow state update lost")

    return _row_to_state(updated)
