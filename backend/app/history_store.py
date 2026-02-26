from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "career_hero.sqlite3"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resume_text_hash_or_excerpt TEXT NOT NULL,
    jd_excerpt TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_breakdown_json TEXT NOT NULL,
    matched_keywords_json TEXT NOT NULL,
    missing_keywords_json TEXT NOT NULL,
    suggestions_json TEXT NOT NULL DEFAULT '[]',
    optimized_resume_text TEXT NOT NULL DEFAULT '',
    insights_json TEXT NOT NULL DEFAULT '{"summary":"","strengths":[],"risks":[]}',
    analysis_source TEXT NOT NULL DEFAULT 'rule',
    session_id TEXT NOT NULL DEFAULT 'anonymous',
    user_scope_id TEXT NOT NULL DEFAULT 'anonymous',
    request_id TEXT NOT NULL
);
"""

CREATE_INDEX_SQLS = [
    """
    CREATE INDEX IF NOT EXISTS idx_analysis_history_created_at
    ON analysis_history (created_at DESC, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analysis_history_request_id
    ON analysis_history (request_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analysis_history_session_id
    ON analysis_history (session_id, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analysis_history_user_scope_id
    ON analysis_history (user_scope_id, id DESC);
    """,
]

OPTIONAL_COLUMNS: dict[str, str] = {
    "suggestions_json": "TEXT NOT NULL DEFAULT '[]'",
    "optimized_resume_text": "TEXT NOT NULL DEFAULT ''",
    "insights_json": "TEXT NOT NULL DEFAULT '{\"summary\":\"\",\"strengths\":[],\"risks\":[]}'",
    "analysis_source": "TEXT NOT NULL DEFAULT 'rule'",
    "session_id": "TEXT NOT NULL DEFAULT 'anonymous'",
    "user_scope_id": "TEXT NOT NULL DEFAULT 'anonymous'",
}


def get_db_path() -> Path:
    configured_path = os.getenv("CAREER_HERO_DB_PATH", "").strip()
    if configured_path:
        path = Path(configured_path)
        if not path.is_absolute():
            path = (Path(__file__).resolve().parents[1] / path).resolve()
        return path
    return DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _json_loads(value: str, *, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _ensure_optional_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(analysis_history)").fetchall()
    existing = {str(row["name"]) for row in rows}
    for column, ddl in OPTIONAL_COLUMNS.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE analysis_history ADD COLUMN {column} {ddl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    _ensure_optional_columns(conn)
    for sql in CREATE_INDEX_SQLS:
        conn.execute(sql)


def _scope_aliases(user_scope_id: str | None) -> list[str]:
    safe_scope = (user_scope_id or "").strip()
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
        if not item or item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


def _session_candidates(*, session_id: str | None, user_scope_id: str | None) -> list[str]:
    safe_scope = (user_scope_id or "").strip()
    if safe_scope.startswith("user:"):
        return []

    values: list[str] = []

    safe_session_id = (session_id or "").strip()
    if safe_session_id:
        values.append(safe_session_id)

    if safe_scope.startswith("session:") or safe_scope.startswith("anonymous:"):
        derived = safe_scope.split(":", 1)[1].strip()
        if derived:
            values.append(derived)

    dedup: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


def _build_scope_filter_clause(
    *,
    session_id: str | None,
    user_scope_id: str | None,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []

    scopes = _scope_aliases(user_scope_id)
    if scopes:
        placeholders = ", ".join(["?"] * len(scopes))
        clauses.append(f"user_scope_id IN ({placeholders})")
        params.extend(scopes)

    sessions = _session_candidates(session_id=session_id, user_scope_id=user_scope_id)
    if sessions:
        placeholders = ", ".join(["?"] * len(sessions))
        clauses.append(f"session_id IN ({placeholders})")
        params.extend(sessions)

    if not clauses:
        return "1=1", ()

    return f"({' OR '.join(clauses)})", tuple(params)


def _build_filters(
    *,
    request_id: str | None,
    session_id: str | None,
    user_scope_id: str | None,
    include_all_sessions: bool,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []

    if request_id:
        clauses.append("request_id = ?")
        params.append(request_id)

    if not include_all_sessions:
        scope_clause, scope_params = _build_scope_filter_clause(
            session_id=session_id,
            user_scope_id=user_scope_id,
        )
        clauses.append(scope_clause)
        params.extend(scope_params)

    if not clauses:
        return "", ()

    return "WHERE " + " AND ".join(clauses), tuple(params)


def insert_analysis_history(
    *,
    resume_text_hash_or_excerpt: str,
    jd_excerpt: str,
    score: int,
    score_breakdown: dict[str, int],
    matched_keywords: list[str],
    missing_keywords: list[str],
    suggestions: list[str],
    optimized_resume: str,
    insights: dict[str, Any],
    analysis_source: str,
    session_id: str,
    request_id: str,
    user_scope_id: str = "anonymous",
) -> int:
    with _connect() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO analysis_history (
                resume_text_hash_or_excerpt,
                jd_excerpt,
                score,
                score_breakdown_json,
                matched_keywords_json,
                missing_keywords_json,
                suggestions_json,
                optimized_resume_text,
                insights_json,
                analysis_source,
                session_id,
                user_scope_id,
                request_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resume_text_hash_or_excerpt,
                jd_excerpt,
                score,
                json.dumps(score_breakdown, ensure_ascii=False),
                json.dumps(matched_keywords, ensure_ascii=False),
                json.dumps(missing_keywords, ensure_ascii=False),
                json.dumps(suggestions, ensure_ascii=False),
                optimized_resume,
                json.dumps(insights, ensure_ascii=False),
                analysis_source,
                session_id,
                user_scope_id,
                request_id,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def enforce_retention(
    *,
    keep_latest: int,
    session_id: str | None = None,
    user_scope_id: str | None = None,
    include_all_sessions: bool = False,
) -> int:
    if keep_latest < 1:
        keep_latest = 1

    with _connect() as conn:
        _ensure_schema(conn)
        if not include_all_sessions and (user_scope_id or session_id):
            clause, scope_params = _build_scope_filter_clause(
                session_id=session_id,
                user_scope_id=user_scope_id,
            )
            deleted = conn.execute(
                f"""
                DELETE FROM analysis_history
                WHERE {clause}
                  AND id NOT IN (
                      SELECT id FROM analysis_history
                      WHERE {clause}
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (*scope_params, *scope_params, keep_latest),
            ).rowcount
        else:
            deleted = conn.execute(
                """
                DELETE FROM analysis_history
                WHERE id NOT IN (
                    SELECT id FROM analysis_history
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (keep_latest,),
            ).rowcount
        conn.commit()

    return int(deleted or 0)


def cleanup_history(
    *,
    keep_latest: int,
    session_id: str | None = None,
    user_scope_id: str | None = None,
    include_all_sessions: bool = False,
) -> int:
    return enforce_retention(
        keep_latest=keep_latest,
        session_id=session_id,
        user_scope_id=user_scope_id,
        include_all_sessions=include_all_sessions,
    )


def delete_all_history(
    *,
    session_id: str | None = None,
    user_scope_id: str | None = None,
    include_all_sessions: bool = False,
) -> int:
    with _connect() as conn:
        _ensure_schema(conn)
        if not include_all_sessions and (user_scope_id or session_id):
            clause, scope_params = _build_scope_filter_clause(
                session_id=session_id,
                user_scope_id=user_scope_id,
            )
            deleted = conn.execute(
                f"DELETE FROM analysis_history WHERE {clause}",
                scope_params,
            ).rowcount
        else:
            deleted = conn.execute("DELETE FROM analysis_history").rowcount
        conn.commit()
    return int(deleted or 0)


def fetch_analysis_history(
    *,
    limit: int,
    request_id: str | None = None,
    session_id: str | None = None,
    user_scope_id: str | None = None,
    include_all_sessions: bool = False,
) -> list[dict[str, Any]]:
    where_sql, params = _build_filters(
        request_id=request_id,
        session_id=session_id,
        user_scope_id=user_scope_id,
        include_all_sessions=include_all_sessions,
    )

    with _connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT
                id,
                created_at,
                resume_text_hash_or_excerpt,
                jd_excerpt,
                score,
                score_breakdown_json,
                matched_keywords_json,
                missing_keywords_json,
                analysis_source,
                session_id,
                user_scope_id,
                request_id
            FROM analysis_history
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"]),
                "resume_text_hash_or_excerpt": str(row["resume_text_hash_or_excerpt"]),
                "jd_excerpt": str(row["jd_excerpt"]),
                "score": int(row["score"]),
                "score_breakdown": _json_loads(str(row["score_breakdown_json"]), fallback={}),
                "matched_keywords": _json_loads(str(row["matched_keywords_json"]), fallback=[]),
                "missing_keywords": _json_loads(str(row["missing_keywords_json"]), fallback=[]),
                "analysis_source": str(row["analysis_source"]),
                "session_id": str(row["session_id"]),
                "user_scope_id": str(row["user_scope_id"]),
                "request_id": str(row["request_id"]),
            }
        )
    return items


def fetch_analysis_item(
    *,
    history_id: int,
    session_id: str | None = None,
    user_scope_id: str | None = None,
    include_all_sessions: bool = False,
) -> dict[str, Any] | None:
    with _connect() as conn:
        _ensure_schema(conn)

        query = """
            SELECT
                id,
                created_at,
                resume_text_hash_or_excerpt,
                jd_excerpt,
                score,
                score_breakdown_json,
                matched_keywords_json,
                missing_keywords_json,
                suggestions_json,
                optimized_resume_text,
                insights_json,
                analysis_source,
                session_id,
                user_scope_id,
                request_id
            FROM analysis_history
            WHERE id = ?
        """
        params: list[Any] = [history_id]

        if not include_all_sessions:
            clause, scope_params = _build_scope_filter_clause(
                session_id=session_id,
                user_scope_id=user_scope_id,
            )
            query += f" AND {clause}"
            params.extend(scope_params)

        query += " LIMIT 1"

        row = conn.execute(query, tuple(params)).fetchone()

    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "created_at": str(row["created_at"]),
        "resume_text_hash_or_excerpt": str(row["resume_text_hash_or_excerpt"]),
        "jd_excerpt": str(row["jd_excerpt"]),
        "score": int(row["score"]),
        "score_breakdown": _json_loads(str(row["score_breakdown_json"]), fallback={}),
        "matched_keywords": _json_loads(str(row["matched_keywords_json"]), fallback=[]),
        "missing_keywords": _json_loads(str(row["missing_keywords_json"]), fallback=[]),
        "suggestions": _json_loads(str(row["suggestions_json"]), fallback=[]),
        "optimized_resume": str(row["optimized_resume_text"]),
        "insights": _json_loads(str(row["insights_json"]), fallback={}),
        "analysis_source": str(row["analysis_source"]),
        "session_id": str(row["session_id"]),
        "user_scope_id": str(row["user_scope_id"]),
        "request_id": str(row["request_id"]),
    }


def get_history_total(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    user_scope_id: str | None = None,
    include_all_sessions: bool = False,
) -> int:
    where_sql, params = _build_filters(
        request_id=request_id,
        session_id=session_id,
        user_scope_id=user_scope_id,
        include_all_sessions=include_all_sessions,
    )

    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM analysis_history {where_sql}",
            params,
        ).fetchone()

    if row is None:
        return 0
    return int(row["c"])
