from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .history_store import get_db_path

CREATE_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS local_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

CREATE_AUTH_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    is_revoked INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(user_id) REFERENCES local_accounts(id)
);
"""

CREATE_INDEX_SQLS = [
    """
    CREATE INDEX IF NOT EXISTS idx_local_accounts_username
    ON local_accounts (username);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
    ON auth_sessions (user_id, created_at DESC, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash
    ON auth_sessions (token_hash);
    """,
]


VERIFY_REASON_NOT_FOUND = "NOT_FOUND"
VERIFY_REASON_ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
VERIFY_REASON_INVALID_PASSWORD = "INVALID_PASSWORD"

REFRESH_REASON_OK = "OK"
REFRESH_REASON_TOKEN_REQUIRED = "TOKEN_REQUIRED"
REFRESH_REASON_TOKEN_NOT_FOUND = "TOKEN_NOT_FOUND"
REFRESH_REASON_SESSION_MISMATCH = "SESSION_MISMATCH"
REFRESH_REASON_TOKEN_REVOKED = "TOKEN_REVOKED"
REFRESH_REASON_USER_INACTIVE = "USER_INACTIVE"
REFRESH_REASON_EXPIRED_TOO_LONG = "EXPIRED_TOO_LONG"
REFRESH_REASON_EXPIRES_INVALID = "EXPIRES_INVALID"


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_USERS_TABLE_SQL)
    conn.execute(CREATE_AUTH_SESSIONS_TABLE_SQL)
    for sql in CREATE_INDEX_SQLS:
        conn.execute(sql)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _hash_password(*, password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return digest.hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def upsert_local_account(*, username: str, password: str) -> dict[str, Any]:
    safe_username = _normalize_username(username)
    safe_password = password.strip()
    if not safe_username:
        raise ValueError("username is required")
    if len(safe_password) < 6:
        raise ValueError("password too short")

    salt = secrets.token_hex(16)
    password_hash = _hash_password(password=safe_password, salt=salt)

    with _connect() as conn:
        _ensure_schema(conn)
        existing = conn.execute(
            """
            SELECT id FROM local_accounts WHERE username = ? LIMIT 1
            """,
            (safe_username,),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO local_accounts (username, password_hash, password_salt, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (safe_username, password_hash, salt),
            )
            user_id = int(cursor.lastrowid)
        else:
            user_id = int(existing["id"])
            conn.execute(
                """
                UPDATE local_accounts
                SET password_hash = ?,
                    password_salt = ?,
                    is_active = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (password_hash, salt, user_id),
            )
        conn.commit()

    return {"id": user_id, "username": safe_username}


def ensure_default_local_account() -> dict[str, Any]:
    default_username = os.getenv("CAREER_HERO_DEFAULT_USERNAME", "demo").strip() or "demo"
    default_password = os.getenv("CAREER_HERO_DEFAULT_PASSWORD", "demo123456").strip() or "demo123456"

    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute("SELECT id, username FROM local_accounts ORDER BY id ASC LIMIT 1").fetchone()
        if row is not None:
            return {"id": int(row["id"]), "username": str(row["username"])}

    return upsert_local_account(username=default_username, password=default_password)


def verify_local_account_with_reason(*, username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    safe_username = _normalize_username(username)
    safe_password = password.strip()
    if not safe_username or not safe_password:
        return None, VERIFY_REASON_NOT_FOUND

    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT id, username, password_hash, password_salt, is_active
            FROM local_accounts
            WHERE username = ?
            LIMIT 1
            """,
            (safe_username,),
        ).fetchone()

    if row is None:
        return None, VERIFY_REASON_NOT_FOUND

    if int(row["is_active"]) != 1:
        return None, VERIFY_REASON_ACCOUNT_INACTIVE

    expected_hash = str(row["password_hash"])
    actual_hash = _hash_password(password=safe_password, salt=str(row["password_salt"]))
    if not secrets.compare_digest(expected_hash, actual_hash):
        return None, VERIFY_REASON_INVALID_PASSWORD

    return {"id": int(row["id"]), "username": str(row["username"])}, None


def verify_local_account(*, username: str, password: str) -> dict[str, Any] | None:
    user, _ = verify_local_account_with_reason(username=username, password=password)
    return user


def create_auth_session(*, user_id: int, session_id: str, ttl_seconds: int = 7 * 24 * 3600) -> dict[str, Any]:
    safe_user_id = int(user_id)
    safe_session_id = session_id.strip() or "anonymous"
    safe_ttl = max(300, int(ttl_seconds))
    expires_at = _utc_now() + timedelta(seconds=safe_ttl)

    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_token(raw_token)

    with _connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO auth_sessions (user_id, token_hash, session_id, is_revoked, expires_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (safe_user_id, token_hash, safe_session_id, _format_utc(expires_at)),
        )
        conn.commit()

    return {
        "token": raw_token,
        "token_hash": token_hash,
        "session_id": safe_session_id,
        "expires_at": _format_utc(expires_at),
        "ttl_seconds": safe_ttl,
    }


def _fetch_auth_session_by_token_hash(conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            s.id,
            s.user_id,
            s.session_id,
            s.is_revoked,
            s.expires_at,
            u.username,
            u.is_active
        FROM auth_sessions s
        JOIN local_accounts u ON u.id = s.user_id
        WHERE s.token_hash = ?
        LIMIT 1
        """,
        (token_hash,),
    ).fetchone()


