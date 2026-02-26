"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, type KeyboardEvent as ReactKeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./page.module.css";
import {
  formatDateTime,
  toErrorMessage,
  toResultText,
  toUserFriendlyError,
  type AnalysisInsights,
  type AnalyzeResponse,
  type DefectCategory,
  type RagHit,
  type ScoreBreakdown,
} from "./page.utils";
import { buildLoginHref, useClientAuth } from "./client-auth";

type RetrievalMode = "keyword" | "vector";
type FeedbackTone = "info" | "success" | "error";

type HistoryItem = {
  id: number;
  createdAt: string;
  score: number;
  scoreBreakdown: ScoreBreakdown;
  keywordSummary: {
    matched: string[];
    missing: string[];
  };
  analysisSource: string;
  requestId: string;
};

type HistoryDetail = {
  id: number;
  createdAt: string;
  score: number;
  scoreBreakdown: ScoreBreakdown;
  matchedKeywords: string[];
  missingKeywords: string[];
  suggestions: string[];
  optimizedResume: string;
  insights: AnalysisInsights;
  analysisSource: string;
  sessionId: string;
  requestId: string;
  pipAdvice: string[];
  defectCategories: DefectCategory[];
  ragHits: RagHit[];
};

type ResumeVersionSnapshot = {
  versionNo: number;
  content: string;
};

type AnalysisWorkspaceSnapshot = {
  resumeText: string;
  jdText: string;
  hasSubmitted: boolean;
  hasAppliedOptimization: boolean;
  result: AnalyzeResponse | null;
  updatedAt: string;
};

type InterviewSignalState = {
  hasInterviewStarted: boolean;
  hasInterviewFinished: boolean;
  latestSessionId: string;
  latestSessionKey: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";
const ANALYSIS_WORKSPACE_STORAGE_KEY = "career_hero.analysis.workspace.v1";
const INTERVIEW_SESSION_STORAGE_KEY = "career_hero.interview.sessions.v2";
const INTERVIEW_BOOTSTRAP_STORAGE_KEY = "career_hero.interview.bootstrap.v1";
const MAX_TEXT_LENGTH = 20_000;
const HISTORY_LIMIT_OPTIONS = [10, 20, 50, 100] as const;

const DEFAULT_BREAKDOWN: ScoreBreakdown = {
  keyword_match: 0,
  coverage: 0,
  writing_quality_stub: 0,
};

const DEFAULT_INSIGHTS: AnalysisInsights = {
  summary: "",
  strengths: [],
  risks: [],
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toSafeString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function toSafeNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean);
}

function toPositiveInt(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed < 1) return null;
  return parsed;
}

function toScoreBreakdown(value: unknown): ScoreBreakdown {
  if (!isRecord(value)) return DEFAULT_BREAKDOWN;
  return {
    keyword_match: Math.max(0, Math.min(100, toSafeNumber(value.keyword_match))),
    coverage: Math.max(0, Math.min(100, toSafeNumber(value.coverage))),
    writing_quality_stub: Math.max(0, Math.min(100, toSafeNumber(value.writing_quality_stub))),
  };
}

function toInsights(value: unknown): AnalysisInsights {
  if (!isRecord(value)) return DEFAULT_INSIGHTS;
  return {
    summary: toSafeString(value.summary),
    strengths: toStringArray(value.strengths),
    risks: toStringArray(value.risks),
  };
}

function toPipAdvice(value: unknown): string[] {
  if (typeof value === "string") {
    return value
      .split(/\r?\n|；|;/)
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 10);
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "string" ? item : toSafeString(isRecord(item) ? item.advice : "")))
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 10);
  }
  return [];
}

function toDefectCategories(value: unknown): DefectCategory[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item, index) => {
      if (typeof item === "string") {
        return { name: item.trim(), details: [] } as DefectCategory;
      }
      if (!isRecord(item)) return null;
      return {
        name: toSafeString(item.name || item.category || item.label, `分类-${index + 1}`).trim(),
        count: typeof item.count === "number" ? item.count : undefined,
        details: toStringArray(item.details ?? item.items ?? item.examples),
      } as DefectCategory;
    })
    .filter((item): item is DefectCategory => Boolean(item?.name))
    .slice(0, 10);
}

function toRagHits(value: unknown): RagHit[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item, index) => {
      if (typeof item === "string") {
        return { title: `片段 #${index + 1}`, snippet: item.trim() } as RagHit;
      }
      if (!isRecord(item)) return null;
      return {
        title: toSafeString(item.title || item.name || item.id, `片段 #${index + 1}`),
        snippet: toSafeString(item.snippet || item.content || item.text),
        score: typeof item.score === "number" ? item.score : undefined,
        source: toSafeString(item.source || item.chunkId) || undefined,
      } as RagHit;
    })
    .filter((item): item is RagHit => item !== null && Boolean(item.title || item.snippet))
    .slice(0, 12);
}

function normalizeAnalyzeResponse(value: unknown): AnalyzeResponse | null {
  if (!isRecord(value)) return null;
  const source = toSafeString(value.analysisSource, "rule");
  const retrievalModeRaw = toSafeString(value.retrievalMode ?? value.retrieval_mode).toLowerCase();

  return {
    score: toSafeNumber(value.score),
    matchedKeywords: toStringArray(value.matchedKeywords),
    missingKeywords: toStringArray(value.missingKeywords),
    suggestions: toStringArray(value.suggestions),
    optimizedResume: toSafeString(value.optimizedResume),
    scoreBreakdown: toScoreBreakdown(value.scoreBreakdown),
    insights: toInsights(value.insights),
    analysisSource: source === "gemini" ? "gemini" : "rule",
    fallbackUsed: Boolean(value.fallbackUsed),
    promptVersion: toSafeString(value.promptVersion, "unknown"),
    historyId: typeof value.historyId === "number" ? value.historyId : undefined,
    requestId: toSafeString(value.requestId),
    pipAdvice: toPipAdvice(value.pipAdvice ?? value.pip_advice),
    defectCategories: toDefectCategories(value.defectCategories ?? value.defects),
    ragEnabled: typeof value.ragEnabled === "boolean" ? value.ragEnabled : undefined,
    ragHits: toRagHits(value.ragHits ?? value.rag_hits ?? value.retrievalHits),
    retrievalMode: retrievalModeRaw === "vector" ? "vector" : "keyword",
    retrievalSource: toSafeString(value.retrievalSource ?? value.retrieval_source),
  };
}

function normalizeHistoryItem(value: unknown): HistoryItem | null {
  if (!isRecord(value)) return null;
  const keywordSummary = isRecord(value.keywordSummary) ? value.keywordSummary : {};
  return {
    id: toSafeNumber(value.id),
    createdAt: toSafeString(value.createdAt),
    score: toSafeNumber(value.score),
    scoreBreakdown: toScoreBreakdown(value.scoreBreakdown),
    keywordSummary: {
      matched: toStringArray(keywordSummary.matched),
      missing: toStringArray(keywordSummary.missing),
    },
    analysisSource: toSafeString(value.analysisSource, "rule"),
    requestId: toSafeString(value.requestId),
  };
}

function normalizeHistoryDetail(value: unknown): HistoryDetail | null {
  if (!isRecord(value)) return null;
  return {
    id: toSafeNumber(value.id),
    createdAt: toSafeString(value.createdAt),
    score: toSafeNumber(value.score),
    scoreBreakdown: toScoreBreakdown(value.scoreBreakdown),
    matchedKeywords: toStringArray(value.matchedKeywords),
    missingKeywords: toStringArray(value.missingKeywords),
    suggestions: toStringArray(value.suggestions),
    optimizedResume: toSafeString(value.optimizedResume),
    insights: toInsights(value.insights),
    analysisSource: toSafeString(value.analysisSource, "rule"),
    sessionId: toSafeString(value.sessionId),
    requestId: toSafeString(value.requestId),
    pipAdvice: toPipAdvice(value.pipAdvice ?? value.pip_advice),
    defectCategories: toDefectCategories(value.defectCategories ?? value.defects),
    ragHits: toRagHits(value.ragHits ?? value.rag_hits ?? value.retrievalHits),
  };
}

