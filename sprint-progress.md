# Sprint Progress

## Day1 收口（2026-02-25 00:58 +08:00）

### 已完成

1. **Backend 独立 venv**
   - 在 `backend/.venv` 创建独立虚拟环境
   - README 已补充创建/激活/安装/运行命令（Windows + macOS/Linux）

2. **FastAPI 统一错误返回格式**
   - 新增统一错误结构：`code` / `message` / `requestId`
   - 覆盖：
     - `400 BAD_REQUEST`
     - `422 VALIDATION_ERROR`
     - `500 INTERNAL_ERROR`
   - 增加 request-id middleware，响应头回写 `x-request-id`

3. **前端 loading/error/empty 状态 + 可用性**
   - loading 状态卡片（分析中提示）
   - error 状态卡片（错误信息 + 重试按钮）
   - empty 状态卡片（未提交/提交后无结果两种提示）
   - 可用性补充：字数计数、清空按钮、Ctrl/Cmd+Enter 快捷提交

4. **README 从零启动 + 验证步骤**
   - 完整补充“从零启动”步骤
   - 增加统一错误返回格式示例
   - 增加 Day1 回归验证命令

---

## 回归结果

### Backend pytest

命令：

```bash
cd backend
.\.venv\Scripts\python -m pytest -q
```

结果：

```text
5 passed in 0.51s
```

### Frontend build

命令：

```bash
cd frontend
npm run build
```

结果：

```text
✓ Compiled successfully
✓ Generating static pages ...
```

---

## Day2 下一步计划（建议）

1. **API 可观测性补强（2h）**
   - 增加结构化日志（含 requestId）
   - 增加关键接口耗时日志

2. **评分策略可解释性升级（3h）**
   - 将匹配分拆成 2-3 个维度（技能、工具、业务词）
   - 返回维度分与解释文案

3. **前端结果页可复制与导出（1.5h）**
   - 一键复制优化简历
   - 导出 txt / markdown（先实现 txt）

4. **基础 E2E 冒烟（1.5h）**
   - 覆盖：输入 -> 分析 -> 展示结果 -> 错误态

**Day2 总 ETA：约 8 小时（1 个工作日）**

---

## Day2 执行记录（2026-02-25 01:23 +08:00）

### 本次改动文件

1. `backend/app/main.py`
2. `backend/tests/test_api.py`
3. `frontend/src/app/page.tsx`
4. `frontend/src/app/page.module.css`
5. `sprint-progress.md`

### 已完成

1. **API 可观测性补强（FastAPI）**
   - 将 request-id 与接口耗时日志合并为统一 middleware（`observability_middleware`）
   - 增加结构化日志字段：
     - `path`
     - `method`
     - `status`
     - `duration_ms`
     - `requestId`
   - 保持现有统一错误返回格式不变（`code/message/requestId`），原错误处理链路继续可用

2. **评分与解释升级（/api/analyze）**
   - 响应新增 `scoreBreakdown` 字段：
     - `keyword_match`
     - `coverage`
     - `writing_quality_stub`
   - 主分 `score` 改为基于拆解分的加权汇总（仍做 5~98 区间保护）
   - 保留原有字段：
     - `score`
     - `matchedKeywords`
     - `missingKeywords`
     - `suggestions`
     - `optimizedResume`
   - 满足向后兼容（原字段未移除）

3. **前端结果页增强**
   - 在结果区新增 `scoreBreakdown` 展示
   - 新增“复制结果（文本）”按钮（调用浏览器剪贴板 API）
   - 新增“导出JSON”按钮（本地下载结果 JSON 文件）
   - 保持 loading / error / empty 三种状态逻辑不变

4. **基础冒烟与补测**
   - 后端新增测试：结构化日志字段覆盖测试（`test_structured_request_log_contains_observability_fields`）
   - 原错误格式测试继续通过

### 测试/构建结果

#### Backend pytest

命令：

