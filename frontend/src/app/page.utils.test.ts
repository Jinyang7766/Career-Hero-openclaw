import { describe, expect, it } from "vitest";

import { formatDateTime, toErrorMessage, toResultText } from "./page.utils";

describe("page utils", () => {
  it("toErrorMessage should include requestId", () => {
    const message = toErrorMessage({
      code: "BAD_REQUEST",
      message: "请求错误",
      requestId: "req-1",
    });

    expect(message).toContain("请求错误");
    expect(message).toContain("req-1");
  });

  it("toErrorMessage should fallback for invalid payload", () => {
    expect(toErrorMessage(null)).toBe("分析失败，请稍后重试");
  });

  it("toResultText should include structured sections", () => {
    const text = toResultText({
      score: 88,
      matchedKeywords: ["python"],
      missingKeywords: ["redis"],
      suggestions: ["补充缓存项目"],
      optimizedResume: "优化版简历",
      scoreBreakdown: {
        keyword_match: 90,
        coverage: 85,
        writing_quality_stub: 70,
      },
      insights: {
        summary: "整体匹配较高",
        strengths: ["后端经验"],
        risks: ["缓存经验不足"],
      },
      pipAdvice: ["两周内补齐 Redis 项目"],
      defectCategories: [{ name: "项目量化不足", count: 2, details: ["缺少性能指标"] }],
      ragEnabled: true,
      ragHits: [{ title: "JD段落#1", snippet: "需要熟悉缓存与高并发", score: 0.91 }],
      analysisSource: "gemini",
      fallbackUsed: false,
      promptVersion: "v2",
      requestId: "req-2",
    });

    expect(text).toContain("匹配分：88/100");
    expect(text).toContain("insights");
    expect(text).toContain("优势：后端经验");
    expect(text).toContain("风险：缓存经验不足");
    expect(text).toContain("pipAdvice");
    expect(text).toContain("缺陷分类");
    expect(text).toContain("RAG Hits");
    expect(text).toContain("优化版简历");
  });

  it("formatDateTime should return original when invalid", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});
