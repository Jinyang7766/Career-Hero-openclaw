from __future__ import annotations

import json
import sqlite3
from typing import Any

from .history_store import get_db_path

CREATE_RESUMES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_scope_id TEXT NOT NULL DEFAULT 'anonymous',
    title TEXT NOT NULL,
    latest_version_no INTEGER NOT NULL DEFAULT 0,
    content_updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    is_deleted INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_RESUME_VERSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS resume_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    content TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    parsed_text TEXT,
    failure_reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(resume_id, version_no),
    FOREIGN KEY(resume_id) REFERENCES resumes(id) ON DELETE CASCADE
);
"""

CREATE_INDEX_SQLS = [
    """
    CREATE INDEX IF NOT EXISTS idx_resumes_updated
    ON resumes (updated_at DESC, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resumes_owner
    ON resumes (owner_scope_id, updated_at DESC, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_versions_resume_version
    ON resume_versions (resume_id, version_no DESC);
    """,
]

RESUMES_OPTIONAL_COLUMNS: dict[str, str] = {
    "owner_scope_id": "TEXT NOT NULL DEFAULT 'anonymous'",
    "content_updated_at": "TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
}

RESUME_VERSION_OPTIONAL_COLUMNS: dict[str, str] = {
    "parse_status": "TEXT NOT NULL DEFAULT 'pending'",
    "parsed_text": "TEXT",
    "failure_reason": "TEXT",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
}


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_optional_columns(conn: sqlite3.Connection, *, table: str, columns: dict[str, str]) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {str(row["name"]) for row in rows}
    for column, ddl in columns.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_RESUMES_TABLE_SQL)
    conn.execute(CREATE_RESUME_VERSIONS_TABLE_SQL)
    _ensure_optional_columns(conn, table="resumes", columns=RESUMES_OPTIONAL_COLUMNS)
    _ensure_optional_columns(conn, table="resume_versions", columns=RESUME_VERSION_OPTIONAL_COLUMNS)
    for sql in CREATE_INDEX_SQLS:
        conn.execute(sql)


def _json_loads(value: str, *, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _resolve_content_updated_at(*, row: sqlite3.Row | dict[str, Any], fallback: str | None = None) -> str:
    if isinstance(row, sqlite3.Row):
        raw = row["content_updated_at"] if "content_updated_at" in row.keys() else None
        updated = row["updated_at"] if "updated_at" in row.keys() else None
    else:
        raw = row.get("content_updated_at")
        updated = row.get("updated_at")

    value = str(raw or "").strip()
    if value:
        return value

    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()

    return str(updated or "").strip()


def _backfill_content_updated_at(conn: sqlite3.Connection, *, resume_id: int, content_updated_at: str) -> None:
    safe_value = (content_updated_at or "").strip()
    if not safe_value:
        conn.execute(
            """
            UPDATE resumes
            SET content_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
              AND (content_updated_at IS NULL OR TRIM(content_updated_at) = '')
            """,
            (resume_id,),
        )
        return

    conn.execute(
        """
        UPDATE resumes
        SET content_updated_at = ?
        WHERE id = ?
          AND (content_updated_at IS NULL OR TRIM(content_updated_at) = '')
        """,
        (safe_value, resume_id),
    )


def _normalize_version_payload(
    *,
    parse_status: str,
    parsed_text: str | None,
    metadata: dict[str, Any] | None,
    failure_reason: str | None,
) -> tuple[str, str | None, str, str | None]:
    safe_status = (parse_status or "pending").strip().lower()
    if safe_status not in {"pending", "parsed", "failed"}:
        safe_status = "pending"

    safe_parsed_text = parsed_text.strip() if isinstance(parsed_text, str) else None
    if safe_status == "pending":
        safe_parsed_text = None

    safe_failure_reason = failure_reason.strip() if isinstance(failure_reason, str) and failure_reason.strip() else None
    if safe_status != "failed":
        safe_failure_reason = None

    safe_metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    return safe_status, safe_parsed_text, safe_metadata_json, safe_failure_reason


def _scope_candidates(owner_scope_id: str | None) -> list[str]:
    safe_scope = (owner_scope_id or "").strip()
    if not safe_scope:
        return []

    values: list[str] = [safe_scope]
    if safe_scope.startswith("session:"):
        sid = safe_scope.split(":", 1)[1]
        if sid:
            values.append(f"anonymous:{sid}")
    elif safe_scope.startswith("anonymous:"):
        sid = safe_scope.split(":", 1)[1]
        if sid:
            values.append(f"session:{sid}")

    dedup: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return dedup


def _scope_clause(*, owner_scope_id: str | None, include_all_users: bool) -> tuple[str, tuple[Any, ...]]:
    if include_all_users:
        return "", ()

    candidates = _scope_candidates(owner_scope_id)
    if not candidates:
        return "", ()

    placeholders = ", ".join(["?"] * len(candidates))
    return f"AND r.owner_scope_id IN ({placeholders})", tuple(candidates)


def _scope_clause_single(*, owner_scope_id: str | None, include_all_users: bool) -> tuple[str, tuple[Any, ...]]:
    if include_all_users:
        return "", ()

    candidates = _scope_candidates(owner_scope_id)
    if not candidates:
        return "", ()

    placeholders = ", ".join(["?"] * len(candidates))
    return f"AND owner_scope_id IN ({placeholders})", tuple(candidates)


def create_resume(
    *,
    title: str,
    content: str,
    owner_scope_id: str = "anonymous",
    parse_status: str = "pending",
    parsed_text: str | None = None,
    metadata: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> int:
    safe_title = title.strip()
    safe_content = content.strip()
    safe_owner_scope_id = (owner_scope_id or "anonymous").strip() or "anonymous"
    safe_parse_status, safe_parsed_text, safe_metadata_json, safe_failure_reason = _normalize_version_payload(
        parse_status=parse_status,
        parsed_text=parsed_text,
        metadata=metadata,
        failure_reason=failure_reason,
    )

    with _connect() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO resumes (owner_scope_id, title, latest_version_no, content_updated_at)
            VALUES (?, ?, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (safe_owner_scope_id, safe_title),
        )
        resume_id = int(cursor.lastrowid)

        conn.execute(
            """
            INSERT INTO resume_versions (
                resume_id,
                version_no,
                content,
                parse_status,
                parsed_text,
                failure_reason,
                metadata_json
            )
            VALUES (?, 1, ?, ?, ?, ?, ?)
            """,
            (
                resume_id,
                safe_content,
                safe_parse_status,
                safe_parsed_text,
                safe_failure_reason,
                safe_metadata_json,
            ),
        )

        conn.execute(
            """
            UPDATE resumes
            SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (resume_id,),
        )
        conn.commit()

    return resume_id


def count_resumes(*, owner_scope_id: str | None = None, include_all_users: bool = False) -> int:
    scope_sql, scope_params = _scope_clause_single(owner_scope_id=owner_scope_id, include_all_users=include_all_users)
    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM resumes
            WHERE is_deleted = 0
            {scope_sql}
            """,
            scope_params,
        ).fetchone()

    if row is None:
        return 0
    return int(row["c"])


def list_resumes(*, limit: int = 50, owner_scope_id: str | None = None, include_all_users: bool = False) -> list[dict[str, Any]]:
    safe_limit = max(1, min(200, int(limit)))
    scope_sql, scope_params = _scope_clause(owner_scope_id=owner_scope_id, include_all_users=include_all_users)

    with _connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT
                r.id,
                r.owner_scope_id,
                r.title,
                r.latest_version_no,
                r.content_updated_at,
                r.created_at,
                r.updated_at,
                rv.content AS latest_content,
                rv.parse_status AS latest_parse_status,
                rv.created_at AS latest_version_created_at
            FROM resumes r
            LEFT JOIN resume_versions rv
                ON rv.resume_id = r.id
               AND rv.version_no = r.latest_version_no
            WHERE r.is_deleted = 0
            {scope_sql}
            ORDER BY COALESCE(NULLIF(r.content_updated_at, ''), r.updated_at) DESC, r.updated_at DESC, r.id DESC
            LIMIT ?
            """,
            (*scope_params, safe_limit),
        ).fetchall()

    items: list[dict[str, Any]] = []
    with _connect() as write_conn:
        _ensure_schema(write_conn)
        for row in rows:
            latest_version_created_at = str(row["latest_version_created_at"] or row["updated_at"])
            content_updated_at = _resolve_content_updated_at(row=row, fallback=latest_version_created_at)
            _backfill_content_updated_at(
                write_conn,
                resume_id=int(row["id"]),
                content_updated_at=content_updated_at,
            )
            items.append(
                {
                    "id": int(row["id"]),
                    "owner_scope_id": str(row["owner_scope_id"]),
                    "title": str(row["title"]),
                    "latest_version_no": int(row["latest_version_no"]),
                    "content_updated_at": content_updated_at,
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "latest_content": str(row["latest_content"] or ""),
                    "latest_parse_status": str(row["latest_parse_status"] or "pending"),
                    "latest_version_created_at": latest_version_created_at,
                }
            )
        write_conn.commit()
    return items


