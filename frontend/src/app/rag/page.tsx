"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";
import { buildLoginHref, useClientAuth } from "../client-auth";
import { toErrorMessage, toUserFriendlyError } from "../page.utils";

type KnowledgeItem = {
  id: string;
  remoteId: number | null;
  title: string;
  content: string;
  tags: string[];
  source: string;
  createdAt: string;
  updatedAt: string;
};

type StorageMode = "remote" | "local-fallback";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";
const STORAGE_KEY = "career_hero.rag_kb.local.v1";
const DEFAULT_SOURCE_LABEL = "手动录入";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toSafeString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function parseTags(input: string): string[] {
  return input
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 12);
}

function toSourceText(value: unknown): string {
  const raw = toSafeString(value).trim();
  if (!raw) return DEFAULT_SOURCE_LABEL;
  if (raw.toLowerCase() === "manual") return DEFAULT_SOURCE_LABEL;
  return raw;
}

function toSourcePayload(value: string): string {
  const raw = value.trim();
  if (!raw || raw === DEFAULT_SOURCE_LABEL) return "manual";
  return raw;
}

function toAuthModeText(mode: string | undefined): string {
  const raw = toSafeString(mode).trim().toLowerCase();
  if (!raw) return "-";
  if (raw === "custom") return "自定义";
  if (raw === "default") return "默认";
  if (raw === "local") return "本地";
  if (/[a-z]/i.test(raw)) return "其他";
  return mode ?? "-";
}

function toLocalDateText(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

async function readJsonSafe(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return null;
  }
  return response.json().catch(() => null);
}

function normalizeKnowledgeItem(value: unknown): KnowledgeItem | null {
  if (!isRecord(value)) return null;

  const remoteId = typeof value.id === "number" && Number.isFinite(value.id) ? value.id : null;
  const title = toSafeString(value.title).trim();
  const content = toSafeString(value.content).trim();
  if (!title || !content) return null;

  const createdAt = toSafeString(value.createdAt || value.created_at) || new Date().toISOString();
  const updatedAt = toSafeString(value.updatedAt || value.updated_at) || createdAt;

  return {
    id: remoteId ? `remote-${remoteId}` : `local-${Date.now()}`,
    remoteId,
    title,
    content,
    tags: toStringArray(value.tags),
    source: toSourceText(value.source),
    createdAt,
    updatedAt,
  };
}

function normalizeLocalKnowledgeItem(value: unknown): KnowledgeItem | null {
  if (!isRecord(value)) return null;

  const id = toSafeString(value.id).trim();
  if (!id) return null;

  const title = toSafeString(value.title).trim();
  const content = toSafeString(value.content).trim();
  if (!title || !content) return null;

  return {
    id,
    remoteId: typeof value.remoteId === "number" && Number.isFinite(value.remoteId) ? value.remoteId : null,
    title,
    content,
    tags: toStringArray(value.tags),
    source: toSourceText(value.source),
    createdAt: toSafeString(value.createdAt) || new Date().toISOString(),
    updatedAt: toSafeString(value.updatedAt) || new Date().toISOString(),
  };
}

function loadLocalKnowledgeStore(): KnowledgeItem[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];

    return parsed
      .map((item) => normalizeLocalKnowledgeItem(item))
      .filter((item): item is KnowledgeItem => item !== null)
      .slice(0, 200);
  } catch {
    return [];
  }
}

function saveLocalKnowledgeStore(items: KnowledgeItem[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, 200)));
  } catch {
    // ignore
  }
}

