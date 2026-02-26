from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from typing import Any

from .history_store import get_db_path

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_base_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by_scope TEXT NOT NULL DEFAULT 'system'
);
"""

CREATE_RAG_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS rag_retriever_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    top_k INTEGER NOT NULL DEFAULT 5,
    score_threshold REAL NOT NULL DEFAULT 0.1,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

CREATE_INDEX_SQLS = [
    """
    CREATE INDEX IF NOT EXISTS idx_knowledge_base_entries_updated
    ON knowledge_base_entries (updated_at DESC, id DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_knowledge_base_entries_source
    ON knowledge_base_entries (source);
    """,
]

OPTIONAL_COLUMNS: dict[str, str] = {
    "updated_by_scope": "TEXT NOT NULL DEFAULT 'system'",
}

STOPWORDS = {
    "the",
    "and",
    "or",
    "for",
    "with",
    "from",
    "that",
    "this",
    "you",
    "your",
    "are",
    "was",
    "were",
    "have",
    "has",
    "can",
    "will",
    "about",
    "into",
    "to",
    "in",
    "on",
    "of",
    "a",
    "an",
    "is",
    "as",
    "by",
    "at",
    "be",
    "it",
    "我们",
    "负责",
    "相关",
    "经验",
    "能力",
    "工作",
    "岗位",
    "要求",
}


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_optional_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(knowledge_base_entries)").fetchall()
    existing = {str(row["name"]) for row in rows}
    for column, ddl in OPTIONAL_COLUMNS.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE knowledge_base_entries ADD COLUMN {column} {ddl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    _ensure_optional_columns(conn)
    conn.execute(CREATE_RAG_SETTINGS_SQL)
    conn.execute(
        """
        INSERT OR IGNORE INTO rag_retriever_settings (id, top_k, score_threshold)
        VALUES (1, 5, 0.1)
        """
    )
    for sql in CREATE_INDEX_SQLS:
        conn.execute(sql)


def _json_loads(value: str, *, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-zA-Z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    return [token for token in tokens if token not in STOPWORDS]


def _score_item(*, query_tokens: list[str], title: str, content: str, tags: list[str]) -> tuple[float, list[str]]:
    haystack = f"{title}\n{content}\n{' '.join(tags)}"
    haystack_tokens = _tokenize(haystack)
    token_counter = Counter(haystack_tokens)

    matched_terms: list[str] = []
    score = 0.0
    for token in query_tokens:
        freq = token_counter.get(token, 0)
        if freq <= 0:
            continue
        matched_terms.append(token)
        score += min(5.0, 1.0 + freq * 0.8)

    if query_tokens:
        coverage = len(set(matched_terms)) / max(1, len(set(query_tokens)))
        score += coverage * 3.0

    title_text = title.lower()
    if any(token in title_text for token in query_tokens):
        score += 1.5

    return round(score, 3), matched_terms


def _normalize_tags(tags: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in tags or []:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if not tag:
            continue
        marker = tag.lower()
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(tag[:40])
        if len(normalized) >= 20:
            break
    return normalized


def _normalize_scope(scope: str | None) -> str:
    return (scope or "system").strip() or "system"


def create_knowledge_item(
    *,
    title: str,
    content: str,
    tags: list[str] | None = None,
    source: str = "manual",
    updated_by_scope: str = "system",
) -> dict[str, Any]:
    safe_title = title.strip()
    safe_content = content.strip()
    safe_tags = _normalize_tags(tags)
    safe_source = source.strip() or "manual"
    safe_updated_by_scope = _normalize_scope(updated_by_scope)

    with _connect() as conn:
        _ensure_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO knowledge_base_entries (title, content, tags_json, source, updated_by_scope)
            VALUES (?, ?, ?, ?, ?)
            """,
            (safe_title, safe_content, json.dumps(safe_tags, ensure_ascii=False), safe_source, safe_updated_by_scope),
        )
        item_id = int(cursor.lastrowid)
        conn.commit()

    item = fetch_knowledge_item(item_id=item_id)
    if item is None:
        raise RuntimeError("knowledge item created but failed to fetch")
    return item


def update_knowledge_item(
    *,
    item_id: int,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
    updated_by_scope: str | None = None,
) -> dict[str, Any] | None:
    existing = fetch_knowledge_item(item_id=item_id)
    if existing is None:
        return None

    next_title = title.strip() if isinstance(title, str) and title.strip() else str(existing["title"])
    next_content = content.strip() if isinstance(content, str) and content.strip() else str(existing["content"])
    next_source = source.strip() if isinstance(source, str) and source.strip() else str(existing["source"])
    next_tags = _normalize_tags(tags) if tags is not None else _normalize_tags(existing.get("tags", []))
    next_updated_by_scope = _normalize_scope(updated_by_scope) if updated_by_scope is not None else _normalize_scope(
        str(existing.get("updated_by_scope", "system"))
    )

    with _connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE knowledge_base_entries
            SET title = ?,
                content = ?,
                tags_json = ?,
                source = ?,
                updated_by_scope = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                next_title,
                next_content,
                json.dumps(next_tags, ensure_ascii=False),
                next_source,
                next_updated_by_scope,
                item_id,
            ),
        )
        conn.commit()

    return fetch_knowledge_item(item_id=item_id)


