import type { Metadata, Viewport } from "next";
import "./globals.css";
import AppShell from "./components/AppShell";

export const metadata: Metadata = {
  title: {
    default: "职途助手",
    template: "%s · 职途助手",
  },
  description: "Career Hero 前端：分析、简历、RAG、面试",
  applicationName: "职途助手",
  appleWebApp: {
    capable: true,
    title: "职途助手",
    statusBarStyle: "default",
  },
  formatDetection: {
    telephone: false,
    date: false,
    address: false,
    email: false,
    url: false,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f3f6fb" },
    { media: "(prefers-color-scheme: dark)", color: "#111b31" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="app-root">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
