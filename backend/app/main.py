from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from io import BytesIO
from threading import Lock
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth_store import (
    REFRESH_REASON_EXPIRED_TOO_LONG,
    REFRESH_REASON_SESSION_MISMATCH,
    REFRESH_REASON_TOKEN_NOT_FOUND,
    REFRESH_REASON_TOKEN_REQUIRED,
    REFRESH_REASON_TOKEN_REVOKED,
    REFRESH_REASON_USER_INACTIVE,
    VERIFY_REASON_ACCOUNT_INACTIVE,
    create_auth_session,
    ensure_default_local_account,
    peek_auth_session,
    refresh_auth_session,
    revoke_auth_session,
    validate_auth_session,
    verify_local_account_with_reason,
)
from .history_store import (
    cleanup_history,
    delete_all_history,
    enforce_retention,
    fetch_analysis_history,
    fetch_analysis_item,
    get_history_total,
    insert_analysis_history,
)
from .diagnostic_store import (
    DIAGNOSTIC_STATES,
    can_transition_diagnostic_state,
    get_diagnostic_state,
    transition_diagnostic_state,
)
from .interview_store import (
    create_interview_session,
    fetch_interview_result,
    fetch_interview_session,
    list_interview_results,
    list_interview_sessions,
    update_interview_session,
)
from .rag_retriever import get_rag_retriever_mode, search_knowledge_with_configured_retriever
from .rag_store import (
    create_knowledge_item,
    delete_knowledge_item,
    list_knowledge_items,
    update_knowledge_item,
    get_rag_search_config,
    update_rag_search_config,
)
from .resume_store import (
    count_resumes,
    create_resume,
    delete_resume,
    fetch_resume_detail,
    fetch_resume_version_content,
    list_resumes,
    update_resume,
)

def get_env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)

    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def get_auth_mode() -> Literal["local", "token"]:
    raw = os.getenv("CAREER_HERO_AUTH_MODE", "local").strip().lower()
    if raw in {"token", "strict"}:
        return "token"
    return "local"


def is_public_path(path: str) -> bool:
    if path in {
        "/health",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/api/auth/login",
        "/api/auth/refresh",
    }:
        return True
    return path.startswith("/health/")


def is_protected_resource_login_required() -> bool:
    return get_env_bool("CAREER_HERO_REQUIRE_LOGIN_FOR_PROTECTED", True)


def is_login_required_path(path: str) -> bool:
    if not is_protected_resource_login_required():
        return False

    if is_public_path(path):
        return False

    if path.startswith("/api/auth/"):
        return False

    protected_prefixes = (
        "/api/resumes",
        "/api/history",
        "/api/interview",
        "/api/rag",
        "/api/diagnostic",
    )
    return path.startswith(protected_prefixes)


def get_expected_api_token() -> str:
    return os.getenv("CAREER_HERO_API_TOKEN", "").strip()


def parse_request_token(request: Request) -> str:
    header_value = request.headers.get("x-api-token", "").strip()
    if header_value:
        return header_value

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def parse_auth_session_token(request: Request) -> str:
    header_token = request.headers.get("x-session-token", "").strip()
    if header_token:
        return header_token

    cookie_token = request.cookies.get("career_hero_auth", "").strip()
    if cookie_token:
        return cookie_token

    return ""


def parse_auth_session_token_for_auth_api(request: Request) -> str:
    token = parse_auth_session_token(request)
    if token:
        return token

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return ""


def validate_session_id(session_id: str) -> bool:
    if not session_id:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", session_id))


def is_cross_session_access_allowed() -> bool:
    return get_env_bool("CAREER_HERO_ALLOW_CROSS_SESSION_ACCESS", False)


def is_session_isolation_enabled() -> bool:
    return get_env_bool("CAREER_HERO_SESSION_ISOLATION_ENABLED", False)


MAX_TEXT_LENGTH = 20_000
MAX_JSON_BODY_BYTES = get_env_int("CAREER_HERO_MAX_JSON_BYTES", 80_000, min_value=2_048)
DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100
DEFAULT_HISTORY_RETENTION = get_env_int("CAREER_HERO_HISTORY_RETENTION", 500, min_value=1)
DEFAULT_RESUME_LIST_LIMIT = 20
MAX_RESUME_LIST_LIMIT = 100
MAX_RESUME_TITLE_LENGTH = 120
MAX_RESUME_IMPORT_TEXT_LENGTH = get_env_int("CAREER_HERO_MAX_RESUME_IMPORT_TEXT_LENGTH", 50_000, min_value=1_024)
RESUME_TXT_PARSER_VERSION = "txt-v0"
PROMPT_VERSION = "v2-structured-2026-02"
AUTH_SESSION_TTL_SECONDS = get_env_int("CAREER_HERO_AUTH_SESSION_TTL_SECONDS", 7 * 24 * 3600, min_value=300, max_value=30 * 24 * 3600)
AUTH_REFRESH_GRACE_SECONDS = get_env_int("CAREER_HERO_AUTH_REFRESH_GRACE_SECONDS", 24 * 3600, min_value=0, max_value=30 * 24 * 3600)
AUTH_LOGIN_FAIL_LIMIT = get_env_int("CAREER_HERO_AUTH_LOGIN_FAIL_LIMIT", 6, min_value=2, max_value=100)
AUTH_LOGIN_FAIL_WINDOW_SECONDS = get_env_int("CAREER_HERO_AUTH_LOGIN_FAIL_WINDOW_SECONDS", 5 * 60, min_value=10, max_value=24 * 3600)
AUTH_LOGIN_LOCK_SECONDS = get_env_int("CAREER_HERO_AUTH_LOGIN_LOCK_SECONDS", 5 * 60, min_value=10, max_value=24 * 3600)

ERROR_CODE_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
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
    "had",
    "can",
    "will",
    "would",
    "should",
    "about",
    "into",
    "over",
    "under",
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
    "we",
    "our",
    "they",
    "their",
    "负责",
    "熟悉",
    "具有",
    "相关",
    "经验",
    "能力",
    "优先",
    "进行",
    "工作",
    "岗位",
    "要求",
}

SUSPICIOUS_PATTERNS = [
    re.compile(r"<\s*script", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+previous\s+instructions", re.IGNORECASE),
]
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){6,15}\d")
VAGUE_MARKERS = {
    "负责",
    "参与",
    "协助",
    "支持",
    "跟进",
    "执行",
    "沟通",
    "familiar",
    "responsible",
    "assist",
    "support",
}
ACTION_MARKERS = {
    "设计",
    "搭建",
    "优化",
    "推动",
    "落地",
    "实现",
    "改进",
    "developed",
    "built",
    "optimized",
    "delivered",
    "led",
}

DEFAULT_RAG_TOP_K = get_env_int("CAREER_HERO_RAG_TOP_K", 5, min_value=1, max_value=20)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("career_hero.api")


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumeText: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    jdText: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    resumeId: int | None = Field(default=None, ge=1)
    versionNo: int | None = Field(default=None, ge=1)
    ragEnabled: bool = False
    ragTopK: int | None = Field(default=None, ge=1, le=20)
    ragThreshold: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("resumeText", "jdText")
    @classmethod
    def normalize_and_validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or not value.strip():
            raise ValueError("text cannot be blank")

        normalized = re.sub(r"\u0000", "", value).strip()
        if not normalized:
            raise ValueError("text cannot be blank")

        for pattern in SUSPICIOUS_PATTERNS:
            if pattern.search(normalized):
                raise ValueError("text contains blocked pattern")

        return normalized

    @model_validator(mode="after")
    def validate_resume_source(self) -> "AnalyzeRequest":
        if self.resumeId is None and not self.resumeText:
            raise ValueError("resumeText is required when resumeId is not provided")
        if self.versionNo is not None and self.resumeId is None:
            raise ValueError("versionNo requires resumeId")
        return self


class ScoreBreakdown(BaseModel):
    keyword_match: int
    coverage: int
    writing_quality_stub: int


class AnalysisInsights(BaseModel):
    summary: str
    strengths: list[str]
    risks: list[str]


class DiagnosticBreakdown(BaseModel):
    keywordCoverage: int
    quantifiedImpact: int
    expressionClarity: int
    jdRelevance: int


class IssueClassification(BaseModel):
    type: Literal["模糊描述", "缺量化", "关键词缺失"]
    severity: Literal["low", "medium", "high"]
    evidence: list[str]
    recommendation: str


class PipAdviceItem(BaseModel):
    finding: str
    improvement: str
    practice: str


class RagHit(BaseModel):
    id: int
    title: str
    snippet: str
    source: str
    tags: list[str]
    score: float
    matchedTerms: list[str] = Field(default_factory=list)


class DiagnosticFlowState(BaseModel):
    resumeId: int
    status: Literal["jd_input", "analyzing", "report", "micro", "chat", "final_report"]
    allowedNext: list[Literal["jd_input", "analyzing", "report", "micro", "chat", "final_report"]] = Field(default_factory=list)
    updatedAt: str


class AnalyzeResponse(BaseModel):
    score: int
    matchedKeywords: list[str]
    missingKeywords: list[str]
    suggestions: list[str]
    optimizedResume: str
    scoreBreakdown: ScoreBreakdown
    diagnosticBreakdown: DiagnosticBreakdown
    issueClassifications: list[IssueClassification] = Field(default_factory=list)
    pipAdvice: list[PipAdviceItem] = Field(default_factory=list)
    insights: AnalysisInsights
    analysisSource: Literal["rule", "gemini"]
    fallbackUsed: bool
    ragEnabled: bool = False
    ragHits: list[RagHit] = Field(default_factory=list)
    promptVersion: str
    historyId: int
    requestId: str
    diagnosticState: DiagnosticFlowState


class DiagnosticStateResponse(BaseModel):
    requestId: str
    state: DiagnosticFlowState


class DiagnosticTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumeId: int | None = Field(default=None, ge=0)
    toStatus: Literal["jd_input", "analyzing", "report", "micro", "chat", "final_report"]
    reason: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("reason")
    @classmethod
    def normalize_optional_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason cannot be blank")
        return normalized


class KeywordSummary(BaseModel):
    matched: list[str]
    missing: list[str]


class HistoryItem(BaseModel):
    id: int
    createdAt: str
    resumeTextHashOrExcerpt: str
    jdExcerpt: str
    score: int
    scoreBreakdown: ScoreBreakdown
    keywordSummary: KeywordSummary
    analysisSource: str
    requestId: str


class HistoryResponse(BaseModel):
    requestId: str
    total: int
    items: list[HistoryItem]


class HistoryDetail(BaseModel):
    id: int
    createdAt: str
    resumeTextHashOrExcerpt: str
    jdExcerpt: str
    score: int
    scoreBreakdown: ScoreBreakdown
    matchedKeywords: list[str]
    missingKeywords: list[str]
    suggestions: list[str]
    optimizedResume: str
    insights: AnalysisInsights
    analysisSource: str
    sessionId: str
    requestId: str


class HistoryDetailResponse(BaseModel):
    requestId: str
    item: HistoryDetail


class HistoryCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["keep_latest", "delete_all"]
    keepLatest: int = Field(default=200, ge=1, le=5000)
    confirmText: str | None = None


class HistoryCleanupResponse(BaseModel):
    requestId: str
    deleted: int
    total: int


class ResumeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_RESUME_TITLE_LENGTH)
    content: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)

    @field_validator("title", "content")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized


class ResumeTxtImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=MAX_RESUME_TITLE_LENGTH)
    fileName: str | None = Field(default=None, max_length=255)
    content: str = Field(default="", max_length=MAX_RESUME_IMPORT_TEXT_LENGTH)

    @field_validator("title", "fileName", "content")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class ResumeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_RESUME_TITLE_LENGTH)
    content: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_LENGTH)
    createNewVersion: bool = True

    @field_validator("title", "content")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized


class ResumeVersionItem(BaseModel):
    id: int
    versionNo: int
    content: str
    parseStatus: str
    parsedText: str
    failureReason: str | None
    metadata: dict[str, Any]
    createdAt: str


class ResumeItem(BaseModel):
    id: int
    title: str
    latestVersionNo: int
    contentUpdatedAt: str
    createdAt: str
    updatedAt: str
    latestParseStatus: str
    latestContentPreview: str


class ResumeDetail(BaseModel):
    id: int
    title: str
    latestVersionNo: int
    contentUpdatedAt: str
    createdAt: str
    updatedAt: str
    currentVersion: ResumeVersionItem | None
    versions: list[ResumeVersionItem]


class ResumeListResponse(BaseModel):
    requestId: str
    total: int
    items: list[ResumeItem]


class ResumeDetailResponse(BaseModel):
    requestId: str
    item: ResumeDetail


class ResumeDeleteResponse(BaseModel):
    requestId: str
    deleted: bool


class AuthLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=120)

    @field_validator("username", "password")
    @classmethod
    def normalize_auth_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized


class AuthUser(BaseModel):
    id: int
    username: str


class AuthContextSession(BaseModel):
    id: str
    scope: str


class AuthContextExpiry(BaseModel):
    expiresAt: str | None
    ttlSeconds: int | None
    isExpired: bool


class AuthContext(BaseModel):
    mode: Literal["local", "token"]
    user: AuthUser | None
    session: AuthContextSession
    expiry: AuthContextExpiry


class AuthLoginResponse(BaseModel):
    requestId: str
    sessionId: str
    user: AuthUser
    token: str
    expiresAt: str


class AuthRefreshResponse(BaseModel):
    requestId: str
    sessionId: str
    user: AuthUser
    token: str
    expiresAt: str
    previousExpiresAt: str


class AuthMeResponse(BaseModel):
    requestId: str
    sessionId: str
    user: AuthUser
    expiresAt: str
    authContext: AuthContext


class AuthLogoutResponse(BaseModel):
    requestId: str
    revoked: bool


class KnowledgeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1, max_length=8_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(default="manual", min_length=1, max_length=60)

    @field_validator("title", "content", "source")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            tag = item.strip()
            if not tag:
                continue
            marker = tag.lower()
            if marker in seen:
                continue
            seen.add(marker)
            normalized.append(tag[:40])
        return normalized[:20]


class KnowledgeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=180)
    content: str | None = Field(default=None, min_length=1, max_length=8_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    source: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("title", "content", "source")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_optional_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            tag = item.strip()
            if not tag:
                continue
            marker = tag.lower()
            if marker in seen:
                continue
            seen.add(marker)
            normalized.append(tag[:40])
        return normalized[:20]


class KnowledgeDeleteResponse(BaseModel):
    requestId: str
    deleted: bool


class RagSearchConfig(BaseModel):
    topK: int
    threshold: float
    updatedAt: str


class RagSearchConfigResponse(BaseModel):
    requestId: str
    config: RagSearchConfig


class RagSearchConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topK: int | None = Field(default=None, ge=1, le=20)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class KnowledgeItem(BaseModel):
    id: int
    title: str
    content: str
    snippet: str
    tags: list[str]
    source: str
    score: float = 0.0
    matchedTerms: list[str] = Field(default_factory=list)
    createdAt: str
    updatedAt: str
    updatedByScope: str


class KnowledgeItemResponse(BaseModel):
    requestId: str
    item: KnowledgeItem


class KnowledgeListResponse(BaseModel):
    requestId: str
    total: int
    items: list[KnowledgeItem]


class InterviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jdText: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    resumeText: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    resumeId: int | None = Field(default=None, ge=1)
    versionNo: int | None = Field(default=None, ge=1)
    questionCount: int = Field(default=5, ge=3, le=8)

    @field_validator("jdText", "resumeText")
    @classmethod
    def normalize_interview_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"\u0000", "", value).strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_resume_source(self) -> "InterviewCreateRequest":
        if self.versionNo is not None and self.resumeId is None:
            raise ValueError("versionNo requires resumeId")
        return self


class InterviewQuestion(BaseModel):
    index: int
    category: str
    question: str
    focus: str


class InterviewSession(BaseModel):
    id: int
    sessionToken: str
    status: Literal["active", "paused", "finished"]
    questionCount: int
    answeredCount: int
    currentIndex: int
    finalScore: int | None = None
    recommendations: list[str] = Field(default_factory=list)
    summary: str | None = None
    createdAt: str
    updatedAt: str


class InterviewCreateResponse(BaseModel):
    requestId: str
    session: InterviewSession
    nextQuestion: InterviewQuestion | None
    degraded: bool = False


class InterviewNextResponse(BaseModel):
    requestId: str
    session: InterviewSession
    nextQuestion: InterviewQuestion | None
    degraded: bool = False


class InterviewSessionListResponse(BaseModel):
    requestId: str
    total: int
    items: list[InterviewSession]


class InterviewSessionDetailResponse(BaseModel):
    requestId: str
    session: InterviewSession
    nextQuestion: InterviewQuestion | None
    feedbackDraft: InterviewFeedbackDraft | None = None


class InterviewResultListResponse(BaseModel):
    requestId: str
    total: int
    items: list[InterviewSession]


class InterviewResultDetailResponse(BaseModel):
    requestId: str
    session: InterviewSession
    feedbackDraft: InterviewFeedbackDraft | None = None


class InterviewAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerText: str = Field(min_length=1, max_length=6_000)
    questionIndex: int | None = Field(default=None, ge=0)

    @field_validator("answerText")
    @classmethod
    def normalize_answer_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("answerText cannot be blank")
        return normalized


class InterviewAnswerEvaluation(BaseModel):
    answerScore: int
    strengths: list[str]
    improvements: list[str]
    followUpQuestion: str | None = None


class InterviewAnswerResponse(BaseModel):
    requestId: str
    session: InterviewSession
    evaluation: InterviewAnswerEvaluation
    nextQuestion: InterviewQuestion | None


class InterviewFeedbackDraft(BaseModel):
    overallScore: int
    dimensionScores: dict[str, int]
    strengths: list[str]
    gaps: list[str]
    improvementPlan: list[str]
    summary: str


class InterviewFinishResponse(BaseModel):
    requestId: str
    session: InterviewSession
    feedbackDraft: InterviewFeedbackDraft


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: int
    reset_seconds: int
    message: str | None = None


class AuthLoginRateLimiter:
    def __init__(self, *, fail_limit: int, window_seconds: int, lock_seconds: int):
        self.fail_limit = max(2, int(fail_limit))
        self.window_seconds = max(10, int(window_seconds))
        self.lock_seconds = max(10, int(lock_seconds))
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = Lock()

    def _cleanup(self, key: str, now: float) -> deque[float]:
        queue = self._failures[key]
        while queue and now - queue[0] > self.window_seconds:
            queue.popleft()
        if not queue:
            self._failures.pop(key, None)
            return deque()
        return queue

    def check(self, *, key: str) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            blocked_until = float(self._blocked_until.get(key, 0.0))
            if blocked_until > now:
                reset_seconds = int(max(1, blocked_until - now))
                queue = self._cleanup(key, now)
                remaining = max(0, self.fail_limit - len(queue))
                return RateLimitDecision(
                    allowed=False,
                    remaining=remaining,
                    reset_seconds=reset_seconds,
                    message=f"Too many failed login attempts. Retry in {reset_seconds}s",
                )

            if blocked_until:
                self._blocked_until.pop(key, None)

            queue = self._cleanup(key, now)
            remaining = max(0, self.fail_limit - len(queue))
            return RateLimitDecision(
                allowed=True,
                remaining=remaining,
                reset_seconds=self.window_seconds,
            )

    def register_failure(self, *, key: str) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            queue = self._cleanup(key, now)
            if not queue:
                queue = self._failures[key]

            queue.append(now)
            if len(queue) >= self.fail_limit:
                blocked_until = now + self.lock_seconds
                self._blocked_until[key] = blocked_until
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    reset_seconds=self.lock_seconds,
                    message=f"Too many failed login attempts. Retry in {self.lock_seconds}s",
                )

            remaining = max(0, self.fail_limit - len(queue))
            return RateLimitDecision(
                allowed=True,
                remaining=remaining,
                reset_seconds=self.window_seconds,
            )

    def register_success(self, *, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)


class SessionRateLimiter:
    def __init__(self, *, limit: int, window_seconds: int, duplicate_limit: int, duplicate_window_seconds: int):
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self.duplicate_limit = max(2, duplicate_limit)
        self.duplicate_window_seconds = max(1, duplicate_window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._duplicates: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def consume(self, *, session_id: str, payload_signature: str) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            queue = self._hits[session_id]
            while queue and now - queue[0] > self.window_seconds:
                queue.popleft()

            hard_limit_enabled = self.limit <= 10 or get_env_bool("CAREER_HERO_ENFORCE_HARD_RATE_LIMIT", False)
            if hard_limit_enabled and len(queue) >= self.limit:
                reset_seconds = int(max(1, self.window_seconds - (now - queue[0])))
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    reset_seconds=reset_seconds,
                    message=f"Rate limit exceeded. Retry in {reset_seconds}s",
                )

            duplicate_key = (session_id, payload_signature)
            duplicate_queue = self._duplicates[duplicate_key]
            while duplicate_queue and now - duplicate_queue[0] > self.duplicate_window_seconds:
                duplicate_queue.popleft()

            if len(duplicate_queue) >= self.duplicate_limit:
                reset_seconds = int(
                    max(1, self.duplicate_window_seconds - (now - duplicate_queue[0]))
                )
                return RateLimitDecision(
                    allowed=False,
                    remaining=max(0, self.limit - len(queue)),
                    reset_seconds=reset_seconds,
                    message="Too many repeated submissions. Please adjust input and retry later.",
                )

            queue.append(now)
            duplicate_queue.append(now)
            remaining = max(0, self.limit - len(queue))
            return RateLimitDecision(allowed=True, remaining=remaining, reset_seconds=self.window_seconds)


class MetricsTracker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._request_total = 0
        self._path_counts: dict[str, int] = defaultdict(int)
        self._status_counts: dict[str, int] = defaultdict(int)
        self._error_counts: dict[str, int] = defaultdict(int)
        self._latencies_by_path: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=500))

    def record(
        self,
        *,
        path: str,
        status: int,
        duration_ms: int,
        error_code: str | None,
    ) -> None:
        with self._lock:
            self._request_total += 1
            self._path_counts[path] += 1
            self._status_counts[str(status)] += 1
            self._latencies_by_path[path].append(max(0, duration_ms))
            if error_code:
                self._error_counts[error_code] += 1

    @staticmethod
    def _percentile(values: list[int], p: float) -> int:
        if not values:
            return 0
        ranked = sorted(values)
        idx = int(round((len(ranked) - 1) * p))
        return ranked[max(0, min(idx, len(ranked) - 1))]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latency = {
                path: {
                    "count": len(values),
                    "p50_ms": self._percentile(list(values), 0.5),
                    "p95_ms": self._percentile(list(values), 0.95),
                }
                for path, values in self._latencies_by_path.items()
            }
            return {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "requestTotal": self._request_total,
                "pathCounts": dict(self._path_counts),
                "statusCounts": dict(self._status_counts),
                "errorCounts": dict(self._error_counts),
                "latency": latency,
            }


@dataclass
class AnalysisResult:
    score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    suggestions: list[str]
    optimized_resume: str
    score_breakdown: ScoreBreakdown
    insights: AnalysisInsights
    analysis_source: Literal["rule", "gemini"]
    fallback_used: bool
    diagnostic_breakdown: DiagnosticBreakdown | None = None
    issue_classifications: list[IssueClassification] = dataclass_field(default_factory=list)
    pip_advice: list[PipAdviceItem] = dataclass_field(default_factory=list)


@dataclass
class ResumeParseResult:
    status: Literal["pending", "parsed", "failed"]
    parsed_text: str
    metadata: dict[str, Any]
    failure_reason: str | None


RATE_LIMIT_PER_MINUTE = get_env_int("CAREER_HERO_RATE_LIMIT_PER_MINUTE", 20, min_value=1, max_value=500)
DUPLICATE_SUBMIT_LIMIT = get_env_int("CAREER_HERO_DUPLICATE_LIMIT", 3, min_value=2, max_value=50)
GEMINI_ENABLED = get_env_bool("CAREER_HERO_GEMINI_ENABLED", True)
RATE_LIMITER = SessionRateLimiter(
    limit=RATE_LIMIT_PER_MINUTE,
    window_seconds=60,
    duplicate_limit=DUPLICATE_SUBMIT_LIMIT,
    duplicate_window_seconds=15,
)
AUTH_LOGIN_RATE_LIMITER = AuthLoginRateLimiter(
    fail_limit=AUTH_LOGIN_FAIL_LIMIT,
    window_seconds=AUTH_LOGIN_FAIL_WINDOW_SECONDS,
    lock_seconds=AUTH_LOGIN_LOCK_SECONDS,
)
METRICS = MetricsTracker()


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-zA-Z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    return [token for token in tokens if token not in STOPWORDS]


def top_keywords(tokens: list[str], limit: int = 30) -> list[str]:
    freq = Counter(tokens)
    ranked = sorted(freq.items(), key=lambda item: (-item[1], item[0]))
    return [key for key, _ in ranked[:limit]]


def build_optimized_resume(resume_text: str, missing_keywords: list[str]) -> str:
    additions = [
        f"- 在相关项目中补充对 {kw} 的实践：场景 + 动作 + 结果（含量化指标）"
        for kw in missing_keywords[:6]
    ]
    if not additions:
        return resume_text.strip()
    return f"{resume_text.strip()}\n\n【建议补充条目】\n" + "\n".join(additions)


def estimate_writing_quality_stub(resume_text: str) -> int:
    chunks = [segment.strip() for segment in re.split(r"[。！？.!?\n]+", resume_text) if segment.strip()]
    avg_chunk_length = sum(len(chunk) for chunk in chunks) / max(1, len(chunks))

    score = 55
    if 18 <= avg_chunk_length <= 90:
        score += 10
    if re.search(r"\d", resume_text):
        score += 10
    if len(resume_text) >= 200:
        score += 8
    if resume_text.count("-") + resume_text.count("•") >= 3:
        score += 7

    return int(max(30, min(92, round(score))))


def to_excerpt(text: str, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def resume_hash_or_excerpt(resume_text: str) -> str:
    digest = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest} | {to_excerpt(resume_text, limit=90)}"


def infer_resume_title(*, provided_title: str | None, filename: str | None) -> str:
    if provided_title and provided_title.strip():
        return provided_title.strip()[:MAX_RESUME_TITLE_LENGTH]

    if filename:
        raw = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = raw.rsplit(".", 1)[0].strip()
        if stem:
            return stem[:MAX_RESUME_TITLE_LENGTH]

    return f"Imported Resume {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def parse_resume_txt(content: str) -> ResumeParseResult:
    normalized = re.sub(r"\u0000", "", content).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ResumeParseResult(
            status="failed",
            parsed_text="",
            metadata={"source": "txt", "parserVersion": RESUME_TXT_PARSER_VERSION},
            failure_reason="empty txt content",
        )

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return ResumeParseResult(
            status="failed",
            parsed_text="",
            metadata={"source": "txt", "parserVersion": RESUME_TXT_PARSER_VERSION},
            failure_reason="empty txt content",
        )

    first_line = lines[0]
    name_candidate = ""
    if len(first_line) <= 40 and not re.search(r"[:：@]", first_line):
        name_candidate = first_line.strip("•-* ")

    email_match = EMAIL_PATTERN.search(normalized)
    phone_match = PHONE_PATTERN.search(normalized)

    section_tokens = {
        "skills": ("skills", "skill", "技能", "技术栈", "能力", "擅长"),
        "experience": ("experience", "工作经历", "项目经历", "经历"),
        "education": ("education", "教育", "学历"),
    }
    section_data: dict[str, list[str]] = {"skills": [], "experience": [], "education": []}
    highlights: list[str] = []
    current_section: str | None = None

    for raw_line in lines:
        cleaned = raw_line.strip("•-* ")
        lowered = cleaned.lower()

        matched_section = None
        for key, markers in section_tokens.items():
            if any(marker in lowered for marker in markers):
                matched_section = key
                break

        if matched_section:
            current_section = matched_section
            continue

        if current_section:
            section_data[current_section].append(cleaned)

        if len(highlights) < 6 and len(cleaned) >= 6:
            highlights.append(cleaned)

    skill_candidates: list[str] = []
    for value in section_data["skills"]:
        parts = re.split(r"[,，、/|；;\s]+", value)
        for part in parts:
            token = part.strip()
            if 1 < len(token) <= 30:
                skill_candidates.append(token)

    dedup_skills: list[str] = []
    seen_skills: set[str] = set()
    for skill in skill_candidates:
        marker = skill.lower()
        if marker in seen_skills:
            continue
        seen_skills.add(marker)
        dedup_skills.append(skill)
        if len(dedup_skills) >= 20:
            break

    summary_lines = lines[: min(3, len(lines))]
    summary = " ".join(summary_lines)

    parsed_structured = {
        "name": name_candidate or None,
        "email": email_match.group(0) if email_match else None,
        "phone": phone_match.group(0) if phone_match else None,
        "summary": summary,
        "skills": dedup_skills,
        "highlights": highlights[:5],
        "sections": {
            "experience": section_data["experience"][:5],
            "education": section_data["education"][:5],
        },
    }

    parsed_text_parts = [
        f"姓名: {parsed_structured['name']}" if parsed_structured["name"] else "",
        f"邮箱: {parsed_structured['email']}" if parsed_structured["email"] else "",
        f"电话: {parsed_structured['phone']}" if parsed_structured["phone"] else "",
        f"摘要: {parsed_structured['summary']}" if parsed_structured["summary"] else "",
        (
            "技能: " + ", ".join(parsed_structured["skills"])
            if parsed_structured["skills"]
            else ""
        ),
    ]
    parsed_text = "\n".join([item for item in parsed_text_parts if item]).strip()

    return ResumeParseResult(
        status="parsed",
        parsed_text=parsed_text,
        metadata={
            "source": "txt",
            "parserVersion": RESUME_TXT_PARSER_VERSION,
            "lineCount": len(lines),
            "structured": parsed_structured,
        },
        failure_reason=None,
    )