function parseFileName(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const normalMatch = disposition.match(/filename="?([^\"]+)"?/i);
  return normalMatch?.[1] || fallback;
}

async function readJsonSafe(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return null;
  }
  return response.json().catch(() => null);
}

function buildApiError(response: Response, payload: unknown, fallback: string): Error {
  const fromApi = toErrorMessage(payload);
  if (fromApi && fromApi !== "分析失败，请稍后重试") {
    return new Error(fromApi);
  }
  if (response.status === 404) return new Error(`${fallback}（接口可能未启用或路径变更）`);
  if (response.status === 429) return new Error("请求过于频繁，请稍后重试");
  if (response.status >= 500) return new Error("服务暂时不可用，请稍后重试");
  return new Error(fallback);
}

function parseRagTopKInput(input: string): { value: number; message: string } {
  const raw = input.trim();
  if (!raw) return { value: 5, message: "召回条数为空，已使用默认值 5。" };
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return { value: 5, message: "召回条数必须是 1-20 的整数，已回退为 5。" };
  const rounded = Math.round(parsed);
  const clamped = Math.max(1, Math.min(20, rounded));
  if (rounded !== parsed) return { value: clamped, message: `召回条数仅支持整数，已自动取整为 ${clamped}。` };
  if (clamped !== rounded) return { value: clamped, message: `召回条数超出范围（1-20），已自动修正为 ${clamped}。` };
  return { value: clamped, message: "" };
}

function parseRagThresholdInput(input: string): { value: number; message: string } {
  const raw = input.trim();
  if (!raw) return { value: 0.2, message: "相似度阈值为空，已使用默认值 0.2。" };
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return { value: 0.2, message: "相似度阈值必须是 0-1 的数字，已回退为 0.2。" };
  const clamped = Math.max(0, Math.min(1, parsed));
  if (clamped !== parsed) return { value: clamped, message: `相似度阈值超出范围（0-1），已自动修正为 ${clamped}。` };
  return { value: clamped, message: "" };
}

function normalizeWorkspaceSnapshot(value: unknown): AnalysisWorkspaceSnapshot | null {
  if (!isRecord(value)) return null;
  return {
    resumeText: toSafeString(value.resumeText),
    jdText: toSafeString(value.jdText),
    hasSubmitted: Boolean(value.hasSubmitted),
    hasAppliedOptimization: Boolean(value.hasAppliedOptimization),
    result: normalizeAnalyzeResponse(value.result),
    updatedAt: toSafeString(value.updatedAt, new Date().toISOString()),
  };
}

function readAnalysisWorkspaceStore(): Record<string, AnalysisWorkspaceSnapshot> {
  if (typeof window === "undefined") return {};

  try {
    const raw = window.localStorage.getItem(ANALYSIS_WORKSPACE_STORAGE_KEY);
    if (!raw) return {};

    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) return {};

    return Object.entries(parsed).reduce<Record<string, AnalysisWorkspaceSnapshot>>((acc, [key, value]) => {
      const normalized = normalizeWorkspaceSnapshot(value);
      if (normalized) acc[key] = normalized;
      return acc;
    }, {});
  } catch {
    return {};
  }
}

function writeAnalysisWorkspaceStore(store: Record<string, AnalysisWorkspaceSnapshot>) {
  if (typeof window === "undefined") return;

  const entries = Object.entries(store)
    .sort((a, b) => new Date(b[1].updatedAt).getTime() - new Date(a[1].updatedAt).getTime())
    .slice(0, 12);

  const compact = entries.reduce<Record<string, AnalysisWorkspaceSnapshot>>((acc, [key, value]) => {
    acc[key] = value;
    return acc;
  }, {});

  try {
    window.localStorage.setItem(ANALYSIS_WORKSPACE_STORAGE_KEY, JSON.stringify(compact));
  } catch {
    // ignore
  }
}

function readInterviewSignalForContext(contextKey: string): InterviewSignalState {
  if (typeof window === "undefined") {
    return { hasInterviewStarted: false, hasInterviewFinished: false, latestSessionId: "", latestSessionKey: "" };
  }

  try {
    const raw = window.localStorage.getItem(INTERVIEW_SESSION_STORAGE_KEY);
    if (!raw) {
      return { hasInterviewStarted: false, hasInterviewFinished: false, latestSessionId: "", latestSessionKey: "" };
    }

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return { hasInterviewStarted: false, hasInterviewFinished: false, latestSessionId: "", latestSessionKey: "" };
    }

    const related = parsed
      .filter((item) => isRecord(item) && toSafeString(item.resumeContextKey) === contextKey)
      .sort((a, b) => new Date(toSafeString((b as Record<string, unknown>).updatedAt)).getTime() - new Date(toSafeString((a as Record<string, unknown>).updatedAt)).getTime());

    if (!related.length) {
      return { hasInterviewStarted: false, hasInterviewFinished: false, latestSessionId: "", latestSessionKey: "" };
    }

    const latest = related[0] as Record<string, unknown>;
    const hasInterviewFinished = related.some((item) => {
      if (!isRecord(item)) return false;
      const status = toSafeString(item.status).toLowerCase();
      return status === "finished" || status === "done" || status === "completed" || isRecord(item.feedback);
    });

    return {
      hasInterviewStarted: true,
      hasInterviewFinished,
      latestSessionId: toSafeString(latest.sessionId),
      latestSessionKey: toSafeString(latest.key),
    };
  } catch {
    return { hasInterviewStarted: false, hasInterviewFinished: false, latestSessionId: "", latestSessionKey: "" };
  }
}

function inferPositionFromJd(jdText: string): string {
  const firstLine = jdText
    .split(/\r?\n/)
    .map((item) => item.trim())
    .find(Boolean);

  if (!firstLine) return "";
  if (firstLine.length <= 30) return firstLine;
  return `${firstLine.slice(0, 30)}…`;
}

function HomeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();

  const {
    authState,
    authReady,
    isAuthenticated,
    authStatusText,
    authFailureReason,
    clearAuthFailureReason,
    tokenDraft,
    setTokenDraft,
    applyAccessToken,
    rotateSession,
    resetAuthState,
    apiFetch,
  } = useClientAuth(API_BASE_URL, { autoRedirectOnUnauthorized: true });

  const fetch = useCallback((input: RequestInfo | URL, init?: RequestInit) => apiFetch(input, init), [apiFetch]);

  const [resumeText, setResumeText] = useState("");
  const [jdText, setJdText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const [hasAppliedOptimization, setHasAppliedOptimization] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [workspaceReady, setWorkspaceReady] = useState(false);
  const [interviewSignal, setInterviewSignal] = useState<InterviewSignalState>({
    hasInterviewStarted: false,
    hasInterviewFinished: false,
    latestSessionId: "",
    latestSessionKey: "",
  });

  const [historyItems, setHistoryItems] = useState<HistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLimit, setHistoryLimit] = useState<number>(20);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyRequestIdFilter, setHistoryRequestIdFilter] = useState("");
  const [historyLiveMessage, setHistoryLiveMessage] = useState("");
  const [latestHistoryId, setLatestHistoryId] = useState<number | null>(null);

  const [selectedHistoryId, setSelectedHistoryId] = useState<number | null>(null);
  const [historyDetails, setHistoryDetails] = useState<Record<number, HistoryDetail>>({});
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [detailError, setDetailError] = useState("");

  const [ragEnabled, setRagEnabled] = useState(false);
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("keyword");
  const [ragTopKInput, setRagTopKInput] = useState("5");
  const [ragThresholdInput, setRagThresholdInput] = useState("0.2");
  const [ragModeMessage, setRagModeMessage] = useState("");
  const [retrievalModeMessage, setRetrievalModeMessage] = useState("");
  const [ragTopKMessage, setRagTopKMessage] = useState("");
  const [ragThresholdMessage, setRagThresholdMessage] = useState("");

  const [keepLatestInput, setKeepLatestInput] = useState("200");
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cleanupMessage, setCleanupMessage] = useState("");

  const [authMessage, setAuthMessage] = useState("");
  const [exportingLabel, setExportingLabel] = useState("");
  const [feedback, setFeedback] = useState<{ tone: FeedbackTone; text: string } | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);

  const loginHref = useMemo(() => {
    const query = searchParams.toString();
    const returnTo = query ? `${pathname || "/"}?${query}` : pathname || "/";
    return buildLoginHref(returnTo);
  }, [pathname, searchParams]);

  const currentResumeId = useMemo(() => toPositiveInt(searchParams.get("resumeId")), [searchParams]);
  const currentVersionNo = useMemo(() => toPositiveInt(searchParams.get("versionNo")), [searchParams]);
  const resumeContextKey = useMemo(() => {
    const explicit = (searchParams.get("resumeContextKey") || "").trim();
    if (explicit) return explicit;
    if (!currentResumeId) return "manual";
    return `resume-${currentResumeId}-v${currentVersionNo ?? 0}`;
  }, [currentResumeId, currentVersionNo, searchParams]);

  const canAttemptRefresh = authState?.mode === "custom" && Boolean(authState.refreshToken);

  useEffect(() => {
    if (!authReady || isAuthenticated || canAttemptRefresh) return;
    const query = searchParams.toString();
    const returnTo = query ? `${pathname || "/"}?${query}` : pathname || "/";
    router.replace(buildLoginHref(returnTo));
  }, [authReady, isAuthenticated, canAttemptRefresh, pathname, router, searchParams]);

  useEffect(() => {
    if (isAuthenticated && authFailureReason) {
      clearAuthFailureReason();
    }
  }, [authFailureReason, clearAuthFailureReason, isAuthenticated]);

  const canSubmit = useMemo(() => !loading && resumeText.trim().length > 0 && jdText.trim().length > 0, [loading, resumeText, jdText]);

  const stepStates = useMemo(() => {
    const step1Done = Boolean(resumeText.trim());
    const step2Done = Boolean(jdText.trim());
    const step3Done = Boolean(result);
    const step4Done = hasAppliedOptimization;
    const step5Done = interviewSignal.hasInterviewStarted;
    const step6Done = interviewSignal.hasInterviewFinished;

    return {
      step1Done,
      step2Done,
      step3Done,
      step4Done,
      step5Done,
      step6Done,
      step2Active: step1Done && !step2Done,
      step3Active: step1Done && step2Done && !step3Done,
      step4Active: step3Done && !step4Done,
      step5Active: step4Done && !step5Done,
      step6Active: step5Done && !step6Done,
    };
  }, [hasAppliedOptimization, interviewSignal.hasInterviewFinished, interviewSignal.hasInterviewStarted, jdText, result, resumeText]);

  const completedStepCount = useMemo(
    () => [stepStates.step1Done, stepStates.step2Done, stepStates.step3Done, stepStates.step4Done, stepStates.step5Done, stepStates.step6Done].filter(Boolean).length,
    [stepStates],
  );
  const coreStepDoneCount = useMemo(
    () => [stepStates.step1Done, stepStates.step2Done, stepStates.step3Done].filter(Boolean).length,
    [stepStates.step1Done, stepStates.step2Done, stepStates.step3Done],
  );
  const coreProgressPercent = Math.round((coreStepDoneCount / 3) * 100);
  const primaryCtaText = loading ? "分析中..." : stepStates.step3Done ? "重新分析" : "开始分析";

  const resultText = useMemo(() => (result ? toResultText(result) : ""), [result]);
  const resultBreakdown = result?.scoreBreakdown ?? DEFAULT_BREAKDOWN;
  const selectedHistoryDetail = selectedHistoryId === null ? null : historyDetails[selectedHistoryId] ?? null;

  const showFeedback = useCallback((text: string, tone: FeedbackTone = "info") => {
    setFeedback({ text, tone });
    if (feedbackTimerRef.current) window.clearTimeout(feedbackTimerRef.current);
    feedbackTimerRef.current = window.setTimeout(() => {
      setFeedback(null);
      feedbackTimerRef.current = null;
    }, 2800);
  }, []);

  useEffect(() => () => {
    if (feedbackTimerRef.current) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  useEffect(() => {
    const workspace = readAnalysisWorkspaceStore();
    const snapshot = workspace[resumeContextKey];

    if (snapshot) {
      setResumeText(snapshot.resumeText);
      setJdText(snapshot.jdText);
      setHasSubmitted(snapshot.hasSubmitted);
      setHasAppliedOptimization(snapshot.hasAppliedOptimization);
      setResult(snapshot.result);
    } else {
      setResumeText("");
      setJdText("");
      setHasSubmitted(false);
      setHasAppliedOptimization(false);
      setResult(null);
    }

    setError("");
    setWorkspaceReady(true);
  }, [resumeContextKey]);

  useEffect(() => {
    if (!workspaceReady) return;

    const workspace = readAnalysisWorkspaceStore();
    workspace[resumeContextKey] = {
      resumeText,
      jdText,
      hasSubmitted,
      hasAppliedOptimization,
      result,
      updatedAt: new Date().toISOString(),
    };
    writeAnalysisWorkspaceStore(workspace);
  }, [workspaceReady, resumeContextKey, resumeText, jdText, hasSubmitted, hasAppliedOptimization, result]);

  const refreshInterviewSignal = useCallback(() => {
    setInterviewSignal(readInterviewSignalForContext(resumeContextKey));
  }, [resumeContextKey]);

  useEffect(() => {
    refreshInterviewSignal();

    const onFocus = () => refreshInterviewSignal();
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        refreshInterviewSignal();
      }
    };

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refreshInterviewSignal]);

  useEffect(() => {
    if (!workspaceReady) return;

    const resumeId = currentResumeId;
    if (!resumeId) return;

    const existingSnapshot = readAnalysisWorkspaceStore()[resumeContextKey];
    if (existingSnapshot?.resumeText.trim()) return;

    let cancelled = false;
    const loadResume = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/resumes/${resumeId}`);
        const data = await readJsonSafe(response);
        if (!response.ok) return;
        const payload = isRecord(data) && isRecord(data.item) ? data.item : data;
        if (!isRecord(payload)) return;

        const versions = Array.isArray(payload.versions)
          ? payload.versions
              .map((item) => {
                if (!isRecord(item)) return null;
                const versionNo = toSafeNumber(item.versionNo);
                if (versionNo < 1) return null;
                return { versionNo, content: toSafeString(item.content) } as ResumeVersionSnapshot;
              })
              .filter((item): item is ResumeVersionSnapshot => item !== null)
          : [];

        const selected = (currentVersionNo ? versions.find((item) => item.versionNo === currentVersionNo) : null) ?? versions[0];

        if (!cancelled && selected?.content.trim()) {
          setResumeText(selected.content);
          showFeedback("已自动填充简历内容", "info");
        }
      } catch {
        // ignore prefill error
      }
    };

    void loadResume();
    return () => {
      cancelled = true;
    };
  }, [currentResumeId, currentVersionNo, fetch, resumeContextKey, showFeedback, workspaceReady]);

  const loadHistory = useCallback(async (params?: { requestId?: string; limit?: number }) => {
    setHistoryLoading(true);
    setHistoryError("");

    const limit = params?.limit ?? historyLimit;
    const requestId = params?.requestId?.trim() ?? historyRequestIdFilter.trim();

    const query = new URLSearchParams({ limit: String(limit) });
    if (requestId) query.set("requestId", requestId);

    try {
      const response = await fetch(`${API_BASE_URL}/api/history?${query.toString()}`);
      const data = await readJsonSafe(response);
      if (!response.ok) throw buildApiError(response, data, "历史记录加载失败");

      const payload = isRecord(data) ? data : {};
      const items = (Array.isArray(payload.items) ? payload.items : [])
        .map((item) => normalizeHistoryItem(item))
        .filter((item): item is HistoryItem => item !== null && item.id > 0);

      setHistoryItems(items);
      setHistoryTotal(typeof payload.total === "number" ? payload.total : items.length);
      setHistoryLiveMessage(`历史记录已刷新，共 ${items.length} 条`);

      if (selectedHistoryId !== null && !items.some((item) => item.id === selectedHistoryId)) {
        setSelectedHistoryId(null);
      }
    } catch (err) {
      const message = toUserFriendlyError(err, "历史记录加载失败");
      setHistoryError(message);
      setHistoryLiveMessage(`历史记录加载失败：${message}`);
    } finally {
      setHistoryLoading(false);
    }
  }, [historyLimit, historyRequestIdFilter, selectedHistoryId]);

  useEffect(() => {
    void loadHistory({ limit: historyLimit });
  }, [historyLimit, loadHistory]);

  const loadHistoryDetail = useCallback(async (id: number) => {
    setDetailLoadingId(id);
    setDetailError("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/history/${id}`);
      const data = await readJsonSafe(response);
      if (!response.ok) throw buildApiError(response, data, "历史详情加载失败");

      const payload = isRecord(data) && isRecord(data.item) ? data.item : data;
      const detail = normalizeHistoryDetail(payload);
      if (!detail) throw new Error("历史详情数据格式异常");
      setHistoryDetails((prev) => ({ ...prev, [id]: detail }));
    } catch (err) {
      const message = toUserFriendlyError(err, "历史详情加载失败");
      setDetailError(message);
      showFeedback(message, "error");
    } finally {
      setDetailLoadingId(null);
    }
  }, [showFeedback]);

  useEffect(() => {
    if (selectedHistoryId === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedHistoryId(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedHistoryId]);

  async function onAnalyze() {
    if (!canSubmit) return;
    setHasSubmitted(true);
    setLoading(true);
    setError("");

    try {
      const topKValidation = parseRagTopKInput(ragTopKInput);
      const thresholdValidation = parseRagThresholdInput(ragThresholdInput);
      const safeRagTopK = topKValidation.value;
      const safeRagThreshold = thresholdValidation.value;

      if (ragEnabled) {
        setRagTopKMessage(topKValidation.message);
        setRagThresholdMessage(thresholdValidation.message);
        if (topKValidation.message) setRagTopKInput(String(safeRagTopK));
        if (thresholdValidation.message) setRagThresholdInput(String(safeRagThreshold));
      } else {
        setRagTopKMessage("");
        setRagThresholdMessage("");
      }

      let requestPayload: Record<string, unknown> = {
        resumeText,
        jdText,
        ...(ragEnabled
          ? {
              ragEnabled: true,
              retrievalMode,
              ragTopK: safeRagTopK,
              ragScoreThreshold: safeRagThreshold,
            }
          : {}),
      };

      let response = await fetch(`${API_BASE_URL}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload),
      });
      let data = await readJsonSafe(response);

      if (!response.ok && ragEnabled) {
        const fallbackPayload = { resumeText, jdText };
        response = await fetch(`${API_BASE_URL}/api/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(fallbackPayload),
        });
        data = await readJsonSafe(response);
        if (response.ok) {
          setRagModeMessage("后端暂不支持知识库增强参数，本次已自动降级为普通分析。");
          setRetrievalModeMessage("");
          showFeedback("知识库增强参数协商失败，已自动降级", "info");
        }
        requestPayload = fallbackPayload;
      }

      if (!response.ok) throw buildApiError(response, data, "分析失败，请稍后重试");

      const parsed = normalizeAnalyzeResponse(data);
      if (!parsed) throw new Error("分析结果解析失败，请稍后重试");

      setResult({
        ...parsed,
        ragEnabled: typeof parsed.ragEnabled === "boolean" ? parsed.ragEnabled : Boolean(requestPayload.ragEnabled),
      });
      setHasAppliedOptimization(false);

      if (typeof parsed.historyId === "number") setLatestHistoryId(parsed.historyId);
      await loadHistory({ requestId: historyRequestIdFilter, limit: historyLimit });
      showFeedback("分析完成", "success");
    } catch (err) {
      const message = toUserFriendlyError(err, "分析失败，请稍后重试");
      setError(message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  function onQuickSubmit(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      void onAnalyze();
    }
  }

  function onReset() {
    setResumeText("");
    setJdText("");
    setError("");
    setResult(null);
    setHasSubmitted(false);
    setHasAppliedOptimization(false);
    setRagModeMessage("");
    setRagTopKMessage("");
    setRagThresholdMessage("");
  }

  function onApplyOptimization() {
    if (!result?.optimizedResume?.trim()) {
      showFeedback("当前暂无可应用的优化简历", "info");
      return;
    }

    setResumeText(result.optimizedResume);
    setHasAppliedOptimization(true);
    showFeedback("已将优化版简历带入输入区，可继续面试", "success");
  }

  function createInterviewHref() {
    const query = new URLSearchParams();
    query.set("resumeContextKey", resumeContextKey);

    if (currentResumeId) {
      query.set("resumeId", String(currentResumeId));
    }
    if (currentVersionNo) {
      query.set("versionNo", String(currentVersionNo));
    }

    if (interviewSignal.latestSessionId) {
      query.set("sessionId", interviewSignal.latestSessionId);
    }
    if (interviewSignal.latestSessionKey) {
      query.set("sessionKey", interviewSignal.latestSessionKey);
    }

    return query.toString() ? `/interview?${query.toString()}` : "/interview";
  }

  function persistInterviewBootstrap() {
    const bootstrapPayload = {
      resumeContextKey,
      position: inferPositionFromJd(jdText),
      jdText,
      resumeText: result?.optimizedResume?.trim() || resumeText,
      requestedAt: new Date().toISOString(),
      autoStart: true,
    };

    try {
      window.localStorage.setItem(INTERVIEW_BOOTSTRAP_STORAGE_KEY, JSON.stringify(bootstrapPayload));
    } catch {
      // ignore bootstrap persistence error
    }
  }

  function onStartInterview() {
    if (!jdText.trim() || !(result?.optimizedResume?.trim() || resumeText.trim())) {
      showFeedback("请先完成诊断并确保有可用简历内容", "info");
      return;
    }

    persistInterviewBootstrap();
    router.push(createInterviewHref());
  }

  function onApplyToken() {
    const next = applyAccessToken(tokenDraft.trim());
    setAuthMessage(`已更新访问令牌（模式：${next.mode === "custom" ? "自定义令牌" : "访客"}）`);
    showFeedback("访问令牌已更新", "success");
  }

  function onRotateSessionId() {
    const next = rotateSession();
    setAuthMessage(`已刷新会话编号：${next.sessionId}`);
    showFeedback("会话已刷新", "success");
  }

  function onResetAuth() {
    resetAuthState();
    setAuthMessage("已重置本地鉴权状态");
    showFeedback("已回到本地默认鉴权", "info");
  }

  async function copyText(content: string, label: string) {
    if (!content.trim()) {
      showFeedback(`${label}为空，无可复制内容`, "info");
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      showFeedback(`${label}已复制`, "success");
    } catch {
      showFeedback("复制失败，请检查浏览器剪贴板权限", "error");
    }
  }

  async function onExportHistory(historyId: number, format: "txt" | "json" | "pdf") {
    const label = `#${historyId} ${format.toUpperCase()}`;
    setExportingLabel(label);

    try {
      const response = await fetch(`${API_BASE_URL}/api/history/${historyId}/export?format=${format}`);
      if (!response.ok) {
        const payload = await readJsonSafe(response);
        throw buildApiError(response, payload, "导出失败");
      }
      const blob = await response.blob();
      const filename = parseFileName(response.headers.get("content-disposition"), `职途助手-${historyId}.${format}`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      showFeedback(`已导出 ${filename}`, "success");
    } catch (err) {
      showFeedback(toUserFriendlyError(err, "导出失败，请稍后重试"), "error");
    } finally {
      setExportingLabel("");
    }
  }

  async function onExportCurrent(format: "txt" | "json" | "pdf") {
    const historyId = result?.historyId ?? selectedHistoryId;
    if (!historyId) {
      showFeedback("暂无可导出的历史记录，请先完成一次分析", "info");
      return;
    }
    await onExportHistory(historyId, format);
  }

  async function onCleanupKeepLatest() {
    const keepLatest = Number.parseInt(keepLatestInput, 10);
    if (!Number.isInteger(keepLatest) || keepLatest < 1) {
      setCleanupMessage("请输入有效的保留数量（>=1）");
      return;
    }

    setCleanupLoading(true);
    setCleanupMessage("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/history/cleanup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "keep_latest", keepLatest }),
      });
      const data = await readJsonSafe(response);
      if (!response.ok) throw buildApiError(response, data, "清理失败");

      const payload = isRecord(data) ? data : {};
      setCleanupMessage(`清理完成：删除 ${toSafeNumber(payload.deleted)} 条，当前剩余 ${toSafeNumber(payload.total)} 条`);
      await loadHistory({ requestId: historyRequestIdFilter, limit: historyLimit });
    } catch (err) {
      setCleanupMessage(toUserFriendlyError(err, "清理失败，请稍后重试"));
    } finally {
      setCleanupLoading(false);
    }
  }

  async function onCleanupDeleteAll() {
    const confirmed = window.prompt("危险操作：输入 DELETE 确认清空所有历史");
    if (!confirmed) return;

    setCleanupLoading(true);
    setCleanupMessage("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/history/cleanup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "delete_all", confirmText: confirmed }),
      });
      const data = await readJsonSafe(response);
      if (!response.ok) throw buildApiError(response, data, "清空失败");
      setCleanupMessage("历史记录已清空");
      setHistoryDetails({});
      setSelectedHistoryId(null);
      await loadHistory({ requestId: historyRequestIdFilter, limit: historyLimit });
    } catch (err) {
      setCleanupMessage(toUserFriendlyError(err, "清空失败，请稍后重试"));
    } finally {
      setCleanupLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <section className={`${styles.hero} ${styles.appHero}`}>
          <div className={styles.heroMain}>
            <div className={styles.heroTop}>
              <p className={styles.taskBadge}>主线任务</p>
              <h1 className={styles.title}>先把 JD 喂给我</h1>
              <p className={styles.subtitle}>填简历 + JD，30 秒拿到匹配建议。</p>
              <p className={styles.lead}>默认只保留主路径：输入 → 分析 → 下一步。</p>
            </div>

            <div className={styles.progressMeta}>
              <span>主线进度：{coreStepDoneCount}/3</span>
              <span>全链路：{completedStepCount}/6</span>
            </div>
            <div className={styles.progressTrackLg} aria-hidden="true">
              <div className={styles.progressFillLg} style={{ width: `${coreProgressPercent}%` }} />
            </div>

            <div className={styles.heroActions}>
              <button className={`${styles.button} ${styles.primaryCta}`} disabled={!canSubmit} onClick={onAnalyze}>
                {primaryCtaText}
              </button>
              <div className={styles.secondaryActionRow}>
                {stepStates.step3Done && !stepStates.step4Done ? (
                  <button
                    className={styles.secondaryButton}
                    onClick={onApplyOptimization}
                    disabled={!result?.optimizedResume?.trim()}
                  >
                    下一步：用优化简历
                  </button>
                ) : stepStates.step4Done ? (
                  <button
                    className={styles.secondaryButton}
                    onClick={onStartInterview}
                    disabled={!jdText.trim() || !(result?.optimizedResume?.trim() || resumeText.trim())}
                  >
                    下一步：去面试
                  </button>
                ) : (
                  <p className={styles.heroHint}>填完简历和 JD，就能开始分析</p>
                )}
                <Link className={styles.ghostLink} href={loginHref}>登录与账号</Link>
              </div>
            </div>
          </div>

          <ol className={styles.routePills}>
            <li className={`${styles.routePill} ${(stepStates.step1Done && stepStates.step2Done) ? styles.routePillDone : styles.routePillActive}`}>
              1. 输入简历 + JD
            </li>
            <li className={`${styles.routePill} ${stepStates.step3Done ? styles.routePillDone : (stepStates.step1Done && stepStates.step2Done ? styles.routePillActive : "")}`}>
              2. 开始分析
            </li>
            <li className={`${styles.routePill} ${(stepStates.step4Done || stepStates.step5Done || stepStates.step6Done) ? styles.routePillDone : (stepStates.step3Done ? styles.routePillNext : "")}`}>
              3. 去下一步（改简历 / 面试）
            </li>
          </ol>
        </section>

        {feedback ? (
          <div className={`${styles.feedback} ${styles[`feedback${feedback.tone}`]}`} role="status" aria-live="polite">
            {feedback.text}
          </div>
        ) : null}

        <section className={styles.workspaceGrid} aria-label="主任务区">
          <section className={`${styles.card} ${styles.taskCard}`} aria-labelledby="analyze-section-title">
            <div className={styles.taskHeader}>
              <h2 id="analyze-section-title" className={styles.sectionTitle}>输入区</h2>
              <p className={styles.meta}>按 Ctrl/⌘ + Enter 也能直接分析。</p>
            </div>

            <div className={styles.taskInputCard}>
              <div className={styles.taskInputHeader}>
                <p className={styles.stepBadge}>简历</p>
                <span className={styles.stepState}>{stepStates.step1Done ? "已填写" : "待填写"}</span>
              </div>
              <label className={styles.label} htmlFor="resume-input">你的简历</label>
              <textarea
                id="resume-input"
                className={styles.textarea}
                rows={9}
                maxLength={MAX_TEXT_LENGTH}
                value={resumeText}
                onChange={(event) => setResumeText(event.target.value)}
                onKeyDown={onQuickSubmit}
                placeholder="贴上你的简历内容"
              />
              <p className={styles.meta}>字数：{resumeText.length}/{MAX_TEXT_LENGTH}</p>
              <p className={styles.meta}>也可以先去 <Link className={styles.inlineLink} href="/resumes">简历库</Link> 选一份。</p>
            </div>

            <div className={styles.taskInputCard}>
              <div className={styles.taskInputHeader}>
                <p className={styles.stepBadge}>JD</p>
                <span className={styles.stepState}>{stepStates.step2Done ? "已填写" : "待填写"}</span>
              </div>
              <label className={styles.label} htmlFor="jd-input">目标岗位 JD</label>
              <textarea
                id="jd-input"
                className={styles.textarea}
                rows={7}
                maxLength={MAX_TEXT_LENGTH}
                value={jdText}
                onChange={(event) => setJdText(event.target.value)}
                onKeyDown={onQuickSubmit}
                placeholder="贴上目标岗位描述"
              />
              <p className={styles.meta}>字数：{jdText.length}/{MAX_TEXT_LENGTH}</p>
            </div>

            <div className={styles.submitBar}>
              <button className={`${styles.button} ${styles.primaryCta}`} disabled={!canSubmit} onClick={onAnalyze}>
                {primaryCtaText}
              </button>
              <button className={styles.secondaryButton} disabled={loading} onClick={onReset}>清空</button>
            </div>
          </section>

          <div className={styles.resultColumn} aria-live="polite">
            {loading ? (
              <section className={`${styles.card} ${styles.statusCard}`} role="status" aria-live="polite" aria-busy="true">
                <p className={styles.loading}>正在分析，请稍候...</p>
                <div className={styles.statusSkeletonBlock}>
                  <div className={styles.skeletonLine} />
                  <div className={styles.skeletonLineShort} />
                </div>
              </section>
            ) : null}

            {!loading && error ? (
              <section className={`${styles.card} ${styles.statusCard}`} role="alert">
                <p className={styles.error}>{error}</p>
                <button className={styles.secondaryButton} disabled={!canSubmit} onClick={onAnalyze}>重试</button>
              </section>
            ) : null}

            {!loading && !error && !result ? (
              <section className={`${styles.card} ${styles.statusCard}`}>
                <p className={styles.emptyState}>{hasSubmitted ? "这次没出结果，改改内容再试一次。" : "填好简历和 JD 后，点“开始分析”。"}</p>
              </section>
            ) : null}

            {result ? (
              <section className={`${styles.card} ${styles.resultCard}`}>
                <div className={styles.historyHeader}>
                  <h2 className={styles.sectionTitle}>分析结果</h2>
                  <div className={styles.compactActions}>
                    <button className={styles.secondaryButton} onClick={() => void copyText(resultText, "分析结果")}>复制</button>
                    <button className={styles.secondaryButton} onClick={() => void onExportCurrent("txt")}>文本</button>
                    <button className={styles.secondaryButton} onClick={() => void onExportCurrent("json")}>数据</button>
                    <button className={styles.secondaryButton} onClick={() => void onExportCurrent("pdf")}>文档</button>
                  </div>
                </div>

                <div className={styles.resultFlow}>
                  <article className={`${styles.resultFlowCard} ${styles.scoreCard}`}>
                    <p className={styles.resultCardTitle}>总分</p>
                    <p className={styles.bigScore}>{result.score}<span>/100</span></p>
                    <p className={styles.meta}>匹配关键词 {result.matchedKeywords.length} 个，待补充 {result.missingKeywords.length} 个。</p>

                    <div className={styles.scoreRows}>
                      <div className={styles.scoreRow}><span>关键词匹配</span><strong>{resultBreakdown.keyword_match}</strong></div>
                      <div className={styles.progressTrack}><div className={styles.progressFill} style={{ width: `${resultBreakdown.keyword_match}%` }} /></div>
                      <div className={styles.scoreRow}><span>需求覆盖</span><strong>{resultBreakdown.coverage}</strong></div>
                      <div className={styles.progressTrack}><div className={styles.progressFill} style={{ width: `${resultBreakdown.coverage}%` }} /></div>
                      <div className={styles.scoreRow}><span>表达质量</span><strong>{resultBreakdown.writing_quality_stub}</strong></div>
                      <div className={styles.progressTrack}><div className={styles.progressFill} style={{ width: `${resultBreakdown.writing_quality_stub}%` }} /></div>
                    </div>
                  </article>

                  <article className={styles.resultFlowCard}>
                    <p className={styles.resultCardTitle}>亮点</p>
                    <p className={styles.meta}>{result.insights?.summary || "暂无整体结论"}</p>
                    <ul className={styles.list}>
                      {(result.insights?.strengths || []).length
                        ? (result.insights?.strengths || []).map((value) => <li key={`strength-${value}`}>{value}</li>)
                        : <li>暂无亮点</li>}
                    </ul>
                    <p className={styles.meta}>匹配关键词</p>
                    <Chips items={result.matchedKeywords.slice(0, 12)} />
                  </article>

                  <article className={styles.resultFlowCard}>
                    <p className={styles.resultCardTitle}>风险</p>
                    <ul className={styles.list}>
                      {(result.insights?.risks || []).length
                        ? (result.insights?.risks || []).map((value) => <li key={`risk-${value}`}>{value}</li>)
                        : <li>暂无明显风险</li>}
                    </ul>
                    <p className={styles.meta}>建议补充关键词</p>
                    <Chips items={result.missingKeywords.slice(0, 12)} />
                  </article>

                  <article className={styles.resultFlowCard}>
                    <p className={styles.resultCardTitle}>下一步行动</p>
                    <ol className={styles.list}>
                      {(result.pipAdvice?.length ? result.pipAdvice : result.suggestions.slice(0, 5)).map((item, index) => (
                        <li key={`action-${index}-${item}`}>{item}</li>
                      ))}
                    </ol>
                  </article>
                </div>

                <details className={styles.resultDetails}>
                  <summary className={styles.developerSummary}>查看完整分析明细</summary>

                  <h3 className={styles.heading}>重点改进方向</h3>
                  {result.defectCategories?.length ? (
                    <ul className={styles.list}>
                      {result.defectCategories.map((item, index) => (
                        <li key={`defect-${item.name}-${index}`}>
                          <strong>{item.name}</strong>
                          {typeof item.count === "number" ? ` (${item.count})` : ""}
                          {item.details?.length ? `：${item.details.join("、")}` : ""}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className={styles.empty}>暂无</p>
                  )}

                  <h3 className={styles.heading}>完整优化建议</h3>
                  <ul className={styles.list}>
                    {result.suggestions.map((item, index) => <li key={`all-suggestion-${index}-${item}`}>{item}</li>)}
                  </ul>

                  {!!result.ragHits?.length ? (
                    <>
                      <h3 className={styles.heading}>知识库参考（高级能力）</h3>
                      <ul className={styles.ragList}>
                        {result.ragHits.map((hit, index) => (
                          <li key={`rag-hit-${index}-${hit.title}`} className={styles.ragItem}>
                            <p className={styles.ragTitle}>{hit.title || `片段 #${index + 1}`}{typeof hit.score === "number" ? `（相关度：${hit.score.toFixed(3)}）` : ""}</p>
                            <p className={styles.ragSnippet}>{hit.snippet || "(无摘要)"}</p>
                            {hit.source ? <p className={styles.meta}>参考来源：{hit.source}</p> : null}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : null}

                  <h3 className={styles.heading}>优化版简历草稿</h3>
                  <textarea className={styles.textarea} rows={12} readOnly value={result.optimizedResume} />
                  <div className={styles.actions}>
                    <button className={styles.secondaryButton} onClick={() => void copyText(result.optimizedResume, "优化简历")}>复制优化简历</button>
                    <button className={styles.secondaryButton} onClick={onApplyOptimization}>应用到输入区</button>
                    <button className={styles.secondaryButton} onClick={onStartInterview}>用当前内容去面试</button>
                  </div>
                </details>
              </section>
            ) : null}
          </div>
        </section>

        <details className={`${styles.card} ${styles.foldCard}`} aria-label="流程信号">
          <summary className={styles.foldSummary}>面试信号与报告</summary>
          <div className={styles.foldContent}>
            <div className={styles.signalGrid}>
              <div className={styles.signalItem}>
                <p className={styles.compactLabel}>诊断信号</p>
                <p className={styles.meta}>{stepStates.step3Done ? "已完成诊断，可继续修改" : "待诊断"}</p>
              </div>
              <div className={styles.signalItem}>
                <p className={styles.compactLabel}>面试信号</p>
                <p className={styles.meta}>
                  {interviewSignal.hasInterviewFinished
                    ? "已完成面试并产出报告"
                    : interviewSignal.hasInterviewStarted
                      ? "面试进行中"
                      : "尚未开始面试"}
                </p>
              </div>
            </div>
            <div className={styles.actionsInline}>
              <button className={styles.secondaryButton} onClick={refreshInterviewSignal}>刷新信号</button>
              <Link
                className={styles.secondaryButton}
                href={interviewSignal.latestSessionId
                  ? `/interview/summary?sessionId=${encodeURIComponent(interviewSignal.latestSessionId)}&sessionKey=${encodeURIComponent(interviewSignal.latestSessionKey)}&resumeContextKey=${encodeURIComponent(resumeContextKey)}`
                  : `/interview/summary?resumeContextKey=${encodeURIComponent(resumeContextKey)}`}
              >
                查看面试报告
              </Link>
            </div>
          </div>
        </details>

        <details className={`${styles.card} ${styles.foldCard}`} aria-label="更多功能入口">
          <summary className={styles.foldSummary}>更多入口</summary>
          <div className={styles.foldContent}>
            <div className={styles.quickLinks}>
              <Link className={`${styles.inlineLinkChip} ${styles.primaryQuickLink}`} href="/resumes">去管理简历库</Link>
              <div className={styles.moreLinksBody}>
                <Link className={styles.inlineLinkChip} href={createInterviewHref()}>面试练习</Link>
                <Link className={styles.inlineLinkChip} href="/rag">知识库管理</Link>
              </div>
            </div>
          </div>
        </details>

        <details className={`${styles.card} ${styles.foldCard}`} aria-labelledby="history-section-title">
          <summary className={styles.foldSummary}>历史记录与导出</summary>
          <div className={styles.foldContent}>
            <div className={styles.historyHeader}>
              <h2 id="history-section-title" className={styles.sectionTitle}>最近分析记录</h2>
              <div className={styles.compactActions}>
                <label className={styles.compactLabel} htmlFor="history-limit">显示条数</label>
                <select id="history-limit" className={styles.select} value={historyLimit} onChange={(event) => setHistoryLimit(Number(event.target.value))} disabled={historyLoading}>
                  {HISTORY_LIMIT_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
                <button className={styles.secondaryButton} onClick={() => void loadHistory({ requestId: historyRequestIdFilter, limit: historyLimit })} disabled={historyLoading}>
                  {historyLoading ? "刷新中..." : "刷新"}
                </button>
              </div>
            </div>

            <p className={styles.meta}>当前显示 {historyItems.length} 条 / 共 {historyTotal} 条。</p>
            <p className={styles.srOnly} aria-live="polite">{historyLiveMessage}</p>
            {historyError ? <p className={styles.error}>{historyError}</p> : null}

            {historyLoading ? (
              <ul className={styles.historyList} aria-label="历史加载骨架">
                {Array.from({ length: 4 }).map((_, idx) => (
                  <li key={`skeleton-${idx}`} className={`${styles.historyItem} ${styles.skeletonItem}`}>
                    <div className={styles.skeletonLine} />
                    <div className={styles.skeletonLineShort} />
                  </li>
                ))}
              </ul>
            ) : !historyItems.length ? (
              <p className={styles.empty}>暂时还没有分析记录，先完成一次分析吧。</p>
            ) : (
              <ul className={styles.historyList}>
                {historyItems.map((item) => {
                  const isLatest = latestHistoryId === item.id;
                  const isExportingThis = exportingLabel.startsWith(`#${item.id} `);
                  const matchedPreview = item.keywordSummary.matched.slice(0, 4).join("、");
                  const missingPreview = item.keywordSummary.missing.slice(0, 4).join("、");

                  return (
                    <li key={item.id} className={`${styles.historyItem} ${isLatest ? styles.historyItemLatest : ""}`}>
                      <div className={styles.historyContent}>
                        <div className={styles.historyMeta}>
                          <span>{formatDateTime(item.createdAt)}</span>
                          <span>匹配度：{item.score}/100</span>
                          {isLatest ? <span className={styles.latestBadge}>最新一次</span> : null}
                        </div>
                        <p className={styles.historyReadable}>亮点关键词：{matchedPreview || "暂无"}；建议补充：{missingPreview || "暂无"}</p>
                        <p className={styles.historySecondary}>
                          分析方式：{item.analysisSource === "gemini" ? "智能增强" : "标准分析"}
                        </p>
                      </div>

                      <div className={styles.historyActions}>
                        <button type="button" className={styles.secondaryButton} onClick={() => {
                          setSelectedHistoryId(item.id);
                          if (!historyDetails[item.id]) void loadHistoryDetail(item.id);
                        }}>
                          查看详情
                        </button>
                        <button className={styles.secondaryButton} onClick={() => void onExportHistory(item.id, "txt")} disabled={isExportingThis}>文本</button>
                        <button className={styles.secondaryButton} onClick={() => void onExportHistory(item.id, "json")} disabled={isExportingThis}>数据</button>
                        <button className={styles.secondaryButton} onClick={() => void onExportHistory(item.id, "pdf")} disabled={isExportingThis}>文档</button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </details>

        <details className={`${styles.card} ${styles.developerCard}`}>
          <summary className={styles.developerSummary}>开发者设置（默认收起）</summary>

          <p className={styles.meta}>技术字段与调试能力统一放在这里，普通使用无需展开。</p>

          <div className={styles.toggleRow}>
            <label className={styles.toggleLabel} htmlFor="rag-enabled-toggle">
              <input id="rag-enabled-toggle" type="checkbox" checked={ragEnabled} onChange={(event) => setRagEnabled(event.target.checked)} />
              启用知识库增强
            </label>
            <p className={styles.meta}>开启后将尝试结合知识库检索结果生成建议；若后端未启用会自动回退。</p>
            {ragModeMessage ? <p className={styles.contextHint}>{ragModeMessage}</p> : null}

            <fieldset className={styles.retrievalFieldset} disabled={!ragEnabled}>
              <legend className={styles.compactLabel}>检索模式</legend>
              <label className={styles.toggleLabel} htmlFor="retrieval-mode-keyword">
                <input id="retrieval-mode-keyword" type="radio" name="retrieval-mode" value="keyword" checked={retrievalMode === "keyword"} onChange={() => setRetrievalMode("keyword")} />
                关键词检索
              </label>
              <label className={styles.toggleLabel} htmlFor="retrieval-mode-vector">
                <input id="retrieval-mode-vector" type="radio" name="retrieval-mode" value="vector" checked={retrievalMode === "vector"} onChange={() => setRetrievalMode("vector")} />
                向量检索
              </label>
            </fieldset>

            <div className={styles.inlineGrid}>
              <label className={styles.compactLabel} htmlFor="rag-topk-input">召回条数</label>
              <input id="rag-topk-input" className={styles.inputSmall} value={ragTopKInput} onChange={(event) => setRagTopKInput(event.target.value)} inputMode="numeric" disabled={!ragEnabled} />

              <label className={styles.compactLabel} htmlFor="rag-threshold-input">相似度阈值</label>
              <input id="rag-threshold-input" className={styles.inputSmall} value={ragThresholdInput} onChange={(event) => setRagThresholdInput(event.target.value)} inputMode="decimal" disabled={!ragEnabled} />
            </div>

            <p className={styles.meta}>仅在开启增强时生效：召回条数取值 1-20，相似度阈值取值 0-1。</p>
            {retrievalModeMessage ? <p className={styles.contextHint}>{retrievalModeMessage}</p> : null}
            {ragTopKMessage ? <p className={styles.contextHint}>{ragTopKMessage}</p> : null}
            {ragThresholdMessage ? <p className={styles.contextHint}>{ragThresholdMessage}</p> : null}
          </div>

          <div className={styles.developerDivider} />

          {!isAuthenticated ? <p className={styles.meta}>当前为访客模式。若后端开启强鉴权，接口可能返回 401，请先登录后再分析。</p> : null}
          {authFailureReason ? <p className={styles.contextHint}>登录状态异常，请重新登录。</p> : null}

          <p className={styles.meta}>用户：{authState?.userName || "访客"}；状态：{authStatusText}</p>
          <p className={styles.meta}>会话编号：{authState?.sessionId || "初始化中..."}</p>

          <label className={styles.label} htmlFor="access-token-input">访问令牌</label>
          <input id="access-token-input" className={styles.input} value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} placeholder="输入自定义访问令牌，留空则切换到访客模式" />

          <div className={styles.actions}>
            <button className={styles.secondaryButton} onClick={onApplyToken} disabled={!authReady}>保存令牌</button>
            <button className={styles.secondaryButton} onClick={onRotateSessionId} disabled={!authReady}>刷新会话</button>
            <button className={styles.secondaryButton} onClick={onResetAuth} disabled={!authReady}>重置本地鉴权</button>
          </div>
          {authMessage ? <p className={styles.contextHint}>{authMessage}</p> : null}

          <div className={styles.developerDivider} />

          <h3 className={styles.heading}>历史调试视图</h3>
          <div className={styles.filterRow}>
            <label htmlFor="history-request-id" className={styles.filterLabel}>按请求编号检索</label>
            <input id="history-request-id" className={styles.input} placeholder="输入请求编号" value={historyRequestIdFilter} onChange={(event) => setHistoryRequestIdFilter(event.target.value)} />
            <button className={styles.secondaryButton} onClick={() => void loadHistory({ requestId: historyRequestIdFilter, limit: historyLimit })} disabled={historyLoading}>检索</button>
            <button className={styles.secondaryButton} onClick={() => {
              setHistoryRequestIdFilter("");
              void loadHistory({ requestId: "", limit: historyLimit });
            }} disabled={historyLoading}>重置</button>
          </div>

          <div className={styles.cleanupRow}>
            <label htmlFor="keep-latest" className={styles.filterLabel}>保留最近数量</label>
            <input id="keep-latest" className={styles.inputSmall} value={keepLatestInput} onChange={(event) => setKeepLatestInput(event.target.value)} inputMode="numeric" />
            <button className={styles.secondaryButton} onClick={() => void onCleanupKeepLatest()} disabled={cleanupLoading}>{cleanupLoading ? "处理中..." : "执行清理"}</button>
            <button className={styles.warnButton} onClick={() => void onCleanupDeleteAll()} disabled={cleanupLoading}>清空全部</button>
          </div>
          {cleanupMessage ? <p className={styles.meta}>{cleanupMessage}</p> : null}
        </details>
      </main>

      {selectedHistoryId !== null ? (
        <div className={styles.drawerOverlay} onClick={() => setSelectedHistoryId(null)}>
          <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="history-detail-title" onClick={(event) => event.stopPropagation()}>
            <header className={styles.drawerHeader}>
              <div>
                <h3 id="history-detail-title" className={styles.sectionTitle}>分析详情 #{selectedHistoryId}</h3>
                <p className={styles.meta}>可复制与导出</p>
              </div>
              <button className={styles.secondaryButton} onClick={() => setSelectedHistoryId(null)}>关闭</button>
            </header>

            {detailLoadingId === selectedHistoryId ? (
              <div className={styles.drawerLoading}>
                <div className={styles.skeletonLine} />
                <div className={styles.skeletonLineShort} />
                <div className={styles.skeletonLine} />
              </div>
            ) : selectedHistoryDetail ? (
              <div className={styles.drawerBody}>
                <p className={styles.meta}>时间：{formatDateTime(selectedHistoryDetail.createdAt)}</p>
                <p className={styles.meta}>匹配度：{selectedHistoryDetail.score}/100</p>
                <p className={styles.meta}>分析方式：{selectedHistoryDetail.analysisSource === "gemini" ? "智能增强" : "标准分析"}</p>
                <details className={styles.inlineDeveloperBlock}>
                  <summary className={styles.inlineDeveloperSummary}>开发者信息（默认收起）</summary>
                  {selectedHistoryDetail.requestId ? <p className={styles.meta}>请求编号：{selectedHistoryDetail.requestId}</p> : <p className={styles.meta}>请求编号：-</p>}
                  {selectedHistoryDetail.sessionId ? <p className={styles.meta}>会话编号：{selectedHistoryDetail.sessionId}</p> : <p className={styles.meta}>会话编号：-</p>}
                </details>

                <h4 className={styles.heading}>匹配度拆解</h4>
                <ul className={styles.list}>
                  <li>关键词匹配度：{selectedHistoryDetail.scoreBreakdown.keyword_match}/100</li>
                  <li>岗位需求覆盖度：{selectedHistoryDetail.scoreBreakdown.coverage}/100</li>
                  <li>表达质量：{selectedHistoryDetail.scoreBreakdown.writing_quality_stub}/100</li>
                </ul>

                <h4 className={styles.heading}>亮点与风险提醒</h4>
                <p className={styles.meta}>{selectedHistoryDetail.insights.summary || "暂无"}</p>
                <ul className={styles.list}>
                  {selectedHistoryDetail.insights.strengths.map((value) => <li key={`detail-strength-${value}`}>亮点：{value}</li>)}
                  {selectedHistoryDetail.insights.risks.map((value) => <li key={`detail-risk-${value}`}>风险：{value}</li>)}
                </ul>

                <h4 className={styles.heading}>下一步优化行动</h4>
                {selectedHistoryDetail.pipAdvice.length ? (
                  <ol className={styles.list}>
                    {selectedHistoryDetail.pipAdvice.map((value, index) => <li key={`detail-pip-${index}-${value}`}>{value}</li>)}
                  </ol>
                ) : (
                  <p className={styles.empty}>暂无</p>
                )}

                <h4 className={styles.heading}>优化建议</h4>
                <ul className={styles.list}>
                  {selectedHistoryDetail.suggestions.map((value) => <li key={`detail-suggestion-${value}`}>{value}</li>)}
                </ul>

                <h4 className={styles.heading}>优化版简历草稿</h4>
                <textarea className={styles.textarea} rows={10} readOnly value={selectedHistoryDetail.optimizedResume} />

                <div className={styles.actions}>
                  <button className={styles.secondaryButton} onClick={() => void copyText(selectedHistoryDetail.optimizedResume, "历史优化简历")}>复制</button>
                  <button className={styles.secondaryButton} onClick={() => void onExportHistory(selectedHistoryDetail.id, "txt")}>文本</button>
                  <button className={styles.secondaryButton} onClick={() => void onExportHistory(selectedHistoryDetail.id, "json")}>数据</button>
                  <button className={styles.secondaryButton} onClick={() => void onExportHistory(selectedHistoryDetail.id, "pdf")}>文档</button>
                </div>
              </div>
            ) : (
              <div className={styles.drawerBody}>
                <p className={styles.error}>{detailError || "详情暂不可用，请稍后重试"}</p>
                <button className={styles.secondaryButton} onClick={() => void loadHistoryDetail(selectedHistoryId)}>重试加载</button>
              </div>
            )}
          </aside>
        </div>
      ) : null}

      <p className={styles.srOnly} aria-live="polite">{feedback?.text || ""}</p>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<div className={styles.page}><main className={styles.main}><p className={styles.meta}>页面加载中...</p></main></div>}>
      <HomeContent />
    </Suspense>
  );
}

function Chips({ items }: { items: string[] }) {
  if (!items.length) {
    return <p className={styles.empty}>无</p>;
  }

  return (
    <div className={styles.chips}>
      {items.map((item) => (
        <span key={item} className={styles.chip}>{item}</span>
      ))}
    </div>
  );
}


