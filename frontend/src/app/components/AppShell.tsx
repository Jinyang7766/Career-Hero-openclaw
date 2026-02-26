"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect } from "react";

type AppShellProps = {
  children: ReactNode;
};

type TabIcon = "home" | "resume" | "knowledge" | "interview" | "profile";

type TabItem = {
  href: string;
  label: string;
  icon: TabIcon;
  matchPrefixes?: string[];
};

type RouteMeta = {
  prefix: string;
  title: string;
  sectionHref: string;
  sectionLabel: string;
  tagline: string;
};

const DEV_MODE_STORAGE_KEY = "career_hero.dev_mode.v1";

function parseDevSwitch(raw: string | null): boolean | null {
  if (!raw) return null;
  const normalized = raw.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return null;
}

function resolveDeveloperMode(): boolean {
  if (typeof window === "undefined") return false;

  let queryMode: boolean | null = null;
  try {
    queryMode = parseDevSwitch(new URLSearchParams(window.location.search).get("dev"));
  } catch {
    queryMode = null;
  }

  if (queryMode !== null) {
    try {
      if (queryMode) {
        window.localStorage.setItem(DEV_MODE_STORAGE_KEY, "1");
      } else {
        window.localStorage.removeItem(DEV_MODE_STORAGE_KEY);
      }
    } catch {
      // ignore storage write errors
    }
    return queryMode;
  }

  try {
    return parseDevSwitch(window.localStorage.getItem(DEV_MODE_STORAGE_KEY)) ?? false;
  } catch {
    return false;
  }
}

const TAB_ITEMS: TabItem[] = [
  { href: "/", label: "首页", icon: "home" },
  { href: "/resumes", label: "简历", icon: "resume", matchPrefixes: ["/resumes"] },
  { href: "/rag", label: "知识库", icon: "knowledge", matchPrefixes: ["/rag"] },
  { href: "/interview", label: "面试", icon: "interview", matchPrefixes: ["/interview"] },
  { href: "/login", label: "我的", icon: "profile", matchPrefixes: ["/login"] },
];

const ROUTE_META: RouteMeta[] = [
  { prefix: "/interview/summary", title: "面试总结", sectionHref: "/interview", sectionLabel: "面试", tagline: "复盘表现，继续提升" },
  { prefix: "/interview", title: "面试练习", sectionHref: "/interview", sectionLabel: "面试", tagline: "随时开练，稳住节奏" },
  { prefix: "/resumes", title: "简历中心", sectionHref: "/resumes", sectionLabel: "简历", tagline: "管理你的简历资产" },
  { prefix: "/rag", title: "知识库", sectionHref: "/rag", sectionLabel: "知识库", tagline: "喂给系统更多行业知识" },
  { prefix: "/login", title: "我的", sectionHref: "/login", sectionLabel: "我的", tagline: "账号与偏好设置" },
  { prefix: "/", title: "首页", sectionHref: "/", sectionLabel: "首页", tagline: "今天先把主线跑通" },
];

function normalizePath(pathname: string): string {
  if (!pathname) return "/";
  return pathname !== "/" && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
}

function matchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

function isTabActive(pathname: string, item: TabItem): boolean {
  if (item.href === "/") {
    return pathname === "/";
  }

  const prefixes = item.matchPrefixes ?? [item.href];
  return prefixes.some((prefix) => matchesPrefix(pathname, prefix));
}

function resolveRoute(pathname: string) {
  const matched = ROUTE_META.find((route) => matchesPrefix(pathname, route.prefix));

  if (matched) {
    return matched;
  }

  return {
    prefix: pathname,
    title: "职途助手",
    sectionHref: "/",
    sectionLabel: "首页",
    tagline: "求职流程助手",
  } as RouteMeta;
}

function TabGlyph({ icon }: { icon: TabIcon }) {
  if (icon === "home") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 10.5L12 3l9 7.5" />
        <path d="M6.5 9.5V20h11V9.5" />
      </svg>
    );
  }

  if (icon === "resume") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 4h8l4 4v12H7z" />
        <path d="M15 4v4h4" />
        <path d="M10 12h6M10 16h6" />
      </svg>
    );
  }

  if (icon === "knowledge") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 6.5A2.5 2.5 0 0 1 7.5 4H19v14H7.5A2.5 2.5 0 0 0 5 20V6.5z" />
        <path d="M5 20h14" />
        <path d="M9 8h6M9 11h6" />
      </svg>
    );
  }

  if (icon === "interview") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3a4.2 4.2 0 0 1 4.2 4.2v4.5A4.2 4.2 0 0 1 12 16a4.2 4.2 0 0 1-4.2-4.3V7.2A4.2 4.2 0 0 1 12 3z" />
        <path d="M5 11.5a7 7 0 0 0 14 0" />
        <path d="M12 18v3" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 4.2a4.2 4.2 0 1 1 0 8.4 4.2 4.2 0 0 1 0-8.4z" />
      <path d="M4.8 20a7.2 7.2 0 0 1 14.4 0" />
    </svg>
  );
}

export default function AppShell({ children }: AppShellProps) {
  const pathname = normalizePath(usePathname() || "/");
  const route = resolveRoute(pathname);
  const isTopLevelPage = TAB_ITEMS.some((tab) => tab.href === pathname);

  useEffect(() => {
    if (typeof document === "undefined") return;

    const applyDevMode = () => {
      const enabled = resolveDeveloperMode();
      document.body.dataset.devMode = enabled ? "1" : "0";
    };

    applyDevMode();
    window.addEventListener("popstate", applyDevMode);

    return () => {
      window.removeEventListener("popstate", applyDevMode);
    };
  }, [pathname]);

  return (
    <div className="app-shell">
      <header className="app-shell__topbar" aria-label="应用顶部导航">
        <div className="app-shell__statusbar" aria-hidden="true" />

        <div className="app-shell__navbar">
          {isTopLevelPage ? (
            <span className="app-shell__nav-spacer" aria-hidden="true" />
          ) : (
            <Link
              href={route.sectionHref}
              className="app-shell__nav-back"
              aria-label={`返回${route.sectionLabel}`}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M14.5 6.5L9 12l5.5 5.5" />
              </svg>
              <span>{route.sectionLabel}</span>
            </Link>
          )}

          <div className="app-shell__nav-center">
            <div className="app-shell__nav-meta">
              <h1 className="app-shell__nav-title">{route.title}</h1>
              <p className="app-shell__nav-brand">{route.tagline}</p>
            </div>
          </div>

          <div className="app-shell__nav-right" aria-hidden="true">
            <span className="app-shell__nav-pill">Beta</span>
          </div>
        </div>
      </header>

      <main className="app-shell__content" id="app-main">
        <div className="app-shell__container">{children}</div>
      </main>

      <nav className="app-shell__tabbar" aria-label="应用底部导航">
        <div className="app-shell__tabbar-inner">
          {TAB_ITEMS.map((item) => {
            const active = isTabActive(pathname, item);

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`app-shell__tab ${active ? "is-active" : ""}`.trim()}
                aria-current={active ? "page" : undefined}
              >
                <span className="app-shell__tab-indicator" aria-hidden="true" />
                <span className="app-shell__tab-icon" aria-hidden="true">
                  <TabGlyph icon={item.icon} />
                </span>
                <span className="app-shell__tab-label">{item.label}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