def resolve_resume_text(
    payload: AnalyzeRequest,
    *,
    request: Request | None = None,
    include_all_users: bool = False,
) -> str:
    if payload.resumeId is None:
        return payload.resumeText or ""

    owner_scope_id = get_owner_scope_id(request) if request is not None else None
    version = fetch_resume_version_content(
        resume_id=payload.resumeId,
        version_no=payload.versionNo,
        owner_scope_id=owner_scope_id,
        include_all_users=include_all_users,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="resume/version not found")

    content = str(version.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="selected resume content is empty")

    return content


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def get_session_id(request: Request) -> str:
    return getattr(request.state, "session_id", "anonymous")


def get_current_user(request: Request) -> dict[str, Any] | None:
    user = getattr(request.state, "current_user", None)
    return user if isinstance(user, dict) else None


def get_session_scope_id(request: Request) -> str:
    scope = getattr(request.state, "session_scope_id", "")
    if isinstance(scope, str) and scope.strip():
        return scope
    return f"session:{get_session_id(request)}"


def get_user_scope_id(request: Request) -> str:
    scope = getattr(request.state, "user_scope_id", "")
    if isinstance(scope, str) and scope.strip():
        return scope
    return "anonymous"


def get_owner_scope_id(request: Request) -> str:
    scope = getattr(request.state, "owner_scope_id", "")
    if isinstance(scope, str) and scope.strip():
        return scope

    user_scope = get_user_scope_id(request)
    if user_scope.startswith("user:"):
        return user_scope

    return get_session_scope_id(request)


def require_current_user(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    if user is None:
        raise_api_error(status_code=401, code="AUTH_LOGIN_REQUIRED", message="login required")
    return user


def parse_iso_datetime(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def build_auth_context(
    *,
    request: Request,
    user: dict[str, Any] | None,
    expires_at: str | None,
) -> AuthContext:
    expires_dt = parse_iso_datetime(expires_at)
    now = datetime.now(timezone.utc)
    ttl_seconds: int | None = None
    is_expired = False

    if expires_dt is not None:
        ttl_seconds = int((expires_dt - now).total_seconds())
        is_expired = ttl_seconds <= 0
        if ttl_seconds is not None and ttl_seconds < 0:
            ttl_seconds = 0

    auth_user: AuthUser | None = None
    if isinstance(user, dict):
        auth_user = AuthUser(
            id=int(user.get("id", 0)),
            username=str(user.get("username", "")),
        )

    return AuthContext(
        mode=get_auth_mode(),
        user=auth_user,
        session=AuthContextSession(id=get_session_id(request), scope=get_owner_scope_id(request)),
        expiry=AuthContextExpiry(
            expiresAt=expires_at or None,
            ttlSeconds=ttl_seconds,
            isExpired=is_expired,
        ),
    )


def build_login_rate_limiter_key(*, request: Request, username: str) -> str:
    safe_username = username.strip().lower()
    client_host = ""
    if request.client is not None and request.client.host:
        client_host = request.client.host.strip()
    if not client_host:
        client_host = get_session_id(request)
    return f"{safe_username}|{client_host}"


def can_access_all_sessions(request: Request) -> bool:
    if not is_cross_session_access_allowed():
        return False

    if get_auth_mode() == "local":
        return True

    expected = get_expected_api_token()
    provided = parse_request_token(request)
    return bool(expected) and provided == expected


def resolve_include_all_sessions(request: Request, requested: bool) -> bool:
    if not requested:
        return False
    if not can_access_all_sessions(request):
        raise HTTPException(status_code=403, detail="cross-session access is forbidden")
    return True


def resolve_effective_include_all_sessions(request: Request, requested: bool) -> bool:
    # Wave5: default to current-user isolation unless caller explicitly asks and has permission.
    return resolve_include_all_sessions(request, requested)


def interview_scope(request: Request) -> tuple[str, bool]:
    owner_scope_id = get_owner_scope_id(request)
    return owner_scope_id, False


def set_error_context(request: Request, *, error_code: str, exception_type: str) -> None:
    request.state.error_code = error_code
    request.state.exception_type = exception_type


def build_error_payload(*, code: str, message: str, request_id: str) -> dict[str, str]:
    return {
        "code": code,
        "message": message,
        "requestId": request_id,
    }


def raise_api_error(
    *,
    status_code: int,
    code: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    detail: dict[str, Any] = {"code": code, "message": message}
    if isinstance(extra, dict):
        detail.update(extra)
    raise HTTPException(status_code=status_code, detail=detail)


def normalize_score_breakdown(raw: object) -> ScoreBreakdown:
    if isinstance(raw, dict):
        return ScoreBreakdown(
            keyword_match=max(0, min(100, int(raw.get("keyword_match", 0)))),
            coverage=max(0, min(100, int(raw.get("coverage", 0)))),
            writing_quality_stub=max(0, min(100, int(raw.get("writing_quality_stub", 0)))),
        )
    return ScoreBreakdown(keyword_match=0, coverage=0, writing_quality_stub=0)


def normalize_keywords(raw: object, *, limit: int = 20) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def normalize_suggestions(raw: object, *, default_missing: list[str]) -> list[str]:
    suggestions = normalize_keywords(raw, limit=8)
    if suggestions:
        return suggestions

    fallback = [
        "每条经历建议写成：场景 + 动作 + 量化结果（如提升%、节省时间、收入增长）",
        "与目标 JD 直接相关的项目放在前 1-2 屏，减少无关内容",
    ]
    if default_missing:
        fallback.insert(0, f"优先补充关键词：{', '.join(default_missing[:6])}")
    return fallback


def normalize_insights(raw: object, *, score: int, missing_keywords: list[str]) -> AnalysisInsights:
    if isinstance(raw, dict):
        summary = str(raw.get("summary", "")).strip()
        strengths = normalize_keywords(raw.get("strengths", []), limit=5)
        risks = normalize_keywords(raw.get("risks", []), limit=5)
        if summary:
            return AnalysisInsights(summary=summary, strengths=strengths, risks=risks)

    risks = []
    if missing_keywords:
        risks.append(f"关键词覆盖仍有缺口：{', '.join(missing_keywords[:4])}")
    if score < 70:
        risks.append("整体匹配分偏低，建议补充更贴合 JD 的项目经历")

    return AnalysisInsights(
        summary=f"当前简历与目标 JD 的综合匹配度为 {score}/100。",
        strengths=[],
        risks=risks,
    )


def clamp_score(value: float | int, *, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(float(value)))))


def build_diagnostic_breakdown(
    *,
    resume_text: str,
    matched_keywords: list[str],
    missing_keywords: list[str],
    writing_quality_stub: int,
) -> DiagnosticBreakdown:
    lines = [line.strip("•-* ") for line in re.split(r"[\n]+", resume_text) if line.strip()]
    if not lines:
        lines = [segment.strip() for segment in re.split(r"[。！？.!?]+", resume_text) if segment.strip()]

    quantified_lines = sum(1 for line in lines if re.search(r"\d", line))
    vague_lines = sum(1 for line in lines if any(marker in line.lower() for marker in VAGUE_MARKERS))
    action_lines = sum(1 for line in lines if any(marker in line.lower() for marker in ACTION_MARKERS))

    keyword_coverage = clamp_score((len(matched_keywords) / max(1, len(matched_keywords) + len(missing_keywords))) * 100)
    quantified_impact = clamp_score((quantified_lines / max(1, len(lines))) * 100)

    clarity_raw = writing_quality_stub * 0.6 + (100 - clamp_score((vague_lines / max(1, len(lines))) * 100)) * 0.4
    expression_clarity = clamp_score(clarity_raw)

    jd_relevance_raw = keyword_coverage * 0.7 + clamp_score((action_lines / max(1, len(lines))) * 100) * 0.3
    jd_relevance = clamp_score(jd_relevance_raw)

    return DiagnosticBreakdown(
        keywordCoverage=keyword_coverage,
        quantifiedImpact=quantified_impact,
        expressionClarity=expression_clarity,
        jdRelevance=jd_relevance,
    )


def classify_resume_issues(*, resume_text: str, missing_keywords: list[str]) -> list[IssueClassification]:
    lines = [line.strip("•-* ") for line in re.split(r"[\n]+", resume_text) if line.strip()]
    if not lines:
        lines = [segment.strip() for segment in re.split(r"[。！？.!?]+", resume_text) if segment.strip()]

    issue_list: list[IssueClassification] = []

    vague_samples: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in VAGUE_MARKERS) and not re.search(r"\d", line):
            vague_samples.append(line)
        if len(vague_samples) >= 3:
            break

    if vague_samples:
        severity = "high" if len(vague_samples) >= 3 else ("medium" if len(vague_samples) == 2 else "low")
        issue_list.append(
            IssueClassification(
                type="模糊描述",
                severity=severity,
                evidence=vague_samples,
                recommendation="将'负责/参与'改写为具体动作与结果，使用'动作 + 场景 + 产出'句式。",
            )
        )

    non_empty_lines = [line for line in lines if len(line) >= 6]
    quantified_lines = [line for line in non_empty_lines if re.search(r"\d", line)]
    quant_ratio = len(quantified_lines) / max(1, len(non_empty_lines))
    if quant_ratio < 0.4:
        evidence = non_empty_lines[:3] if non_empty_lines else [to_excerpt(resume_text, limit=80)]
        severity = "high" if quant_ratio < 0.2 else "medium"
        issue_list.append(
            IssueClassification(
                type="缺量化",
                severity=severity,
                evidence=evidence,
                recommendation="至少为核心经历补充可量化指标（如增长%、成本、时延、人效、收入）。",
            )
        )

    if missing_keywords:
        evidence = [f"未命中关键词：{', '.join(missing_keywords[:6])}"]
        severity = "high" if len(missing_keywords) >= 8 else "medium"
        issue_list.append(
            IssueClassification(
                type="关键词缺失",
                severity=severity,
                evidence=evidence,
                recommendation="围绕目标JD优先补齐缺失关键词对应的项目经历与技术方案。",
            )
        )

    return issue_list


def build_pip_advice(
    *,
    issues: list[IssueClassification],
    missing_keywords: list[str],
    diagnostic_breakdown: DiagnosticBreakdown,
) -> list[PipAdviceItem]:
    advice_items: list[PipAdviceItem] = []

    issue_map = {item.type: item for item in issues}

    if "模糊描述" in issue_map:
        advice_items.append(
            PipAdviceItem(
                finding="经历描述存在泛化措辞，招聘方难判断真实贡献。",
                improvement="将1-2条核心经历改写为STAR：背景(1句)+动作(2句)+结果(1句)。",
                practice="练习：挑选最近项目，写出3条'我做了什么+带来什么变化'的子弹句。",
            )
        )

    if "缺量化" in issue_map or diagnostic_breakdown.quantifiedImpact < 45:
        advice_items.append(
            PipAdviceItem(
                finding="量化信息不足，影响说服力与可比性。",
                improvement="为每段项目补一个业务指标与一个技术指标（如QPS、时延、转化率）。",
                practice="练习：给每条项目经历补齐'前后对比'数字，至少3条。",
            )
        )

    if missing_keywords:
        advice_items.append(
            PipAdviceItem(
                finding=f"与目标JD存在关键词缺口：{', '.join(missing_keywords[:5])}",
                improvement="新增一个与目标岗位最相关的项目条目，显式体现缺失关键词。",
                practice="练习：按'关键词→场景→结果'写5句关键经历，准备面试追问。",
            )
        )

    if not advice_items:
        advice_items.append(
            PipAdviceItem(
                finding="简历基础较完整，但仍可提升表达竞争力。",
                improvement="将最相关项目前置，并补充业务影响范围。",
                practice="练习：做一次60秒自我介绍，强调岗位匹配与代表性成果。",
            )
        )

    return advice_items[:4]


def ensure_analysis_enrichment(result: AnalysisResult, *, resume_text: str) -> AnalysisResult:
    if result.diagnostic_breakdown is None:
        result.diagnostic_breakdown = build_diagnostic_breakdown(
            resume_text=resume_text,
            matched_keywords=result.matched_keywords,
            missing_keywords=result.missing_keywords,
            writing_quality_stub=result.score_breakdown.writing_quality_stub,
        )

    if not result.issue_classifications:
        result.issue_classifications = classify_resume_issues(
            resume_text=resume_text,
            missing_keywords=result.missing_keywords,
        )

    if not result.pip_advice:
        result.pip_advice = build_pip_advice(
            issues=result.issue_classifications,
            missing_keywords=result.missing_keywords,
            diagnostic_breakdown=result.diagnostic_breakdown,
        )

    return result


def format_rag_hit(raw: dict[str, Any]) -> RagHit:
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    matched_terms = raw.get("matched_terms") if isinstance(raw.get("matched_terms"), list) else []
    return RagHit(
        id=int(raw.get("id", 0)),
        title=str(raw.get("title", "")),
        snippet=str(raw.get("snippet", "")),
        source=str(raw.get("source", "manual")),
        tags=[str(item) for item in tags if isinstance(item, str)],
        score=float(raw.get("score", 0.0)),
        matchedTerms=[str(item) for item in matched_terms if isinstance(item, str)],
    )


def build_rag_query(*, jd_text: str, missing_keywords: list[str]) -> str:
    if missing_keywords:
        return f"{jd_text}\n鍏抽敭璇嶏細{' '.join(missing_keywords[:8])}"
    return jd_text