```bash
cd backend
.\.venv\Scripts\python -m pytest -q
```

结果：

```text
6 passed in 0.59s
```

#### Frontend build

命令：

```bash
cd frontend
npm run build
```

结果：

```text
✓ Compiled successfully
✓ Generating static pages ...
```

### Day3 建议与 ETA

1. **可观测性继续补强（2h）**
   - 增加 `error_code` / `exception_type` 维度日志
   - 预留 Prometheus 指标端点（请求量、延迟分位）

2. **评分策略可配置化（2.5h）**
   - 将权重与阈值抽离到配置
   - 增加评分解释文案（给用户看的自然语言说明）

3. **前端结果导出扩展（1.5h）**
   - 增加 markdown/txt 导出
   - 复制反馈增加自动消失与失败兜底

4. **端到端最小链路回归（2h）**
   - 增加一条从输入到结果展示的 E2E 脚本

**Day3 预计 ETA：约 8 小时（1 个工作日）**

---

## Day3 执行记录（2026-02-25 01:35 +08:00）

### 本次改动文件

1. `backend/app/main.py`
2. `backend/app/history_store.py`（新增）
3. `backend/tests/test_api.py`
4. `frontend/src/app/page.tsx`
5. `frontend/src/app/page.module.css`
6. `sprint-progress.md`

### 已完成

1. **SQLite 本地持久化（backend）**
   - 新增 `backend/app/history_store.py`，使用 Python 内置 `sqlite3`，默认落库到项目目录内：`backend/data/career_hero.sqlite3`
   - 建立 `analysis_history` 表，包含并覆盖以下字段：
     - `id`
     - `created_at`
     - `resume_text_hash_or_excerpt`
     - `jd_excerpt`
     - `score`
     - `score_breakdown_json`
     - `request_id`
   - 额外补充：`matched_keywords_json`、`missing_keywords_json`，用于前端历史关键词摘要
   - `/api/analyze` 成功后写入历史记录，保留原主链路响应结构

2. **历史查询接口（backend）**
   - 新增 `GET /api/history`
   - 支持 `limit` 参数：默认 20，最大 100（超出自动按 100 处理）
   - 返回最近记录（按最新在前）
   - 返回结构清晰，并保留 `requestId`：
     - 顶层 `requestId`（本次查询请求）
     - 每条历史 `requestId`（对应当次 analyze 请求）

3. **前端历史展示（Next.js）**
   - 首页新增“最近分析记录”区块
   - 支持“刷新历史”按钮
   - 展示每条历史：
     - 时间
     - score
     - requestId
     - 关键词摘要（匹配/缺失简版）
   - 在分析成功后自动刷新历史列表

4. **回归与测试补充**
   - backend 新增 history 相关测试：
     - analyze 成功后写入历史
     - `/api/history` 默认 limit=20 + 倒序校验
     - `/api/history` 最大 limit=100 行为
   - 错误返回统一格式仍保持：`code` / `message` / `requestId`

### 测试/构建结果

#### Backend pytest

命令：

```bash
cd backend
.\.venv\Scripts\python -m pytest -q
```

结果：

```text
9 passed in 0.93s
```

#### Frontend build

命令：

```bash
cd frontend
npm run build
```

结果：

```text
✓ Compiled successfully
✓ Generating static pages ...
Process exited with code 0
```

### Day4 建议与 ETA

1. **历史详情能力补强（2h）**
   - 增加历史记录详情展开（scoreBreakdown + 建议摘要）
   - 增加按 requestId 快速检索

2. **数据治理与清理策略（1.5h）**
   - 增加历史保留上限（如最近 N 条）
   - 增加手动清理接口（带确认）

3. **前后端联调体验优化（2h）**
   - 前端历史区增加加载骨架与错误重试提示优化
   - analyze 完成后高亮最新一条历史

4. **最小 E2E 回归（2.5h）**
   - 覆盖：分析成功 -> 历史可见 -> 刷新后仍可见