def fetch_resume_detail(
    *,
    resume_id: int,
    owner_scope_id: str | None = None,
    include_all_users: bool = False,
) -> dict[str, Any] | None:
    scope_sql, scope_params = _scope_clause_single(owner_scope_id=owner_scope_id, include_all_users=include_all_users)
    with _connect() as conn:
        _ensure_schema(conn)

        resume_row = conn.execute(
            f"""
            SELECT
                id,
                owner_scope_id,
                title,
                latest_version_no,
                content_updated_at,
                created_at,
                updated_at
            FROM resumes
            WHERE id = ? AND is_deleted = 0
            {scope_sql}
            LIMIT 1
            """,
            (resume_id, *scope_params),
        ).fetchone()

        if resume_row is None:
            return None

        version_rows = conn.execute(
            """
            SELECT
                id,
                version_no,
                content,
                parse_status,
                parsed_text,
                failure_reason,
                metadata_json,
                created_at
            FROM resume_versions
            WHERE resume_id = ?
            ORDER BY version_no DESC
            """,
            (resume_id,),
        ).fetchall()

    versions: list[dict[str, Any]] = []
    for row in version_rows:
        versions.append(
            {
                "id": int(row["id"]),
                "version_no": int(row["version_no"]),
                "content": str(row["content"]),
                "parse_status": str(row["parse_status"]),
                "parsed_text": str(row["parsed_text"] or ""),
                "failure_reason": str(row["failure_reason"] or "") or None,
                "metadata": _json_loads(str(row["metadata_json"]), fallback={}),
                "created_at": str(row["created_at"]),
            }
        )

    current_version = versions[0] if versions else None
    content_updated_fallback = current_version["created_at"] if current_version else str(resume_row["updated_at"])
    content_updated_at = _resolve_content_updated_at(row=resume_row, fallback=content_updated_fallback)

    with _connect() as conn:
        _ensure_schema(conn)
        _backfill_content_updated_at(
            conn,
            resume_id=int(resume_row["id"]),
            content_updated_at=content_updated_at,
        )
        conn.commit()

    return {
        "id": int(resume_row["id"]),
        "owner_scope_id": str(resume_row["owner_scope_id"]),
        "title": str(resume_row["title"]),
        "latest_version_no": int(resume_row["latest_version_no"]),
        "content_updated_at": content_updated_at,
        "created_at": str(resume_row["created_at"]),
        "updated_at": str(resume_row["updated_at"]),
        "current_version": current_version,
        "versions": versions,
    }


