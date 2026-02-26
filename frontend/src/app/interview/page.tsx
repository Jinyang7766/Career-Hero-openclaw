"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./page.module.css";
import { toErrorMessage, toUserFriendlyError } from "../page.utils";
import { buildLoginHref, useClientAuth } from "../client-auth";

type InterviewQuestion = {
  id: string;
  text: string;
  tips: string[];
  index?: number;
  category?: string;
  focus?: string;
};

type InterviewFeedback = {
  summary: string;
  strengths: string[];
  improvements: string[];
  score?: number;
};

type InterviewMode = "remote" | "local";
type InterviewStatus = "active" | "paused" | "finished";

type EndpointCandidate = {
  path: string;
  method: "GET" | "POST";
};

type InterviewSessionRecord = {
  key: string;
  sessionId: string;
  mode: InterviewMode;
  status: InterviewStatus;
  position: string;
  jdText: string;
  resumeText: string;
  resumeContextKey?: string;
  createdAt: string;
  updatedAt: string;
  questionCount: number;
  answeredCount: number;
  currentIndex: number;
  questions: InterviewQuestion[];
  answers: Record<string, string>;
  feedback: InterviewFeedback | null;
  remoteNumericId?: number;
};

type InterviewBootstrapPayload = {
  resumeContextKey: string;
  position?: string;
  jdText?: string;
  resumeText?: string;
  autoStart?: boolean;
  requestedAt?: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";
const SESSION_STORAGE_KEY = "career_hero.interview.sessions.v2";
const INTERVIEW_BOOTSTRAP_STORAGE_KEY = "career_hero.interview.bootstrap.v1";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toSafeString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function toSafeNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
}

function toDateText(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function toModeText(mode: InterviewMode): string {
  return mode === "remote" ? "在线练习" : "本地练习";
}

function toStatusText(status: InterviewStatus): string {
  if (status === "paused") return "已暂停";
  if (status === "finished") return "已完成";
  return "进行中";
}

function toAuthModeText(mode: string): string {
  const normalized = mode.trim().toLowerCase();
  if (normalized === "custom") return "自定义";
  if (normalized === "default") return "默认";
  if (normalized === "local") return "本地";
  if (/[a-z]/i.test(normalized)) return "其他";
  return mode;
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

function readInterviewBootstrap(contextKey: string): InterviewBootstrapPayload | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(INTERVIEW_BOOTSTRAP_STORAGE_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) return null;

    const payload: InterviewBootstrapPayload = {
      resumeContextKey: toSafeString(parsed.resumeContextKey),
      position: toSafeString(parsed.position),
      jdText: toSafeString(parsed.jdText),
      resumeText: toSafeString(parsed.resumeText),
      autoStart: Boolean(parsed.autoStart),
      requestedAt: toSafeString(parsed.requestedAt),
    };

    if (!payload.resumeContextKey || payload.resumeContextKey !== contextKey) return null;
    return payload;
  } catch {
    return null;
  }
}

function clearInterviewBootstrap() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(INTERVIEW_BOOTSTRAP_STORAGE_KEY);
  } catch {
    // ignore
  }
}

async function readJsonSafe(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return null;
  }
  return response.json().catch(() => null);
}

function toInterviewQuestion(value: unknown, fallbackId: string): InterviewQuestion | null {
  if (typeof value === "string") {
    const text = value.trim();
    if (!text) return null;
    return { id: fallbackId, text, tips: [] };
  }

  if (!isRecord(value)) return null;

  const text = toSafeString(value.text || value.question || value.questionText || value.prompt).trim();
  if (!text) return null;

  const id = toSafeString(value.id || value.questionId || value.qid || value.index, fallbackId).trim() || fallbackId;
  const tips = toStringArray(value.tips ?? value.hints ?? value.keyPoints);

  return {
    id,
    text,
    tips,
    index: toSafeNumber(value.index),
    category: toSafeString(value.category),
    focus: toSafeString(value.focus),
  };
}

function toQuestionList(payload: unknown): InterviewQuestion[] {
  if (!isRecord(payload)) return [];

  const listCandidates = [payload.questions, payload.questionList, payload.items, payload.bank];
  for (const raw of listCandidates) {
    if (!Array.isArray(raw)) continue;
    const mapped = raw
      .map((item, index) => toInterviewQuestion(item, `q-${index + 1}`))
      .filter((item): item is InterviewQuestion => item !== null);
    if (mapped.length) {
      return mapped;
    }
  }

  const single = toInterviewQuestion(payload.currentQuestion ?? payload.question ?? payload.nextQuestion, "q-1");
  return single ? [single] : [];
}

function toInterviewFeedback(payload: unknown): InterviewFeedback | null {
  if (!isRecord(payload)) return null;

  const maybeFeedback = isRecord(payload.feedback)
    ? payload.feedback
    : isRecord(payload.feedbackDraft)
      ? payload.feedbackDraft
      : payload;

  const summary = toSafeString(maybeFeedback.summary || maybeFeedback.overall || maybeFeedback.comment).trim();
  const strengths = toStringArray(maybeFeedback.strengths ?? maybeFeedback.highlights ?? maybeFeedback.goodPoints);
  const improvements = toStringArray(
    maybeFeedback.improvements ?? maybeFeedback.suggestions ?? maybeFeedback.gaps ?? maybeFeedback.improvementPlan,
  );
  const score = toSafeNumber(maybeFeedback.score ?? maybeFeedback.overallScore);

  if (!summary && !strengths.length && !improvements.length && score === undefined) {
    return null;
  }

  return {
    summary: summary || "本次练习已完成，继续打磨表达会更好。",
    strengths,
    improvements,
    score,
  };
}

