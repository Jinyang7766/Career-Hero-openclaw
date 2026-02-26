"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";
import { buildLoginHref, useClientAuth } from "../../client-auth";

type SummaryFeedback = {
  summary: string;
  strengths: string[];
  improvements: string[];
  score?: number;
};

type SessionSummaryItem = {
  key: string;
  sessionId: string;
  status: string;
  mode: string;
  createdAt: string;
  updatedAt: string;
  questionCount: number;
  answeredCount: number;
  feedback: SummaryFeedback | null;
  position?: string;
  resumeContextKey?: string;
};

const STORAGE_KEY = "career_hero.interview.sessions.v2";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toSafeString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toSafeNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function normalizeSession(value: unknown): SessionSummaryItem | null {
  if (!isRecord(value)) return null;

  const key = toSafeString(value.key).trim();
  const sessionId = toSafeString(value.sessionId).trim();
  if (!key || !sessionId) return null;

  const feedbackRaw = isRecord(value.feedback) ? value.feedback : null;
  const feedback: SummaryFeedback | null = feedbackRaw
    ? {
        summary: toSafeString(feedbackRaw.summary),
        strengths: toStringArray(feedbackRaw.strengths),
        improvements: toStringArray(feedbackRaw.improvements),
        score: toSafeNumber(feedbackRaw.score) || undefined,
      }
    : null;

  return {
    key,
    sessionId,
    status: toSafeString(value.status) || "active",
    mode: toSafeString(value.mode) || "local",
    createdAt: toSafeString(value.createdAt),
    updatedAt: toSafeString(value.updatedAt),
    questionCount: toSafeNumber(value.questionCount),
    answeredCount: toSafeNumber(value.answeredCount),
    feedback,
    position: toSafeString(value.position),
    resumeContextKey: toSafeString(value.resumeContextKey),
  };
}

function loadSessionsFromStorage(): SessionSummaryItem[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];

    return parsed
      .map((item) => normalizeSession(item))
      .filter((item): item is SessionSummaryItem => item !== null)
      .slice(0, 80);
  } catch {
    return [];
  }
}

function dedupeBySessionId(items: SessionSummaryItem[]): SessionSummaryItem[] {
  const sessionMap = new Map<string, SessionSummaryItem>();

  items.forEach((item) => {
    const existing = sessionMap.get(item.sessionId);
    if (!existing) {
      sessionMap.set(item.sessionId, item);
      return;
    }

    const existingTime = new Date(existing.updatedAt).getTime();
    const nextTime = new Date(item.updatedAt).getTime();
    if (Number.isFinite(nextTime) && (!Number.isFinite(existingTime) || nextTime >= existingTime)) {
      sessionMap.set(item.sessionId, item);
    }
  });

  return Array.from(sessionMap.values());
}

