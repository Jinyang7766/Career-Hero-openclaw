# Release Checklist（上线前检查清单）

> 适用项目：Career Hero MVP（Next.js + FastAPI）
>
> Wave6 重点：在 Wave5 基础上补齐 **Auth Refresh + E2E 门禁**，并形成可交接放行口径。

---

## A. 发布基础信息

- [ ] 发布版本号：`____________`
- [ ] 发布负责人（Owner）：`____________`
- [ ] 发布时间窗口：`____________`
- [ ] 关联分支/Tag：`____________`
- [ ] 回滚负责人已指定：`____________`

---

## B. 代码与制品确认

- [ ] 主干代码已冻结（除紧急修复外不再合并）
- [ ] 后端依赖锁定（`backend/requirements.txt`）
- [ ] 前端依赖锁定（`frontend/package-lock.json`）
- [ ] 变更范围已过审（代码 + docs + sprint-progress 一致）
- [ ] 关键接口变更已同步前端/测试/值班
- [ ] 接口文档版本已冻结并标注：
  - [ ] `docs/auth-api-spec.md`
  - [ ] `docs/knowledge-base-spec.md`

---

## C. 配置与环境变量

### Frontend
- [ ] `NEXT_PUBLIC_API_BASE_URL` 指向正确后端地址
- [ ] 前端已确认 Auth/Knowledge 接口路径与版本口径一致
- [ ] 会话过期提示与 refresh 失败提示文案已确认

### Backend（基础）
- [ ] `CAREER_HERO_AI_PROVIDER`（`rule` / `gemini` / `auto`）
- [ ] `GEMINI_API_KEY` / `GEMINI_MODEL`（如使用）
- [ ] `CAREER_HERO_DB_PATH` 可写
- [ ] `CAREER_HERO_HISTORY_RETENTION` 已确认
- [ ] `CAREER_HERO_RATE_LIMIT_PER_MINUTE` / `CAREER_HERO_DUPLICATE_LIMIT` 已确认

### Backend（Wave4 Auth 专项）
- [ ] `CAREER_HERO_AUTH_MODE`（`legacy` / `guest` / `hybrid`）已确认
- [ ] `CAREER_HERO_AUTH_SECRET` 已配置并校验长度/来源
- [ ] `CAREER_HERO_SESSION_TTL_MINUTES` / `CAREER_HERO_REFRESH_TTL_DAYS` 已确认
- [ ] Cookie 安全策略（HttpOnly/Secure/SameSite）已核对
- [ ] CSRF 防护策略已启用（如采用 Cookie 鉴权）

### Backend（Wave6 Auth Refresh 门禁）
- [ ] `/api/auth/session/refresh` 行为与 `auth-api-spec` 一致
- [ ] refresh 成功后 session 续期与历史可见性策略一致
- [ ] refresh 失败时错误码与前端兜底路径一致（401/403）

### Backend（Wave4 RAG 专项）
- [ ] `CAREER_HERO_RAG_ENABLED` 开关值符合发布策略
- [ ] `CAREER_HERO_RAG_INDEX_VERSION` 已确认
- [ ] Embedding 相关 Key/Provider 已配置
- [ ] `TOP_K` / `MIN_SCORE` / `CONTEXT_BUDGET` 配置已确认

### Backend（Wave5 Knowledge 专项）
- [ ] `CAREER_HERO_KB_ENABLED` 开关值符合发布策略
- [ ] `CAREER_HERO_KB_INDEX_VERSION` 已确认
- [ ] `CAREER_HERO_KB_DEFAULT_TOP_K` / `CAREER_HERO_KB_MIN_SCORE` 已确认
- [ ] `CAREER_HERO_KB_WRITE_MODE`（`readwrite/readonly`）已确认
- [ ] 知识库与检索配置和 `knowledge-base-spec` 一致

---

## D. 数据、索引与备份

- [ ] SQLite 文件已备份：`____________`
- [ ] 备份可恢复抽样通过
- [ ] 历史清理策略确认
- [ ] 无破坏性迁移，或迁移方案已评审
- [ ] RAG 索引已预热（至少核心样本）
- [ ] 索引构建任务状态抽样通过（SUCCESS 可复现）
- [ ] Knowledge Base 文档抽样校验通过（source/版本/标签）
- [ ] Knowledge 索引任务可从任务 ID 追踪到结果