**Day4 预计 ETA：约 8 小时（1 个工作日）**

---

## Day4-Day15 并行执行框架模板（结果留空）

> 使用方式：每天复制对应区块，先填“计划/负责人/依赖”，收工时再补“结果”。
> 
> 注意：本模板中的“当日结果”字段默认留空，执行后再回填。

### Day4
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day5
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day6
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day7
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day8
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day9
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day10
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day11
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day12
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day13
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day14
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

### Day15
- 日期：
- 当日目标：

#### 并行泳道
- A（后端）：
- B（前端）：
- C（测试/验收）：
- D（发布/文档）：

#### 依赖与风险
- 依赖：
- 风险：

#### 当日结果（留空）
- 实际完成：
- 测试结果：
- 阻塞问题：
- 次日计划：

## PRD Full Sprint - Wave1 (2026-02-25 12:13 +08:00)

### Goal
- Execute first 30-min wave for full PRD alignment: gap analysis, phased delivery plan, and Phase A (4.1) minimal executable slice.

### Changed files
- docs/prd-gap-analysis.md (new)
- docs/prd-delivery-plan.md (new)
- backend/app/resume_store.py (new)
- backend/app/main.py (resume CRUD and version APIs)
- backend/tests/test_api.py (resume API tests)
- frontend/src/app/resumes/page.tsx (new resume management page)
- frontend/src/app/resumes/page.module.css (new styles)
- frontend/src/app/page.tsx (entry link to /resumes)
- sprint-progress.md (this section)

### Delivered
- PRD gap assessment by section 4.1 to 4.5 with current status, gap, and DoD.
- Phased delivery roadmap with dependencies, risks, and acceptance criteria.
- Phase A minimal implementation:
  - Backend resume CRUD with multi-version SQLite schema.
  - Frontend resume management entry with list, create, and view.
- Existing analyze/history flow kept compatible.

### Regression results
Backend pytest
- Command: cd backend && ./.venv/Scripts/python -m pytest -q
- Result: 25 passed in 1.64s

Frontend build
- Command: cd frontend && npm run build
- Result: Compiled successfully; static routes generated for / and /resumes.

### Risks and notes
- Resume parsing pipeline is not implemented yet (file upload and async parse deferred to next wave).
- DB schema is runtime-created; migration mechanism is needed in later waves.

---

## PRD Full Sprint - Wave2（模板，待执行）

> 状态：Planned（预创建模板，执行后回填）

### Goal（计划）
- 在 Phase A 基线之上补齐“文件上传 + 解析状态骨架 + 查询接口 + 测试回归”最小闭环。

### Planned scope（计划范围）
- 上传接口占位（含基础校验）
- 解析任务状态机骨架（PENDING/PROCESSING/SUCCESS/FAILED）
- 任务状态查询接口
- 最小前端状态展示/轮询
- API 测试与回归

### Planned changed files（待回填）
- [待回填] backend:
- [待回填] frontend:
- [待回填] docs:

### Acceptance checklist（执行前定义）
- [ ] 上传成功可返回 `taskId`
- [ ] 非法文件返回统一错误结构 `code/message/requestId`
- [ ] 状态查询接口可返回任务状态与元信息
- [ ] 失败场景有可读错误原因
- [ ] API 测试覆盖成功/失败/不存在等关键分支
- [ ] `/api/analyze` 与 `/api/history` 回归通过

### Execution log（待回填）
- 开始时间： [待回填]
- 完成时间： [待回填]
- 实际完成： [待回填]
- 未完成项： [待回填]

### Test and build result（待回填）
- Backend pytest： [待回填]
- Frontend build： [待回填]
- 其他验证： [待回填]

### Risks and notes（待回填）
- [待回填]

### Next wave handoff（待回填）
- Wave3 准备项： [待回填]

---

## PRD Full Sprint - Wave3（模板，待执行）

> 状态：Planned（文档轨已建立，研发执行后回填）

