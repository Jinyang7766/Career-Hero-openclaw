# 知识库接口规格说明（Knowledge Base Spec）

> 版本：v0.1（Wave5 文档交付轨）
>
> 依赖文档：`docs/rag-retriever-spec.md`
>
> 目标：冻结 Knowledge Base 的核心对象与 API 契约，作为 Wave6 实现与联调基线。

---

## 1. 设计目标

1. 建立统一知识资产模型（库 -> 文档 -> 切块 -> 索引任务）。
2. 支持 JD/Resume/通用知识片段的统一检索入口。
3. 保证检索失败可降级，不阻断 analyze 主流程。
4. 为发布与回滚提供配置、索引版本、可观测性检查点。

---

## 2. 范围与非目标

### 2.1 范围（v0.1）
- Knowledge Base 管理（创建、查询、更新状态）。
- 文档入库（手工文本、JD 关联、Resume 关联）。
- 索引任务（创建、查询）。
- 检索接口与返回字段（`hits/retrievalMeta`）。

### 2.2 非目标（v0.1）
- 不覆盖多租户权限模型。
- 不覆盖在线学习与自动标注。
- 不覆盖复杂文档解析（PDF/OCR）引擎细节。

---

## 3. 核心对象与字段

## 3.1 `KnowledgeBase`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `knowledgeBaseId` | string | 是 | 知识库 ID |
| `name` | string | 是 | 名称 |
| `scope` | enum | 是 | `global/team/personal` |
| `status` | enum | 是 | `ACTIVE/INACTIVE/ARCHIVED` |
| `description` | string | 否 | 描述 |
| `defaultTopK` | integer | 否 | 默认检索数量 |
| `minScore` | number | 否 | 默认阈值 |
| `createdAt` | string | 是 | 创建时间 |
| `updatedAt` | string | 是 | 更新时间 |

## 3.2 `KnowledgeDocument`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `documentId` | string | 是 | 文档 ID |
| `knowledgeBaseId` | string | 是 | 所属知识库 |
| `title` | string | 是 | 标题 |
| `sourceType` | enum | 是 | `manual/jd/resume/url` |
| `sourceId` | string | 否 | 关联 JD/Resume ID |
| `versionNo` | integer | 否 | 关联版本号 |
| `language` | string | 否 | 默认 `zh-CN` |
| `tags` | string[] | 否 | 标签 |
| `status` | enum | 是 | `DRAFT/READY/INDEXING/FAILED/ARCHIVED` |
| `contentHash` | string | 否 | 内容摘要 hash |
| `createdAt` | string | 是 | 创建时间 |
| `updatedAt` | string | 是 | 更新时间 |

## 3.3 `KnowledgeChunk`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chunkId` | string | 是 | 切块 ID |
| `documentId` | string | 是 | 所属文档 |
| `chunkIndex` | integer | 是 | 片段序号 |
| `text` | string | 是 | 片段文本 |
| `tokenCount` | integer | 否 | token 数 |
| `embeddingRef` | string | 否 | 向量存储引用 |
| `metadata` | object | 否 | 自定义元数据 |

## 3.4 `IndexJob`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `jobId` | string | 是 | 任务 ID |
| `knowledgeBaseId` | string | 是 | 目标知识库 |
| `targetType` | enum | 是 | `knowledge_base/document` |
| `targetId` | string | 是 | 目标对象 ID |
| `indexVersion` | string | 是 | 索引版本号 |
| `status` | enum | 是 | `PENDING/RUNNING/SUCCESS/FAILED/CANCELLED` |
| `errorMessage` | string | 否 | 失败原因 |
| `createdAt` | string | 是 | 创建时间 |
| `updatedAt` | string | 是 | 更新时间 |

## 3.5 `RetrievalHit`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chunkId` | string | 是 | 命中 chunk |
| `documentId` | string | 是 | 来源文档 |
| `knowledgeBaseId` | string | 是 | 来源知识库 |
| `score` | number | 是 | 相似度分数 |
| `text` | string | 是 | 命中文本 |
| `sourceType` | enum | 是 | `manual/jd/resume/url` |
| `sourceId` | string | 否 | 来源业务对象 ID |
| `versionNo` | integer | 否 | 来源对象版本 |

## 3.6 `RetrievalMeta`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `enabled` | boolean | 是 | 是否开启检索 |
| `degraded` | boolean | 是 | 是否降级 |
| `topK` | integer | 是 | 检索参数 |
| `minScore` | number | 是 | 阈值参数 |
| `hitCount` | integer | 是 | 命中数量 |
| `durationMs` | number | 是 | 检索耗时 |
| `indexVersion` | string | 否 | 实际使用索引版本 |
| `reason` | string | 否 | 降级或异常原因 |

---

## 4. API 定义

> 错误返回统一结构：`code/message/requestId`。

