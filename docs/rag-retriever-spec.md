# RAG 检索器规格说明（RAG Retriever Spec）

> 版本：v0.1（Wave4 文档冻结版）
>
> 目标：为 JD/Resume 驱动的分析链路提供可追溯、可降级、可观测的检索增强能力。

---

## 1. 设计目标

1. 对 Resume/JD 的版本内容建立可复用索引，减少重复解析成本。
2. 在 analyze 前增加“证据召回层”，提升建议一致性与解释性。
3. 检索失败时自动降级，不能阻塞主流程可用性。
4. 为后续评测（命中率、延迟、成本）提供统一指标口径。

---

## 2. 适用范围

### 2.1 数据来源（v0.1）
- Resume 版本文本（`resume_id + version_no`）
- JD 版本文本（`jd_id + version_no`）
- 可选：内置建议知识片段（模板库）

### 2.2 非目标
- 不做跨租户知识共享。
- 不做复杂在线学习与自动权重训练。

---

## 3. 架构分层

1. **Ingestion（入库）**
   - 规范化 -> 切块 -> 向量化 -> 索引写入
2. **Retrieval（检索）**
   - Query 构建 -> 向量召回 -> 过滤 -> 重排
3. **Context Pack（上下文打包）**
   - 预算裁剪 -> 去重 -> 组装证据段
4. **Fallback（降级）**
   - 检索失败时回退至 rule/gemini 直推链路

---

## 4. 数据模型建议

### 4.1 `retrieval_documents`
- `doc_id`
- `source_type`（resume/jd/knowledge）
- `source_id`（如 resume_id/jd_id）
- `version_no`
- `content_hash`
- `created_at`

### 4.2 `retrieval_chunks`
- `chunk_id`
- `doc_id`
- `chunk_index`
- `chunk_text`
- `embedding_vector`（或外部索引引用）
- `token_count`
- `metadata_json`

### 4.3 `retrieval_jobs`
- `job_id`
- `job_type`（build/rebuild/delete）
- `target_type`, `target_id`, `version_no`
- `status`（PENDING/RUNNING/SUCCESS/FAILED）
- `error_message`
- `created_at`, `updated_at`

---

## 5. 切块与检索参数（建议默认值）

- Chunk 长度：350~500 tokens
- Overlap：60~100 tokens
- 向量召回 TopK：8
- 重排后保留：3~5 段
- 相似度阈值：0.45（可配置）
- 上下文总预算：1200~1800 tokens

> 注：具体阈值在 Wave5 通过离线样本集再调优。

---

## 6. API 草案

### 6.1 触发索引构建
- `POST /api/rag/index/resumes/{resumeId}/versions/{versionNo}`
- `POST /api/rag/index/jds/{jdId}/versions/{versionNo}`
- 返回：`jobId`, `status`

### 6.2 查询索引任务
- `GET /api/rag/index/jobs/{jobId}`

### 6.3 检索接口
- `POST /api/rag/retrieve`
- 入参（示例）：
  - `query`
  - `resumeId/versionNo`
  - `jdId/versionNo`
  - `topK?`, `minScore?`
- 出参（示例）：
  - `hits[]`（含 chunk 文本、score、source）
  - `retrievalMeta`（耗时、阈值、命中数、degraded）

### 6.4 analyze 集成建议
- `POST /api/analyze` 增加可选参数 `enableRetrieval`（默认按配置）
- 响应增加 `retrievalMeta`（无命中时标记 degraded）

---

## 7. 失败处理与降级策略

### 7.1 失败场景
- 向量服务超时
- 索引不存在或版本不一致
- 命中低于阈值

### 7.2 降级规则
- 检索失败不直接返回 500。
- 设置 `retrievalMeta.degraded=true`，并走原有分析链路。
- 连续失败超阈值时触发告警并可自动关闭 RAG 开关。

---

## 8. 可观测性指标

核心指标：
- 索引任务成功率
- 平均索引耗时
- 检索命中率（hits > 0）
- 检索延迟 p95
- degraded 比例
- 单次请求 token 成本（可选）

日志字段建议：
- `requestId`, `sessionId`, `retrieval_enabled`, `hit_count`, `top_score`, `degraded`, `duration_ms`

---

## 9. 发布与回滚策略

### 发布前
- 预热基础索引（核心样本 Resume/JD）。
- 验证 `/api/rag/retrieve` 在预发稳定。
- 配置降级开关：`CAREER_HERO_RAG_ENABLED`。

### 回滚时
- 可一键关闭 RAG，保留主流程。
- 索引版本错误时可回切到上一版本索引。
- 保留检索日志用于事后复盘。

---

## 10. 验收建议（Wave5 实施前置）

- [ ] 至少支持 Resume/JD 各 1 条版本索引构建全链路。
- [ ] analyze 在检索成功与降级两种路径都可返回结果。
- [ ] 输出中可看到 `retrievalMeta` 且字段稳定。
- [ ] 发布后 30 分钟可观测项包含命中率与 degraded 比例。