function toDateText(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function toModeText(mode: string): string {
  return mode === "remote" ? "在线练习" : "本地练习";
}

function toStatusText(status: string): string {
  if (status === "finished") return "已完成";
  if (status === "paused") return "已暂停";
  return "进行中";
}

function buildSummaryText(item: SessionSummaryItem): string {
  const feedback = item.feedback;
  const lines = [
    `岗位方向：${item.position || "未填写"}`,
    `练习方式：${toModeText(item.mode)}`,
    `练习进度：${item.answeredCount}/${Math.max(item.questionCount, 1)}`,
    `更新时间：${toDateText(item.updatedAt)}`,
    "",
    `总结：${feedback?.summary || "暂无"}`,
    "",
    "亮点：",
    ...(feedback?.strengths.length ? feedback.strengths.map((value) => `- ${value}`) : ["- 暂无"]),
    "",
    "改进建议：",
    ...(feedback?.improvements.length ? feedback.improvements.map((value) => `- ${value}`) : ["- 暂无"]),
  ];

  if (typeof feedback?.score === "number") {
    lines.splice(5, 0, `建议得分：${feedback.score}/100`);
  }

  return lines.join("\n");
}

function InterviewSummaryContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionKey = (searchParams.get("sessionKey") || "").trim();
  const sessionId = (searchParams.get("sessionId") || "").trim();
  const resumeContextKey = (searchParams.get("resumeContextKey") || "").trim();

  const { authState, authReady, isAuthenticated, authFailureReason } = useClientAuth(API_BASE_URL, {
    autoRedirectOnUnauthorized: true,
  });

  const loginHref = useMemo(
    () => buildLoginHref(`/interview/summary${searchParams.toString() ? `?${searchParams.toString()}` : ""}`, authFailureReason ?? undefined),
    [authFailureReason, searchParams],
  );
  const canAttemptRefresh = authState?.mode === "custom" && Boolean(authState.refreshToken);

  const [items, setItems] = useState<SessionSummaryItem[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const records = loadSessionsFromStorage();
    const finished = dedupeBySessionId(
      records
        .filter((item) => {
          const scoped = !resumeContextKey || toSafeString(item.resumeContextKey) === resumeContextKey;
          if (!scoped) return false;
          return item.feedback !== null || item.status === "finished";
        })
        .sort((a, b) => {
          const aTime = new Date(a.updatedAt).getTime();
          const bTime = new Date(b.updatedAt).getTime();
          if (Number.isFinite(aTime) && Number.isFinite(bTime)) {
            return bTime - aTime;
          }
          return 0;
        }),
    );

    setItems(finished);

    if (sessionId) {
      const bySessionId = finished.find((item) => item.sessionId === sessionId);
      if (bySessionId) {
        setSelectedKey(bySessionId.key);
        return;
      }
    }

    if (sessionKey && finished.some((item) => item.key === sessionKey)) {
      setSelectedKey(sessionKey);
      return;
    }

    if (finished[0]) {
      setSelectedKey(finished[0].key);
    }
  }, [resumeContextKey, sessionId, sessionKey]);

  useEffect(() => {
    if (!authReady || isAuthenticated || canAttemptRefresh) return;
    router.replace(loginHref);
  }, [authReady, canAttemptRefresh, isAuthenticated, loginHref, router]);

  const selected = useMemo(() => items.find((item) => item.key === selectedKey) ?? null, [items, selectedKey]);
  const summaryText = useMemo(() => (selected ? buildSummaryText(selected) : ""), [selected]);
  const interviewHref = useMemo(() => {
    const query = new URLSearchParams();
    if (selected) {
      query.set("sessionId", selected.sessionId);
      query.set("sessionKey", selected.key);
      if (selected.resumeContextKey) {
        query.set("resumeContextKey", selected.resumeContextKey);
      }
    } else if (resumeContextKey) {
      query.set("resumeContextKey", resumeContextKey);
    }

    return query.toString() ? `/interview?${query.toString()}` : "/interview";
  }, [resumeContextKey, selected]);

  function goBackToInterview() {
    router.replace(interviewHref);
  }

  async function copySummary() {
    if (!selected) {
      setError("请先选择一个练习总结。");
      return;
    }

    try {
      await navigator.clipboard.writeText(summaryText);
      setError("");
      setNotice("总结已复制到剪贴板。");
    } catch {
      setNotice("");
      setError("复制失败，请检查浏览器剪贴板权限。");
    }
  }

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <header className={styles.headerCard}>
          <p className={styles.badge}>主流程：选择总结 → 复制复盘</p>
          <h1 className={styles.title}>面试总结</h1>
          <p className={styles.subtitle}>从已完成练习里挑选一份总结，快速复盘下一步改进重点。</p>
          <div className={styles.headerActions}>
            <button type="button" className={styles.linkChip} onClick={goBackToInterview}>返回面试练习</button>
          </div>
        </header>

        <section className={styles.grid}>
          <div className={styles.card}>
            <h2>已完成练习</h2>
            {!items.length ? <p className={styles.empty}>暂无可用总结，请先在“面试练习”完成一次练习。</p> : null}
            <ul className={styles.list}>
              {items.map((item, index) => (
                <li key={item.key}>
                  <button
                    type="button"
                    className={`${styles.itemButton} ${selectedKey === item.key ? styles.itemActive : ""}`}
                    onClick={() => setSelectedKey(item.key)}
                  >
                    <span>第 {items.length - index} 次练习</span>
                    <span>{item.position || "未命名岗位"}</span>
                    <span>{toStatusText(item.status)} · {toModeText(item.mode)}</span>
                    <span>进度：{item.answeredCount}/{Math.max(item.questionCount, 1)}</span>
                    <span>更新时间：{toDateText(item.updatedAt)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className={styles.card}>
            <h2>总结详情</h2>
            {!selected ? <p className={styles.empty}>请选择一个练习查看总结。</p> : null}
            {selected ? (
              <>
                <p className={styles.meta}>岗位方向：{selected.position || "未填写"}</p>
                <p className={styles.meta}>创建时间：{toDateText(selected.createdAt)}</p>
                <p className={styles.meta}>更新时间：{toDateText(selected.updatedAt)}</p>
                <div className={styles.summaryBox}>{summaryText}</div>
                <div className={styles.actions}>
                  <button className={styles.primaryButton} onClick={() => void copySummary()}>复制总结</button>
                  <button type="button" className={styles.secondaryButton} onClick={goBackToInterview}>
                    返回当前练习
                  </button>
                </div>

                <details className={styles.devPanel}>
                  <summary className={styles.summary}>开发者信息（默认收起）</summary>
                  <p className={styles.meta}>会话编号：{selected.sessionId}</p>
                  <p className={styles.meta}>模式：{toModeText(selected.mode)}</p>
                  <p className={styles.meta}>状态：{toStatusText(selected.status)}</p>
                </details>
              </>
            ) : null}

            {notice ? <p className={styles.notice}>{notice}</p> : null}
            {error ? <p className={styles.error}>{error}</p> : null}
          </div>
        </section>
      </main>
    </div>
  );
}

export default function InterviewSummaryPage() {
  return (
    <Suspense fallback={<div className={styles.page}><main className={styles.main}><p className={styles.empty}>页面加载中...</p></main></div>}>
      <InterviewSummaryContent />
    </Suspense>
  );
}