def fetch_resume_version_content(
    *,
    resume_id: int,
    version_no: int | None = None,
    owner_scope_id: str | None = None,
    include_all_users: bool = False,
) -> dict[str, Any] | None:
    scope_sql, scope_params = _scope_clause_single(owner_scope_id=owner_scope_id, include_all_users=include_all_users)
    with _connect() as conn:
        _ensure_schema(conn)

        resume_row = conn.execute(
            f"""
            SELECT id, latest_version_no
            FROM resumes
            WHERE id = ? AND is_deleted = 0
            {scope_sql}
            LIMIT 1
            """,
            (resume_id, *scope_params),
        ).fetchone()
        if resume_row is None:
            return None

        target_version = int(version_no) if version_no is not None else int(resume_row["latest_version_no"])

        row = conn.execute(
            """
            SELECT
                id,
                resume_id,
                version_no,
                content,
                parse_status,
                parsed_text,
                failure_reason,
                metadata_json,
                created_at
            FROM resume_versions
            WHERE resume_id = ? AND version_no = ?
            LIMIT 1
            """,
            (resume_id, target_version),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "resume_id": int(row["resume_id"]),
        "version_no": int(row["version_no"]),
        "content": str(row["content"]),
        "parse_status": str(row["parse_status"]),
        "parsed_text": str(row["parsed_text"] or ""),
        "failure_reason": str(row["failure_reason"] or "") or None,
        "metadata": _json_loads(str(row["metadata_json"]), fallback={}),
        "created_at": str(row["created_at"]),
    }


