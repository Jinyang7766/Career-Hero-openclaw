"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";

export type ClientAuthMode = "local" | "custom";
export type AuthFailureReason = "expired" | "unauthorized" | "refresh_failed";

export type ClientAuthState = {
  sessionId: string;
  accessToken: string;
  refreshToken: string | null;
  mode: ClientAuthMode;
  updatedAt: string;
  userName: string | null;
  expiresAt: string | null;
};

export type LoginPayload = {
  token?: string;
  refreshToken?: string;
  userName?: string;
  expiresInHours?: number;
  expiresAt?: string;
  sessionId?: string;
};

export type UseClientAuthOptions = {
  autoRedirectOnUnauthorized?: boolean;
  loginPath?: string;
};

const STORAGE_KEY = "career_hero.client_auth.v3";

function randomHex(length: number): string {
  const target = Math.max(4, length);

  if (typeof window !== "undefined" && window.crypto?.getRandomValues) {
    const bytes = new Uint8Array(Math.ceil(target / 2));
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes)
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("")
      .slice(0, target);
  }

  return Math.random().toString(16).replace("0.", "").padEnd(target, "0").slice(0, target);
}

export function generateSessionId(): string {
  return `sess_${Date.now().toString(36)}_${randomHex(12)}`;
}

export function generateAccessToken(): string {
  return `local_${randomHex(28)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isAuthExpired(state: Pick<ClientAuthState, "expiresAt"> | null | undefined): boolean {
  const expiresAt = state?.expiresAt;
  if (!expiresAt) return false;

  const expiresAtMs = new Date(expiresAt).getTime();
  if (!Number.isFinite(expiresAtMs)) return false;
  return Date.now() >= expiresAtMs;
}

export function isClientAuthenticated(state: ClientAuthState | null): boolean {
  if (!state) return false;
  if (state.mode !== "custom") return false;
  return !isAuthExpired(state);
}

function toFutureTimeIso(hours?: number): string | null {
  if (typeof hours !== "number" || !Number.isFinite(hours) || hours <= 0) {
    return null;
  }

  return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
}

function toSafeAuthReason(raw: string | null): AuthFailureReason | null {
  if (raw === "expired" || raw === "unauthorized" || raw === "refresh_failed") {
    return raw;
  }
  return null;
}

function toAbsoluteInput(apiBaseUrl: string, input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input === "string") {
    if (/^https?:\/\//i.test(input)) {
      return input;
    }

    const normalizedPath = input.startsWith("/") ? input : `/${input}`;
    return `${apiBaseUrl}${normalizedPath}`;
  }

  return input;
}

function normalizeExpiresAt(payload: LoginPayload): string | null {
  if (typeof payload.expiresAt === "string" && payload.expiresAt.trim()) {
    return payload.expiresAt.trim();
  }
  return toFutureTimeIso(payload.expiresInHours);
}

function normalizeClientAuthState(value: unknown): ClientAuthState | null {
  if (!isRecord(value)) return null;

  const sessionId = typeof value.sessionId === "string" ? value.sessionId.trim() : "";
  const accessToken = typeof value.accessToken === "string" ? value.accessToken.trim() : "";
  const modeRaw = typeof value.mode === "string" ? value.mode.trim().toLowerCase() : "";
  const mode: ClientAuthMode = modeRaw === "custom" ? "custom" : "local";

  if (!sessionId || !accessToken) {
    return null;
  }

  const userNameRaw = typeof value.userName === "string" ? value.userName.trim() : "";
  const expiresAtRaw = typeof value.expiresAt === "string" ? value.expiresAt.trim() : "";
  const refreshTokenRaw = typeof value.refreshToken === "string" ? value.refreshToken.trim() : "";

  const normalized: ClientAuthState = {
    sessionId,
    accessToken,
    refreshToken: refreshTokenRaw || null,
    mode,
    updatedAt: typeof value.updatedAt === "string" && value.updatedAt ? value.updatedAt : new Date().toISOString(),
    userName: userNameRaw || null,
    expiresAt: expiresAtRaw || null,
  };

  if (mode === "custom" && isAuthExpired(normalized) && !normalized.refreshToken) {
    return null;
  }

  return normalized;
}

export function createGuestAuthState(): ClientAuthState {
  return {
    sessionId: generateSessionId(),
    accessToken: generateAccessToken(),
    refreshToken: null,
    mode: "local",
    updatedAt: new Date().toISOString(),
    userName: null,
    expiresAt: null,
  };
}

export function createAuthenticatedAuthState(payload: LoginPayload = {}): ClientAuthState {
  const token = payload.token?.trim() || generateAccessToken();
  const refreshToken = payload.refreshToken?.trim() || null;
  return {
    sessionId: payload.sessionId?.trim() || generateSessionId(),
    accessToken: token,
    refreshToken,
    mode: "custom",
    updatedAt: new Date().toISOString(),
    userName: payload.userName?.trim() || "候选人",
    expiresAt: normalizeExpiresAt(payload),
  };
}

export function clearClientAuthState(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
    window.localStorage.removeItem("career_hero.client_auth.v2");
  } catch {
    // ignore
  }
}

export function loadClientAuthState(): ClientAuthState | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY) ?? window.localStorage.getItem("career_hero.client_auth.v2");
    if (!raw) return null;

    const parsed = JSON.parse(raw) as unknown;
    const normalized = normalizeClientAuthState(parsed);
    if (!normalized) {
      clearClientAuthState();
      return null;
    }

    return normalized;
  } catch {
    return null;
  }
}

export function saveClientAuthState(state: ClientAuthState): ClientAuthState {
  if (typeof window === "undefined") return state;

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore storage failure and keep in-memory fallback
  }

  return state;
}

export function getOrCreateClientAuthState(): ClientAuthState {
  const current = loadClientAuthState();
  if (current) {
    return current;
  }

  const guest = createGuestAuthState();
  return saveClientAuthState(guest);
}

function buildInitWithAuth(init: RequestInit | undefined, authState: ClientAuthState): RequestInit {
  const headers = new Headers(init?.headers);
  headers.set("x-session-id", authState.sessionId);
  headers.set("authorization", `Bearer ${authState.accessToken}`);
  headers.set("x-session-token", authState.accessToken);
  headers.set("x-client-auth-mode", authState.mode);

  if (authState.userName) {
    headers.set("x-user-name", encodeURIComponent(authState.userName));
  }

  return {
    ...init,
    headers,
    credentials: init?.credentials ?? "include",
  };
}

function syncAuthStateFromResponse(current: ClientAuthState, response: Response): ClientAuthState {
  const responseSessionId = response.headers.get("x-session-id")?.trim();
  if (!responseSessionId || responseSessionId === current.sessionId) {
    return current;
  }

  return saveClientAuthState({
    ...current,
    sessionId: responseSessionId,
    updatedAt: new Date().toISOString(),
  });
}

export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  authState?: ClientAuthState,
): Promise<{ response: Response; authState: ClientAuthState }> {
  const current = authState ?? getOrCreateClientAuthState();
  const response = await fetch(input, buildInitWithAuth(init, current));
  const next = syncAuthStateFromResponse(current, response);
  return { response, authState: next };
}

function toSafeReturnTo(value: string | null): string {
  if (!value) return "/";
  if (!value.startsWith("/")) return "/";
  if (value.startsWith("//")) return "/";
  return value;
}

export function getCurrentReturnToPath(): string {
  if (typeof window === "undefined") return "/";
  return toSafeReturnTo(`${window.location.pathname}${window.location.search}`);
}

export function buildLoginHref(returnTo: string, reason?: AuthFailureReason): string {
  const query = new URLSearchParams({ returnTo: toSafeReturnTo(returnTo) });
  if (reason) {
    query.set("reason", reason);
  }
  return `/login?${query.toString()}`;
}

function shouldSkipAutoRedirect(loginPath: string): boolean {
  if (typeof window === "undefined") return true;
  const pathname = window.location.pathname || "/";
  return pathname === loginPath || pathname.startsWith(`${loginPath}/`);
}

function formatExpiresHint(state: ClientAuthState | null): string {
  if (!state) return "未初始化";
  if (state.mode !== "custom") return "未登录（访客模式）";
  if (!state.expiresAt) return "已登录（长期有效）";

  const expiresAt = new Date(state.expiresAt).getTime();
  if (!Number.isFinite(expiresAt)) return "已登录（有效期未知）";

  const deltaMs = expiresAt - Date.now();
  if (deltaMs <= 0) {
    return state.refreshToken
      ? "登录已过期，将在下一次请求时自动尝试 refresh"
      : "登录已过期，请重新登录";
  }

  const totalMinutes = Math.ceil(deltaMs / 60_000);
  if (totalMinutes < 60) {
    return `已登录（约 ${totalMinutes} 分钟后过期）`;
  }

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `已登录（约 ${hours} 小时${minutes > 0 ? `${minutes} 分钟` : ""}后过期）`;
}

function extractTokenFromPayload(payload: unknown): string | null {
  if (!isRecord(payload)) return null;

  const directKeys = ["accessToken", "access_token", "token", "jwt", "sessionToken"];
  for (const key of directKeys) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  const nestedKeys = ["data", "item", "session", "auth"];
  for (const key of nestedKeys) {
    const nested = payload[key];
    const nestedToken = extractTokenFromPayload(nested);
    if (nestedToken) return nestedToken;
  }

  return null;
}

function extractRefreshTokenFromPayload(payload: unknown): string | null {
  if (!isRecord(payload)) return null;

  const directKeys = ["refreshToken", "refresh_token", "refresh"];
  for (const key of directKeys) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  const nestedKeys = ["data", "item", "session", "auth"];
  for (const key of nestedKeys) {
    const nested = payload[key];
    const nestedToken = extractRefreshTokenFromPayload(nested);
    if (nestedToken) return nestedToken;
  }

  return null;
}

function extractExpiresAtFromPayload(payload: unknown): string | null {
  if (!isRecord(payload)) return null;

  const directKeys = ["expiresAt", "expires_at", "expiry", "expiredAt"];
  for (const key of directKeys) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  const expiresIn = payload.expiresIn ?? payload.expires_in ?? payload.ttlSeconds;
  if (typeof expiresIn === "number" && Number.isFinite(expiresIn) && expiresIn > 0) {
    return new Date(Date.now() + expiresIn * 1000).toISOString();
  }

  return null;
}

function extractUserNameFromPayload(payload: unknown): string | null {
  if (!isRecord(payload)) return null;

  const directKeys = ["userName", "username", "name"];
  for (const key of directKeys) {
    const candidate = payload[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  const user = payload.user;
  if (isRecord(user)) {
    return extractUserNameFromPayload(user);
  }

  return null;
}

async function readJsonSafe(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return null;
  }
  return response.json().catch(() => null);
}

async function tryAutoRefreshAuth(
  apiBaseUrl: string,
  current: ClientAuthState,
): Promise<ClientAuthState | null> {
  if (!current.refreshToken) {
    return null;
  }

  const endpoints = [
    "/api/auth/refresh",
    "/api/auth/token/refresh",
    "/api/auth/session/refresh",
    "/api/auth/login/refresh",
  ];

  const payloads: Record<string, unknown>[] = [
    { refreshToken: current.refreshToken, sessionId: current.sessionId },
    { refresh_token: current.refreshToken, session_id: current.sessionId },
    { token: current.refreshToken, sessionId: current.sessionId },
  ];

  for (const endpoint of endpoints) {
    for (const payload of payloads) {
      try {
        const response = await fetch(`${apiBaseUrl}${endpoint}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-session-id": current.sessionId,
          },
          credentials: "include",
          body: JSON.stringify(payload),
        });

        if (response.status === 404 || response.status === 405) {
          break;
        }

        const data = await readJsonSafe(response);
        if (!response.ok) {
          continue;
        }

        const nextAccessToken = extractTokenFromPayload(data);
        if (!nextAccessToken) {
          continue;
        }

        const nextRefreshToken = extractRefreshTokenFromPayload(data) || current.refreshToken;
        const nextExpiresAt = extractExpiresAtFromPayload(data) || current.expiresAt;
        const nextUserName = extractUserNameFromPayload(data) || current.userName;
        const nextSessionId = response.headers.get("x-session-id")?.trim() || current.sessionId;

        return {
          ...current,
          sessionId: nextSessionId,
          accessToken: nextAccessToken,
          refreshToken: nextRefreshToken,
          expiresAt: nextExpiresAt,
          userName: nextUserName,
          updatedAt: new Date().toISOString(),
          mode: "custom",
        };
      } catch {
        continue;
      }
    }
  }

  return null;
}