export default function RagPage() {
  const router = useRouter();

  const { authState, authReady, isAuthenticated, authFailureReason, logout, apiFetch } = useClientAuth(API_BASE_URL, {
    autoRedirectOnUnauthorized: true,
  });
  const fetch = useCallback((input: RequestInfo | URL, init?: RequestInit) => apiFetch(input, init), [apiFetch]);

  const [mode, setMode] = useState<StorageMode>("remote");
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [source, setSource] = useState(DEFAULT_SOURCE_LABEL);

  const [editingId, setEditingId] = useState<string | null>(null);

  const loginHref = useMemo(() => buildLoginHref("/rag", authFailureReason ?? undefined), [authFailureReason]);
  const canAttemptRefresh = authState?.mode === "custom" && Boolean(authState.refreshToken);

  const editingItem = useMemo(() => {
    if (!editingId) return null;
    return items.find((item) => item.id === editingId) ?? null;
  }, [editingId, items]);

  const canSubmit = Boolean(title.trim() && content.trim() && !submitting);

  useEffect(() => {
    if (!editingItem) return;
    setTitle(editingItem.title);
    setContent(editingItem.content);
    setTagsInput(editingItem.tags.join(", "));
    setSource(toSourceText(editingItem.source));
  }, [editingItem]);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/rag/knowledge?limit=100`);
      const data = await readJsonSafe(response);
      if (!response.ok) {
        throw new Error(toErrorMessage(data));
      }

      const payload = isRecord(data) ? data : {};
      const rawItems = Array.isArray(payload.items) ? payload.items : [];
      const nextItems = rawItems
        .map((item) => normalizeKnowledgeItem(item))
        .filter((item): item is KnowledgeItem => item !== null);

      setItems(nextItems);
      setMode("remote");
      setNotice("知识库已同步到最新状态。");
    } catch {
      const localItems = loadLocalKnowledgeStore();
      setItems(localItems);
      setMode("local-fallback");
      setNotice("当前使用本地临时保存模式，仍可继续新增、编辑和删除。\n网络恢复后可再次同步。");
    } finally {
      setLoading(false);
    }
  }, [fetch]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!authReady || isAuthenticated || canAttemptRefresh) return;
    router.replace(loginHref);
  }, [authReady, canAttemptRefresh, isAuthenticated, loginHref, router]);

  function resetForm() {
    setEditingId(null);
    setTitle("");
    setContent("");
    setTagsInput("");
    setSource(DEFAULT_SOURCE_LABEL);
  }

  function persistLocal(itemsToPersist: KnowledgeItem[], reason?: string) {
    saveLocalKnowledgeStore(itemsToPersist);
    setItems(itemsToPersist);
    setMode("local-fallback");
    if (reason) setNotice(reason);
  }

  async function createLocalItem() {
    const now = new Date().toISOString();
    const nextItem: KnowledgeItem = {
      id: `local-${Date.now()}`,
      remoteId: null,
      title: title.trim(),
      content: content.trim(),
      tags: parseTags(tagsInput),
      source: toSourcePayload(source),
      createdAt: now,
      updatedAt: now,
    };

    const next = [nextItem, ...items];
    persistLocal(next, "已添加知识条目（本地模式）。");
    resetForm();
  }

  async function updateLocalItem(targetId: string) {
    const now = new Date().toISOString();
    const next = items.map((item) =>
      item.id === targetId
        ? {
            ...item,
            title: title.trim(),
            content: content.trim(),
            tags: parseTags(tagsInput),
            source: toSourcePayload(source),
            updatedAt: now,
          }
        : item,
    );

    persistLocal(next, "已更新知识条目（本地模式）。");
    resetForm();
  }

  async function onSubmit() {
    if (!canSubmit) return;

    setSubmitting(true);
    setError("");

    try {
      if (mode === "local-fallback") {
        if (editingId) {
          await updateLocalItem(editingId);
        } else {
          await createLocalItem();
        }
        return;
      }

      if (!editingId) {
        const response = await fetch(`${API_BASE_URL}/api/rag/knowledge`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: title.trim(),
            content: content.trim(),
            tags: parseTags(tagsInput),
            source: toSourcePayload(source),
          }),
        });

        const data = await readJsonSafe(response);
        if (!response.ok) {
          throw new Error(toErrorMessage(data));
        }

        const payload = isRecord(data) ? data.item : null;
        const created = normalizeKnowledgeItem(payload);
        if (!created) {
          throw new Error("新增成功，但返回格式异常");
        }

        setItems((prev) => [created, ...prev]);
        setNotice("知识条目已新增。");
        resetForm();
        return;
      }

      const target = items.find((item) => item.id === editingId) ?? null;
      if (!target) {
        throw new Error("待编辑条目不存在");
      }

      if (target.remoteId === null) {
        await updateLocalItem(target.id);
        return;
      }

      const payload = {
        id: target.remoteId,
        title: title.trim(),
        content: content.trim(),
        tags: parseTags(tagsInput),
        source: toSourcePayload(source),
      };

      const candidates = [
        { url: `${API_BASE_URL}/api/rag/knowledge/${target.remoteId}`, method: "PUT" },
        { url: `${API_BASE_URL}/api/rag/knowledge/${target.remoteId}`, method: "PATCH" },
        { url: `${API_BASE_URL}/api/rag/knowledge`, method: "PUT" },
      ] as const;

      let updated: KnowledgeItem | null = null;
      for (const candidate of candidates) {
        const response = await fetch(candidate.url, {
          method: candidate.method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (response.status === 404 || response.status === 405) {
          continue;
        }

        const data = await readJsonSafe(response);
        if (!response.ok) {
          throw new Error(toErrorMessage(data));
        }

        const normalized = normalizeKnowledgeItem(isRecord(data) ? data.item ?? data : data);
        if (normalized) {
          updated = normalized;
        }
        break;
      }

      if (!updated) {
        const fallbackItems = items.map((item) =>
          item.id === target.id
            ? {
                ...item,
                title: payload.title,
                content: payload.content,
                tags: payload.tags,
                source: payload.source,
                updatedAt: new Date().toISOString(),
              }
            : item,
        );
        persistLocal(fallbackItems, "当前环境不支持在线编辑，已切换本地模式保存。");
        resetForm();
        return;
      }

      setItems((prev) => prev.map((item) => (item.id === target.id ? updated : item)));
      setNotice("知识条目已更新。");
      resetForm();
    } catch (err) {
      setError(toUserFriendlyError(err, "提交失败，请稍后重试"));
    } finally {
      setSubmitting(false);
    }
  }

  async function onDelete(item: KnowledgeItem) {
    setError("");

    if (!window.confirm(`确认删除「${item.title}」吗？`)) {
      return;
    }

    if (mode === "local-fallback" || item.remoteId === null) {
      const next = items.filter((entry) => entry.id !== item.id);
      persistLocal(next, "已删除知识条目（本地模式）。");
      if (editingId === item.id) {
        resetForm();
      }
      return;
    }

    try {
      const candidates = [
        `${API_BASE_URL}/api/rag/knowledge/${item.remoteId}`,
        `${API_BASE_URL}/api/rag/knowledge?id=${item.remoteId}`,
      ];

      let remoteDeleted = false;
      for (const url of candidates) {
        const response = await fetch(url, { method: "DELETE" });
        if (response.status === 404 || response.status === 405) {
          continue;
        }

        if (!response.ok) {
          const data = await readJsonSafe(response);
          throw new Error(toErrorMessage(data));
        }

        remoteDeleted = true;
        break;
      }

      if (!remoteDeleted) {
        const fallbackItems = items.filter((entry) => entry.id !== item.id);
        persistLocal(fallbackItems, "当前环境不支持在线删除，已切换本地模式处理。");
        if (editingId === item.id) {
          resetForm();
        }
        return;
      }

      setItems((prev) => prev.filter((entry) => entry.id !== item.id));
      setNotice("知识条目已删除。");
      if (editingId === item.id) {
        resetForm();
      }
    } catch (err) {
      setError(toUserFriendlyError(err, "删除失败，请稍后重试"));
    }
  }

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <header className={styles.headerCard}>
          <p className={styles.badge}>主流程：维护知识条目</p>
          <h1 className={styles.title}>知识库</h1>
          <p className={styles.subtitle}>持续补充可复用话术与案例，分析与面试会自动受益。</p>
          <div className={styles.headerActions}>
            <Link href="/" className={styles.linkChip}>返回岗位分析</Link>
            <Link href={loginHref} className={styles.linkChip}>账号与登录</Link>
          </div>
        </header>

        <section className={styles.card}>
          <div className={styles.sectionHead}>
            <h2>{editingId ? "编辑知识条目" : "新增知识条目"}</h2>
            <p>建议每条围绕一个高频问题，标题清晰、内容可直接复用。</p>
          </div>

          <label htmlFor="kb-title" className={styles.label}>标题</label>
          <input
            id="kb-title"
            className={styles.input}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例如：如何回答“项目难点”"
          />

          <label htmlFor="kb-content" className={styles.label}>内容</label>
          <textarea
            id="kb-content"
            className={styles.textarea}
            rows={7}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="写下关键观点、示例话术或结构化答案"
          />

          <label htmlFor="kb-tags" className={styles.label}>标签（逗号分隔）</label>
          <input
            id="kb-tags"
            className={styles.input}
            value={tagsInput}
            onChange={(event) => setTagsInput(event.target.value)}
            placeholder="面试, 项目, 后端"
          />

          <label htmlFor="kb-source" className={styles.label}>来源备注（可选）</label>
          <input
            id="kb-source"
            className={styles.input}
            value={source}
            onChange={(event) => setSource(event.target.value)}
            placeholder="例如：复盘、课程、真实面试"
          />

          <div className={styles.actions}>
            <button className={styles.primaryButton} onClick={() => void onSubmit()} disabled={!canSubmit}>
              {submitting ? "保存中..." : editingId ? "保存修改" : "新增条目"}
            </button>
            {editingId ? <button className={styles.secondaryButton} onClick={resetForm} disabled={submitting}>取消编辑</button> : null}
          </div>
        </section>

        <section className={styles.card}>
          <div className={styles.sectionHead}>
            <h2>条目列表</h2>
            <p>当前模式：{mode === "remote" ? "云端知识库" : "本地临时保存"}</p>
          </div>

          <div className={styles.actionsInline}>
            <button className={styles.secondaryButton} onClick={() => void loadList()} disabled={loading}>
              {loading ? "同步中..." : "立即同步"}
            </button>
          </div>

          {notice ? <p className={styles.notice}>{notice}</p> : null}
          {error ? <p className={styles.error}>{error}</p> : null}
          {!items.length ? <p className={styles.empty}>暂无条目，先新增一条知识内容吧。</p> : null}

          <ul className={styles.list}>
            {items.map((item) => (
              <li key={item.id} className={styles.item}>
                <div className={styles.itemHeader}>
                  <h3 className={styles.itemTitle}>{item.title}</h3>
                  <span className={styles.meta}>更新于：{toLocalDateText(item.updatedAt)}</span>
                </div>
                <div className={styles.tagRow}>
                  {item.tags.length
                    ? item.tags.map((tag) => (
                        <span key={`${item.id}-${tag}`} className={styles.tag}>{tag}</span>
                      ))
                    : <span className={styles.tag}>未设置标签</span>}
                </div>
                <p className={styles.itemContent}>{item.content}</p>
                <div className={styles.itemActions}>
                  <button className={styles.secondaryButton} onClick={() => setEditingId(item.id)}>编辑</button>
                  <button className={styles.warnButton} onClick={() => void onDelete(item)}>删除</button>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <details className={`${styles.card} ${styles.developerCard}`}>
          <summary className={styles.summary}>开发者设置（默认收起）</summary>
          <p className={styles.meta}>用户：{authState?.userName || "访客"}</p>
          <p className={styles.meta}>会话：{authState?.sessionId || "-"}</p>
          <p className={styles.meta}>模式：{toAuthModeText(authState?.mode)}</p>
          <div className={styles.actionsInline}>
            <button className={styles.warnButton} onClick={() => logout()} disabled={!authReady}>退出登录</button>
          </div>
        </details>
      </main>
    </div>
  );
}
