# Release Readiness Report（Wave6）

> 项目：Career Hero MVP（Next.js + FastAPI）
>
> 时间：2026-02-25
>
> 口径：本报告基于 Wave6 文档与交接轨产物，评估当前“上线前门禁”准备度。

---

## 1. 结论摘要

- **文档发布就绪度：高（可放行）**
- **功能发布就绪度：中（条件放行）**
- **关键约束：Auth refresh 与 Wave6 E2E 仍需代码侧执行证据**

建议：
1. 若本次仅发布文档与流程基线，可按正常变更窗口发布。
2. 若包含 Auth/KB/检索增强功能变更，需先完成 P0 门禁后再放行。

---

## 2. 门禁评估矩阵

| 门禁项 | 状态 | 证据 | 说明 |
|---|---|---|---|
| PRD 差距与路线回填 | ✅ 通过 | `docs/prd-gap-analysis.md`、`docs/prd-delivery-plan.md` | Wave6 进度与剩余差距已更新 |
| 接口契约基线 | ✅ 通过 | `docs/auth-api-spec.md`、`docs/knowledge-base-spec.md` | 契约已冻结，术语口径一致 |
| 发布清单（Wave6） | ✅ 通过 | `docs/release-checklist.md` | 已纳入 auth refresh + e2e 专项 |
| 回滚手册（Wave6） | ✅ 通过 | `docs/rollback-runbook.md` | 已纳入 refresh 故障止血与回滚验证 |
| 自动化 E2E 执行证据 | ⚠️ 条件通过 | 待补（CI/预发报告） | 场景已定义，执行证据待落地 |
| 预发回滚演练证据 | ⚠️ 条件通过 | 待补（演练记录） | runbook 可执行，演练记录未归档 |

---

## 3. Wave6 新增门禁（重点）

### 3.1 Auth refresh 门禁
- 会话过期后 `refresh -> retry` 路径可验证
- refresh 失败后存在可恢复路径（重建会话/重新登录）
- logout 后接口状态符合预期（401/403）

### 3.2 E2E 门禁（最小集合）
- E2E-A：guest 会话下 analyze + history 主流程成功
- E2E-B：会话过期后 refresh 成功并恢复请求
- E2E-C：refresh 失败时前端提示与降级路径可用
- E2E-D：检索失败时 `degraded=true` 且主流程成功

---

## 4. 上线建议（分级）

### A. 文档发布（当前可执行）
- 放行条件：
  - 文档链接完整
  - checklist/runbook 责任人已明确
  - sprint-progress 已完成 Wave6 交接回填
- 风险：低

### B. 功能发布（需条件放行）
- **P0（必须完成）**
  1. `/api/auth/session/refresh` 冒烟通过并留存结果
  2. Wave6 E2E 至少 4 条场景执行并归档
  3. 预发完成一次回滚演练，记录 RTO 与操作人
- **P1（建议完成）**
  1. 监控面板纳入 refresh 成功率/失败率
  2. degraded 比例与 401/403 比例设置阈值告警
  3. 发布后 T+30/T+120 验收模板固化到值班流程

---

## 5. 交接清单（给发布值班）

- 发布前阅读：
  - `docs/release-checklist.md`
  - `docs/rollback-runbook.md`
  - `docs/prd-delivery-plan.md`
- 发布中记录：
  - 版本号 / 执行人 / 时间窗口
  - 关键门禁结果（refresh + E2E）
- 发布后归档：
  - 验收截图/日志
  - 回滚演练记录（如执行）
  - 复盘待办清单

---

## 5.1 Go/No-Go 判定规则（Wave6）

### Go（可放行）
- 文档类发布：门禁矩阵均为 ✅ 或已登记风险且不影响主流程。
- 功能类发布：P0 门禁全部通过，且回滚演练记录可追溯。

### Conditional Go（条件放行）
- 存在 ⚠️ 条件通过项，但已明确 Owner、ETA、兜底方案。
- 条件项仅限非核心链路，不影响 analyze/history 主流程可用性。

### No-Go（禁止放行）
- Auth refresh 冒烟失败且无可用兜底。
- Wave6 E2E 关键场景（B/C）连续失败无法在窗口内修复。
- 回滚流程执行人未到位或 runbook 无法在预发复现。

## 5.2 上线窗口建议（T-48h / T-24h / T-2h）
- **T-48h**：完成 checklist 首轮预检、确认变更范围与值班人。
- **T-24h**：完成 E2E 场景跑批与缺陷分级，冻结发布包。
- **T-2h**：执行 refresh 冒烟 + 回滚指令演练，确认 Go/No-Go。

## 6. 阻塞项与下一步

### 当前阻塞
1. refresh 链路仍缺“实现+证据”双闭环。
2. Wave6 E2E 场景未在 CI 中稳定执行。

### 下一步（建议 Wave7）
1. 落地 refresh 最小实现并完成预发验证。
2. 将 Wave6 E2E 门禁接入 CI，统一产出报告。
3. 完成一次可审计回滚演练，形成标准交接包。