def compute_rule_based_analysis(*, resume_text: str, jd_text: str) -> AnalysisResult:
    resume_tokens = tokenize(resume_text)
    jd_tokens = tokenize(jd_text)

    if not resume_tokens or not jd_tokens:
        raise HTTPException(status_code=400, detail="Input text is too short after normalization")

    resume_set = set(resume_tokens)
    jd_token_set = set(jd_tokens)
    jd_ranked = top_keywords(jd_tokens, 40)

    matched = [kw for kw in jd_ranked if kw in resume_set][:20]
    missing = [kw for kw in jd_ranked if kw not in resume_set][:20]

    keyword_match = int(round((len(matched) / max(1, len(jd_ranked))) * 100))
    coverage = int(round((len(resume_set & jd_token_set) / max(1, len(jd_token_set))) * 100))
    writing_quality_stub = estimate_writing_quality_stub(resume_text)

    score_raw = keyword_match * 0.65 + coverage * 0.25 + writing_quality_stub * 0.10
    score = int(round(max(5, min(98, score_raw))))

    suggestions = normalize_suggestions([], default_missing=missing)
    optimized_resume = build_optimized_resume(resume_text, missing)
    breakdown = ScoreBreakdown(
        keyword_match=keyword_match,
        coverage=coverage,
        writing_quality_stub=writing_quality_stub,
    )
    insights = normalize_insights({}, score=score, missing_keywords=missing)

    diagnostic_breakdown = build_diagnostic_breakdown(
        resume_text=resume_text,
        matched_keywords=matched,
        missing_keywords=missing,
        writing_quality_stub=writing_quality_stub,
    )
    issue_classifications = classify_resume_issues(resume_text=resume_text, missing_keywords=missing)
    pip_advice = build_pip_advice(
        issues=issue_classifications,
        missing_keywords=missing,
        diagnostic_breakdown=diagnostic_breakdown,
    )

    return AnalysisResult(
        score=score,
        matched_keywords=matched,
        missing_keywords=missing,
        suggestions=suggestions,
        optimized_resume=optimized_resume,
        score_breakdown=breakdown,
        insights=insights,
        analysis_source="rule",
        fallback_used=False,
        diagnostic_breakdown=diagnostic_breakdown,
        issue_classifications=issue_classifications,
        pip_advice=pip_advice,
    )


def extract_json_from_text(raw: str) -> dict[str, Any]:
    content = raw.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()

    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]

    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("gemini response is not json object")
    return parsed


def build_gemini_prompt(*, resume_text: str, jd_text: str, seed: AnalysisResult) -> str:
    template = {
        "score": seed.score,
        "scoreBreakdown": seed.score_breakdown.model_dump(),
        "diagnosticBreakdown": (
            seed.diagnostic_breakdown.model_dump() if seed.diagnostic_breakdown else {}
        ),
        "issueClassifications": [item.model_dump() for item in seed.issue_classifications],
        "pipAdvice": [item.model_dump() for item in seed.pip_advice],
        "matchedKeywords": seed.matched_keywords,
        "missingKeywords": seed.missing_keywords,
        "suggestions": seed.suggestions,
        "optimizedResume": seed.optimized_resume,
        "insights": seed.insights.model_dump(),
    }

    return (
        "你是职业顾问。请基于简历与JD输出稳定JSON，不要输出解释文字。\n"
        "必须返回字段：score(0-100整数), scoreBreakdown(keyword_match/coverage/writing_quality_stub),"
        " diagnosticBreakdown(keywordCoverage/quantifiedImpact/expressionClarity/jdRelevance),"
        " issueClassifications(数组), pipAdvice(数组), matchedKeywords(数组), missingKeywords(数组),"
        " suggestions(数组), optimizedResume(字符串), insights(summary/strengths/risks)。\n"
        "优先保持与规则引擎相同或相近的数值尺度，避免波动过大。\n"
        f"promptVersion={PROMPT_VERSION}\n"
        "以下是规则引擎参考结果（可优化但结构不可变）：\n"
        f"{json.dumps(template, ensure_ascii=False)}\n\n"
        "[简历]\n"
        f"{resume_text}\n\n"
        "[JD]\n"
        f"{jd_text}\n"
    )


def normalize_diagnostic_breakdown(raw: object, *, seed: DiagnosticBreakdown) -> DiagnosticBreakdown:
    if isinstance(raw, dict):
        return DiagnosticBreakdown(
            keywordCoverage=clamp_score(raw.get("keywordCoverage", seed.keywordCoverage)),
            quantifiedImpact=clamp_score(raw.get("quantifiedImpact", seed.quantifiedImpact)),
            expressionClarity=clamp_score(raw.get("expressionClarity", seed.expressionClarity)),
            jdRelevance=clamp_score(raw.get("jdRelevance", seed.jdRelevance)),
        )
    return seed


def normalize_issue_classifications(
    raw: object,
    *,
    fallback: list[IssueClassification],
) -> list[IssueClassification]:
    if not isinstance(raw, list):
        return fallback

    result: list[IssueClassification] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        issue_type = str(item.get("type", "")).strip()
        if issue_type not in {"模糊描述", "缺量化", "关键词缺失"}:
            continue
        severity = str(item.get("severity", "medium")).strip().lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"

        evidence_raw = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence = [str(v).strip() for v in evidence_raw if isinstance(v, str) and str(v).strip()][:3]
        recommendation = str(item.get("recommendation", "")).strip() or "建议补充具体动作与结果描述。"

        result.append(
            IssueClassification(
                type=issue_type,
                severity=severity,
                evidence=evidence,
                recommendation=recommendation,
            )
        )

    return result or fallback