### Goal（计划）
- 启动 Phase B（JD 实体化）并冻结面试流程状态机规格，确保 Wave4 可直接进入实现。

### Planned scope（计划范围）
- JD 实体与版本策略定义（含 API 草案）
- analyze 输入链路预留 `jdId/versionNo` 方案
- 面试流程规格文档（Session/Question 状态机）
- Wave4 任务拆解与风险清单

### Planned changed files（待回填）
- [待回填] backend:
- [待回填] frontend:
- [待回填] docs:

### Acceptance checklist（执行前定义）
- [ ] JD 数据模型与字段定义完成并通过评审
- [ ] 面试流程主状态机与子状态机定义完整（含非法迁移约束）
- [ ] 异常中断与恢复策略可执行（超时/失败/重试/取消）
- [ ] 文档术语统一（Resume/JD/Session/Round/Question）
- [ ] Wave4 开发任务可直接领取执行

### Execution log（待回填）
- 开始时间： [待回填]
- 完成时间： [待回填]
- 实际完成： [待回填]
- 未完成项： [待回填]

### Test and review result（待回填）
- 文档评审结论： [待回填]
- 方案一致性检查： [待回填]
- 其他验证： [待回填]

### Risks and notes（待回填）
- [待回填]

### Next wave handoff（待回填）
- Wave4 准备项： [待回填]

---

## PRD Full Sprint - Wave4（执行模板，新增）

> 状态：Ready（本模板用于研发执行与发布轨并行回填）

### Goal（计划）
- 在 Wave3 文档冻结基础上，完成 Auth Session + RAG Retriever 的最小可执行落地与发布演练准备。

### Planned scope（计划范围）
- A（后端）：Auth Session 最小实现（guest/legacy 兼容）
- B（后端）：RAG Retriever 最小链路（索引 + 检索 + analyze 接入）
- C（前端）：会话异常提示 + 检索增强结果展示（含 retrievalMeta）
- D（测试/发布）：Auth/RAG 冒烟回归 + 发布清单 + 回滚演练记录

### Planned changed files（待回填）
- [待回填] backend:
- [待回填] frontend:
- [待回填] docs:

### Acceptance checklist（执行前定义）
- [ ] `/api/auth/session/current` 与刷新/登出链路可用
- [ ] `/api/rag/retrieve` 可返回可追溯证据片段
- [ ] analyze 在检索失败时可降级返回，不阻断主流程
- [ ] 历史查询具备最小 session 归属能力
- [ ] 发布清单与回滚手册覆盖 Auth/RAG 专项
- [ ] backend pytest + frontend build + smoke 回归通过

### Execution log（待回填）
- 开始时间： [待回填]
- 完成时间： [待回填]
- 实际完成： [待回填]
- 未完成项： [待回填]

### Test and release result（待回填）
- Backend pytest： [待回填]
- Frontend build： [待回填]
- Auth/RAG smoke： [待回填]
- 预发回滚演练： [待回填]

### Risks and notes（待回填）
- [待回填]

### Next wave handoff（待回填）
- Wave5 准备项： [待回填]

---

## PRD Full Sprint - Wave4（文档与发布轨回填，2026-02-25 12:53 +08:00）

> 状态：Done（本次仅文档与发布轨，不含代码改动）

### Goal（本次实际目标）
- 继续完整 PRD 版，补齐 Wave4 文档与发布轨：
  1) 回填 `prd-gap-analysis` 与 `prd-delivery-plan`
  2) 产出 `auth-session-spec` 与 `rag-retriever-spec`
  3) 更新发布清单与回滚手册 Wave4 专项
  4) 新增 Wave4 执行模板与回填区

### Actual changed files（已回填）
- `docs/prd-gap-analysis.md`
- `docs/prd-delivery-plan.md`
- `docs/auth-session-spec.md`（新增）
- `docs/rag-retriever-spec.md`（新增）
- `docs/release-checklist.md`
- `docs/rollback-runbook.md`
- `sprint-progress.md`