function buildLocalQuestions(position: string): InterviewQuestion[] {
  const role = position.trim() || "目标岗位";
  return [
    {
      id: "q-1",
      text: `请你用 90 秒做一个与「${role}」相关的自我介绍。`,
      tips: ["突出与岗位最相关的经历", "给出 1-2 个量化结果"],
      category: "self_intro",
      focus: "岗位匹配",
    },
    {
      id: "q-2",
      text: "描述一个你解决复杂问题的案例：背景、动作、结果分别是什么？",
      tips: ["建议使用“情境-任务-行动-结果”结构", "结果尽量给出数字"],
      category: "project_depth",
      focus: "项目深挖",
    },
    {
      id: "q-3",
      text: "如果你加入团队，前 30 天会如何快速创造价值？",
      tips: ["拆分为学习、交付、协同三个维度"],
      category: "onboarding",
      focus: "落地计划",
    },
  ];
}

function buildLocalFeedback(answers: Record<string, string>, totalQuestions: number, position: string): InterviewFeedback {
  const answerList = Object.values(answers).filter((value) => value.trim().length > 0);
  const avgLength =
    answerList.length > 0
      ? Math.round(answerList.reduce((sum, item) => sum + item.trim().length, 0) / answerList.length)
      : 0;

  const score = Math.min(95, 45 + answerList.length * 12 + (avgLength >= 80 ? 12 : 0) + (avgLength >= 140 ? 6 : 0));

  const strengths: string[] = [];
  if (answerList.length >= Math.min(3, totalQuestions)) strengths.push("回答完整度较好，覆盖了主要问题");
  if (avgLength >= 80) strengths.push("表述细节较充分，具备一定说服力");

  const improvements: string[] = [];
  if (avgLength < 80) improvements.push("答案偏短，建议补充具体背景与结果数据");
  improvements.push("每个回答都可补充“你做了什么、带来什么变化”来增强说服力");

  return {
    summary: `已完成 ${position.trim() || "目标岗位"} 的模拟面试，建议继续打磨高频问题表达。`,
    strengths,
    improvements,
    score,
  };
}

function extractSessionId(payload: unknown): string {
  if (!isRecord(payload)) return "";

  const direct = toSafeString(payload.sessionId || payload.session_id || payload.id);
  if (direct) return direct;

  const session = isRecord(payload.session) ? payload.session : null;
  if (session) {
    const nested = toSafeString(session.id || session.sessionId || session.session_id || session.sessionToken);
    if (nested) return nested;
  }

  return "";
}

function normalizeStatus(value: unknown): InterviewStatus {
  const raw = toSafeString(value).toLowerCase();
  if (raw === "paused") return "paused";
  if (raw === "finished" || raw === "completed" || raw === "done") return "finished";
  return "active";
}

function normalizeRemoteSession(value: unknown): InterviewSessionRecord | null {
  if (!isRecord(value)) return null;

  const id = toSafeNumber(value.id);
  const sessionToken = toSafeString(value.sessionToken || value.session_id || value.sessionId).trim();
  if (!id || !sessionToken) return null;

  const createdAt = toSafeString(value.createdAt) || new Date().toISOString();
  const updatedAt = toSafeString(value.updatedAt) || createdAt;

  return {
    key: `remote-history-${id}`,
    sessionId: sessionToken,
    mode: "remote",
    status: normalizeStatus(value.status),
    position: "",
    jdText: "",
    resumeText: "",
    resumeContextKey: toSafeString(value.resumeContextKey),
    createdAt,
    updatedAt,
    questionCount: toSafeNumber(value.questionCount) ?? 0,
    answeredCount: toSafeNumber(value.answeredCount) ?? 0,
    currentIndex: Math.max(0, toSafeNumber(value.currentIndex) ?? 0),
    questions: [],
    answers: {},
    feedback: null,
    remoteNumericId: id,
  };
}

function normalizeRecord(value: unknown): InterviewSessionRecord | null {
  if (!isRecord(value)) return null;

  const key = toSafeString(value.key);
  const sessionId = toSafeString(value.sessionId);
  if (!key || !sessionId) return null;

  const modeRaw = toSafeString(value.mode).toLowerCase();
  const mode: InterviewMode = modeRaw === "remote" ? "remote" : "local";

  const rawQuestions = Array.isArray(value.questions) ? value.questions : [];
  const questions = rawQuestions
    .map((item, index) => toInterviewQuestion(item, `q-${index + 1}`))
    .filter((item): item is InterviewQuestion => item !== null);

  const answersRaw = isRecord(value.answers) ? value.answers : {};
  const answers = Object.entries(answersRaw).reduce<Record<string, string>>((acc, [k, v]) => {
    if (typeof v === "string") acc[k] = v;
    return acc;
  }, {});

  const feedback = toInterviewFeedback(value.feedback);

  return {
    key,
    sessionId,
    mode,
    status: normalizeStatus(value.status),
    position: toSafeString(value.position),
    jdText: toSafeString(value.jdText),
    resumeText: toSafeString(value.resumeText),
    resumeContextKey: toSafeString(value.resumeContextKey),
    createdAt: toSafeString(value.createdAt),
    updatedAt: toSafeString(value.updatedAt),
    questionCount: toSafeNumber(value.questionCount) ?? questions.length,
    answeredCount: toSafeNumber(value.answeredCount) ?? Object.keys(answers).length,
    currentIndex: toSafeNumber(value.currentIndex) ?? 0,
    questions,
    answers,
    feedback,
    remoteNumericId: toSafeNumber(value.remoteNumericId),
  };
}

function loadSessionRecords(): InterviewSessionRecord[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];

    return parsed
      .map((item) => normalizeRecord(item))
      .filter((item): item is InterviewSessionRecord => item !== null)
      .slice(0, 50);
  } catch {
    return [];
  }
}

function saveSessionRecords(records: InterviewSessionRecord[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(records.slice(0, 50)));
  } catch {
    // ignore
  }
}