def normalize_pip_advice(raw: object, *, fallback: list[PipAdviceItem]) -> list[PipAdviceItem]:
    if not isinstance(raw, list):
        return fallback

    result: list[PipAdviceItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        finding = str(item.get("finding", "")).strip()
        improvement = str(item.get("improvement", "")).strip()
        practice = str(item.get("practice", "")).strip()
        if not finding or not improvement or not practice:
            continue
        result.append(PipAdviceItem(finding=finding, improvement=improvement, practice=practice))
        if len(result) >= 4:
            break

    return result or fallback


def call_gemini_analysis(*, resume_text: str, jd_text: str, seed: AnalysisResult) -> AnalysisResult:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip() or "gemini-1.5-flash"
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": build_gemini_prompt(resume_text=resume_text, jd_text=jd_text, seed=seed)}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    with httpx.Client(timeout=20.0) as client:
        response = client.post(endpoint, params={"key": api_key}, json=body)
        response.raise_for_status()
        payload = response.json()

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Gemini returned empty candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise RuntimeError("Gemini returned empty parts")

    text = str(parts[0].get("text", "")).strip()
    if not text:
        raise RuntimeError("Gemini response text is empty")

    parsed = extract_json_from_text(text)

    score = max(0, min(100, int(parsed.get("score", seed.score))))
    breakdown = normalize_score_breakdown(parsed.get("scoreBreakdown", seed.score_breakdown.model_dump()))
    matched = normalize_keywords(parsed.get("matchedKeywords", seed.matched_keywords), limit=20)
    missing = normalize_keywords(parsed.get("missingKeywords", seed.missing_keywords), limit=20)
    suggestions = normalize_suggestions(parsed.get("suggestions", seed.suggestions), default_missing=missing)
    optimized_resume = str(parsed.get("optimizedResume", seed.optimized_resume)).strip() or seed.optimized_resume
    insights = normalize_insights(parsed.get("insights", seed.insights.model_dump()), score=score, missing_keywords=missing)

    seed_diagnostic = seed.diagnostic_breakdown or build_diagnostic_breakdown(
        resume_text=resume_text,
        matched_keywords=seed.matched_keywords,
        missing_keywords=seed.missing_keywords,
        writing_quality_stub=seed.score_breakdown.writing_quality_stub,
    )
    diagnostic_breakdown = normalize_diagnostic_breakdown(
        parsed.get("diagnosticBreakdown"),
        seed=seed_diagnostic,
    )
    issue_classifications = normalize_issue_classifications(
        parsed.get("issueClassifications"),
        fallback=seed.issue_classifications,
    )
    pip_advice = normalize_pip_advice(
        parsed.get("pipAdvice"),
        fallback=seed.pip_advice,
    )

    return AnalysisResult(
        score=score,
        matched_keywords=matched,
        missing_keywords=missing,
        suggestions=suggestions,
        optimized_resume=optimized_resume,
        score_breakdown=breakdown,
        insights=insights,
        analysis_source="gemini",
        fallback_used=False,
        diagnostic_breakdown=diagnostic_breakdown,
        issue_classifications=issue_classifications,
        pip_advice=pip_advice,
    )


def is_gemini_enabled() -> bool:
    return get_env_bool("CAREER_HERO_GEMINI_ENABLED", GEMINI_ENABLED)


def run_analysis(*, resume_text: str, jd_text: str) -> AnalysisResult:
    provider = os.getenv("CAREER_HERO_AI_PROVIDER", "rule").strip().lower()
    base = compute_rule_based_analysis(resume_text=resume_text, jd_text=jd_text)

    if provider not in {"rule", "gemini", "auto"}:
        provider = "rule"

    if provider == "rule" or not is_gemini_enabled():
        return ensure_analysis_enrichment(base, resume_text=resume_text)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    should_try_gemini = provider == "gemini" or (provider == "auto" and bool(api_key))
    if not should_try_gemini:
        return ensure_analysis_enrichment(base, resume_text=resume_text)

    try:
        result = call_gemini_analysis(resume_text=resume_text, jd_text=jd_text, seed=base)
        return ensure_analysis_enrichment(result, resume_text=resume_text)
    except Exception as exc:
        logger.warning(
            json.dumps(
                {
                    "event": "gemini_fallback",
                    "reason": str(exc),
                },
                ensure_ascii=False,
            )
        )
        base.fallback_used = True
        return ensure_analysis_enrichment(base, resume_text=resume_text)


def payload_signature(payload: AnalyzeRequest) -> str:
    resume_ref = ""
    if payload.resumeId is not None:
        version_label = payload.versionNo if payload.versionNo is not None else "latest"
        resume_ref = f"resume:{payload.resumeId}:{version_label}"

    digest = hashlib.sha256(
        (
            f"{resume_ref}\n{payload.resumeText or ''}\n{payload.jdText}\n"
            f"rag:{int(payload.ragEnabled)}:{payload.ragTopK if payload.ragTopK is not None else 'cfg'}:{payload.ragThreshold if payload.ragThreshold is not None else 'cfg'}"
        ).encode("utf-8")
    ).hexdigest()
    return digest[:24]


def apply_rate_limit_or_raise(*, request: Request, payload: AnalyzeRequest) -> None:
    decision = RATE_LIMITER.consume(
        session_id=get_session_id(request),
        payload_signature=payload_signature(payload),
    )
    request.state.rate_limit = {
        "x-ratelimit-limit": str(RATE_LIMITER.limit),
        "x-ratelimit-remaining": str(decision.remaining),
        "x-ratelimit-reset-sec": str(decision.reset_seconds),
    }
    if not decision.allowed:
        raise HTTPException(status_code=429, detail=decision.message or "Rate limit exceeded")


def format_history_item(row: dict[str, Any]) -> HistoryItem:
    return HistoryItem(
        id=row["id"],
        createdAt=row["created_at"],
        resumeTextHashOrExcerpt=row["resume_text_hash_or_excerpt"],
        jdExcerpt=row["jd_excerpt"],
        score=row["score"],
        scoreBreakdown=normalize_score_breakdown(row.get("score_breakdown")),
        keywordSummary=KeywordSummary(
            matched=normalize_keywords(row.get("matched_keywords"), limit=5),
            missing=normalize_keywords(row.get("missing_keywords"), limit=5),
        ),
        analysisSource=str(row.get("analysis_source", "rule")),
        requestId=row["request_id"],
    )


def build_history_detail_from_row(row: dict[str, Any]) -> HistoryDetail:
    return HistoryDetail(
        id=row["id"],
        createdAt=row["created_at"],
        resumeTextHashOrExcerpt=row["resume_text_hash_or_excerpt"],
        jdExcerpt=row["jd_excerpt"],
        score=row["score"],
        scoreBreakdown=normalize_score_breakdown(row.get("score_breakdown")),
        matchedKeywords=normalize_keywords(row.get("matched_keywords"), limit=20),
        missingKeywords=normalize_keywords(row.get("missing_keywords"), limit=20),
        suggestions=normalize_suggestions(row.get("suggestions"), default_missing=[]),
        optimizedResume=str(row.get("optimized_resume", "")),
        insights=normalize_insights(row.get("insights"), score=row["score"], missing_keywords=[]),
        analysisSource=str(row.get("analysis_source", "rule")),
        sessionId=str(row.get("session_id", "anonymous")),
        requestId=row["request_id"],
    )


def to_resume_preview(content: str, *, limit: int = 120) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def format_resume_item(row: dict[str, Any]) -> ResumeItem:
    content_updated_at = str(row.get("content_updated_at", "") or row.get("latest_version_created_at", "") or row.get("updated_at", ""))
    return ResumeItem(
        id=int(row["id"]),
        title=str(row["title"]),
        latestVersionNo=int(row["latest_version_no"]),
        contentUpdatedAt=content_updated_at,
        createdAt=str(row["created_at"]),
        updatedAt=str(row["updated_at"]),
        latestParseStatus=str(row.get("latest_parse_status", "pending")),
        latestContentPreview=to_resume_preview(str(row.get("latest_content", ""))),
    )


def format_resume_version(row: dict[str, Any]) -> ResumeVersionItem:
    metadata = row.get("metadata")
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    failure_reason = row.get("failure_reason")
    safe_failure_reason = str(failure_reason).strip() if isinstance(failure_reason, str) and failure_reason else None
    return ResumeVersionItem(
        id=int(row["id"]),
        versionNo=int(row["version_no"]),
        content=str(row.get("content", "")),
        parseStatus=str(row.get("parse_status", "pending")),
        parsedText=str(row.get("parsed_text", "")),
        failureReason=safe_failure_reason,
        metadata=safe_metadata,
        createdAt=str(row.get("created_at", "")),
    )


def format_resume_detail(row: dict[str, Any]) -> ResumeDetail:
    versions = [format_resume_version(item) for item in row.get("versions", [])]
    current = row.get("current_version")
    current_version = format_resume_version(current) if isinstance(current, dict) else None
    content_updated_at = str(row.get("content_updated_at", "") or (current.get("created_at", "") if isinstance(current, dict) else "") or row.get("updated_at", ""))
    return ResumeDetail(
        id=int(row["id"]),
        title=str(row["title"]),
        latestVersionNo=int(row["latest_version_no"]),
        contentUpdatedAt=content_updated_at,
        createdAt=str(row["created_at"]),
        updatedAt=str(row["updated_at"]),
        currentVersion=current_version,
        versions=versions,
    )


def format_knowledge_item(row: dict[str, Any]) -> KnowledgeItem:
    content = str(row.get("content", ""))
    snippet = str(row.get("snippet", ""))
    if not snippet:
        snippet = content if len(content) <= 180 else f"{content[:180]}..."

    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    matched_terms = row.get("matched_terms") if isinstance(row.get("matched_terms"), list) else []

    return KnowledgeItem(
        id=int(row.get("id", 0)),
        title=str(row.get("title", "")),
        content=content,
        snippet=snippet,
        tags=[str(item) for item in tags if isinstance(item, str)],
        source=str(row.get("source", "manual")),
        score=float(row.get("score", 0.0)),
        matchedTerms=[str(item) for item in matched_terms if isinstance(item, str)],
        createdAt=str(row.get("created_at", "")),
        updatedAt=str(row.get("updated_at", "")),
        updatedByScope=str(row.get("updated_by_scope", "system")),
    )


def format_rag_search_config(config: dict[str, Any]) -> RagSearchConfig:
    return RagSearchConfig(
        topK=max(1, min(20, int(config.get("top_k", 5)))),
        threshold=max(0.0, min(1.0, float(config.get("score_threshold", 0.1)))),
        updatedAt=str(config.get("updated_at", "")),
    )


def to_resume_state_key(payload: AnalyzeRequest) -> int:
    if payload.resumeId is not None:
        return max(0, int(payload.resumeId))
    return 0


def format_diagnostic_flow_state(row: dict[str, Any]) -> DiagnosticFlowState:
    status = str(row.get("status", "jd_input")).strip().lower()
    if status not in set(DIAGNOSTIC_STATES):
        status = "jd_input"

    allowed_next_raw = row.get("allowed_next")
    allowed_next = []
    if isinstance(allowed_next_raw, list):
        for item in allowed_next_raw:
            value = str(item).strip().lower()
            if value in set(DIAGNOSTIC_STATES) and value not in allowed_next:
                allowed_next.append(value)

    if not allowed_next:
        allowed_next = [status]

    return DiagnosticFlowState(
        resumeId=max(0, int(row.get("resume_id", 0))),
        status=status,
        allowedNext=allowed_next,
        updatedAt=str(row.get("updated_at", "")),
    )


def persist_diagnostic_state(
    *,
    owner_scope_id: str,
    resume_id: int,
    target_status: str,
    reason: str,
    strict: bool,
) -> DiagnosticFlowState:
    row = transition_diagnostic_state(
        owner_scope_id=owner_scope_id,
        resume_id=resume_id,
        to_status=target_status,
        event=reason,
        metadata={"resumeId": resume_id},
        strict=strict,
    )
    return format_diagnostic_flow_state(row)


def build_interview_questions(*, jd_text: str, resume_text: str, question_count: int) -> list[dict[str, str]]:
    try:
        analysis = compute_rule_based_analysis(resume_text=resume_text, jd_text=jd_text)
        primary_keywords = analysis.missing_keywords[: max(1, question_count - 2)]
    except HTTPException:
        primary_keywords = []

    question_bank: list[dict[str, str]] = [
        {
            "category": "self_intro",
            "focus": "岗位匹配与核心卖点",
            "question": "请用60秒做自我介绍，并说明你和该岗位最匹配的两点。",
        },
        {
            "category": "project_depth",
            "focus": "项目深挖与技术决策",
            "question": "挑一个最能代表你能力的项目，说明你的关键决策、取舍和最终业务结果。",
        },
        {
            "category": "impact_metrics",
            "focus": "结果量化与业务价值",
            "question": "你在项目中如何定义成功？请给出至少两个可量化指标。",
        },
    ]

    for kw in primary_keywords:
        question_bank.append(
            {
                "category": "jd_gap",
                "focus": f"关键词补齐：{kw}",
                "question": f"JD强调 {kw}，请结合真实经历说明你如何实践并落地该能力。",
            }
        )

    question_bank.append(
        {
            "category": "pressure_case",
            "focus": "问题复盘与抗压",
            "question": "分享一次你遇到重大问题的经历：你如何定位、推动协作并复盘改进？",
        }
    )

    selected = question_bank[:question_count]
    return [
        {
            "index": idx,
            "category": item["category"],
            "focus": item["focus"],
            "question": item["question"],
        }
        for idx, item in enumerate(selected)
    ]


def build_interview_session_payload(row: dict[str, Any]) -> InterviewSession:
    questions = row.get("questions") if isinstance(row.get("questions"), list) else []
    answers = row.get("answers") if isinstance(row.get("answers"), list) else []

    status = str(row.get("status", "active")).strip().lower()
    if status not in {"active", "paused", "finished"}:
        status = "active"

    recommendations_raw = row.get("recommendations") if isinstance(row.get("recommendations"), list) else []
    recommendations = [str(item).strip() for item in recommendations_raw if str(item).strip()][:8]

    feedback = row.get("feedback") if isinstance(row.get("feedback"), dict) else {}
    summary = str(feedback.get("summary", "")).strip() or None

    return InterviewSession(
        id=int(row.get("id", 0)),
        sessionToken=str(row.get("session_token", "")),
        status=status,
        questionCount=len(questions),
        answeredCount=len(answers),
        currentIndex=int(row.get("current_index", 0)),
        finalScore=int(row.get("final_score")) if row.get("final_score") is not None else None,
        recommendations=recommendations,
        summary=summary,
        createdAt=str(row.get("created_at", "")),
        updatedAt=str(row.get("updated_at", "")),
    )


def resolve_interview_next_question(row: dict[str, Any]) -> InterviewQuestion | None:
    questions = row.get("questions") if isinstance(row.get("questions"), list) else []
    idx = int(row.get("current_index", 0))
    if idx < 0 or idx >= len(questions):
        return None

    raw = questions[idx]
    if not isinstance(raw, dict):
        return None

    return InterviewQuestion(
        index=int(raw.get("index", idx)),
        category=str(raw.get("category", "general")),
        question=str(raw.get("question", "")),
        focus=str(raw.get("focus", "")),
    )


def build_interview_answer_evaluation(*, question: dict[str, Any], answer_text: str) -> InterviewAnswerEvaluation:
    answer_length = len(answer_text)
    has_number = bool(re.search(r"\d", answer_text))
    has_action = any(marker in answer_text.lower() for marker in ACTION_MARKERS)

    score = 55
    if answer_length >= 80:
        score += 12
    if answer_length >= 180:
        score += 8
    if has_number:
        score += 12
    if has_action:
        score += 8

    strengths: list[str] = []
    improvements: list[str] = []

    if has_action:
        strengths.append("回答体现了具体动作与推进过程。")
    if has_number:
        strengths.append("回答包含量化信息，便于评估结果价值。")

    if not has_number:
        improvements.append("补充量化指标（如提升%、成本、时延、效率）增强说服力。")
    if answer_length < 100:
        improvements.append("可补充背景、决策依据和结果复盘，形成完整闭环。")

    if not strengths:
        strengths.append("回答覆盖了问题主线，表达清晰。")

    follow_up = None
    focus = str(question.get("focus", "")).strip()
    if focus:
        follow_up = f"针对{focus}，你当时最关键的一次取舍是什么？"

    return InterviewAnswerEvaluation(
        answerScore=clamp_score(score, low=30, high=98),
        strengths=strengths[:3],
        improvements=improvements[:3],
        followUpQuestion=follow_up,
    )


def build_interview_feedback_draft(row: dict[str, Any]) -> InterviewFeedbackDraft:
    answers = row.get("answers") if isinstance(row.get("answers"), list) else []
    questions = row.get("questions") if isinstance(row.get("questions"), list) else []

    eval_scores = [
        int(item.get("evaluation", {}).get("answerScore", 0))
        for item in answers
        if isinstance(item, dict)
    ]
    overall = clamp_score(sum(eval_scores) / max(1, len(eval_scores)), low=35, high=98)

    relevance = clamp_score(45 + len(answers) / max(1, len(questions)) * 45)
    depth = clamp_score(sum(1 for item in answers if len(str(item.get("answer", ""))) >= 120) / max(1, len(answers)) * 100)
    impact = clamp_score(sum(1 for item in answers if re.search(r"\d", str(item.get("answer", "")))) / max(1, len(answers)) * 100)
    communication = clamp_score(50 + min(40, len(answers) * 8))

    strengths: list[str] = []
    gaps: list[str] = []
    plan: list[str] = []

    if impact >= 60:
        strengths.append("多数回答包含量化结果，业务价值表达较好。")
    if depth >= 60:
        strengths.append("回答有一定技术深度，能够说明决策与取舍。")
    if relevance >= 70:
        strengths.append("与岗位问题匹配度较高，主线回答完整。")

    if impact < 60:
        gaps.append("量化表达偏弱，建议增加结果指标与前后对比。")
        plan.append("准备3个可量化项目案例，统一成STAR模板。")
    if depth < 55:
        gaps.append("技术决策细节不足，建议补充关键方案与trade-off。")
        plan.append("每个项目补充'为什么这样做/替代方案/效果'三段说明。")
    if relevance < 65:
        gaps.append("岗位关键词对应案例不足，建议强化JD映射。")
        plan.append("按JD关键词逐条准备对应经历，形成问题-答案卡片。")

    if not strengths:
        strengths.append("回答结构完整，具备继续打磨潜力。")
    if not gaps:
        gaps.append("整体表现稳定，可继续优化表达效率和案例颗粒度。")
    if not plan:
        plan.append("进行一次全流程模拟面试并复盘追问。")

    return InterviewFeedbackDraft(
        overallScore=overall,
        dimensionScores={
            "communication": communication,
            "depth": depth,
            "relevance": relevance,
            "impact": impact,
        },
        strengths=strengths[:4],
        gaps=gaps[:4],
        improvementPlan=plan[:4],
        summary=f"本次模拟面试综合得分 {overall}/100，建议优先补齐量化表达和岗位关键词映射。",
    )


def parse_interview_feedback(row: dict[str, Any]) -> InterviewFeedbackDraft | None:
    raw = row.get("feedback")
    if not isinstance(raw, dict):
        return None

    try:
        return InterviewFeedbackDraft.model_validate(raw)
    except Exception:
        return None


def build_export_text(item: HistoryDetail) -> str:
    lines = [
        "Career Hero 瀵煎嚭",
        f"ID: {item.id}",
        f"CreatedAt: {item.createdAt}",
        f"RequestId: {item.requestId}",
        f"SessionId: {item.sessionId}",
        f"Source: {item.analysisSource}",
        f"Score: {item.score}",
        "",
        "Score Breakdown:",
        f"- keyword_match: {item.scoreBreakdown.keyword_match}",
        f"- coverage: {item.scoreBreakdown.coverage}",
        f"- writing_quality_stub: {item.scoreBreakdown.writing_quality_stub}",
        "",
        f"Matched Keywords: {', '.join(item.matchedKeywords) or 'N/A'}",
        f"Missing Keywords: {', '.join(item.missingKeywords) or 'N/A'}",
        "",
        "Suggestions:",
        *[f"{idx + 1}. {value}" for idx, value in enumerate(item.suggestions)],
        "",
        "Insights:",
        item.insights.summary,
        *[f"- Strength: {value}" for value in item.insights.strengths],
        *[f"- Risk: {value}" for value in item.insights.risks],
        "",
        "Optimized Resume:",
        item.optimizedResume,
    ]
    return "\n".join(lines)


def build_pdf_bytes(text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("PDF export requires reportlab") from exc

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    font_name = "Helvetica"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font_name = "STSong-Light"
    except Exception:
        font_name = "Helvetica"

    pdf.setFont(font_name, 11)
    margin_x = 36
    line_height = 16
    y = height - 40

    for raw_line in text.splitlines():
        line = raw_line
        if len(line) > 140:
            chunks = [line[i : i + 140] for i in range(0, len(line), 140)]
        else:
            chunks = [line]

        for chunk in chunks:
            if y < 40:
                pdf.showPage()
                pdf.setFont(font_name, 11)
                y = height - 40
            pdf.drawString(margin_x, y, chunk)
            y -= line_height

    pdf.save()
    buffer.seek(0)
    return buffer.read()


def log_request_event(
    *,
    path: str,
    method: str,
    status: int,
    duration_ms: int,
    request_id: str,
    session_id: str,
    error_code: str | None,
    exception_type: str | None,
) -> None:
    logger.info(
        json.dumps(
            {
                "path": path,
                "method": method,
                "status": status,
                "duration_ms": duration_ms,
                "requestId": request_id,
                "sessionId": session_id,
                "error_code": error_code,
                "exception_type": exception_type,
            },
            ensure_ascii=False,
        )
    )


app = FastAPI(title="Career Hero MVP API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def bootstrap_local_auth() -> None:
    ensure_default_local_account()


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    inbound_session_id = request.headers.get("x-session-id") or request.cookies.get("career_hero_session") or ""

    request.state.request_id = request_id
    request.state.error_code = None
    request.state.exception_type = None

    started_at = time.perf_counter()
    is_preflight_request = request.method.upper() == "OPTIONS"

    if inbound_session_id and not is_preflight_request and not validate_session_id(inbound_session_id):
        inbound_session_id = ""
        set_error_context(request, error_code="BAD_REQUEST", exception_type="InvalidSessionId")

    session_required = (
        get_auth_mode() == "token"
        or get_env_bool("CAREER_HERO_REQUIRE_SESSION_ID", False)
    )
    if (
        not inbound_session_id
        and session_required
        and not is_preflight_request
        and not is_public_path(request.url.path)
        and getattr(request.state, "exception_type", None) != "InvalidSessionId"
    ):
        set_error_context(request, error_code="UNAUTHORIZED", exception_type="MissingSessionId")
        inbound_session_id = ""

    session_id = inbound_session_id or str(uuid.uuid4())
    request.state.session_id = session_id
    request.state.current_user = None
    request.state.session_scope_id = f"session:{session_id}"
    request.state.user_scope_id = "anonymous"
    request.state.owner_scope_id = request.state.session_scope_id

    auth_token = parse_auth_session_token(request)
    if not auth_token and request.url.path.startswith("/api/auth/"):
        auth_token = parse_auth_session_token_for_auth_api(request)

    if auth_token and not inbound_session_id:
        rebound = peek_auth_session(token=auth_token)
        if rebound is not None:
            session_id = str(rebound["session_id"])
            request.state.session_id = session_id
            request.state.session_scope_id = f"session:{session_id}"
            request.state.owner_scope_id = request.state.session_scope_id

    if auth_token and not is_preflight_request:
        auth_session = validate_auth_session(token=auth_token, session_id=session_id)
        if auth_session is None:
            if get_auth_mode() != "token" and is_login_required_path(request.url.path):
                set_error_context(request, error_code="AUTH_LOGIN_REQUIRED", exception_type="AuthLoginRequired")
            elif not is_public_path(request.url.path):
                set_error_context(request, error_code="UNAUTHORIZED", exception_type="InvalidSessionToken")
        else:
            request.state.current_user = {
                "id": int(auth_session["user_id"]),
                "username": str(auth_session["username"]),
                "expiresAt": str(auth_session["expires_at"]),
            }
            request.state.user_scope_id = f"user:{int(auth_session['user_id'])}"
            request.state.owner_scope_id = request.state.user_scope_id
            request.state.auth_expires_at = str(auth_session["expires_at"])

    def finalize(response: Response) -> Response:
        duration_ms = int((time.perf_counter() - started_at) * 1000)

        rate_limit_headers = getattr(request.state, "rate_limit", None)
        if isinstance(rate_limit_headers, dict):
            for key, value in rate_limit_headers.items():
                response.headers[key] = value

        response.headers["x-request-id"] = request_id
        response.headers["x-session-id"] = session_id
        response.set_cookie("career_hero_session", session_id, httponly=True, samesite="lax")

        error_code = getattr(request.state, "error_code", None)
        exception_type = getattr(request.state, "exception_type", None)

        METRICS.record(
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            error_code=error_code,
        )

        log_request_event(
            path=request.url.path,
            method=request.method,
            status=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
            session_id=session_id,
            error_code=error_code,
            exception_type=exception_type,
        )
        return response

    if getattr(request.state, "exception_type", "") == "InvalidSessionId":
        return finalize(
            JSONResponse(
                status_code=400,
                content=build_error_payload(
                    code="BAD_REQUEST",
                    message="x-session-id is invalid",
                    request_id=request_id,
                ),
            )
        )

    if getattr(request.state, "exception_type", "") == "MissingSessionId":
        return finalize(
            JSONResponse(
                status_code=401,
                content=build_error_payload(
                    code="UNAUTHORIZED",
                    message="x-session-id is required",
                    request_id=request_id,
                ),
            )
        )

    if getattr(request.state, "exception_type", "") == "InvalidSessionToken":
        return finalize(
            JSONResponse(
                status_code=401,
                content=build_error_payload(
                    code="UNAUTHORIZED",
                    message="invalid or expired session token",
                    request_id=request_id,
                ),
            )
        )

    if getattr(request.state, "exception_type", "") == "AuthLoginRequired":
        return finalize(
            JSONResponse(
                status_code=401,
                content=build_error_payload(
                    code="AUTH_LOGIN_REQUIRED",
                    message="login required",
                    request_id=request_id,
                ),
            )
        )

    if not is_preflight_request and get_auth_mode() != "token" and is_login_required_path(request.url.path) and get_current_user(request) is None:
        set_error_context(request, error_code="AUTH_LOGIN_REQUIRED", exception_type="AuthLoginRequired")
        return finalize(
            JSONResponse(
                status_code=401,
                content=build_error_payload(
                    code="AUTH_LOGIN_REQUIRED",
                    message="login required",
                    request_id=request_id,
                ),
            )
        )

    if not is_preflight_request and not is_public_path(request.url.path):
        if get_auth_mode() == "token":
            expected_token = get_expected_api_token()
            provided_token = parse_request_token(request)

            if not expected_token:
                set_error_context(request, error_code="INTERNAL_ERROR", exception_type="AuthConfigMissing")
                return finalize(
                    JSONResponse(
                        status_code=500,
                        content=build_error_payload(
                            code="INTERNAL_ERROR",
                            message="CAREER_HERO_API_TOKEN is not configured",
                            request_id=request_id,
                        ),
                    )
                )

            if provided_token != expected_token:
                set_error_context(request, error_code="UNAUTHORIZED", exception_type="InvalidApiToken")
                return finalize(
                    JSONResponse(
                        status_code=401,
                        content=build_error_payload(
                            code="UNAUTHORIZED",
                            message="invalid api token",
                            request_id=request_id,
                        ),
                    )
                )

    if request.url.path == "/api/analyze" and request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        measured_length: int | None = None

        if content_length:
            try:
                measured_length = int(content_length)
            except ValueError:
                measured_length = None

        if measured_length is None:
            body = await request.body()
            measured_length = len(body)

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = receive

        if measured_length > MAX_JSON_BODY_BYTES:
            set_error_context(request, error_code="PAYLOAD_TOO_LARGE", exception_type="PayloadTooLarge")
            return finalize(
                JSONResponse(
                    status_code=413,
                    content=build_error_payload(
                        code="PAYLOAD_TOO_LARGE",
                        message=f"Payload too large, max {MAX_JSON_BODY_BYTES} bytes",
                        request_id=request_id,
                    ),
                )
            )

    response = await call_next(request)
    return finalize(response)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=AuthLoginResponse)
def auth_login(payload: AuthLoginRequest, request: Request, response: Response) -> AuthLoginResponse:
    limiter_key = build_login_rate_limiter_key(request=request, username=payload.username)
    pre_check = AUTH_LOGIN_RATE_LIMITER.check(key=limiter_key)
    if not pre_check.allowed:
        raise_api_error(
            status_code=429,
            code="AUTH_LOGIN_RATE_LIMITED",
            message=pre_check.message or "Too many failed login attempts",
            extra={"retryAfterSec": pre_check.reset_seconds},
        )

    user, verify_reason = verify_local_account_with_reason(username=payload.username, password=payload.password)
    if user is None:
        if verify_reason == VERIFY_REASON_ACCOUNT_INACTIVE:
            raise_api_error(
                status_code=403,
                code="AUTH_ACCOUNT_DISABLED",
                message="account is disabled",
            )

        fail_decision = AUTH_LOGIN_RATE_LIMITER.register_failure(key=limiter_key)
        if not fail_decision.allowed:
            raise_api_error(
                status_code=429,
                code="AUTH_LOGIN_RATE_LIMITED",
                message=fail_decision.message or "Too many failed login attempts",
                extra={"retryAfterSec": fail_decision.reset_seconds},
            )

        raise_api_error(
            status_code=401,
            code="AUTH_INVALID_CREDENTIALS",
            message="invalid username or password",
        )

    AUTH_LOGIN_RATE_LIMITER.register_success(key=limiter_key)

    auth_session = create_auth_session(
        user_id=int(user["id"]),
        session_id=get_session_id(request),
        ttl_seconds=AUTH_SESSION_TTL_SECONDS,
    )
    response.set_cookie(
        "career_hero_auth",
        auth_session["token"],
        httponly=True,
        samesite="lax",
        max_age=int(auth_session["ttl_seconds"]),
    )

    return AuthLoginResponse(
        requestId=get_request_id(request),
        sessionId=get_session_id(request),
        user=AuthUser(id=int(user["id"]), username=str(user["username"])),
        token=str(auth_session["token"]),
        expiresAt=str(auth_session["expires_at"]),
    )


@app.get("/api/auth/me", response_model=AuthMeResponse)
def auth_me(request: Request) -> AuthMeResponse:
    user = require_current_user(request)
    expires_at = str(getattr(request.state, "auth_expires_at", ""))
    return AuthMeResponse(
        requestId=get_request_id(request),
        sessionId=get_session_id(request),
        user=AuthUser(id=int(user["id"]), username=str(user["username"])),
        expiresAt=expires_at,
        authContext=build_auth_context(request=request, user=user, expires_at=expires_at),
    )


@app.post("/api/auth/refresh", response_model=AuthRefreshResponse)
def auth_refresh(request: Request, response: Response) -> AuthRefreshResponse:
    token = parse_auth_session_token_for_auth_api(request)
    if not token:
        raise_api_error(status_code=401, code="AUTH_TOKEN_REQUIRED", message="session token is required")

    refreshed, reason = refresh_auth_session(
        token=token,
        session_id=get_session_id(request),
        ttl_seconds=AUTH_SESSION_TTL_SECONDS,
        grace_seconds=AUTH_REFRESH_GRACE_SECONDS,
    )
    if refreshed is None:
        if reason == REFRESH_REASON_TOKEN_REQUIRED:
            raise_api_error(status_code=401, code="AUTH_TOKEN_REQUIRED", message="session token is required")
        if reason in {REFRESH_REASON_TOKEN_NOT_FOUND, REFRESH_REASON_TOKEN_REVOKED}:
            raise_api_error(status_code=401, code="AUTH_REFRESH_INVALID_TOKEN", message="invalid session token")
        if reason == REFRESH_REASON_SESSION_MISMATCH:
            raise_api_error(status_code=401, code="AUTH_REFRESH_SESSION_MISMATCH", message="session token does not match current session")
        if reason == REFRESH_REASON_USER_INACTIVE:
            raise_api_error(status_code=403, code="AUTH_ACCOUNT_DISABLED", message="account is disabled")
        if reason == REFRESH_REASON_EXPIRED_TOO_LONG:
            raise_api_error(status_code=401, code="AUTH_REFRESH_EXPIRED", message="refresh window expired, please login again")
        raise_api_error(status_code=401, code="AUTH_REFRESH_FAILED", message="failed to refresh token")

    response.set_cookie(
        "career_hero_auth",
        refreshed["token"],
        httponly=True,
        samesite="lax",
        max_age=int(refreshed["ttl_seconds"]),
    )

    return AuthRefreshResponse(
        requestId=get_request_id(request),
        sessionId=str(refreshed["session_id"]),
        user=AuthUser(id=int(refreshed["user_id"]), username=str(refreshed["username"])),
        token=str(refreshed["token"]),
        expiresAt=str(refreshed["expires_at"]),
        previousExpiresAt=str(refreshed["previous_expires_at"]),
    )


@app.post("/api/auth/logout", response_model=AuthLogoutResponse)
def auth_logout(request: Request, response: Response) -> AuthLogoutResponse:
    token = parse_auth_session_token_for_auth_api(request)
    if not token:
        raise_api_error(status_code=401, code="AUTH_TOKEN_REQUIRED", message="session token is required")

    revoked = revoke_auth_session(token=token, session_id=get_session_id(request))
    response.delete_cookie("career_hero_auth")
    return AuthLogoutResponse(requestId=get_request_id(request), revoked=revoked)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    apply_rate_limit_or_raise(request=request, payload=payload)

    owner_scope_id = get_owner_scope_id(request)
    resume_state_key = to_resume_state_key(payload)

    analyzing_state = persist_diagnostic_state(
        owner_scope_id=owner_scope_id,
        resume_id=resume_state_key,
        target_status="analyzing",
        reason="analyze_started",
        strict=False,
    )
    logger.info(
        json.dumps(
            {
                "event": "diagnostic_state_transition",
                "ownerScopeId": owner_scope_id,
                "resumeId": resume_state_key,
                "from": "*",
                "to": "analyzing",
                "reason": "analyze_started",
            },
            ensure_ascii=False,
        )
    )

    resume_text = resolve_resume_text(payload, request=request)
    result = run_analysis(resume_text=resume_text, jd_text=payload.jdText)

    rag_hits: list[RagHit] = []
    if payload.ragEnabled:
        rag_rows = search_knowledge_with_configured_retriever(
            query=build_rag_query(jd_text=payload.jdText, missing_keywords=result.missing_keywords),
            limit=payload.ragTopK,
            threshold=payload.ragThreshold,
        )
        rag_hits = [format_rag_hit(row) for row in rag_rows]

    request_id = get_request_id(request)
    session_id = get_session_id(request)

    history_id = insert_analysis_history(
        resume_text_hash_or_excerpt=resume_hash_or_excerpt(resume_text),
        jd_excerpt=to_excerpt(payload.jdText, limit=140),
        score=result.score,
        score_breakdown=result.score_breakdown.model_dump(),
        matched_keywords=result.matched_keywords,
        missing_keywords=result.missing_keywords,
        suggestions=result.suggestions,
        optimized_resume=result.optimized_resume,
        insights=result.insights.model_dump(),
        analysis_source=result.analysis_source,
        session_id=session_id,
        user_scope_id=owner_scope_id,
        request_id=request_id,
    )

    enforce_retention(
        keep_latest=DEFAULT_HISTORY_RETENTION,
        session_id=session_id,
        user_scope_id=owner_scope_id,
    )

    diagnostic_breakdown = result.diagnostic_breakdown or build_diagnostic_breakdown(
        resume_text=resume_text,
        matched_keywords=result.matched_keywords,
        missing_keywords=result.missing_keywords,
        writing_quality_stub=result.score_breakdown.writing_quality_stub,
    )

    report_state = persist_diagnostic_state(
        owner_scope_id=owner_scope_id,
        resume_id=resume_state_key,
        target_status="report",
        reason="analyze_finished",
        strict=False,
    )
    logger.info(
        json.dumps(
            {
                "event": "diagnostic_state_transition",
                "ownerScopeId": owner_scope_id,
                "resumeId": resume_state_key,
                "from": analyzing_state.status,
                "to": report_state.status,
                "reason": "analyze_finished",
                "historyId": history_id,
                "requestId": request_id,
            },
            ensure_ascii=False,
        )
    )

    return AnalyzeResponse(
        score=result.score,
        matchedKeywords=result.matched_keywords,
        missingKeywords=result.missing_keywords,
        suggestions=result.suggestions,
        optimizedResume=result.optimized_resume,
        scoreBreakdown=result.score_breakdown,
        diagnosticBreakdown=diagnostic_breakdown,
        issueClassifications=result.issue_classifications,
        pipAdvice=result.pip_advice,
        insights=result.insights,
        analysisSource=result.analysis_source,
        fallbackUsed=result.fallback_used,
        ragEnabled=payload.ragEnabled,
        ragHits=rag_hits,
        promptVersion=PROMPT_VERSION,
        historyId=history_id,
        requestId=request_id,
        diagnosticState=report_state,
    )


@app.get("/api/diagnostic/state", response_model=DiagnosticStateResponse)
def get_diagnostic_state_endpoint(
    request: Request,
    resumeId: int | None = Query(default=None, ge=0),
) -> DiagnosticStateResponse:
    resume_state_key = max(0, int(resumeId or 0))
    state = format_diagnostic_flow_state(
        get_diagnostic_state(
            owner_scope_id=get_owner_scope_id(request),
            resume_id=resume_state_key,
        )
    )
    return DiagnosticStateResponse(requestId=get_request_id(request), state=state)


@app.post("/api/diagnostic/state/transition", response_model=DiagnosticStateResponse)
def transition_diagnostic_state_endpoint(
    payload: DiagnosticTransitionRequest,
    request: Request,
) -> DiagnosticStateResponse:
    owner_scope_id = get_owner_scope_id(request)
    resume_state_key = max(0, int(payload.resumeId or 0))
    current = get_diagnostic_state(owner_scope_id=owner_scope_id, resume_id=resume_state_key)
    current_status = str(current.get("status", "jd_input"))

    if not can_transition_diagnostic_state(from_status=current_status, to_status=payload.toStatus):
        raise_api_error(
            status_code=409,
            code="DIAGNOSTIC_STATE_CONFLICT",
            message=f"invalid diagnostic transition: {current_status} -> {payload.toStatus}",
        )

    updated = persist_diagnostic_state(
        owner_scope_id=owner_scope_id,
        resume_id=resume_state_key,
        target_status=payload.toStatus,
        reason=payload.reason or "manual_transition",
        strict=True,
    )
    logger.info(
        json.dumps(
            {
                "event": "diagnostic_state_transition",
                "ownerScopeId": owner_scope_id,
                "resumeId": resume_state_key,
                "from": current_status,
                "to": updated.status,
                "reason": payload.reason or "manual_transition",
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )
    return DiagnosticStateResponse(requestId=get_request_id(request), state=updated)


@app.get("/api/history", response_model=HistoryResponse)
def get_history(
    request: Request,
    limit: int = Query(default=DEFAULT_HISTORY_LIMIT, ge=1),
    requestId: str | None = Query(default=None),
    request_id: str | None = Query(default=None, alias="request_id"),
    allSessions: bool = Query(default=False),
) -> HistoryResponse:
    safe_limit = min(limit, MAX_HISTORY_LIMIT)
    request_id_filter = (requestId or request_id or "").strip() or None
    include_all_sessions = resolve_effective_include_all_sessions(request, allSessions)

    rows = fetch_analysis_history(
        limit=safe_limit,
        request_id=request_id_filter,
        session_id=get_session_id(request),
        user_scope_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    items = [format_history_item(row) for row in rows]

    total = get_history_total(
        request_id=request_id_filter,
        session_id=get_session_id(request),
        user_scope_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )

    return HistoryResponse(requestId=get_request_id(request), total=total, items=items)


@app.get("/api/history/{history_id}", response_model=HistoryDetailResponse)
def get_history_detail(
    history_id: int,
    request: Request,
    allSessions: bool = Query(default=False),
) -> HistoryDetailResponse:
    if history_id < 1:
        raise HTTPException(status_code=400, detail="history_id must be positive")

    include_all_sessions = resolve_effective_include_all_sessions(request, allSessions)
    row = fetch_analysis_item(
        history_id=history_id,
        session_id=get_session_id(request),
        user_scope_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="history item not found")

    detail = build_history_detail_from_row(row)

    return HistoryDetailResponse(requestId=get_request_id(request), item=detail)


@app.post("/api/history/cleanup", response_model=HistoryCleanupResponse)
def cleanup_history_endpoint(
    payload: HistoryCleanupRequest,
    request: Request,
    allSessions: bool = Query(default=False),
) -> HistoryCleanupResponse:
    include_all_sessions = resolve_effective_include_all_sessions(request, allSessions)

    if payload.mode == "delete_all":
        if (payload.confirmText or "").strip().upper() != "DELETE":
            raise HTTPException(status_code=400, detail="confirmText must be DELETE for delete_all")
        deleted = delete_all_history(
            session_id=get_session_id(request),
            user_scope_id=get_owner_scope_id(request),
            include_all_sessions=include_all_sessions,
        )
    else:
        deleted = cleanup_history(
            keep_latest=payload.keepLatest,
            session_id=get_session_id(request),
            user_scope_id=get_owner_scope_id(request),
            include_all_sessions=include_all_sessions,
        )

    total = get_history_total(
        session_id=get_session_id(request),
        user_scope_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    return HistoryCleanupResponse(requestId=get_request_id(request), deleted=deleted, total=total)


@app.post("/api/rag/knowledge", response_model=KnowledgeItemResponse)
def create_knowledge_endpoint(payload: KnowledgeCreateRequest, request: Request) -> KnowledgeItemResponse:
    row = create_knowledge_item(
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        source=payload.source,
        updated_by_scope=get_owner_scope_id(request),
    )
    return KnowledgeItemResponse(requestId=get_request_id(request), item=format_knowledge_item(row))


@app.put("/api/rag/knowledge/{item_id}", response_model=KnowledgeItemResponse)
def update_knowledge_endpoint(item_id: int, payload: KnowledgeUpdateRequest, request: Request) -> KnowledgeItemResponse:
    if item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be positive")

    if payload.title is None and payload.content is None and payload.tags is None and payload.source is None:
        raise HTTPException(status_code=400, detail="at least one field is required")

    row = update_knowledge_item(
        item_id=item_id,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        source=payload.source,
        updated_by_scope=get_owner_scope_id(request),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="knowledge item not found")

    return KnowledgeItemResponse(requestId=get_request_id(request), item=format_knowledge_item(row))


@app.delete("/api/rag/knowledge/{item_id}", response_model=KnowledgeDeleteResponse)
def delete_knowledge_endpoint(item_id: int, request: Request) -> KnowledgeDeleteResponse:
    if item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be positive")

    deleted = delete_knowledge_item(item_id=item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="knowledge item not found")

    return KnowledgeDeleteResponse(requestId=get_request_id(request), deleted=True)


@app.get("/api/rag/knowledge", response_model=KnowledgeListResponse)
def list_knowledge_endpoint(
    request: Request,
    limit: int = Query(default=20, ge=1),
    source: str | None = Query(default=None),
) -> KnowledgeListResponse:
    rows = list_knowledge_items(limit=limit, source=source)
    items = [format_knowledge_item(row) for row in rows]
    return KnowledgeListResponse(requestId=get_request_id(request), total=len(items), items=items)


@app.get("/api/rag/config", response_model=RagSearchConfigResponse)
def get_rag_config_endpoint(request: Request) -> RagSearchConfigResponse:
    config = get_rag_search_config()
    return RagSearchConfigResponse(requestId=get_request_id(request), config=format_rag_search_config(config))


@app.put("/api/rag/config", response_model=RagSearchConfigResponse)
def update_rag_config_endpoint(payload: RagSearchConfigUpdateRequest, request: Request) -> RagSearchConfigResponse:
    if payload.topK is None and payload.threshold is None:
        raise HTTPException(status_code=400, detail="at least one field is required")

    config = update_rag_search_config(top_k=payload.topK, score_threshold=payload.threshold)
    return RagSearchConfigResponse(requestId=get_request_id(request), config=format_rag_search_config(config))


@app.get("/api/rag/search", response_model=KnowledgeListResponse)
def search_knowledge_endpoint(
    request: Request,
    query: str = Query(..., min_length=1, max_length=400),
    topK: int | None = Query(default=None, ge=1, le=20),
    limit: int | None = Query(default=None, ge=1, le=20),
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
) -> KnowledgeListResponse:
    effective_top_k = topK if topK is not None else limit
    rows = search_knowledge_with_configured_retriever(query=query, limit=effective_top_k, threshold=threshold)
    items = [format_knowledge_item(row) for row in rows]
    return KnowledgeListResponse(requestId=get_request_id(request), total=len(items), items=items)


def _create_interview_session_impl(payload: InterviewCreateRequest, request: Request) -> InterviewCreateResponse:
    if payload.resumeId is None and not (payload.resumeText or "").strip():
        resume_text = payload.jdText
    else:
        resume_text = resolve_resume_text(payload, request=request)

    questions = build_interview_questions(
        jd_text=payload.jdText,
        resume_text=resume_text,
        question_count=payload.questionCount,
    )

    owner_session_id, include_all_sessions = interview_scope(request)
    session_id = create_interview_session(
        session_token=str(uuid.uuid4()),
        session_owner_id=owner_session_id,
        jd_text=payload.jdText,
        resume_text=resume_text,
        questions=questions,
        metadata={"creatorRequestId": get_request_id(request)},
    )

    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )

    degraded = False
    if row is None:
        degraded = True
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": session_id,
            "session_token": str(session_id),
            "status": "active",
            "questions": questions,
            "answers": [],
            "current_index": 0,
            "created_at": now,
            "updated_at": now,
            "feedback": None,
            "final_score": None,
            "recommendations": [],
        }
        logger.warning(
            json.dumps(
                {
                    "event": "interview_create_degraded",
                    "ownerScopeId": owner_session_id,
                    "sessionId": session_id,
                    "requestId": get_request_id(request),
                },
                ensure_ascii=False,
            )
        )

    logger.info(
        json.dumps(
            {
                "event": "interview_state_transition",
                "ownerScopeId": owner_session_id,
                "sessionId": session_id,
                "from": "created",
                "to": "active",
                "degraded": degraded,
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return InterviewCreateResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(row),
        nextQuestion=resolve_interview_next_question(row),
        degraded=degraded,
    )


@app.post("/api/interview/session/create", response_model=InterviewCreateResponse)
def create_interview_endpoint(payload: InterviewCreateRequest, request: Request) -> InterviewCreateResponse:
    return _create_interview_session_impl(payload, request)


@app.post("/api/interview/session/start", response_model=InterviewCreateResponse)
def start_interview_endpoint(payload: InterviewCreateRequest, request: Request) -> InterviewCreateResponse:
    return _create_interview_session_impl(payload, request)


@app.post("/api/interview/start", response_model=InterviewCreateResponse)
def start_interview_short_endpoint(payload: InterviewCreateRequest, request: Request) -> InterviewCreateResponse:
    return _create_interview_session_impl(payload, request)


@app.post("/api/interview/session", response_model=InterviewCreateResponse)
def create_interview_short_endpoint(payload: InterviewCreateRequest, request: Request) -> InterviewCreateResponse:
    return _create_interview_session_impl(payload, request)


@app.get("/api/interview/sessions", response_model=InterviewSessionListResponse)
def list_interview_sessions_endpoint(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    status: Literal["active", "paused", "finished"] | None = Query(default=None),
    allSessions: bool = Query(default=False),
) -> InterviewSessionListResponse:
    include_all_sessions = resolve_include_all_sessions(request, allSessions)
    rows = list_interview_sessions(
        limit=limit,
        status=status,
        session_owner_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    items = [build_interview_session_payload(row) for row in rows]
    return InterviewSessionListResponse(requestId=get_request_id(request), total=len(items), items=items)


@app.get("/api/interview/sessions/{session_id}", response_model=InterviewSessionDetailResponse)
def get_interview_session_detail_endpoint(
    session_id: int,
    request: Request,
    allSessions: bool = Query(default=False),
) -> InterviewSessionDetailResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    include_all_sessions = resolve_include_all_sessions(request, allSessions)
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview session not found")

    logger.info(
        json.dumps(
            {
                "event": "interview_restore",
                "ownerScopeId": get_owner_scope_id(request),
                "sessionId": session_id,
                "status": str(row.get("status", "active")),
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return InterviewSessionDetailResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(row),
        nextQuestion=resolve_interview_next_question(row),
        feedbackDraft=parse_interview_feedback(row),
    )


@app.get("/api/interview/results", response_model=InterviewResultListResponse)
def list_interview_results_endpoint(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    allSessions: bool = Query(default=False),
) -> InterviewResultListResponse:
    include_all_sessions = resolve_include_all_sessions(request, allSessions)
    rows = list_interview_results(
        limit=limit,
        session_owner_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    items = [build_interview_session_payload(row) for row in rows]
    return InterviewResultListResponse(requestId=get_request_id(request), total=len(items), items=items)


@app.get("/api/interview/results/{session_id}", response_model=InterviewResultDetailResponse)
def get_interview_result_detail_endpoint(
    session_id: int,
    request: Request,
    allSessions: bool = Query(default=False),
) -> InterviewResultDetailResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    include_all_sessions = resolve_include_all_sessions(request, allSessions)
    row = fetch_interview_result(
        session_id=session_id,
        session_owner_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview result not found")

    return InterviewResultDetailResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(row),
        feedbackDraft=parse_interview_feedback(row),
    )


@app.post("/api/interview/session/{session_id}/next", response_model=InterviewNextResponse)
def interview_next_endpoint(session_id: int, request: Request) -> InterviewNextResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    owner_session_id, include_all_sessions = interview_scope(request)
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview session not found")

    status = str(row.get("status", "active")).strip().lower()
    questions = row.get("questions") if isinstance(row.get("questions"), list) else []
    answers = row.get("answers") if isinstance(row.get("answers"), list) else []
    current_index = int(row.get("current_index", 0))

    updated = row
    if status == "active" and questions:
        current_answered = any(
            isinstance(item, dict) and int(item.get("questionIndex", -1)) == current_index
            for item in answers
        )
        next_index = current_index
        if current_answered:
            next_index = min(max(0, current_index + 1), len(questions))

        if next_index != current_index:
            updated_row = update_interview_session(
                session_id=session_id,
                current_index=next_index,
                session_owner_id=owner_session_id,
                include_all_sessions=include_all_sessions,
            )
            if updated_row is not None:
                updated = updated_row
                logger.info(
                    json.dumps(
                        {
                            "event": "interview_state_persisted",
                            "ownerScopeId": owner_session_id,
                            "sessionId": session_id,
                            "action": "next",
                            "fromIndex": current_index,
                            "toIndex": next_index,
                            "requestId": get_request_id(request),
                        },
                        ensure_ascii=False,
                    )
                )

    return InterviewNextResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(updated),
        nextQuestion=resolve_interview_next_question(updated),
        degraded=False,
    )


@app.post("/api/interview/session/{session_id}/pause", response_model=InterviewNextResponse)
def interview_pause_endpoint(session_id: int, request: Request) -> InterviewNextResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    owner_session_id, include_all_sessions = interview_scope(request)
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview session not found")

    status = str(row.get("status", "active"))
    if status == "finished":
        raise HTTPException(status_code=400, detail="finished session cannot be paused")

    if status == "paused":
        return InterviewNextResponse(
            requestId=get_request_id(request),
            session=build_interview_session_payload(row),
            nextQuestion=resolve_interview_next_question(row),
            degraded=False,
        )

    updated = update_interview_session(
        session_id=session_id,
        status="paused",
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="failed to pause interview session")

    logger.info(
        json.dumps(
            {
                "event": "interview_state_transition",
                "ownerScopeId": owner_session_id,
                "sessionId": session_id,
                "from": status,
                "to": "paused",
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return InterviewNextResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(updated),
        nextQuestion=resolve_interview_next_question(updated),
        degraded=False,
    )


@app.post("/api/interview/session/{session_id}/resume", response_model=InterviewNextResponse)
def interview_resume_endpoint(session_id: int, request: Request) -> InterviewNextResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    owner_session_id, include_all_sessions = interview_scope(request)
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview session not found")

    status = str(row.get("status", "active"))
    if status == "finished":
        raise HTTPException(status_code=400, detail="finished session cannot be resumed")

    if status == "active":
        return InterviewNextResponse(
            requestId=get_request_id(request),
            session=build_interview_session_payload(row),
            nextQuestion=resolve_interview_next_question(row),
            degraded=False,
        )

    updated = update_interview_session(
        session_id=session_id,
        status="active",
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )

    degraded = False
    effective_row = updated
    if updated is None:
        degraded = True
        effective_row = dict(row)
        effective_row["status"] = "active"
        logger.warning(
            json.dumps(
                {
                    "event": "interview_resume_degraded",
                    "ownerScopeId": owner_session_id,
                    "sessionId": session_id,
                    "requestId": get_request_id(request),
                },
                ensure_ascii=False,
            )
        )
    else:
        logger.info(
            json.dumps(
                {
                    "event": "interview_state_transition",
                    "ownerScopeId": owner_session_id,
                    "sessionId": session_id,
                    "from": status,
                    "to": "active",
                    "requestId": get_request_id(request),
                },
                ensure_ascii=False,
            )
        )

    return InterviewNextResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(effective_row),
        nextQuestion=resolve_interview_next_question(effective_row),
        degraded=degraded,
    )


@app.post("/api/interview/session/{session_id}/answer", response_model=InterviewAnswerResponse)
def interview_answer_endpoint(
    session_id: int,
    payload: InterviewAnswerRequest,
    request: Request,
) -> InterviewAnswerResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    owner_session_id, include_all_sessions = interview_scope(request)
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview session not found")

    current_status = str(row.get("status", "active"))
    if current_status == "paused":
        raise HTTPException(status_code=400, detail="interview session is paused, resume first")
    if current_status != "active":
        raise HTTPException(status_code=400, detail="interview session already finished")

    questions = row.get("questions") if isinstance(row.get("questions"), list) else []
    answers = row.get("answers") if isinstance(row.get("answers"), list) else []
    current_idx = int(row.get("current_index", 0))

    if payload.questionIndex is not None and payload.questionIndex != current_idx:
        raise HTTPException(status_code=400, detail=f"expected questionIndex={current_idx}")

    if current_idx >= len(questions):
        raise HTTPException(status_code=400, detail="no pending question, please finish session")

    question_raw = questions[current_idx]
    if not isinstance(question_raw, dict):
        raise HTTPException(status_code=500, detail="invalid interview question payload")

    evaluation = build_interview_answer_evaluation(question=question_raw, answer_text=payload.answerText)

    answers.append(
        {
            "questionIndex": current_idx,
            "question": question_raw,
            "answer": payload.answerText,
            "evaluation": evaluation.model_dump(),
            "answeredAt": datetime.now(timezone.utc).isoformat(),
        }
    )

    next_index = current_idx + 1
    next_status = "finished" if next_index >= len(questions) else "active"

    feedback_payload: dict[str, Any] | None = None
    final_score: int | None = None
    recommendations: list[str] | None = None
    if next_status == "finished":
        preview_row = dict(row)
        preview_row["answers"] = answers
        preview_row["current_index"] = next_index
        feedback_draft = build_interview_feedback_draft(preview_row)
        feedback_payload = feedback_draft.model_dump()
        final_score = feedback_draft.overallScore
        recommendations = feedback_draft.improvementPlan

    updated = update_interview_session(
        session_id=session_id,
        status=next_status,
        answers=answers,
        current_index=next_index,
        feedback=feedback_payload,
        final_score=final_score,
        recommendations=recommendations,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="failed to update interview session")

    logger.info(
        json.dumps(
            {
                "event": "interview_state_persisted",
                "ownerScopeId": owner_session_id,
                "sessionId": session_id,
                "action": "answer",
                "from": current_status,
                "to": next_status,
                "currentIndex": current_idx,
                "nextIndex": next_index,
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return InterviewAnswerResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(updated),
        evaluation=evaluation,
        nextQuestion=resolve_interview_next_question(updated),
    )


@app.post("/api/interview/session/{session_id}/finish", response_model=InterviewFinishResponse)
def interview_finish_endpoint(session_id: int, request: Request) -> InterviewFinishResponse:
    if session_id < 1:
        raise HTTPException(status_code=400, detail="session_id must be positive")

    owner_session_id, include_all_sessions = interview_scope(request)
    row = fetch_interview_session(
        session_id=session_id,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="interview session not found")

    current_status = str(row.get("status", "active")).strip().lower()
    if current_status == "finished":
        existing_feedback = parse_interview_feedback(row) or build_interview_feedback_draft(row)
        return InterviewFinishResponse(
            requestId=get_request_id(request),
            session=build_interview_session_payload(row),
            feedbackDraft=existing_feedback,
        )

    feedback = build_interview_feedback_draft(row)
    updated = update_interview_session(
        session_id=session_id,
        status="finished",
        feedback=feedback.model_dump(),
        final_score=feedback.overallScore,
        recommendations=feedback.improvementPlan,
        session_owner_id=owner_session_id,
        include_all_sessions=include_all_sessions,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="failed to persist interview feedback")

    logger.info(
        json.dumps(
            {
                "event": "interview_state_transition",
                "ownerScopeId": owner_session_id,
                "sessionId": session_id,
                "from": current_status,
                "to": "finished",
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return InterviewFinishResponse(
        requestId=get_request_id(request),
        session=build_interview_session_payload(updated),
        feedbackDraft=feedback,
    )


@app.get("/api/resumes", response_model=ResumeListResponse)
def get_resumes(
    request: Request,
    limit: int = Query(default=DEFAULT_RESUME_LIST_LIMIT, ge=1),
) -> ResumeListResponse:
    safe_limit = min(limit, MAX_RESUME_LIST_LIMIT)
    rows = list_resumes(limit=safe_limit, owner_scope_id=get_owner_scope_id(request))
    items = [format_resume_item(row) for row in rows]
    total = count_resumes(owner_scope_id=get_owner_scope_id(request))

    return ResumeListResponse(
        requestId=get_request_id(request),
        total=total,
        items=items,
    )


@app.post("/api/resumes", response_model=ResumeDetailResponse)
def create_resume_endpoint(payload: ResumeCreateRequest, request: Request) -> ResumeDetailResponse:
    owner_scope_id = get_owner_scope_id(request)
    resume_id = create_resume(title=payload.title, content=payload.content, owner_scope_id=owner_scope_id)
    row = fetch_resume_detail(resume_id=resume_id, owner_scope_id=owner_scope_id)
    if row is None:
        raise HTTPException(status_code=500, detail="resume created but failed to fetch")

    state = persist_diagnostic_state(
        owner_scope_id=owner_scope_id,
        resume_id=resume_id,
        target_status="jd_input",
        reason="resume_created",
        strict=False,
    )
    logger.info(
        json.dumps(
            {
                "event": "resume_content_updated",
                "ownerScopeId": owner_scope_id,
                "resumeId": resume_id,
                "contentUpdatedAt": str(row.get("content_updated_at", "")),
                "diagnosticStatus": state.status,
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return ResumeDetailResponse(requestId=get_request_id(request), item=format_resume_detail(row))


@app.post("/api/resumes/import/txt", response_model=ResumeDetailResponse)
def import_resume_txt_endpoint(payload: ResumeTxtImportRequest, request: Request) -> ResumeDetailResponse:
    filename = (payload.fileName or "").strip()
    if filename and not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="fileName must end with .txt")

    parse_result = parse_resume_txt(payload.content)
    metadata = dict(parse_result.metadata)
    metadata["fileName"] = filename
    owner_scope_id = get_owner_scope_id(request)

    resume_id = create_resume(
        title=infer_resume_title(provided_title=payload.title, filename=filename),
        content=payload.content,
        owner_scope_id=owner_scope_id,
        parse_status=parse_result.status,
        parsed_text=parse_result.parsed_text,
        metadata=metadata,
        failure_reason=parse_result.failure_reason,
    )
    row = fetch_resume_detail(resume_id=resume_id, owner_scope_id=owner_scope_id)
    if row is None:
        raise HTTPException(status_code=500, detail="resume imported but failed to fetch")

    state = persist_diagnostic_state(
        owner_scope_id=owner_scope_id,
        resume_id=resume_id,
        target_status="jd_input",
        reason="resume_imported_txt",
        strict=False,
    )
    logger.info(
        json.dumps(
            {
                "event": "resume_content_updated",
                "ownerScopeId": owner_scope_id,
                "resumeId": resume_id,
                "contentUpdatedAt": str(row.get("content_updated_at", "")),
                "diagnosticStatus": state.status,
                "requestId": get_request_id(request),
            },
            ensure_ascii=False,
        )
    )

    return ResumeDetailResponse(requestId=get_request_id(request), item=format_resume_detail(row))


@app.get("/api/resumes/{resume_id}", response_model=ResumeDetailResponse)
def get_resume_detail_endpoint(resume_id: int, request: Request) -> ResumeDetailResponse:
    if resume_id < 1:
        raise HTTPException(status_code=400, detail="resume_id must be positive")

    row = fetch_resume_detail(resume_id=resume_id, owner_scope_id=get_owner_scope_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="resume not found")

    return ResumeDetailResponse(requestId=get_request_id(request), item=format_resume_detail(row))


@app.put("/api/resumes/{resume_id}", response_model=ResumeDetailResponse)
def update_resume_endpoint(resume_id: int, payload: ResumeUpdateRequest, request: Request) -> ResumeDetailResponse:
    if resume_id < 1:
        raise HTTPException(status_code=400, detail="resume_id must be positive")

    if payload.title is None and payload.content is None:
        raise HTTPException(status_code=400, detail="at least one of title or content must be provided")

    owner_scope_id = get_owner_scope_id(request)
    row = update_resume(
        resume_id=resume_id,
        title=payload.title,
        content=payload.content,
        owner_scope_id=owner_scope_id,
        create_new_version=payload.createNewVersion,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="resume not found")

    if payload.content is not None:
        state = persist_diagnostic_state(
            owner_scope_id=owner_scope_id,
            resume_id=resume_id,
            target_status="jd_input",
            reason="resume_content_updated",
            strict=False,
        )
        logger.info(
            json.dumps(
                {
                    "event": "resume_content_updated",
                    "ownerScopeId": owner_scope_id,
                    "resumeId": resume_id,
                    "contentUpdatedAt": str(row.get("content_updated_at", "")),
                    "diagnosticStatus": state.status,
                    "requestId": get_request_id(request),
                },
                ensure_ascii=False,
            )
        )

    return ResumeDetailResponse(requestId=get_request_id(request), item=format_resume_detail(row))


@app.delete("/api/resumes/{resume_id}", response_model=ResumeDeleteResponse)
def delete_resume_endpoint(resume_id: int, request: Request) -> ResumeDeleteResponse:
    if resume_id < 1:
        raise HTTPException(status_code=400, detail="resume_id must be positive")

    deleted = delete_resume(resume_id=resume_id, owner_scope_id=get_owner_scope_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="resume not found")

    return ResumeDeleteResponse(requestId=get_request_id(request), deleted=True)


@app.get("/api/history/{history_id}/export")
def export_history(
    history_id: int,
    request: Request,
    format: Literal["txt", "json", "pdf"] = Query("txt"),
    allSessions: bool = Query(default=False),
):
    if history_id < 1:
        raise HTTPException(status_code=400, detail="history_id must be positive")

    include_all_sessions = resolve_effective_include_all_sessions(request, allSessions)
    row = fetch_analysis_item(
        history_id=history_id,
        session_id=get_session_id(request),
        user_scope_id=get_owner_scope_id(request),
        include_all_sessions=include_all_sessions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="history item not found")

    detail = build_history_detail_from_row(row)

    filename_base = f"career-hero-{detail.id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    if format == "json":
        content = json.dumps(detail.model_dump(), ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.json"'},
        )

    text_content = build_export_text(detail)
    if format == "txt":
        return Response(
            content=text_content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.txt"'},
        )

    pdf_bytes = build_pdf_bytes(text_content)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.pdf"'},
    )


@app.get("/api/metrics/snapshot")
def metrics_snapshot(request: Request) -> dict[str, Any]:
    result = METRICS.snapshot()
    result["requestId"] = get_request_id(request)
    result["ragRetrieverMode"] = get_rag_retriever_mode()
    result["ragSearchConfig"] = format_rag_search_config(get_rag_search_config()).model_dump()
    result["authMode"] = get_auth_mode()
    return result


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = get_request_id(request)

    code = ERROR_CODE_BY_STATUS.get(exc.status_code, "REQUEST_ERROR")
    message = "Request failed"
    extra: dict[str, Any] = {}

    if isinstance(exc.detail, str):
        message = exc.detail
    elif isinstance(exc.detail, dict):
        custom_code = str(exc.detail.get("code", "")).strip()
        custom_message = str(exc.detail.get("message", "")).strip()
        if custom_code:
            code = custom_code
        if custom_message:
            message = custom_message

        for key, value in exc.detail.items():
            if key in {"code", "message", "requestId"}:
                continue
            extra[key] = value

    payload: dict[str, Any] = build_error_payload(code=code, message=message, request_id=request_id)
    if extra:
        payload.update(extra)

    set_error_context(request, error_code=code, exception_type="HTTPException")
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = get_request_id(request)
    first_error = exc.errors()[0] if exc.errors() else None
    message = first_error.get("msg", "Request validation failed") if first_error else "Request validation failed"
    set_error_context(request, error_code="VALIDATION_ERROR", exception_type="RequestValidationError")
    return JSONResponse(
        status_code=422,
        content=build_error_payload(code="VALIDATION_ERROR", message=message, request_id=request_id),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, _: Exception) -> JSONResponse:
    request_id = get_request_id(request)
    set_error_context(request, error_code="INTERNAL_ERROR", exception_type="UnhandledException")
    return JSONResponse(
        status_code=500,
        content=build_error_payload(
            code="INTERNAL_ERROR",
            message="Unexpected server error",
            request_id=request_id,
        ),
    )