### Acceptance checklist（本次回填）
- [x] PRD 差距盘点已更新为 Wave4 口径并回填进度
- [x] 分阶段交付路线已加入 Wave4 交付与 Wave5 执行建议
- [x] Auth Session 规格文档已新增并可评审
- [x] RAG Retriever 规格文档已新增并可评审
- [x] 发布清单已补齐 Auth/RAG 专项检查项
- [x] 回滚手册已补齐 Auth/RAG 专项回退步骤
- [x] sprint-progress 已新增 Wave4 执行模板与回填区

### Review notes
- 本次为“文档基线 + 发布轨基线”回填，仍需 Wave5 工程实现闭环。
- 建议下一步先做 Auth 最小实现，再接 RAG 最小实现，最后接入 CI 与回滚演练证据。

### Next wave handoff
- Wave5 准备项：按 `docs/prd-delivery-plan.md` 的 Wave5-A/B/C 分解进入开发与测试排期。

---

## PRD Full Sprint - Wave5（执行模板，新增）

> 状态：Ready（本模板用于 Wave5 文档轨/实现轨并行回填）

### Goal（计划）
- 在 Wave4 文档基线之上，冻结 Auth API 与 Knowledge Base 契约，并补齐发布/回滚 Wave5 专项执行项。

### Planned scope（计划范围）
- A（后端文档）：Auth API 请求/响应字段定义
- B（后端文档）：Knowledge Base 模型/API/检索字段定义
- C（发布文档）：Release Checklist Wave5 专项检查项
- D（应急文档）：Rollback Runbook Wave5 专项回退步骤

### Planned changed files（待回填）
- [待回填] backend:
- [待回填] frontend:
- [待回填] docs:

### Acceptance checklist（执行前定义）
- [ ] `docs/auth-api-spec.md` 已产出且字段完整
- [ ] `docs/knowledge-base-spec.md` 已产出且字段完整
- [ ] `docs/prd-gap-analysis.md` 与 `docs/prd-delivery-plan.md` 已回填 Wave5 进度
- [ ] `docs/release-checklist.md` 与 `docs/rollback-runbook.md` 已补齐 Wave5 项
- [ ] sprint-progress 已新增 Wave5 模板与回填区

### Execution log（待回填）
- 开始时间： [待回填]
- 完成时间： [待回填]
- 实际完成： [待回填]
- 未完成项： [待回填]

### Test and review result（待回填）
- 文档评审结论： [待回填]
- 术语一致性检查： [待回填]
- 发布/回滚可执行性检查： [待回填]

### Risks and notes（待回填）
- [待回填]

### Next wave handoff（待回填）
- Wave6 准备项： [待回填]

---

## PRD Full Sprint - Wave5（文档交付轨回填，2026-02-25 13:23 +08:00）

> 状态：Done（本次仅文档与进度回填，不含代码改动）

### Goal（本次实际目标）
- 继续完整 PRD 版，完成 Wave5 文档交付轨：
  1) 更新 `prd-gap-analysis` 与 `prd-delivery-plan`（回填 Wave5 进度）
  2) 新增 `docs/auth-api-spec.md` 与 `docs/knowledge-base-spec.md`
  3) 更新 `release-checklist` / `rollback-runbook` 的 Wave5 项
  4) 在 `sprint-progress.md` 新增 Wave5 执行模板与回填区

### Actual changed files（已回填）
- `docs/prd-gap-analysis.md`
- `docs/prd-delivery-plan.md`
- `docs/auth-api-spec.md`（新增）
- `docs/knowledge-base-spec.md`（新增）
- `docs/release-checklist.md`
- `docs/rollback-runbook.md`
- `sprint-progress.md`

