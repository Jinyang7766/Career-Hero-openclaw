"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";
import { useClientAuth } from "../client-auth";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

function toSafeReturnTo(value: string | null): string {
  if (!value) return "/";
  if (!value.startsWith("/")) return "/";
  if (value.startsWith("//")) return "/";
  return value;
}

function toReasonHint(reason: string | null): string {
  if (reason === "expired") {
    return "你的登录状态已过期，请重新登录。";
  }
  if (reason === "refresh_failed") {
    return "自动续期失败，请重新登录后继续。";
  }
  if (reason === "unauthorized") {
    return "该页面需要登录后使用。";
  }
  return "";
}

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    authState,
    authReady,
    authFailureReason,
    clearAuthFailureReason,
    login,
    logout,
  } = useClientAuth(API_BASE_URL);

  const [displayName, setDisplayName] = useState("");
  const [expiresHours, setExpiresHours] = useState("24");

  const [token, setToken] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [sessionId, setSessionId] = useState("");

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const returnTo = useMemo(() => toSafeReturnTo(searchParams.get("returnTo")), [searchParams]);
  const reason = searchParams.get("reason");
  const reasonHint = useMemo(() => toReasonHint(reason || authFailureReason), [authFailureReason, reason]);

  useEffect(() => {
    if (!authState) return;
    setDisplayName((prev) => prev || authState.userName || "");
    setSessionId((prev) => prev || authState.sessionId || "");
  }, [authState]);

  function onLoginSubmit() {
    setError("");
    setMessage("");

    const expiresInHours = Number.parseFloat(expiresHours);
    if (!Number.isFinite(expiresInHours) || expiresInHours <= 0) {
      setError("请输入有效的登录时长（小时）。");
      return;
    }

    const next = login({
      userName: displayName,
      expiresInHours,
      token: token.trim() || undefined,
      refreshToken: refreshToken.trim() || undefined,
      sessionId: sessionId.trim() || undefined,
    });

    setMessage(`欢迎你，${next.userName || "候选人"}！正在返回上一页...`);
    clearAuthFailureReason();
    window.setTimeout(() => {
      router.push(returnTo);
    }, 380);
  }

  function onLogout() {
    logout();
    setMessage("你已退出当前登录状态。");
    setError("");
  }

  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <header className={styles.headerCard}>
          <p className={styles.badge}>主流程：登录并返回业务页</p>
          <h1 className={styles.title}>账号登录</h1>
          <p className={styles.subtitle}>完成登录后，会自动返回你刚才的页面继续操作。</p>
          <div className={styles.headerActions}>
            <Link href={returnTo} className={styles.linkChip}>返回上一页</Link>
          </div>
        </header>

        {reasonHint ? <p className={styles.banner}>{reasonHint}</p> : null}

        <section className={styles.card}>
          <div className={styles.sectionHead}>
            <h2>快速登录</h2>
            <p>默认只需昵称 + 时长即可完成登录。</p>
          </div>

          <label className={styles.label} htmlFor="login-username">昵称（可选）</label>
          <input
            id="login-username"
            className={styles.input}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="例如：张三"
          />

          <label className={styles.label} htmlFor="login-expire-hours">登录时长（小时）</label>
          <input
            id="login-expire-hours"
            className={styles.input}
            value={expiresHours}
            onChange={(event) => setExpiresHours(event.target.value)}
            inputMode="decimal"
            placeholder="例如：24"
          />

          <div className={styles.actions}>
            <button className={styles.primaryButton} onClick={onLoginSubmit} disabled={!authReady}>登录并继续</button>
            <button className={styles.secondaryButton} onClick={() => router.push(returnTo)}>暂不登录，直接返回</button>
          </div>

          {message ? <p className={styles.success}>{message}</p> : null}
          {error ? <p className={styles.error}>{error}</p> : null}
        </section>

        <details className={`${styles.card} ${styles.developerCard}`}>
          <summary className={styles.summary}>开发者设置（默认收起）</summary>
          <p className={styles.meta}>仅用于调试：可手动指定令牌、刷新令牌与会话编号。</p>
          <p className={styles.meta}>当前用户：{authState?.userName || "访客"}</p>
          <p className={styles.meta}>当前会话：{authState?.sessionId || "-"}</p>

          <label className={styles.label} htmlFor="login-token">访问令牌</label>
          <textarea
            id="login-token"
            className={styles.textarea}
            rows={3}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="留空则自动生成"
          />

          <label className={styles.label} htmlFor="login-refresh-token">刷新令牌（可选）</label>
          <textarea
            id="login-refresh-token"
            className={styles.textarea}
            rows={2}
            value={refreshToken}
            onChange={(event) => setRefreshToken(event.target.value)}
            placeholder="可留空"
          />

          <label className={styles.label} htmlFor="login-session-id">会话编号（可选）</label>
          <input
            id="login-session-id"
            className={styles.input}
            value={sessionId}
            onChange={(event) => setSessionId(event.target.value)}
            placeholder="留空则自动生成"
          />

          <div className={styles.actions}>
            <button className={styles.warnButton} onClick={onLogout} disabled={!authReady}>退出登录</button>
          </div>
        </details>
      </main>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className={styles.page}><main className={styles.main}><p className={styles.meta}>页面加载中...</p></main></div>}>
      <LoginContent />
    </Suspense>
  );
}