def peek_auth_session(*, token: str) -> dict[str, Any] | None:
    safe_token = token.strip()
    if not safe_token:
        return None

    token_hash = _hash_token(safe_token)
    with _connect() as conn:
        _ensure_schema(conn)
        row = _fetch_auth_session_by_token_hash(conn, token_hash)

    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "username": str(row["username"]),
        "session_id": str(row["session_id"]),
        "expires_at": str(row["expires_at"]),
        "is_revoked": int(row["is_revoked"]) == 1,
        "is_active": int(row["is_active"]) == 1,
    }


def validate_auth_session(*, token: str, session_id: str | None = None) -> dict[str, Any] | None:
    safe_token = token.strip()
    if not safe_token:
        return None

    token_hash = _hash_token(safe_token)
    with _connect() as conn:
        _ensure_schema(conn)
        row = _fetch_auth_session_by_token_hash(conn, token_hash)

    if row is None:
        return None

    if int(row["is_revoked"]) == 1:
        return None

    if int(row["is_active"]) != 1:
        return None

    expires_at_raw = str(row["expires_at"])
    expires_at = _parse_utc(expires_at_raw)
    if expires_at is None or expires_at <= _utc_now():
        return None

    bound_session_id = str(row["session_id"])
    if session_id and bound_session_id != session_id:
        return None

    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "username": str(row["username"]),
        "session_id": bound_session_id,
        "expires_at": expires_at_raw,
    }


def refresh_auth_session(
    *,
    token: str,
    session_id: str | None = None,
    ttl_seconds: int = 7 * 24 * 3600,
    grace_seconds: int = 24 * 3600,
) -> tuple[dict[str, Any] | None, str]:
    safe_token = token.strip()
    if not safe_token:
        return None, REFRESH_REASON_TOKEN_REQUIRED

    safe_ttl = max(300, int(ttl_seconds))
    safe_grace = max(0, int(grace_seconds))
    now = _utc_now()

    token_hash = _hash_token(safe_token)
    with _connect() as conn:
        _ensure_schema(conn)
        row = _fetch_auth_session_by_token_hash(conn, token_hash)
        if row is None:
            return None, REFRESH_REASON_TOKEN_NOT_FOUND

        bound_session_id = str(row["session_id"])
        if session_id and bound_session_id != session_id:
            return None, REFRESH_REASON_SESSION_MISMATCH

        if int(row["is_revoked"]) == 1:
            return None, REFRESH_REASON_TOKEN_REVOKED

        if int(row["is_active"]) != 1:
            return None, REFRESH_REASON_USER_INACTIVE

        old_expires_at_raw = str(row["expires_at"])
        old_expires_at = _parse_utc(old_expires_at_raw)
        if old_expires_at is None:
            return None, REFRESH_REASON_EXPIRES_INVALID

        if old_expires_at <= now and (now - old_expires_at).total_seconds() > safe_grace:
            return None, REFRESH_REASON_EXPIRED_TOO_LONG

        new_token = secrets.token_urlsafe(48)
        new_token_hash = _hash_token(new_token)
        new_expires_at = now + timedelta(seconds=safe_ttl)

        conn.execute(
            """
            UPDATE auth_sessions
            SET is_revoked = 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND is_revoked = 0
            """,
            (int(row["id"]),),
        )
        conn.execute(
            """
            INSERT INTO auth_sessions (user_id, token_hash, session_id, is_revoked, expires_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (int(row["user_id"]), new_token_hash, bound_session_id, _format_utc(new_expires_at)),
        )
        conn.commit()

    return {
        "token": new_token,
        "token_hash": new_token_hash,
        "session_id": bound_session_id,
        "user_id": int(row["user_id"]),
        "username": str(row["username"]),
        "expires_at": _format_utc(new_expires_at),
        "previous_expires_at": old_expires_at_raw,
        "ttl_seconds": safe_ttl,
    }, REFRESH_REASON_OK


def revoke_auth_session(*, token: str, session_id: str | None = None) -> bool:
    safe_token = token.strip()
    if not safe_token:
        return False

    token_hash = _hash_token(safe_token)
    with _connect() as conn:
        _ensure_schema(conn)
        if session_id:
            affected = conn.execute(
                """
                UPDATE auth_sessions
                SET is_revoked = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE token_hash = ? AND session_id = ? AND is_revoked = 0
                """,
                (token_hash, session_id),
            ).rowcount
        else:
            affected = conn.execute(
                """
                UPDATE auth_sessions
                SET is_revoked = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE token_hash = ? AND is_revoked = 0
                """,
                (token_hash,),
            ).rowcount
        conn.commit()

    return bool(affected)


def revoke_user_sessions(*, user_id: int) -> int:
    with _connect() as conn:
        _ensure_schema(conn)
        affected = conn.execute(
            """
            UPDATE auth_sessions
            SET is_revoked = 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE user_id = ? AND is_revoked = 0
            """,
            (int(user_id),),
        ).rowcount
        conn.commit()

    return int(affected or 0)
