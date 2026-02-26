# 面试流程规格说明（Interview Flow Spec）

> 版本：v0.1（Wave3 文档冻结版）
>
> 适用范围：Career Hero MVP 下一阶段（面试准备/模拟/复盘）能力设计基线。

---

## 1. 目标与设计原则

### 1.1 目标
- 建立统一的面试流程主状态机与子状态机，避免后续实现出现状态歧义。
- 支持“准备 -> 进行 -> 复盘 -> 行动项”完整闭环。
- 与现有 Resume/JD/Analyze 能力可串联（不破坏现有接口）。

### 1.2 设计原则
1. **状态可观测**：每次迁移必须有事件（event）和时间戳。
2. **状态可回溯**：关键节点保留 requestId/sessionId 与触发来源。
3. **状态可恢复**：异常中断后可重入，不强制重新创建会话。
4. **向后兼容**：初版允许“文档先行、接口后补”，不强依赖一次性实现全部功能。

---

## 2. 术语与对象模型

- **InterviewSession**：一次面试会话（通常绑定 1 个 JD + 1 个 ResumeVersion）。
- **Round**：会话中的轮次（例如：自我介绍轮、项目深挖轮、反问轮）。
- **Question**：每轮的问题项，可由系统生成或面试官输入。
- **Answer**：候选人回答文本/语音转写。
- **Review**：系统对回答给出的评分、建议与风险提示。
- **ActionItem**：复盘后可执行改进任务。

建议核心关联：
- `InterviewSession` ↔ `resume_id/version_no`
- `InterviewSession` ↔ `jd_id/version_no`
- `Review` ↔ `history_id`（可选，用于与 analyze 历史对齐）

---

## 3. 端到端流程（业务视角）

1. 用户选择简历版本与目标 JD，创建会话（Session）。
2. 系统根据 JD/简历生成面试轮次与问题草案（可人工编辑）。
3. 会话开始后，按轮次逐题作答。
4. 每题可即时评分；轮次结束可输出阶段总结。
5. 会话结束后进入复盘，生成 ActionItems。
6. ActionItems 可回写到后续训练计划或下一次会话。

---

## 4. 主状态机：InterviewSession

### 4.1 状态定义

| 状态 | 含义 | 是否终态 |
|---|---|---|
| `DRAFT` | 会话草稿，已绑定基础信息但未开始 | 否 |
| `READY` | 题目与轮次已准备完成，可开始 | 否 |
| `IN_PROGRESS` | 正在面试中 | 否 |
| `PAUSED` | 暂停中，可恢复 | 否 |
| `REVIEWING` | 面试已结束，正在生成复盘结果 | 否 |
| `COMPLETED` | 复盘完成，产出可查看 | 是 |
| `CANCELLED` | 主动取消（用户/系统） | 是 |
| `FAILED` | 异常失败（可重试恢复） | 否（可恢复） |
| `ARCHIVED` | 归档只读 | 是 |

### 4.2 允许迁移

| From | Event | To | 说明 |
|---|---|---|---|
| `DRAFT` | `prepare_success` | `READY` | 轮次/题目准备完毕 |
| `DRAFT` | `cancel` | `CANCELLED` | 用户取消 |
| `READY` | `start` | `IN_PROGRESS` | 正式开始 |
| `READY` | `cancel` | `CANCELLED` | 未开始取消 |
| `IN_PROGRESS` | `pause` | `PAUSED` | 手动暂停 |
| `PAUSED` | `resume` | `IN_PROGRESS` | 恢复继续 |
| `IN_PROGRESS` | `finish` | `REVIEWING` | 完成答题，进入复盘 |
| `IN_PROGRESS` | `error` | `FAILED` | 运行异常 |
| `PAUSED` | `cancel` | `CANCELLED` | 暂停时取消 |
| `FAILED` | `retry` | `IN_PROGRESS` 或 `REVIEWING` | 按失败阶段恢复 |
| `REVIEWING` | `review_done` | `COMPLETED` | 复盘报告完成 |
| `REVIEWING` | `error` | `FAILED` | 复盘失败 |
| `COMPLETED` | `archive` | `ARCHIVED` | 归档只读 |