def update_resume(
    *,
    resume_id: int,
    title: str | None,
    content: str | None,
    owner_scope_id: str | None = None,
    include_all_users: bool = False,
    create_new_version: bool = True,
    parse_status: str = "pending",
    parsed_text: str | None = None,
    metadata: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    safe_parse_status, safe_parsed_text, safe_metadata_json, safe_failure_reason = _normalize_version_payload(
        parse_status=parse_status,
        parsed_text=parsed_text,
        metadata=metadata,
        failure_reason=failure_reason,
    )
    scope_sql, scope_params = _scope_clause_single(owner_scope_id=owner_scope_id, include_all_users=include_all_users)

    with _connect() as conn:
        _ensure_schema(conn)

        row = conn.execute(
            f"""
            SELECT id, latest_version_no, content_updated_at
            FROM resumes
            WHERE id = ? AND is_deleted = 0
            {scope_sql}
            LIMIT 1
            """,
            (resume_id, *scope_params),
        ).fetchone()

        if row is None:
            return None

        latest_version_no = int(row["latest_version_no"])

        if title is not None:
            conn.execute(
                """
                UPDATE resumes
                SET title = ?
                WHERE id = ?
                """,
                (title.strip(), resume_id),
            )

        if content is not None:
            safe_content = content.strip()
            if create_new_version:
                next_version_no = latest_version_no + 1
                conn.execute(
                    """
                    INSERT INTO resume_versions (
                        resume_id,
                        version_no,
                        content,
                        parse_status,
                        parsed_text,
                        failure_reason,
                        metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resume_id,
                        next_version_no,
                        safe_content,
                        safe_parse_status,
                        safe_parsed_text,
                        safe_failure_reason,
                        safe_metadata_json,
                    ),
                )
                latest_version_no = next_version_no
            else:
                conn.execute(
                    """
                    UPDATE resume_versions
                    SET content = ?,
                        parse_status = ?,
                        parsed_text = ?,
                        failure_reason = ?,
                        metadata_json = ?
                    WHERE resume_id = ? AND version_no = ?
                    """,
                    (
                        safe_content,
                        safe_parse_status,
                        safe_parsed_text,
                        safe_failure_reason,
                        safe_metadata_json,
                        resume_id,
                        latest_version_no,
                    ),
                )

            conn.execute(
                """
                UPDATE resumes
                SET latest_version_no = ?,
                    content_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (latest_version_no, resume_id),
            )
        else:
            fallback_content_updated = _resolve_content_updated_at(
                row={
                    "content_updated_at": row["content_updated_at"] if row is not None else None,
                    "updated_at": "",
                },
            )
            _backfill_content_updated_at(
                conn,
                resume_id=resume_id,
                content_updated_at=fallback_content_updated,
            )

        conn.execute(
            """
            UPDATE resumes
            SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (resume_id,),
        )

        conn.commit()

    return fetch_resume_detail(
        resume_id=resume_id,
        owner_scope_id=owner_scope_id,
        include_all_users=include_all_users,
    )


def delete_resume(
    *,
    resume_id: int,
    owner_scope_id: str | None = None,
    include_all_users: bool = False,
) -> bool:
    scope_sql, scope_params = _scope_clause_single(owner_scope_id=owner_scope_id, include_all_users=include_all_users)
    with _connect() as conn:
        _ensure_schema(conn)
        affected = conn.execute(
            f"""
            UPDATE resumes
            SET is_deleted = 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND is_deleted = 0
            {scope_sql}
            """,
            (resume_id, *scope_params),
        ).rowcount
        conn.commit()

    return bool(affected)