### Acceptance checklist（本次回填）
- [x] PRD 差距盘点已更新为 Wave5 口径并回填进度
- [x] 分阶段交付路线已加入 Wave5 交付与 Wave6 执行建议
- [x] Auth API 规格文档已新增并可评审
- [x] Knowledge Base 规格文档已新增并可评审
- [x] 发布清单已补齐 Auth API + Knowledge Wave5 专项检查项
- [x] 回滚手册已补齐 Auth API + Knowledge Wave5 专项回退步骤
- [x] sprint-progress 已新增 Wave5 执行模板与回填区

### Review notes
- 本次为“接口契约 + 发布轨”文档回填，研发实现仍需进入 Wave6 执行。
- 建议优先落地 Auth API 最小实现，再接 Knowledge Base 最小链路，最后补 CI 与预发回滚演练证据。

### Next wave handoff
- Wave6 准备项：按 `docs/prd-delivery-plan.md` 的 Wave6-A/B/C 分解进入开发、联调与验收排期。

---

## PRD Full Sprint - Wave6（执行模板，新增）

> 状态：Ready（本模板用于 Wave6 文档交接轨/实现轨并行回填）

### Goal（计划）
- 在 Wave5 契约基线之上，完成发布门禁汇总、auth refresh/e2e 验收口径补强与交接文档固化。

### Planned scope（计划范围）
- A（PRD文档）：回填 gap-analysis / delivery-plan 的 Wave6 进度与剩余差距
- B（发布文档）：新增 release-readiness-report（门禁与放行建议）
- C（发布清单）：补齐 release-checklist 的 auth refresh + e2e 项
- D（回滚手册）：补齐 rollback-runbook 的 refresh 故障与回退验证项

### Planned changed files（待回填）
- [待回填] backend:
- [待回填] frontend:
- [待回填] docs:

### Acceptance checklist（执行前定义）
- [ ] `docs/prd-gap-analysis.md` 与 `docs/prd-delivery-plan.md` 已回填 Wave6 进度
- [ ] `docs/release-readiness-report.md` 已新增并给出上线建议
- [ ] `docs/release-checklist.md` 与 `docs/rollback-runbook.md` 已补齐 Wave6 项
- [ ] sprint-progress 已新增 Wave6 模板与回填区

### Execution log（待回填）
- 开始时间： [待回填]
- 完成时间： [待回填]
- 实际完成： [待回填]
- 未完成项： [待回填]

### Test and review result（待回填）
- 文档评审结论： [待回填]
- 门禁一致性检查： [待回填]
- 交接可执行性检查： [待回填]

### Risks and notes（待回填）
- [待回填]

### Next wave handoff（待回填）
- Wave7 准备项： [待回填]

---

## PRD Full Sprint - Wave6（交接包模板，新增）

> 用途：给发布负责人/值班同学一键交接，避免仅有文档无执行证据。

### 交接包索引（模板）
- 发布版本：`[待回填]`
- 值班负责人：`[待回填]`
- 门禁结论：`Go / Conditional Go / No-Go`
- 风险等级：`P0 / P1 / P2`

### 证据路径（模板）
- checklist 勾选记录：`[待回填路径]`
- E2E 报告与截图：`[待回填路径]`
- refresh 冒烟日志：`[待回填路径]`
- 回滚演练记录（含 RTO）：`[待回填路径]`
- Readiness 评审结论：`[待回填路径]`

### 交接验收（模板）
- [ ] 发布负责人已签收
- [ ] 值班负责人已签收
- [ ] QA 已确认 E2E 结果可追溯
- [ ] 未收口风险已登记（含 owner/ETA）

---

## PRD Full Sprint - Wave6（文档与交接轨回填，2026-02-25 13:51 +08:00）

> 状态：Done（本次仅文档与交接回填，不含代码改动）