### 4.3 非法迁移规则
- 终态（`CANCELLED/ARCHIVED`）不可迁回执行态。
- `COMPLETED` 不允许再次 `start`。
- `DRAFT` 不能直接 `finish`。
- 非法迁移统一返回 `409 CONFLICT` + 标准错误结构 `code/message/requestId`。

---

## 5. 子状态机：Question

### 5.1 状态定义

| 状态 | 含义 |
|---|---|
| `QUEUED` | 已排队，尚未展示 |
| `ASKED` | 问题已展示，等待回答 |
| `ANSWERING` | 用户作答中 |
| `ANSWERED` | 回答提交完成 |
| `SCORING` | 系统评分中 |
| `SCORED` | 评分完成 |
| `SKIPPED` | 跳过 |
| `FAILED` | 评分或处理失败 |

### 5.2 允许迁移

`QUEUED -> ASKED -> ANSWERING -> ANSWERED -> SCORING -> SCORED`

旁路：
- `ASKED/ANSWERING -> SKIPPED`
- `SCORING -> FAILED`
- `FAILED -> SCORING`（重试）

### 5.3 约束
- 同一时刻仅允许一个 Question 处于 `ANSWERING`。
- `SKIPPED` 题目不进入 `SCORED`，但可在会后补答触发二次评分。

---

## 6. 异常与恢复策略

1. **超时**
   - 会话级超时：`IN_PROGRESS -> PAUSED`（保留进度）
   - 题目级超时：`ANSWERING -> SKIPPED`
2. **评分服务异常**
   - `SCORING -> FAILED`，记录错误类型与可读原因
   - 支持指数退避重试（建议最多 3 次）
3. **手动终止**
   - 任意非终态可触发 `cancel` 到 `CANCELLED`
4. **恢复策略**
   - `FAILED` 状态可 `retry`，恢复到最近合法执行态

---

## 7. 数据与审计建议字段

### 7.1 Session（建议）
- `id`
- `status`
- `resume_id`, `resume_version_no`
- `jd_id`, `jd_version_no`
- `started_at`, `ended_at`
- `current_round_no`
- `last_error_code`, `last_error_message`
- `request_id`, `session_id`

### 7.2 Question（建议）
- `id`, `session_id`, `round_no`, `order_no`
- `question_text`, `status`
- `answer_text`
- `score`, `feedback_json`
- `latency_ms`

---

## 8. API 契约建议（草案）

- `POST /api/interviews`：创建会话（`DRAFT`）
- `POST /api/interviews/{id}/prepare`：准备题目（`DRAFT -> READY`）
- `POST /api/interviews/{id}/start`：开始会话（`READY -> IN_PROGRESS`）
- `POST /api/interviews/{id}/pause`：暂停
- `POST /api/interviews/{id}/resume`：恢复
- `POST /api/interviews/{id}/finish`：结束并复盘（`IN_PROGRESS -> REVIEWING`）
- `GET /api/interviews/{id}`：查询会话详情
- `POST /api/interviews/{id}/questions/{qid}/answer`：提交回答
- `POST /api/interviews/{id}/questions/{qid}/retry-score`：重试评分

错误结构沿用全局标准：`code/message/requestId`。

---

## 9. 可观测性与验收建议

### 9.1 关键指标
- Session 完成率（`COMPLETED / started`）
- Question 评分成功率（`SCORED / ANSWERED`）
- 平均复盘耗时（`REVIEWING -> COMPLETED`）
- 中断恢复率（`FAILED/PAUSED` 后成功继续比例）

### 9.2 Wave3 文档验收（本文件）
- 状态定义完整，无冲突。
- 迁移规则可直接映射到接口行为。
- 异常与恢复路径覆盖核心故障场景。
- 与现有 Resume/JD/History 术语一致。

---

## 10. 后续落地建议（Wave4 起）

1. 先实现 Session 主状态机最小闭环（不含语音）。
2. 再补 Question 子状态机与评分重试。
3. 最后接入 ActionItems 与历史关联视图。

> 说明：本规格为实现前冻结版本，若需变更请新增版本节（v0.2+）并记录迁移影响。