function upsertRecord(list: InterviewSessionRecord[], next: InterviewSessionRecord): InterviewSessionRecord[] {
  const exists = list.findIndex(
    (item) =>
      item.key === next.key ||
      item.sessionId === next.sessionId ||
      (typeof item.remoteNumericId === "number" && item.remoteNumericId === next.remoteNumericId),
  );

  if (exists < 0) {
    return [next, ...list].slice(0, 50);
  }

  const current = list[exists];
  const merged: InterviewSessionRecord = {
    ...current,
    ...next,
    key: current.key,
    questions: next.questions.length ? next.questions : current.questions,
    answers: { ...current.answers, ...next.answers },
    feedback: next.feedback ?? current.feedback,
  };

  const copied = [...list];
  copied[exists] = merged;
  return copied;
}

async function callInterviewApi(
  fetcher: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
  candidates: EndpointCandidate[],
  payload?: Record<string, unknown>,
): Promise<unknown> {
  let lastError: Error | null = null;

  for (const candidate of candidates) {
    try {
      const response = await fetcher(`${API_BASE_URL}${candidate.path}`, {
        method: candidate.method,
        headers: candidate.method === "POST" ? { "Content-Type": "application/json" } : undefined,
        body: candidate.method === "POST" ? JSON.stringify(payload ?? {}) : undefined,
      });

      const data = await readJsonSafe(response);
      if (response.ok) return data;

      if (response.status === 404 || response.status === 405) continue;

      const message = toErrorMessage(data);
      lastError = new Error(message === "分析失败，请稍后重试" ? "请求失败，请稍后重试" : message);
    } catch (err) {
      lastError = new Error(toUserFriendlyError(err, "网络开小差了，请稍后再试"));
    }
  }

  throw lastError ?? new Error("面试服务暂时不可用，请稍后再试");
}

function InterviewContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const querySessionKey = (searchParams.get("sessionKey") || "").trim();
  const querySessionId = (searchParams.get("sessionId") || "").trim();
  const queryResumeContextKey = (searchParams.get("resumeContextKey") || "").trim();
  const queryResumeId = (searchParams.get("resumeId") || "").trim();
  const queryVersionNo = (searchParams.get("versionNo") || "").trim();
  const resumeContextKey = queryResumeContextKey || (queryResumeId ? `resume-${queryResumeId}-v${queryVersionNo || "0"}` : "manual");

  const {
    authState,
    authReady,
    isAuthenticated,
    authStatusText,
    authFailureReason,
    tokenDraft,
    setTokenDraft,
    applyAccessToken,
    rotateSession,
    resetAuthState,
    logout,
    apiFetch,
  } = useClientAuth(API_BASE_URL, { autoRedirectOnUnauthorized: true });

  const fetch = useCallback((input: RequestInfo | URL, init?: RequestInit) => apiFetch(input, init), [apiFetch]);

  const [position, setPosition] = useState("");
  const [jdText, setJdText] = useState("");
  const [resumeText, setResumeText] = useState("");

  const [records, setRecords] = useState<InterviewSessionRecord[]>([]);
  const [selectedRecordKey, setSelectedRecordKey] = useState("");
  const [currentSession, setCurrentSession] = useState<InterviewSessionRecord | null>(null);

  const [answerInput, setAnswerInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [authMessage, setAuthMessage] = useState("");

  const querySelectionRef = useRef("");

  const visibleRecords = useMemo(
    () => records.filter((item) => (item.resumeContextKey || "manual") === resumeContextKey),
    [records, resumeContextKey],
  );

  const selectedRecord = useMemo(() => visibleRecords.find((item) => item.key === selectedRecordKey) ?? null, [selectedRecordKey, visibleRecords]);

  const loginHref = useMemo(() => {
    const returnTo = searchParams.toString() ? `/interview?${searchParams.toString()}` : "/interview";
    return buildLoginHref(returnTo, authFailureReason ?? undefined);
  }, [authFailureReason, searchParams]);
  const canAttemptRefresh = authState?.mode === "custom" && Boolean(authState.refreshToken);

  const currentQuestion = useMemo(() => {
    if (!currentSession) return null;
    return currentSession.questions[currentSession.currentIndex] ?? null;
  }, [currentSession]);

  const canAnswer = Boolean(currentSession && currentQuestion && currentSession.status === "active");

  const step = useMemo(() => {
    if (!currentSession) return 1;
    if (currentSession.status === "finished" || currentSession.feedback) return 3;
    return 2;
  }, [currentSession]);

  const progressText = useMemo(() => {
    if (!currentSession) return "准备开始";
    const total = Math.max(currentSession.questions.length, currentSession.questionCount, 1);
    const done = Object.keys(currentSession.answers).filter((key) => currentSession.answers[key]?.trim()).length;
    return `已回答 ${done} / ${total}`;
  }, [currentSession]);

  const sessionInProgress = Boolean(currentSession && currentSession.status !== "finished");
  const lockInterviewContextFields = sessionInProgress;

  useEffect(() => {
    const initial = loadSessionRecords();
    const scoped = initial.filter((item) => (item.resumeContextKey || "manual") === resumeContextKey);
    const list = resumeContextKey === "manual" ? (scoped.length ? scoped : initial) : scoped;
    setRecords(list);
    if (list[0]) setSelectedRecordKey(list[0].key);

    const bootstrap = readInterviewBootstrap(resumeContextKey);
    if (!bootstrap) return;

    if (bootstrap.position) setPosition(bootstrap.position);
    if (bootstrap.jdText) setJdText(bootstrap.jdText);
    if (bootstrap.resumeText) setResumeText(bootstrap.resumeText);

    if (bootstrap.autoStart) {
      setNotice("已接收诊断页上下文，正在进入面试练习...");
      window.setTimeout(() => {
        void startSession({
          position: bootstrap.position,
          jdText: bootstrap.jdText,
          resumeText: bootstrap.resumeText,
          silent: true,
        });
      }, 0);
    }

    clearInterviewBootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeContextKey]);

  useEffect(() => {
    if (!authReady || isAuthenticated || canAttemptRefresh) return;
    router.replace(loginHref);
  }, [authReady, canAttemptRefresh, isAuthenticated, loginHref, router]);

  useEffect(() => {
    const queryToken = `${querySessionId}|${querySessionKey}`;
    if (!visibleRecords.length) return;
    if (querySelectionRef.current === queryToken) return;

    let matched: InterviewSessionRecord | null = null;
    if (querySessionKey) {
      matched = visibleRecords.find((item) => item.key === querySessionKey) ?? null;
    }

    if (!matched && querySessionId) {
      matched =
        visibleRecords
          .filter((item) => item.sessionId === querySessionId)
          .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())[0] ?? null;
    }

    querySelectionRef.current = queryToken;
    if (!matched) return;

    setSelectedRecordKey(matched.key);
    setCurrentSession(matched);
    setNotice("已恢复你上次的练习记录。");
  }, [querySessionId, querySessionKey, visibleRecords]);

  useEffect(() => {
    if (!authReady) return;
    void syncRemoteSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authReady, authState?.sessionId, resumeContextKey]);

  useEffect(() => {
    saveSessionRecords(records);
  }, [records]);

  useEffect(() => {
    if (!currentSession) return;
    setRecords((prev) => upsertRecord(prev, currentSession));
  }, [currentSession]);

  function applySessionPatch(updater: (current: InterviewSessionRecord) => InterviewSessionRecord) {
    setCurrentSession((prev) => {
      if (!prev) return prev;
      const next = updater(prev);
      return {
        ...next,
        updatedAt: new Date().toISOString(),
        questionCount: Math.max(next.questionCount, next.questions.length),
        answeredCount: Object.keys(next.answers).length,
      };
    });
  }

  async function syncRemoteSessions() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/interview/sessions?limit=20`);
      const data = await readJsonSafe(response);
      if (!response.ok) return;

      const payload = isRecord(data) ? data : {};
      const remoteItems = Array.isArray(payload.items)
        ? payload.items.map((item) => normalizeRemoteSession(item)).filter((item): item is InterviewSessionRecord => item !== null)
        : [];

      const scopedRemoteItems = remoteItems.filter((record) => {
        if (!record.resumeContextKey) return resumeContextKey === "manual";
        return record.resumeContextKey === resumeContextKey;
      });

      if (!scopedRemoteItems.length) return;

      setRecords((prev) => {
        const merged = [...prev];
        scopedRemoteItems.forEach((record) => {
          const existsIndex = merged.findIndex(
            (item) => item.remoteNumericId === record.remoteNumericId || item.sessionId === record.sessionId,
          );

          if (existsIndex >= 0) {
            merged[existsIndex] = {
              ...merged[existsIndex],
              ...record,
              key: merged[existsIndex].key,
              resumeContextKey: merged[existsIndex].resumeContextKey || record.resumeContextKey || resumeContextKey,
              questions: merged[existsIndex].questions.length ? merged[existsIndex].questions : record.questions,
              feedback: merged[existsIndex].feedback ?? record.feedback,
            };
          } else {
            merged.push(record);
          }
        });
        return merged.slice(0, 50);
      });

      setNotice("已更新练习历史。");
    } catch {
      // ignore
    }
  }

  async function hydrateRemoteRecord(record: InterviewSessionRecord): Promise<InterviewSessionRecord> {
    if (!record.remoteNumericId) return record;

    const response = await fetch(`${API_BASE_URL}/api/interview/sessions/${record.remoteNumericId}`);
    const data = await readJsonSafe(response);
    if (!response.ok) return record;

    const payload = isRecord(data) ? data : {};
    const session = isRecord(payload.session) ? payload.session : {};
    const nextQuestion = toInterviewQuestion(payload.nextQuestion, `q-${record.currentIndex + 1}`);
    const feedback = toInterviewFeedback(payload.feedbackDraft);

    return {
      ...record,
      status: normalizeStatus(session.status || record.status),
      questionCount: toSafeNumber(session.questionCount) ?? record.questionCount,
      answeredCount: toSafeNumber(session.answeredCount) ?? record.answeredCount,
      currentIndex: Math.max(0, toSafeNumber(session.currentIndex) ?? record.currentIndex),
      updatedAt: toSafeString(session.updatedAt) || record.updatedAt,
      createdAt: toSafeString(session.createdAt) || record.createdAt,
      questions: nextQuestion && !record.questions.length ? [nextQuestion] : record.questions,
      feedback: feedback ?? record.feedback,
    };
  }

  function onApplyToken() {
    const next = applyAccessToken(tokenDraft.trim());
    setAuthMessage(`开发者设置已更新（模式：${toAuthModeText(next.mode)}）`);
  }

  function onRotateSession() {
    const next = rotateSession();
    setAuthMessage(`开发者设置已刷新会话：${next.sessionId}`);
  }

  function onResetAuth() {
    const next = resetAuthState();
    setTokenDraft(next.accessToken);
    setAuthMessage("已恢复默认登录状态。");
  }

  function onLogout() {
    const next = logout();
    setTokenDraft(next.accessToken);
    setAuthMessage("你已退出登录。");
  }

  async function startSession(options?: { position?: string; jdText?: string; resumeText?: string; silent?: boolean }) {
    setLoading(true);
    setError("");
    if (!options?.silent) {
      setNotice("");
    }
    setAnswerInput("");

    const positionValue = (options?.position ?? position).trim();
    const jdValue = (options?.jdText ?? jdText).trim();
    const resumeValue = (options?.resumeText ?? resumeText).trim();

    const jd = jdValue || positionValue;
    if (!jd) {
      setLoading(false);
      setError("请先填写岗位方向或岗位描述。");
      return;
    }

    if (options?.position !== undefined) setPosition(positionValue);
    if (options?.jdText !== undefined) setJdText(jdValue);
    if (options?.resumeText !== undefined) setResumeText(resumeValue);

    const now = new Date().toISOString();

    try {
      const payload = {
        jdText: jd,
        ...(resumeValue ? { resumeText: resumeValue } : {}),
        questionCount: 5,
        resumeContextKey,
      };

      const data = await callInterviewApi(
        fetch,
        [
          { path: "/api/interview/session/create", method: "POST" },
          { path: "/api/interview/session/start", method: "POST" },
          { path: "/api/interview/start", method: "POST" },
          { path: "/api/interview/session", method: "POST" },
        ],
        payload,
      );

      const body = isRecord(data) ? data : {};
      const sessionPayload = isRecord(body.session) ? body.session : body;
      const remoteSessionId = extractSessionId(body) || extractSessionId(sessionPayload);
      if (!remoteSessionId) throw new Error("面试服务返回异常");

      const remoteQuestions = toQuestionList(body);
      const questionList = remoteQuestions.length ? remoteQuestions : buildLocalQuestions(positionValue || inferPositionFromJd(jd));
      const status = normalizeStatus(isRecord(sessionPayload) ? sessionPayload.status : "active");
      const remoteNumericId = Number.parseInt(remoteSessionId, 10);

      const nextRecord: InterviewSessionRecord = {
        key: `remote-${remoteSessionId}-${Date.now()}`,
        sessionId: remoteSessionId,
        mode: "remote",
        status,
        position: positionValue || inferPositionFromJd(jd),
        jdText: jd,
        resumeText: resumeValue,
        resumeContextKey,
        createdAt: now,
        updatedAt: now,
        questionCount: toSafeNumber(sessionPayload.questionCount) ?? questionList.length,
        answeredCount: toSafeNumber(sessionPayload.answeredCount) ?? 0,
        currentIndex: Math.max(0, toSafeNumber(sessionPayload.currentIndex) ?? 0),
        questions: questionList,
        answers: {},
        feedback: null,
        remoteNumericId: Number.isFinite(remoteNumericId) ? remoteNumericId : undefined,
      };

      setCurrentSession(nextRecord);
      setSelectedRecordKey(nextRecord.key);
      setNotice("已进入面试练习页，可直接开始作答。");
      return;
    } catch {
      const localQuestions = buildLocalQuestions(positionValue || inferPositionFromJd(jd));
      const localId = `local-${Date.now()}`;
      const fallbackRecord: InterviewSessionRecord = {
        key: localId,
        sessionId: localId,
        mode: "local",
        status: "active",
        position: positionValue || inferPositionFromJd(jd),
        jdText: jd,
        resumeText: resumeValue,
        resumeContextKey,
        createdAt: now,
        updatedAt: now,
        questionCount: localQuestions.length,
        answeredCount: 0,
        currentIndex: 0,
        questions: localQuestions,
        answers: {},
        feedback: null,
      };

      setCurrentSession(fallbackRecord);
      setSelectedRecordKey(fallbackRecord.key);
      setNotice("在线恢复失败，已自动降级到本地练习模式。你现在可以继续完整流程。");
    } finally {
      setLoading(false);
    }
  }

  async function submitAnswer() {
    if (!currentSession || !currentQuestion) return;

    if (currentSession.status !== "active") {
      setError("当前练习已暂停，请先继续后再提交回答。");
      return;
    }

    const trimmed = answerInput.trim();
    if (!trimmed) {
      setError("请先输入你的回答。");
      return;
    }

    setError("");
    setAnswerInput("");

    applySessionPatch((prev) => ({
      ...prev,
      answers: {
        ...prev.answers,
        [currentQuestion.id]: trimmed,
      },
    }));

    if (currentSession.mode !== "remote") {
      setNotice("回答已保存，继续下一题即可。");
      return;
    }

    try {
      const data = await callInterviewApi(
        fetch,
        [
          { path: `/api/interview/session/${currentSession.sessionId}/answer`, method: "POST" },
          { path: `/api/interview/${currentSession.sessionId}/answer`, method: "POST" },
        ],
        {
          sessionId: currentSession.sessionId,
          questionId: currentQuestion.id,
          questionIndex: currentSession.currentIndex,
          answerText: trimmed,
          answer: trimmed,
        },
      );

      const body = isRecord(data) ? data : {};
      const sessionPayload = isRecord(body.session) ? body.session : {};
      const maybeNext = toInterviewQuestion(body.nextQuestion ?? body.question ?? body.currentQuestion, `q-${Date.now()}`);
      const remoteIndex = toSafeNumber(sessionPayload.currentIndex);
      const remoteStatus = normalizeStatus(sessionPayload.status);

      applySessionPatch((prev) => {
        let questions = prev.questions;
        if (maybeNext && !questions.some((item) => item.id === maybeNext.id)) {
          questions = [...questions, maybeNext];
        }

        let nextIndex = prev.currentIndex;
        if (typeof remoteIndex === "number") {
          nextIndex = Math.max(0, remoteIndex);
        } else if (maybeNext) {
          const idx = questions.findIndex((item) => item.id === maybeNext.id);
          if (idx >= 0) nextIndex = idx;
        }

        return {
          ...prev,
          status: remoteStatus,
          currentIndex: Math.min(nextIndex, Math.max(questions.length - 1, 0)),
          questions,
        };
      });

      setNotice("回答已提交，继续保持这个节奏。");
    } catch {
      applySessionPatch((prev) => ({ ...prev, mode: "local" }));
      setNotice("网络波动，已切换本地模式继续保存你的练习。");
    }
  }

  async function nextQuestion() {
    if (!currentSession) return;

    if (currentSession.status !== "active") {
      setError("当前练习已暂停，请先继续后再切题。");
      return;
    }

    setError("");

    if (currentSession.mode === "remote") {
      try {
        const data = await callInterviewApi(
          fetch,
          [
            { path: `/api/interview/session/${currentSession.sessionId}/next`, method: "POST" },
            { path: `/api/interview/${currentSession.sessionId}/next`, method: "POST" },
          ],
          { sessionId: currentSession.sessionId },
        );

        const body = isRecord(data) ? data : {};
        const sessionPayload = isRecord(body.session) ? body.session : {};
        const maybeNext = toInterviewQuestion(body.nextQuestion ?? body.question ?? body.currentQuestion, `q-${Date.now()}`);
        const remoteIndex = toSafeNumber(sessionPayload.currentIndex);

        applySessionPatch((prev) => {
          let questions = prev.questions;
          if (maybeNext && !questions.some((item) => item.id === maybeNext.id)) {
            questions = [...questions, maybeNext];
          }

          let nextIndex = prev.currentIndex;
          if (typeof remoteIndex === "number") {
            nextIndex = Math.max(0, remoteIndex);
          } else if (maybeNext) {
            const idx = questions.findIndex((item) => item.id === maybeNext.id);
            if (idx >= 0) nextIndex = idx;
          }

          return {
            ...prev,
            currentIndex: Math.min(nextIndex, Math.max(questions.length - 1, 0)),
            status: normalizeStatus(sessionPayload.status || prev.status),
            questions,
          };
        });

        setNotice("已进入下一题。");
        return;
      } catch {
        applySessionPatch((prev) => ({ ...prev, mode: "local" }));
        setNotice("网络波动，已切换本地模式继续练习。");
      }
    }

    applySessionPatch((prev) => {
      if (prev.currentIndex >= prev.questions.length - 1) {
        return prev;
      }
      return { ...prev, currentIndex: prev.currentIndex + 1 };
    });

    if (currentSession.currentIndex >= currentSession.questions.length - 1) {
      setNotice("已是最后一题，完成后可直接生成总结。");
    } else {
      setNotice("已进入下一题。");
    }
  }

  async function pauseSession() {
    if (!currentSession || currentSession.status !== "active") return;

    if (currentSession.mode === "remote") {
      try {
        await callInterviewApi(
          fetch,
          [
            { path: `/api/interview/session/${currentSession.sessionId}/pause`, method: "POST" },
            { path: `/api/interview/${currentSession.sessionId}/pause`, method: "POST" },
          ],
          { sessionId: currentSession.sessionId },
        );
      } catch {
        // ignore
      }
    }

    applySessionPatch((prev) => ({ ...prev, status: "paused" }));
    setNotice("练习已暂停，你可以随时继续。");
  }

  async function resumeSession() {
    if (!currentSession || currentSession.status !== "paused") return;

    if (currentSession.mode === "remote") {
      try {
        await callInterviewApi(
          fetch,
          [
            { path: `/api/interview/session/${currentSession.sessionId}/resume`, method: "POST" },
            { path: `/api/interview/${currentSession.sessionId}/resume`, method: "POST" },
          ],
          { sessionId: currentSession.sessionId },
        );
      } catch {
        // ignore
      }
    }

    applySessionPatch((prev) => ({ ...prev, status: "active" }));
    setNotice("已继续练习，加油！");
  }

  async function endSession() {
    if (!currentSession) return;

    setError("");

    if (currentSession.mode === "remote") {
      try {
        const data = await callInterviewApi(
          fetch,
          [
            { path: `/api/interview/session/${currentSession.sessionId}/finish`, method: "POST" },
            { path: `/api/interview/${currentSession.sessionId}/end`, method: "POST" },
            { path: `/api/interview/session/${currentSession.sessionId}/feedback`, method: "GET" },
          ],
          { sessionId: currentSession.sessionId },
        );

        const parsed = toInterviewFeedback(data);

        applySessionPatch((prev) => ({
          ...prev,
          status: "finished",
          feedback: parsed ?? buildLocalFeedback(prev.answers, prev.questions.length, prev.position),
        }));

        setNotice(parsed ? "练习完成！已生成总结。" : "练习完成！已使用本地规则生成总结。");
        return;
      } catch {
        applySessionPatch((prev) => ({ ...prev, mode: "local" }));
        setNotice("网络波动，已改用本地规则生成总结。");
      }
    }

    applySessionPatch((prev) => ({
      ...prev,
      status: "finished",
      feedback: buildLocalFeedback(prev.answers, prev.questions.length, prev.position),
    }));
    setNotice("练习完成！已生成总结。");
  }

  async function onLoadRecord(record: InterviewSessionRecord) {
    try {
      const hydrated = record.mode === "remote" ? await hydrateRemoteRecord(record) : record;

      setCurrentSession({ ...hydrated });
      setSelectedRecordKey(record.key);
      setAnswerInput("");
      setError("");

      setRecords((prev) => {
        const next = [...prev];
        const index = next.findIndex((item) => item.key === record.key);
        if (index >= 0) next[index] = hydrated;
        return next;
      });

      setNotice("已载入该练习记录。");
    } catch {
      setError("加载记录失败，请稍后重试。");
    }
  }

  function resetCurrentSession() {
    setCurrentSession(null);
    setAnswerInput("");
    setError("");
    setNotice("");
  }

  function buildSummaryText(session: InterviewSessionRecord): string {
    const feedback = session.feedback;
    const total = Math.max(session.questionCount, session.questions.length, 1);
    const lines = [
      `岗位方向：${session.position || "未填写"}`,
      `练习方式：${toModeText(session.mode)}`,
      `练习进度：${session.answeredCount}/${total}`,
      feedback?.summary ? `总结：${feedback.summary}` : "总结：暂无",
      "",
      "亮点：",
      ...(feedback?.strengths.length ? feedback.strengths.map((item) => `- ${item}`) : ["- 暂无"]),
      "",
      "改进建议：",
      ...(feedback?.improvements.length ? feedback.improvements.map((item) => `- ${item}`) : ["- 暂无"]),
    ];

    if (typeof feedback?.score === "number") {
      lines.splice(3, 0, `建议得分：${feedback.score}/100`);
    }

    return lines.join("\n");
  }

  async function copyFinalSummary(session: InterviewSessionRecord) {
    if (!session.feedback) {
      setError("当前练习尚未生成总结。");
      return;
    }

    try {
      await navigator.clipboard.writeText(buildSummaryText(session));
      setNotice("总结已复制到剪贴板。");
    } catch {
      setError("复制失败，请检查浏览器剪贴板权限。");
    }
  }

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <header className={styles.headerCard}>
          <p className={styles.badge}>主流程：开始练习 → 回答问题 → 生成总结</p>
          <h1 className={styles.title}>面试练习</h1>
          <p className={styles.subtitle}>聚焦一次完整练习流程，其他操作都放在次级区域，避免分心。</p>
          <div className={styles.headerActions}>
            <Link href="/" className={styles.linkChip}>返回岗位分析</Link>
            <Link href={`/interview/summary?resumeContextKey=${encodeURIComponent(resumeContextKey)}`} className={styles.linkChip}>查看历史总结</Link>
          </div>
        </header>

        {error ? <p className={styles.error}>{error}</p> : null}
        {notice ? <p className={styles.notice}>{notice}</p> : null}

        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>当前进度</h2>
          <ol className={styles.stepRow}>
            <li className={step >= 1 ? styles.stepActive : ""}>1. 开始练习</li>
            <li className={step >= 2 ? styles.stepActive : ""}>2. 回答问题</li>
            <li className={step >= 3 ? styles.stepActive : ""}>3. 查看总结</li>
          </ol>
          <p className={styles.meta}>{progressText}</p>

          <details className={styles.developerPanel}>
            <summary className={styles.summary}>开发者设置（默认收起）</summary>
            <p className={styles.meta}>会话：{authState?.sessionId || "初始化中..."}</p>
            <p className={styles.meta}>用户：{authState?.userName || "访客"} · 状态：{authStatusText}</p>
            <label htmlFor="interview-token-input" className={styles.label}>访问令牌</label>
            <textarea
              id="interview-token-input"
              className={styles.textarea}
              rows={2}
              value={tokenDraft}
              onChange={(event) => setTokenDraft(event.target.value)}
              placeholder="输入自定义令牌"
            />
            <div className={styles.actions}>
              <button className={styles.secondaryButton} onClick={onApplyToken} disabled={!authReady}>保存令牌</button>
              <button className={styles.secondaryButton} onClick={onRotateSession} disabled={!authReady}>刷新会话</button>
              <button className={styles.secondaryButton} onClick={onResetAuth} disabled={!authReady}>重置</button>
              <button className={styles.warnButton} onClick={onLogout} disabled={!authReady}>退出登录</button>
            </div>
            {authMessage ? <p className={styles.notice}>{authMessage}</p> : null}
          </details>
        </section>

        {!currentSession ? (
          <section className={styles.card}>
            <h2 className={styles.sectionTitle}>开始练习</h2>
            <p className={styles.meta}>填写岗位信息后点击主按钮，即可进入答题状态。</p>

            <label htmlFor="position-input" className={styles.label}>目标岗位（可选）</label>
            <textarea
              id="position-input"
              className={styles.textarea}
              rows={2}
              value={position}
              onChange={(event) => setPosition(event.target.value)}
              placeholder="例如：后端工程师 / 数据分析师 / 产品经理"
            />

            <label htmlFor="jd-input" className={styles.label}>岗位描述（建议填写）</label>
            <textarea
              id="jd-input"
              className={styles.textarea}
              rows={4}
              value={jdText}
              onChange={(event) => setJdText(event.target.value)}
              placeholder="粘贴岗位描述，系统会根据内容生成更贴近岗位的问题"
            />

            <label htmlFor="resume-input" className={styles.label}>你的经历摘要（可选）</label>
            <textarea
              id="resume-input"
              className={styles.textarea}
              rows={4}
              value={resumeText}
              onChange={(event) => setResumeText(event.target.value)}
              placeholder="可输入简历摘要，让问题更贴近你的背景"
            />

            <div className={styles.actions}>
              <button className={styles.primaryButton} onClick={() => void startSession()} disabled={loading}>
                {loading ? "准备中..." : "开始练习"}
              </button>
            </div>

            <details className={styles.secondaryPanel}>
              <summary className={styles.summary}>次要操作（可选）</summary>
              <div className={styles.actions}>
                <button className={styles.secondaryButton} onClick={resetCurrentSession}>清空当前练习</button>
                <button className={styles.secondaryButton} onClick={() => void syncRemoteSessions()}>刷新历史记录</button>
                <Link className={styles.secondaryButton} href={loginHref}>账号与登录</Link>
              </div>
            </details>
          </section>
        ) : (
          <section className={styles.card}>
            <h2 className={styles.sectionTitle}>回答问题</h2>
            <p className={styles.meta}>当前状态：{toStatusText(currentSession.status)} · {toModeText(currentSession.mode)}</p>
            <p className={styles.meta}>
              当前进度：{Math.min(currentSession.currentIndex + 1, Math.max(currentSession.questions.length, 1))}/
              {Math.max(currentSession.questions.length, 1)}
            </p>

            <div className={styles.contextLockedCard}>
              <p className={styles.meta}>场景字段（面试进行中锁定）</p>

              <label htmlFor="running-position-input" className={styles.label}>目标岗位</label>
              <textarea
                id="running-position-input"
                className={styles.textarea}
                rows={2}
                value={position}
                onChange={(event) => setPosition(event.target.value)}
                disabled={lockInterviewContextFields}
                placeholder="目标岗位"
              />

              <label htmlFor="running-jd-input" className={styles.label}>岗位描述</label>
              <textarea
                id="running-jd-input"
                className={styles.textarea}
                rows={3}
                value={jdText}
                onChange={(event) => setJdText(event.target.value)}
                disabled={lockInterviewContextFields}
                placeholder="岗位描述"
              />

              <label htmlFor="running-resume-input" className={styles.label}>经历摘要</label>
              <textarea
                id="running-resume-input"
                className={styles.textarea}
                rows={3}
                value={resumeText}
                onChange={(event) => setResumeText(event.target.value)}
                disabled={lockInterviewContextFields}
                placeholder="经历摘要"
              />

              {lockInterviewContextFields ? <p className={styles.meta}>字段已锁定，结束本次练习后可修改并发起下一次。</p> : null}
            </div>

            {currentQuestion ? (
              <div className={styles.questionCard}>
                <h3 className={styles.questionTitle}>当前题目</h3>
                <p className={styles.questionText}>{currentQuestion.text}</p>
                {currentQuestion.tips.length ? (
                  <ul className={styles.tipList}>
                    {currentQuestion.tips.map((tip) => (
                      <li key={tip}>{tip}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : (
              <p className={styles.empty}>暂无题目，可直接结束并查看总结。</p>
            )}

            <label htmlFor="answer-input" className={styles.label}>你的回答</label>
            <textarea
              id="answer-input"
              className={styles.textarea}
              rows={6}
              value={answerInput}
              onChange={(event) => setAnswerInput(event.target.value)}
              placeholder={currentSession.status === "paused" ? "练习已暂停，继续后可输入" : "输入你的回答..."}
              disabled={currentSession.status === "paused"}
            />

            <div className={styles.actions}>
              <button className={styles.primaryButton} onClick={() => void submitAnswer()} disabled={!canAnswer}>提交回答</button>
              <button className={styles.secondaryButton} onClick={() => void nextQuestion()} disabled={currentSession.status !== "active"}>下一题</button>
              {currentSession.status === "paused" ? (
                <button className={styles.secondaryButton} onClick={() => void resumeSession()}>继续练习</button>
              ) : (
                <button className={styles.secondaryButton} onClick={() => void pauseSession()} disabled={currentSession.status !== "active"}>暂停练习</button>
              )}
              <button className={styles.warnButton} onClick={() => void endSession()}>完成并生成总结</button>
            </div>

            <details className={styles.secondaryPanel}>
              <summary className={styles.summary}>次要操作（可选）</summary>
              <div className={styles.actions}>
                <button className={styles.secondaryButton} onClick={resetCurrentSession}>结束当前并重新开始</button>
                <button className={styles.secondaryButton} onClick={() => void syncRemoteSessions()}>刷新历史记录</button>
              </div>
            </details>
          </section>
        )}

        <details className={styles.card}>
          <summary className={styles.summary}>历史练习（可选）</summary>
          {!visibleRecords.length ? <p className={styles.empty}>暂无历史记录，开始一次练习后会自动保存。</p> : null}
          <ul className={styles.sessionList}>
            {visibleRecords.map((item) => {
              const total = Math.max(item.questionCount, item.questions.length, 1);
              return (
                <li key={item.key}>
                  <button
                    type="button"
                    className={`${styles.sessionButton} ${selectedRecordKey === item.key ? styles.sessionButtonActive : ""}`}
                    onClick={() => setSelectedRecordKey(item.key)}
                  >
                    <span>{item.position || "未命名练习"}</span>
                    <span>{toStatusText(item.status)} · {toModeText(item.mode)}</span>
                    <span>进度：{item.answeredCount}/{total}</span>
                    <span>更新时间：{toDateText(item.updatedAt)}</span>
                  </button>
                </li>
              );
            })}
          </ul>

          {selectedRecord ? (
            <div className={styles.detailBox}>
              <h3 className={styles.subHeading}>记录详情</h3>
              <p>岗位方向：{selectedRecord.position || "未填写"}</p>
              <p>状态：{toStatusText(selectedRecord.status)}</p>
              <p>练习方式：{toModeText(selectedRecord.mode)}</p>
              <p>创建时间：{toDateText(selectedRecord.createdAt)}</p>
              <p>更新时间：{toDateText(selectedRecord.updatedAt)}</p>
              <div className={styles.actions}>
                <button className={styles.secondaryButton} onClick={() => void onLoadRecord(selectedRecord)}>载入为当前练习</button>
              </div>
              {selectedRecord.feedback ? (
                <>
                  <h3 className={styles.subHeading}>最近总结</h3>
                  <p className={styles.feedbackSummary}>{selectedRecord.feedback.summary}</p>
                  <div className={styles.actions}>
                    <button className={styles.secondaryButton} onClick={() => void copyFinalSummary(selectedRecord)}>复制总结</button>
                    <Link
                      className={styles.secondaryButton}
                      href={`/interview/summary?sessionId=${encodeURIComponent(selectedRecord.sessionId)}&sessionKey=${encodeURIComponent(selectedRecord.key)}&resumeContextKey=${encodeURIComponent(resumeContextKey)}`}
                    >
                      打开总结页
                    </Link>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </details>

        {currentSession?.feedback ? (
          <section className={styles.card}>
            <h2 className={styles.sectionTitle}>本次练习总结</h2>
            {typeof currentSession.feedback.score === "number" ? <p className={styles.score}>建议得分：{currentSession.feedback.score}/100</p> : null}
            <p className={styles.feedbackSummary}>{currentSession.feedback.summary}</p>

            <h3 className={styles.subHeading}>做得好的地方</h3>
            {currentSession.feedback.strengths.length ? (
              <ul className={styles.tipList}>
                {currentSession.feedback.strengths.map((item) => (
                  <li key={`strength-${item}`}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className={styles.empty}>暂无</p>
            )}

            <h3 className={styles.subHeading}>下一步改进</h3>
            {currentSession.feedback.improvements.length ? (
              <ul className={styles.tipList}>
                {currentSession.feedback.improvements.map((item) => (
                  <li key={`improve-${item}`}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className={styles.empty}>暂无</p>
            )}

            <div className={styles.actions}>
              <button className={styles.secondaryButton} onClick={() => void copyFinalSummary(currentSession)}>复制总结</button>
              <Link
                className={styles.secondaryButton}
                href={`/interview/summary?sessionId=${encodeURIComponent(currentSession.sessionId)}&sessionKey=${encodeURIComponent(currentSession.key)}&resumeContextKey=${encodeURIComponent(resumeContextKey)}`}
              >
                在总结页查看
              </Link>
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}

export default function InterviewPage() {
  return (
    <Suspense fallback={<div className={styles.page}><main className={styles.main}><p className={styles.empty}>页面加载中...</p></main></div>}>
      <InterviewContent />
    </Suspense>
  );
}