def delete_knowledge_item(*, item_id: int) -> bool:
    with _connect() as conn:
        _ensure_schema(conn)
        affected = conn.execute(
            "DELETE FROM knowledge_base_entries WHERE id = ?",
            (item_id,),
        ).rowcount
        conn.commit()
    return bool(affected)


def fetch_knowledge_item(*, item_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT id, title, content, tags_json, source, created_at, updated_at, updated_by_scope
            FROM knowledge_base_entries
            WHERE id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "title": str(row["title"]),
        "content": str(row["content"]),
        "tags": _json_loads(str(row["tags_json"]), fallback=[]),
        "source": str(row["source"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "updated_by_scope": str(row["updated_by_scope"]),
    }


def list_knowledge_items(*, limit: int = 20, source: str | None = None) -> list[dict[str, Any]]:
    safe_limit = max(1, min(400, int(limit)))
    source_filter = (source or "").strip() or None

    where_sql = ""
    params: list[Any] = []
    if source_filter:
        where_sql = "WHERE source = ?"
        params.append(source_filter)

    with _connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT id, title, content, tags_json, source, created_at, updated_at, updated_by_scope
            FROM knowledge_base_entries
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"]),
                "title": str(row["title"]),
                "content": str(row["content"]),
                "tags": _json_loads(str(row["tags_json"]), fallback=[]),
                "source": str(row["source"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "updated_by_scope": str(row["updated_by_scope"]),
            }
        )

    return result


def search_knowledge_items(*, query: str, limit: int = 5) -> list[dict[str, Any]]:
    safe_query = query.strip()
    if not safe_query:
        return []

    safe_limit = max(1, min(100, int(limit)))
    query_tokens = _tokenize(safe_query)
    if not query_tokens:
        query_tokens = [safe_query.lower()]

    with _connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, title, content, tags_json, source, created_at, updated_at, updated_by_scope
            FROM knowledge_base_entries
            ORDER BY updated_at DESC, id DESC
            LIMIT 600
            """
        ).fetchall()

    scored: list[dict[str, Any]] = []
    for row in rows:
        tags = _json_loads(str(row["tags_json"]), fallback=[])
        score, matched_terms = _score_item(
            query_tokens=query_tokens,
            title=str(row["title"]),
            content=str(row["content"]),
            tags=tags if isinstance(tags, list) else [],
        )
        if score <= 0:
            continue

        content = str(row["content"])
        snippet = content if len(content) <= 220 else f"{content[:220]}..."
        scored.append(
            {
                "id": int(row["id"]),
                "title": str(row["title"]),
                "content": content,
                "snippet": snippet,
                "tags": tags if isinstance(tags, list) else [],
                "source": str(row["source"]),
                "score": score,
                "matched_terms": matched_terms,
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "updated_by_scope": str(row["updated_by_scope"]),
            }
        )

    scored.sort(key=lambda item: (-float(item["score"]), -int(item["id"])))
    return scored[:safe_limit]


def get_rag_search_config() -> dict[str, Any]:
    with _connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT top_k, score_threshold, updated_at
            FROM rag_retriever_settings
            WHERE id = 1
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return {"top_k": 5, "score_threshold": 0.1, "updated_at": ""}

    top_k = int(row["top_k"])
    top_k = max(1, min(20, top_k))
    threshold = float(row["score_threshold"])
    threshold = max(0.0, min(1.0, threshold))
    return {
        "top_k": top_k,
        "score_threshold": threshold,
        "updated_at": str(row["updated_at"]),
    }


def update_rag_search_config(*, top_k: int | None = None, score_threshold: float | None = None) -> dict[str, Any]:
    current = get_rag_search_config()
    next_top_k = int(top_k) if top_k is not None else int(current["top_k"])
    next_threshold = float(score_threshold) if score_threshold is not None else float(current["score_threshold"])

    next_top_k = max(1, min(20, next_top_k))
    next_threshold = max(0.0, min(1.0, next_threshold))

    with _connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE rag_retriever_settings
            SET top_k = ?,
                score_threshold = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = 1
            """,
            (next_top_k, next_threshold),
        )
        conn.commit()

    return get_rag_search_config()