export type UseClientAuthResult = {
  authState: ClientAuthState | null;
  authReady: boolean;
  isAuthenticated: boolean;
  authStatusText: string;
  tokenDraft: string;
  setTokenDraft: Dispatch<SetStateAction<string>>;
  authFailureReason: AuthFailureReason | null;
  login: (payload?: LoginPayload) => ClientAuthState;
  logout: () => ClientAuthState;
  applyAccessToken: (token: string) => ClientAuthState;
  rotateSession: () => ClientAuthState;
  resetAuthState: () => ClientAuthState;
  clearAuthFailureReason: () => void;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
};

export function useClientAuth(apiBaseUrl: string, options: UseClientAuthOptions = {}): UseClientAuthResult {
  const [authReady, setAuthReady] = useState(false);
  const [authState, setAuthState] = useState<ClientAuthState | null>(null);
  const [tokenDraft, setTokenDraft] = useState("");
  const [authFailureReason, setAuthFailureReason] = useState<AuthFailureReason | null>(null);

  const authRef = useRef<ClientAuthState | null>(null);
  const autoRedirectOnUnauthorized = options.autoRedirectOnUnauthorized ?? false;
  const loginPath = options.loginPath?.trim() || "/login";

  useEffect(() => {
    const loaded = loadClientAuthState();
    const current = loaded ?? createGuestAuthState();
    const saved = saveClientAuthState(current);
    authRef.current = saved;
    setAuthState(saved);
    setTokenDraft(saved.accessToken);

    if (typeof window !== "undefined") {
      const reasonParam = new URLSearchParams(window.location.search).get("reason");
      setAuthFailureReason(toSafeAuthReason(reasonParam));
    }

    setAuthReady(true);
  }, []);

  const persistState = useCallback((next: ClientAuthState) => {
    const expiredWithoutRefresh = next.mode === "custom" && isAuthExpired(next) && !next.refreshToken;
    const fallbackToGuest = expiredWithoutRefresh ? createGuestAuthState() : next;
    const saved = saveClientAuthState(fallbackToGuest);
    authRef.current = saved;
    setAuthState(saved);
    setTokenDraft(saved.accessToken);
    return saved;
  }, []);

  const ensureAuthState = useCallback(() => {
    const current = authRef.current;
    if (current) {
      if (current.mode === "custom" && isAuthExpired(current) && !current.refreshToken) {
        const guest = createGuestAuthState();
        return persistState(guest);
      }
      return current;
    }

    const fallback = getOrCreateClientAuthState();
    authRef.current = fallback;
    setAuthState(fallback);
    setTokenDraft(fallback.accessToken);
    return fallback;
  }, [persistState]);

  const apiFetch = useCallback(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestInput = toAbsoluteInput(apiBaseUrl, input);
      const current = ensureAuthState();

      let result = await authFetch(requestInput, init, current);

      if (
        result.authState.sessionId !== current.sessionId ||
        result.authState.accessToken !== current.accessToken ||
        result.authState.mode !== current.mode ||
        result.authState.updatedAt !== current.updatedAt ||
        result.authState.refreshToken !== current.refreshToken
      ) {
        persistState(result.authState);
      }

      if (result.response.status !== 401 || current.mode !== "custom") {
        return result.response;
      }

      const hadRefreshToken = Boolean(current.refreshToken);
      const refreshCandidate = hadRefreshToken ? await tryAutoRefreshAuth(apiBaseUrl, current) : null;
      if (refreshCandidate) {
        const refreshedState = persistState(refreshCandidate);
        setAuthFailureReason(null);
        const retry = await authFetch(requestInput, init, refreshedState);
        if (retry.response.ok || retry.response.status !== 401) {
          persistState(retry.authState);
          return retry.response;
        }
        result = retry;
      }

      const reason: AuthFailureReason = hadRefreshToken
        ? "refresh_failed"
        : isAuthExpired(current)
          ? "expired"
          : "unauthorized";
      setAuthFailureReason(reason);
      persistState(createGuestAuthState());

      if (autoRedirectOnUnauthorized && !shouldSkipAutoRedirect(loginPath)) {
        const href = buildLoginHref(getCurrentReturnToPath(), reason);
        window.location.replace(href);
      }

      return result.response;
    },
    [apiBaseUrl, autoRedirectOnUnauthorized, ensureAuthState, loginPath, persistState],
  );

  const login = useCallback(
    (payload: LoginPayload = {}) => {
      setAuthFailureReason(null);
      const next = createAuthenticatedAuthState(payload);
      return persistState(next);
    },
    [persistState],
  );

  const logout = useCallback(() => {
    setAuthFailureReason(null);
    const next = createGuestAuthState();
    return persistState(next);
  }, [persistState]);

  const applyAccessToken = useCallback(
    (token: string) => {
      const normalized = token.trim();
      if (!normalized) {
        return logout();
      }

      const current = ensureAuthState();
      const next: ClientAuthState = {
        ...current,
        accessToken: normalized,
        mode: "custom",
        updatedAt: new Date().toISOString(),
        userName: current.userName || "候选人",
      };
      setAuthFailureReason(null);
      return persistState(next);
    },
    [ensureAuthState, logout, persistState],
  );

  const rotateSession = useCallback(() => {
    const current = ensureAuthState();
    const next = {
      ...current,
      sessionId: generateSessionId(),
      updatedAt: new Date().toISOString(),
    };
    return persistState(next);
  }, [ensureAuthState, persistState]);

  const resetAuthState = useCallback(() => {
    setAuthFailureReason(null);
    return logout();
  }, [logout]);

  const clearAuthFailureReason = useCallback(() => {
    setAuthFailureReason(null);
  }, []);

  const isAuthenticated = useMemo(() => isClientAuthenticated(authState), [authState]);
  const authStatusText = useMemo(() => formatExpiresHint(authState), [authState]);

  return {
    authState,
    authReady,
    isAuthenticated,
    authStatusText,
    tokenDraft,
    setTokenDraft,
    authFailureReason,
    login,
    logout,
    applyAccessToken,
    rotateSession,
    resetAuthState,
    clearAuthFailureReason,
    apiFetch,
  };
}
