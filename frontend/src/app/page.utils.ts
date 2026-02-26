export type ScoreBreakdown = {
  keyword_match: number;
  coverage: number;
  writing_quality_stub: number;
};

export type AnalysisInsights = {
  summary: string;
  strengths: string[];
  risks: string[];
};

export type DefectCategory = {
  name: string;
  count?: number;
  details?: string[];
};

export type RagHit = {
  title: string;
  snippet: string;
  score?: number;
  source?: string;
};

export type AnalyzeResponse = {
  score: number;
  matchedKeywords: string[];
  missingKeywords: string[];
  suggestions: string[];
  optimizedResume: string;
  scoreBreakdown?: ScoreBreakdown;
  insights?: AnalysisInsights;
  analysisSource?: "rule" | "gemini";
  fallbackUsed?: boolean;
  promptVersion?: string;
  historyId?: number;
  requestId?: string;
  pipAdvice?: string[];
  defectCategories?: DefectCategory[];
  ragEnabled?: boolean;
  ragHits?: RagHit[];
  retrievalMode?: "keyword" | "vector";
  retrievalSource?: string;
};

export type ApiErrorPayload = {
  code?: string;
  message?: string;
  requestId?: string;
  detail?: string;
};

const TECHNICAL_ERROR_PATTERNS = [
  /failed to fetch/i,
  /network\s*error/i,
  /network request failed/i,
  /typeerror/i,
  /cors/i,
  /econn|enotfound|ehost|etimedout/i,
  /unexpected token/i,
  /json/i,
  /request failed\s*\(\d{3}\)/i,
  /请求失败（\d{3}）/,
];

function containsTechnicalDetail(message: string): boolean {
  return TECHNICAL_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

export function toUserFriendlyError(raw: unknown, fallback: string): string {
  const source = typeof raw === "string"
    ? raw
    : raw instanceof Error
      ? raw.message
      : "";

  const message = source.trim();
  if (!message) return fallback;
  if (containsTechnicalDetail(message)) return fallback;
  return message;
}

export function toErrorMessage(payload: unknown): string {
  if (!payload || typeof payload !== "object") {
    return "分析失败，请稍后重试";
  }

  const error = payload as ApiErrorPayload;
  if (error.message) {
    const message = error.requestId
      ? `${error.message} (requestId: ${error.requestId})`
      : error.message;
    return toUserFriendlyError(message, "分析失败，请稍后重试");
  }

  if (typeof error.detail === "string" && error.detail) {
    return toUserFriendlyError(error.detail, "分析失败，请稍后重试");
  }

  return "分析失败，请稍后重试";
}

export function toResultText(result: AnalyzeResponse): string {
  const breakdown = result.scoreBreakdown
    ? [
        `- keyword_match: ${result.scoreBreakdown.keyword_match}/100`,
        `- coverage: ${result.scoreBreakdown.coverage}/100`,
        `- writing_quality_stub: ${result.scoreBreakdown.writing_quality_stub}/100`,
      ]
    : ["- 无"];

  const insights = result.insights
    ? [
        `总结：${result.insights.summary || "无"}`,
        `优势：${result.insights.strengths.join("、") || "无"}`,
        `风险：${result.insights.risks.join("、") || "无"}`,
      ]
    : ["总结：无", "优势：无", "风险：无"];

  const pipAdvice = result.pipAdvice?.length
    ? result.pipAdvice.map((item, index) => `${index + 1}. ${item}`)
    : ["无"];

  const defectCategories = result.defectCategories?.length
    ? result.defectCategories.map((item) => {
        const suffix = item.count !== undefined ? ` (${item.count})` : "";
        const details = item.details?.length ? `：${item.details.join("、")}` : "";
        return `- ${item.name}${suffix}${details}`;
      })
    : ["- 无"];

  const ragHits = result.ragHits?.length
    ? result.ragHits.map((item, index) => {
        const scorePart = typeof item.score === "number" ? `；score=${item.score.toFixed(3)}` : "";
        const sourcePart = item.source ? `；source=${item.source}` : "";
        return `${index + 1}. ${item.title || "未命名片段"}：${item.snippet || "(无摘要)"}${scorePart}${sourcePart}`;
      })
    : ["无"];

  const lines = [
    `匹配分：${result.score}/100`,
    `分析来源：${result.analysisSource || "rule"}`,
    `降级：${result.fallbackUsed ? "是" : "否"}`,
    `promptVersion：${result.promptVersion || "unknown"}`,
    result.requestId ? `requestId：${result.requestId}` : "",
    `ragEnabled：${result.ragEnabled ? "true" : "false"}`,
    `retrievalMode：${result.retrievalMode || "keyword"}`,
    result.retrievalSource ? `retrievalSource：${result.retrievalSource}` : "",
    "",
    "scoreBreakdown:",
    ...breakdown,
    "",
    "insights:",
    ...insights,
    "",
    "pipAdvice:",
    ...pipAdvice,
    "",
    "缺陷分类:",
    ...defectCategories,
    "",
    "RAG Hits:",
    ...ragHits,
    "",
    `匹配关键词：${result.matchedKeywords.join(", ") || "无"}`,
    `缺失关键词：${result.missingKeywords.join(", ") || "无"}`,
    "",
    "优化建议：",
    ...result.suggestions.map((item, index) => `${index + 1}. ${item}`),
    "",
    "优化版简历草稿：",
    result.optimizedResume,
  ];

  return lines.filter(Boolean).join("\n");
}

export function formatDateTime(input: string): string {
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) {
    return input;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}
