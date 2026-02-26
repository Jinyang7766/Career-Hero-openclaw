"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";
import { buildLoginHref, useClientAuth } from "../client-auth";
import { toUserFriendlyError } from "../page.utils";

type ResumeItem = {
  id: number;
  title: string;
  latestVersionNo: number;
  createdAt: string;
  updatedAt: string;
  latestParseStatus: string;
  latestContentPreview: string;
};

type ResumeVersion = {
  id: number;
  versionNo: number;
  content: string;
  parseStatus: string;
  parsedText: string;
  metadata: Record<string, unknown>;
  createdAt: string;
};

type ResumeDetail = {
  id: number;
  title: string;
  latestVersionNo: number;
  createdAt: string;
  updatedAt: string;
  currentVersion: ResumeVersion | null;
  versions: ResumeVersion[];
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

function buildAnalyzeHref(resumeId: number, versionNo?: number | null): string {
  const safeVersion = typeof versionNo === "number" && Number.isFinite(versionNo) && versionNo > 0 ? versionNo : 0;
  const query = new URLSearchParams({
    resumeId: String(resumeId),
    resumeContextKey: `resume-${resumeId}-v${safeVersion}`,
  });

  if (safeVersion > 0) {
    query.set("versionNo", String(safeVersion));
  }
  return `/?${query.toString()}`;
}

function toDateText(value: string): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function toVersion(value: unknown): ResumeVersion | null {
  if (!isRecord(value)) return null;
  return {
    id: toNumber(value.id),
    versionNo: toNumber(value.versionNo),
    content: toString(value.content),
    parseStatus: toString(value.parseStatus),
    parsedText: toString(value.parsedText),
    metadata: isRecord(value.metadata) ? value.metadata : {},
    createdAt: toString(value.createdAt),
  };
}

function toItem(value: unknown): ResumeItem | null {
  if (!isRecord(value)) return null;
  return {
    id: toNumber(value.id),
    title: toString(value.title),
    latestVersionNo: toNumber(value.latestVersionNo),
    createdAt: toString(value.createdAt),
    updatedAt: toString(value.updatedAt),
    latestParseStatus: toString(value.latestParseStatus),
    latestContentPreview: toString(value.latestContentPreview),
  };
}

function toDetail(payload: unknown): ResumeDetail | null {
  if (!isRecord(payload)) return null;
  const item = isRecord(payload.item) ? payload.item : payload;
  const versionsRaw = Array.isArray(item.versions) ? item.versions : [];
  const versions = versionsRaw.map((v) => toVersion(v)).filter((v): v is ResumeVersion => v !== null);

  return {
    id: toNumber(item.id),
    title: toString(item.title),
    latestVersionNo: toNumber(item.latestVersionNo),
    createdAt: toString(item.createdAt),
    updatedAt: toString(item.updatedAt),
    currentVersion: toVersion(item.currentVersion),
    versions,
  };
}

function toParseStatusText(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (!normalized) return "待处理";
  if (normalized.includes("success") || normalized.includes("done") || normalized.includes("ok")) return "已处理";
  if (normalized.includes("fail") || normalized.includes("error")) return "处理失败";
  if (normalized.includes("processing") || normalized.includes("running") || normalized.includes("pending")) return "处理中";
  if (/[a-z]/i.test(normalized)) return "状态未知";
  return status;
}

function toAuthModeText(mode: string): string {
  const normalized = mode.trim().toLowerCase();
  if (normalized === "custom") return "自定义";
  if (normalized === "default") return "默认";
  if (normalized === "local") return "本地";
  if (/[a-z]/i.test(normalized)) return "其他";
  return mode;
}

export default function ResumesPage() {
  const router = useRouter();

  const {
    authState,
    authReady,
    isAuthenticated,
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

  const [items, setItems] = useState<ResumeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [creating, setCreating] = useState(false);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ResumeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [authMessage, setAuthMessage] = useState("");

  const canCreate = useMemo(() => Boolean(title.trim() && content.trim() && !creating), [title, content, creating]);
  const loginHref = useMemo(() => buildLoginHref("/resumes", authFailureReason ?? undefined), [authFailureReason]);
  const canAttemptRefresh = authState?.mode === "custom" && Boolean(authState.refreshToken);

  async function loadList() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/resumes?limit=50`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((isRecord(data) && toString(data.message)) || "简历列表加载失败");
      }

      const rawItems = isRecord(data) && Array.isArray(data.items) ? data.items : [];
      const next = rawItems.map((raw) => toItem(raw)).filter((item): item is ResumeItem => item !== null);
      setItems(next);
    } catch (err) {
      setError(toUserFriendlyError(err, "简历列表加载失败，请稍后重试"));
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(id: number) {
    setDetailLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/resumes/${id}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((isRecord(data) && toString(data.message)) || "简历详情加载失败");
      }

      const parsed = toDetail(data);
      if (!parsed) throw new Error("简历详情格式异常");
      setDetail(parsed);
      setSelectedId(id);
    } catch (err) {
      setError(toUserFriendlyError(err, "简历详情加载失败，请稍后重试"));
    } finally {
      setDetailLoading(false);
    }
  }

  async function onCreate() {
    if (!canCreate) return;

    setCreating(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/resumes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, content }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((isRecord(data) && toString(data.message)) || "简历创建失败");
      }

      const parsed = toDetail(data);
      if (!parsed) throw new Error("创建成功但返回格式异常");

      setTitle("");
      setContent("");
      setDetail(parsed);
      setSelectedId(parsed.id);
      await loadList();
    } catch (err) {
      setError(toUserFriendlyError(err, "保存简历失败，请稍后重试"));
    } finally {
      setCreating(false);
    }
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

  useEffect(() => {
    void loadList();
  }, []);

  useEffect(() => {
    if (!authReady || isAuthenticated || canAttemptRefresh) return;
    router.replace(loginHref);
  }, [authReady, canAttemptRefresh, isAuthenticated, loginHref, router]);

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <header className={styles.headerCard}>
          <p className={styles.badge}>主流程：新增简历 → 选择版本 → 带入分析</p>
          <h1 className={styles.title}>简历库</h1>
          <p className={styles.subtitle}>先存好可复用简历，再回到首页一键做岗位匹配分析。</p>
          <div className={styles.headerActions}>
            <Link href="/" className={styles.linkChip}>返回岗位分析</Link>
          </div>
        </header>

        {error ? <p className={styles.error}>{error}</p> : null}

        <section className={styles.card}>
          <div className={styles.stepHead}>
            <p className={styles.stepBadge}>第 1 步</p>
            <h2>新增简历</h2>
            <p>填写标题和内容，保存后会自动进入下方列表。</p>
          </div>

          <label className={styles.label} htmlFor="resume-title">简历标题</label>
          <input
            id="resume-title"
            className={styles.input}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例如：后端工程师简历（社招）"
          />

          <label className={styles.label} htmlFor="resume-content">简历内容</label>
          <textarea
            id="resume-content"
            className={styles.textarea}
            rows={9}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="粘贴你的简历全文或核心内容"
          />

          <div className={styles.actions}>
            <button className={styles.primaryButton} disabled={!canCreate} onClick={() => void onCreate()}>
              {creating ? "保存中..." : "保存并加入简历库"}
            </button>
          </div>
        </section>

        <section className={styles.card}>
          <div className={styles.stepHead}>
            <p className={styles.stepBadge}>第 2 步</p>
            <h2>选择简历并进入分析</h2>
            <p>点击条目查看详情，然后用对应版本直接跳转到岗位分析页。</p>
          </div>

          {!items.length && !loading ? <p className={styles.empty}>还没有简历，先完成上面的新增步骤。</p> : null}

          <ul className={styles.list}>
            {items.map((item) => (
              <li key={item.id} className={styles.listItem}>
                <button className={styles.itemButton} onClick={() => void loadDetail(item.id)}>
                  <strong>{item.title}</strong>
                  <span>第 {item.latestVersionNo} 版 · {toParseStatusText(item.latestParseStatus)}</span>
                  <span>{item.latestContentPreview || "（暂无内容预览）"}</span>
                  <span>最近更新：{toDateText(item.updatedAt)}</span>
                </button>
                <Link className={styles.primaryLink} href={buildAnalyzeHref(item.id, item.latestVersionNo)}>
                  用这个版本做岗位分析
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <details className={styles.card} open={Boolean(selectedId)}>
          <summary className={styles.summary}>次级操作与详情（可选）</summary>

          <div className={styles.actions}>
            <button className={styles.secondaryButton} disabled={loading} onClick={() => void loadList()}>
              {loading ? "刷新中..." : "刷新简历列表"}
            </button>
            <Link href={loginHref} className={styles.secondaryButton}>账号与登录</Link>
          </div>

          {detailLoading ? <p className={styles.empty}>加载详情中...</p> : null}
          {!detailLoading && !detail ? <p className={styles.empty}>从上方列表选择一份简历查看详情。</p> : null}

          {detail ? (
            <div className={styles.detail}>
              <p><strong>{detail.title}</strong></p>
              <p>创建时间：{toDateText(detail.createdAt)}</p>
              <p>最近更新：{toDateText(detail.updatedAt)}</p>
              <p>当前版本：第 {detail.latestVersionNo} 版</p>

              <div className={styles.actionsInline}>
                <Link className={styles.primaryButton} href={buildAnalyzeHref(detail.id, detail.latestVersionNo)}>
                  分析当前版本
                </Link>
              </div>

              <h3 className={styles.subHeading}>历史版本</h3>
              <ul className={styles.versionList}>
                {detail.versions.map((version) => (
                  <li key={version.id} className={styles.versionItem}>
                    <div className={styles.versionHeader}>
                      <p>第 {version.versionNo} 版 · {toParseStatusText(version.parseStatus)} · {toDateText(version.createdAt)}</p>
                      <Link className={styles.secondaryButton} href={buildAnalyzeHref(detail.id, version.versionNo)}>
                        分析这个版本
                      </Link>
                    </div>
                    <textarea className={styles.textarea} rows={4} readOnly value={version.content} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </details>

        <details className={`${styles.card} ${styles.developerCard}`}>
          <summary className={styles.summary}>开发者设置（默认收起）</summary>
          <p className={styles.meta}>用户：{authState?.userName || "访客"}</p>
          <p className={styles.meta}>会话：{authState?.sessionId || "初始化中..."}</p>

          <label className={styles.label} htmlFor="resume-auth-token">访问令牌</label>
          <input
            id="resume-auth-token"
            className={styles.input}
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
          {authMessage ? <p className={styles.meta}>{authMessage}</p> : null}
        </details>
      </main>
    </div>
  );
}
