from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .rag_store import get_rag_search_config, list_knowledge_items, search_knowledge_items

RetrieverMode = Literal["keyword", "mock-vector"]

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


def _get_env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(low, min(high, value))


def _get_env_float(name: str, default: float, *, low: float, high: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(low, min(high, value))


DEFAULT_TOP_K = _get_env_int("CAREER_HERO_RAG_TOP_K", 5, low=1, high=20)
DEFAULT_THRESHOLD = _get_env_float("CAREER_HERO_RAG_SCORE_THRESHOLD", 0.1, low=0.0, high=1.0)


class KnowledgeRetriever(Protocol):
    mode: RetrieverMode

    def search(self, *, query: str, limit: int, threshold: float) -> list[dict[str, Any]]:
        ...


@dataclass
class KeywordKnowledgeRetriever:
    mode: RetrieverMode = "keyword"

    def search(self, *, query: str, limit: int, threshold: float) -> list[dict[str, Any]]:
        rows = search_knowledge_items(query=query, limit=limit)
        safe_threshold = max(0.0, float(threshold))
        return [row for row in rows if float(row.get("score", 0.0)) >= safe_threshold]


@dataclass
class MockVectorKnowledgeRetriever:
    mode: RetrieverMode = "mock-vector"

    def _tokenize(self, text: str) -> list[str]:
        lowered = text.lower()
        tokens = re.findall(r"[a-zA-Z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}", lowered)
        return [token for token in tokens if token not in STOPWORDS]

    def _counter_norm(self, counter: Counter[str]) -> float:
        return math.sqrt(sum(value * value for value in counter.values()))

    def search(self, *, query: str, limit: int, threshold: float) -> list[dict[str, Any]]:
        safe_query = query.strip()
        if not safe_query:
            return []

        safe_limit = max(1, min(100, int(limit)))
        safe_threshold = max(0.0, float(threshold))
        query_tokens = self._tokenize(safe_query)
        if not query_tokens:
            keyword_rows = search_knowledge_items(query=safe_query, limit=safe_limit)
            return [row for row in keyword_rows if float(row.get("score", 0.0)) >= safe_threshold]

        query_counter = Counter(query_tokens)
        query_norm = self._counter_norm(query_counter)
        if query_norm <= 0:
            keyword_rows = search_knowledge_items(query=safe_query, limit=safe_limit)
            return [row for row in keyword_rows if float(row.get("score", 0.0)) >= safe_threshold]

        rows = list_knowledge_items(limit=600)
        scored: list[dict[str, Any]] = []

        for row in rows:
            title = str(row.get("title", ""))
            content = str(row.get("content", ""))
            tags = row.get("tags") if isinstance(row.get("tags"), list) else []

            doc_text = f"{title}\n{content}\n{' '.join(str(tag) for tag in tags if isinstance(tag, str))}"
            doc_counter = Counter(self._tokenize(doc_text))
            if not doc_counter:
                continue

            shared_terms = set(query_counter) & set(doc_counter)
            if not shared_terms:
                continue

            dot = sum(query_counter[token] * doc_counter[token] for token in shared_terms)
            doc_norm = self._counter_norm(doc_counter)
            if doc_norm <= 0:
                continue

            cosine = dot / (query_norm * doc_norm)
            title_boost = 0.08 if any(token in title.lower() for token in query_counter) else 0.0
            score = round(float(cosine + title_boost), 6)
            if score < safe_threshold:
                continue

            matched_terms = sorted(
                shared_terms,
                key=lambda token: (-query_counter[token] * doc_counter[token], token),
            )
            snippet = content if len(content) <= 220 else f"{content[:220]}..."

            scored.append(
                {
                    "id": int(row.get("id", 0)),
                    "title": title,
                    "content": content,
                    "snippet": snippet,
                    "tags": [str(tag) for tag in tags if isinstance(tag, str)],
                    "source": str(row.get("source", "manual")),
                    "score": score,
                    "matched_terms": matched_terms[:10],
                    "created_at": str(row.get("created_at", "")),
                    "updated_at": str(row.get("updated_at", "")),
                    "updated_by_scope": str(row.get("updated_by_scope", "system")),
                }
            )

        scored.sort(key=lambda item: (-float(item["score"]), -int(item["id"])))
        return scored[:safe_limit]


def get_rag_retriever_mode() -> RetrieverMode:
    raw = os.getenv("CAREER_HERO_RAG_RETRIEVER", "keyword").strip().lower()
    if raw in {"vector", "mock-vector", "mock_vector"}:
        return "mock-vector"
    return "keyword"


def get_configured_retriever() -> KnowledgeRetriever:
    mode = get_rag_retriever_mode()
    if mode == "mock-vector":
        return MockVectorKnowledgeRetriever()
    return KeywordKnowledgeRetriever()


def _resolve_retriever_params(*, limit: int | None, threshold: float | None) -> tuple[int, float]:
    db_config = get_rag_search_config()

    config_top_k = int(db_config.get("top_k", DEFAULT_TOP_K))
    config_top_k = max(1, min(20, config_top_k))
    config_threshold = float(db_config.get("score_threshold", DEFAULT_THRESHOLD))
    config_threshold = max(0.0, min(1.0, config_threshold))

    # precedence: request > config > default
    effective_limit = int(limit) if limit is not None else config_top_k
    effective_limit = max(1, min(100, effective_limit))

    effective_threshold = float(threshold) if threshold is not None else config_threshold
    effective_threshold = max(0.0, min(1.0, effective_threshold))

    return effective_limit, effective_threshold


def search_knowledge_with_configured_retriever(
    *,
    query: str,
    limit: int | None = None,
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    retriever = get_configured_retriever()
    effective_limit, effective_threshold = _resolve_retriever_params(
        limit=limit,
        threshold=threshold,
    )
    return retriever.search(query=query, limit=effective_limit, threshold=effective_threshold)