### Goal（本次实际目标）
- 继续完整 PRD 版，完成 Wave6 文档与交接轨：
  1) 回填 `prd-gap-analysis` 与 `prd-delivery-plan` 的 Wave6 进度与剩余差距
  2) 新增 `docs/release-readiness-report.md`（汇总门禁与上线建议）
  3) 更新 `release-checklist` / `rollback-runbook` 的 Wave6 项（auth refresh/e2e）
  4) 在 `sprint-progress.md` 新增 Wave6 模板与回填区

### Actual changed files（已回填）
- `docs/prd-gap-analysis.md`
- `docs/prd-delivery-plan.md`
- `docs/release-readiness-report.md`（新增）
- `docs/release-checklist.md`
- `docs/rollback-runbook.md`
- `sprint-progress.md`

### Handoff artifact index（本次新增）
- 门禁汇总入口：`docs/release-readiness-report.md`
- 发布执行入口：`docs/release-checklist.md`
- 回滚执行入口：`docs/rollback-runbook.md`
- 进度与交接入口：`sprint-progress.md`

### Acceptance checklist（本次回填）
- [x] PRD 差距盘点与交付路线已更新为 Wave6 口径
- [x] Release Readiness 报告已新增并给出放行建议
- [x] 发布清单已补齐 `/api/auth/session/refresh` 与 Wave6 E2E 门禁
- [x] 回滚手册已补齐 refresh 故障止血与回滚后验证项
- [x] sprint-progress 已新增 Wave6 模板与回填区

### Review notes
- 本次将“契约级准备”推进到“门禁级交接”，提升了发布执行一致性。
- 仍需 Wave7 工程实现闭环：refresh 实装、E2E 接入 CI、预发回滚演练证据归档。

### Next wave handoff
- Wave7 准备项（已转轨到 Wave8）：按 `docs/prd-delivery-plan.md` 的 Wave8-A/B/C/D 进入实现、自动化、UX 验收与预发演练排期。

---

## PRD Full Sprint - Wave7（文档回填：用户可用转轨，2026-02-25 14:48 +08:00）

> 状态：Done（本次仅文档与进度回填，不含代码改动）

### Goal（本次实际目标）
- 完成 Wave7 文档回填，把发布口径从“工程可用”推进到“用户可用”：
  1) 新增 user-facing UX 规范（信息层级、文案语气、技术字段默认隐藏规则）
  2) 更新 `prd-gap-analysis` / `prd-delivery-plan`，补充“工程可用 -> 用户可用”转轨说明
  3) 在 `sprint-progress.md` 新增 Wave7 回填段
  4) 产出 release-readiness 的 UX 验收清单

### Actual changed files（已回填）
- `docs/user-facing-ux-guidelines.md`（新增）
- `docs/release-readiness-ux-checklist.md`（新增）
- `docs/prd-gap-analysis.md`
- `docs/prd-delivery-plan.md`
- `sprint-progress.md`

### Acceptance checklist（本次回填）
- [x] 已新增 UX 规范并覆盖信息层级/文案语气/技术字段默认隐藏
- [x] PRD 差距盘点已补充“工程可用 -> 用户可用”转轨定义与完成度变化
- [x] 交付路线已加入 Wave7 转轨说明与 Wave8 UX 落地执行项
- [x] 已新增 `release-readiness-ux-checklist.md` 作为发布前 UX 验收门禁
- [x] sprint-progress 已新增 Wave7 文档回填记录

### Review notes
- 本次回填将“能发布”进一步约束为“能被用户顺畅使用”，重点防止技术字段噪音与错误提示不可行动。
- 当前仍缺前端实现证据，需在 Wave8 将 UX 规范落地到页面并完成走查归档。

### Next wave handoff
- Wave8 准备项：
  1. 按 `docs/user-facing-ux-guidelines.md` 改造核心页面并补截图证据
  2. 使用 `docs/release-readiness-ux-checklist.md` 完成预发验收
  3. 将 UX 验收项与 E2E 回归脚本绑定，形成持续验证

---

## PRD Full Sprint - PRD10（第1-10部分执行模板，忽略第11部分）