## 4.1 创建知识库
`POST /api/knowledge/bases`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 知识库名称 |
| `scope` | enum | 否 | 默认 `team` |
| `description` | string | 否 | 描述 |
| `defaultTopK` | integer | 否 | 默认检索数量 |
| `minScore` | number | 否 | 默认阈值 |

### 200 响应
- `knowledgeBase`（`KnowledgeBase`）
- `requestId`

---

## 4.2 查询知识库列表
`GET /api/knowledge/bases`

### Query 参数
- `scope?`
- `status?`
- `page?` / `pageSize?`

### 200 响应
- `items: KnowledgeBase[]`
- `total`
- `requestId`

---

## 4.3 更新知识库状态
`PATCH /api/knowledge/bases/{knowledgeBaseId}`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 否 | 名称 |
| `description` | string | 否 | 描述 |
| `status` | enum | 否 | `ACTIVE/INACTIVE/ARCHIVED` |
| `defaultTopK` | integer | 否 | 默认参数 |
| `minScore` | number | 否 | 默认参数 |

### 200 响应
- `knowledgeBase`（更新后）
- `requestId`

---

## 4.4 添加知识文档
`POST /api/knowledge/bases/{knowledgeBaseId}/documents`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 是 | 标题 |
| `sourceType` | enum | 是 | `manual/jd/resume/url` |
| `content` | string | 否 | 手工文本内容 |
| `sourceId` | string | 否 | 关联 JD/Resume ID |
| `versionNo` | integer | 否 | 关联版本 |
| `tags` | string[] | 否 | 标签 |

### 200 响应
- `document`（`KnowledgeDocument`）
- `requestId`

### 异常
- `400 BAD_REQUEST`（content/source 参数冲突）
- `404 KNOWLEDGE_BASE_NOT_FOUND`

---

## 4.5 查询知识文档
`GET /api/knowledge/bases/{knowledgeBaseId}/documents`

### Query 参数
- `status?`
- `sourceType?`
- `page?` / `pageSize?`

### 200 响应
- `items: KnowledgeDocument[]`
- `total`
- `requestId`

---

## 4.6 创建索引任务
`POST /api/knowledge/index/jobs`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `knowledgeBaseId` | string | 是 | 知识库 ID |
| `targetType` | enum | 是 | `knowledge_base/document` |
| `targetId` | string | 是 | 目标 ID |
| `indexVersion` | string | 否 | 可选，不传则系统生成 |
| `forceRebuild` | boolean | 否 | 默认 false |

### 200 响应
- `job`（`IndexJob`）
- `requestId`

---

## 4.7 查询索引任务
`GET /api/knowledge/index/jobs/{jobId}`

### 200 响应
- `job`（`IndexJob`）
- `requestId`

---

## 4.8 知识检索
`POST /api/knowledge/retrieve`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 检索语句 |
| `knowledgeBaseIds` | string[] | 否 | 目标知识库集合 |
| `resumeId` | string | 否 | 关联简历 |
| `resumeVersionNo` | integer | 否 | 关联简历版本 |
| `jdId` | string | 否 | 关联 JD |
| `jdVersionNo` | integer | 否 | 关联 JD 版本 |
| `topK` | integer | 否 | 覆盖默认值 |
| `minScore` | number | 否 | 覆盖默认值 |

### 200 响应
- `hits: RetrievalHit[]`
- `retrievalMeta: RetrievalMeta`
- `requestId`

### 降级约束
- 检索失败时不返回 500（除系统性故障），优先返回：
  - `hits=[]`
  - `retrievalMeta.degraded=true`
  - `retrievalMeta.reason=<具体原因>`

---

## 4.9 与 analyze 集成（建议）

`POST /api/analyze` 可选入参：
- `enableRetrieval`
- `knowledgeBaseIds`

建议响应附带：
- `retrievalMeta`
- `evidence[]`（可映射 `RetrievalHit` 关键字段）

---

## 5. 配置与发布基线（建议）

- `CAREER_HERO_KB_ENABLED`（是否启用知识库）
- `CAREER_HERO_KB_INDEX_VERSION`（默认索引版本）
- `CAREER_HERO_KB_DEFAULT_TOP_K`
- `CAREER_HERO_KB_MIN_SCORE`
- `CAREER_HERO_KB_WRITE_MODE`（`readwrite/readonly`）

---

## 6. 可观测性建议

关键指标：
- 知识文档入库成功率
- 索引任务成功率/平均耗时
- 检索命中率（`hitCount > 0`）
- degraded 比例
- 检索延迟 p95

建议日志字段：
- `requestId`, `sessionId`, `knowledgeBaseIds`, `hitCount`, `topScore`, `degraded`, `durationMs`, `indexVersion`

---

## 7. Wave5 文档验收建议

- [ ] Knowledge Base 对象字段与状态机已冻结并通过评审。
- [ ] `/api/knowledge/*` 请求/响应字段可直接生成联调 stub。
- [ ] 与 `rag-retriever-spec`、`auth-api-spec` 术语一致。
- [ ] release-checklist 与 rollback-runbook 已覆盖 KB 专项。