---

## E. 自动化验证与冒烟

### Backend
- [ ] `cd backend && .\.venv\Scripts\python -m pytest -q` 通过
- [ ] `/health`、`/api/analyze`、`/api/history` 冒烟通过
- [ ] `/api/auth/session/current` 冒烟通过（如启用 Auth）
- [ ] `/api/auth/session/refresh` 冒烟通过（Wave6）
- [ ] `/api/rag/retrieve` 冒烟通过（如启用 RAG）
- [ ] `/api/knowledge/bases`、`/api/knowledge/retrieve` 冒烟通过（如启用 KB）

### Frontend
- [ ] `cd frontend && npm run build` 通过
- [ ] 首页主流程（输入 -> 分析 -> 结果 -> 导出）通过
- [ ] 历史检索/展开/清理通过
- [ ] 会话异常（过期/未登录）提示路径可用（如启用 Auth）
- [ ] 检索降级提示路径可用（命中为空但主流程成功）

### Wave6 E2E 门禁（Auth Refresh + 主流程）
- [ ] E2E-A：guest 会话下 `analyze -> history` 成功
- [ ] E2E-B：会话过期后 `refresh -> retry` 成功
- [ ] E2E-C：refresh 失败后前端提示与恢复路径可用
- [ ] E2E-D：检索失败时 `degraded=true` 且主流程成功
- [ ] E2E 结果已归档（报告/截图/日志路径）：`____________`
- [ ] E2E 失败场景已记录 owner/ETA/兜底动作（Wave6）

---

## F. 监控与可观测性

- [ ] 请求日志可见并包含 `requestId/sessionId/status/duration_ms`
- [ ] 错误码聚合可查看（`error_code`）
- [ ] 发布后 30 分钟观察项已设定：
  - [ ] 500 比例
  - [ ] 401/403 比例（Auth）
  - [ ] 429 比例
  - [ ] refresh 失败率（Wave6）
  - [ ] 检索 degraded 比例（RAG/KB）
  - [ ] 知识索引任务失败率
  - [ ] 平均响应时长 / p95
  - [ ] refresh 失败率阈值已设定（建议：`< 3%`）
  - [ ] E2E 关键场景成功率阈值已设定（建议：`>= 95%`）

---

## G. 回滚准备

- [ ] 上一稳定版本（Tag/Commit）已确认：`____________`
- [ ] 回滚命令已预演（至少预发）
- [ ] `docs/rollback-runbook.md` 已过一遍并明确执行人
- [ ] 回滚通知模板已准备
- [ ] Auth 模式回退（`hybrid/guest -> legacy`）已演练
- [ ] Auth refresh 故障临时止血路径已演练（Wave6）
- [ ] RAG 降级（`enabled -> disabled`）已演练
- [ ] KB 降级（`enabled -> disabled` 或 `readwrite -> readonly`）已演练

---

## H. 发布执行

- [ ] 按发布步骤执行完成
- [ ] 健康检查通过（`/health`）
- [ ] 核心接口抽样通过（analyze + history）
- [ ] Auth/RAG/KB 专项接口抽样通过（如启用）
- [ ] 前端核心路径抽样通过
- [ ] 发布公告已发送（版本/影响面/回滚方式）

---

## I. 发布后验收（T+30 / T+120）

### T+30 分钟
- [ ] 无高优先级告警
- [ ] 核心路径成功率正常
- [ ] 用户侧无集中异常反馈
- [ ] Auth/KB 契约相关错误码未异常升高
- [ ] refresh 失败率未异常升高（Wave6）

### T+120 分钟
- [ ] 关键指标稳定
- [ ] 无需触发回滚
- [ ] 发布复盘待办已记录

---

## J. 签字确认

- 发布负责人：`____________`（时间：`____________`）
- 测试负责人：`____________`（时间：`____________`）
- 值班/运维负责人：`____________`（时间：`____________`）