> 状态：Ready（模板预置，执行后按波次回填）

### Scope lock（固定范围）
- [x] 本模板仅覆盖 PRD v2.0 第1~10部分
- [x] 第11部分明确排除（不拆解、不排期、不计入本次验收）

### Wave-P1（第1~3部分：目标/角色/主流程）
- Goal：统一范围与流程口径，冻结验收边界。
- Planned scope：
  - 需求边界冻结（In/Out）
  - 角色与会话行为矩阵
  - 主流程 + 异常恢复流程
- Planned changed files（待回填）：
  - docs: [待回填]
  - sprint-progress: [待回填]
- Acceptance checklist：
  - [ ] 范围锁定并评审通过
  - [ ] 角色-权限-场景矩阵可执行
  - [ ] 主流程用例与异常用例均可落地

### Wave-P2（第4~6部分：Resume/JD/Analyze）
- Goal：打通核心功能链路并形成最小可用闭环。
- Planned scope：
  - Resume 管理与解析状态闭环
  - JD/Knowledge 最小实体化与复用
  - Analyze 解释性与降级稳定
- Planned changed files（待回填）：
  - backend: [待回填]
  - frontend: [待回填]
  - docs: [待回填]
- Acceptance checklist：
  - [ ] Resume/JD/Analyze 主链路可回归
  - [ ] 命中与降级双路径可验证
  - [ ] 关键接口统一错误结构稳定

### Wave-P3（第7~8部分：Interview/History）
- Goal：补齐面试生命周期与历史追溯导出能力。
- Planned scope：
  - Interview create/list/detail/pause/resume/finish
  - History 检索、详情、导出与证据链
- Planned changed files（待回填）：
  - backend: [待回填]
  - frontend: [待回填]
  - docs: [待回填]
- Acceptance checklist：
  - [ ] Interview 生命周期用例通过
  - [ ] History + Export 三格式可用
  - [ ] session/requestId/version 追溯可验证

### Wave-P4（第9~10部分：Auth/SRE/Release）
- Goal：完成上线门禁、回滚与运维可执行闭环。
- Planned scope：
  - login/me/logout/refresh + 安全隔离
  - 发布清单、readiness、回滚演练与证据化
- Planned changed files（待回填）：
  - backend: [待回填]
  - docs: [待回填]
  - sprint-progress: [待回填]
- Acceptance checklist：
  - [ ] Auth refresh 成功/失败恢复路径可验收
  - [ ] E2E 关键场景已接入 CI
  - [ ] Go/Conditional-Go/No-Go 判定可独立执行

### PRD10 波次回填记录（通用模板）
- 开始时间：`[待回填]`
- 完成时间：`[待回填]`
- 实际改动文件：`[待回填]`
- 验收证据路径：`[待回填]`
- 阻塞与风险：`[待回填]`
- 下一波交接：`[待回填]`

---

## PRD Full Sprint - PRD10（文档基线回填，2026-02-26 00:26 +08:00）

> 状态：Done（本次仅文档与进度模板回填，不含代码改动）

### Goal（本次实际目标）
- 按 PRD v2.0 第1~10部分（忽略第11部分）完成执行文档基线：
  1) 新建 `docs/prd10-delivery-plan.md`
  2) 新建 `docs/prd10-acceptance-matrix.md`
  3) 在 `sprint-progress.md` 增加 PRD10 波次回填模板

### Actual changed files（已回填）
- `docs/prd10-delivery-plan.md`（新增）
- `docs/prd10-acceptance-matrix.md`（新增）
- `sprint-progress.md`

### Acceptance checklist（本次回填）
- [x] 已按 1~10 点完成阶段与里程碑拆解（第11部分排除）
- [x] 已建立“需求点 -> 用例 -> 验收状态 -> 证据路径”矩阵
- [x] 已在 sprint-progress 新增 PRD10 波次回填模板
- [x] 已形成可直接执行的下一步优先级建议输入基础

