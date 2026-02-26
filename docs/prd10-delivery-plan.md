# PRD v2.0（第1-10部分）交付执行计划

> 版本：v1.0（2026-02-26）
>
> 适用范围：**仅覆盖 PRD v2.0 第1~10部分**，明确**不纳入第11部分**。
>
> 执行口径：以当前仓库已落地能力（API、测试、现有 docs）为基线，按“阶段 + 里程碑 + 验收门禁”推进。

---

## 0. 范围锁定（Scope Lock）

- **In Scope**：PRD v2.0 第1~10部分的功能、体验、发布、运维与验收闭环。
- **Out of Scope**：PRD v2.0 第11部分（本计划中不拆解、不排期、不计入验收）。
- **变更规则**：若后续要纳入第11部分，需新增独立计划文档，不在本计划内透支。

---

## 1. 分阶段执行框架（1~10点）

### Phase A：基线对齐（第1~3部分）
- 目标：冻结范围、角色和主流程，确保研发/测试/产品使用同一口径。
- 输出：统一术语、主流程图、DoD/验收口径。

### Phase B：核心能力闭环（第4~6部分）
- 目标：打通 Resume/JD/Analyze 主链路，形成“可运行 + 可解释 + 可降级”能力。
- 输出：接口与前端链路可跑、核心测试可回归。

### Phase C：场景扩展闭环（第7~8部分）
- 目标：补齐 Interview + 历史追溯导出，形成可复盘与可复用能力。
- 输出：完整用户任务闭环与证据链。

### Phase D：上线治理闭环（第9~10部分）
- 目标：完成 Auth/Security + Release/Ops 门禁，确保可发布、可回滚、可观测。
- 输出：上线放行标准、回滚剧本、持续化验收机制。

---

## 2. 里程碑拆解（按第1~10部分逐点落地）

| PRD部分 | 里程碑ID | 所属阶段 | 里程碑目标 | 主要产物 | 进入门禁 | 退出门禁（DoD） |
|---|---|---|---|---|---|---|
| 第1部分（目标/范围） | M1 | Phase A | 统一业务目标、边界、成功标准 | `docs/prd10-delivery-plan.md` | 已有 PRD 与现状盘点 | 范围清单冻结，含“明确忽略第11部分” |
| 第2部分（用户/角色） | M2 | Phase A | 明确用户角色、权限和场景 | Auth/Session 规格、角色视图约束 | 会话模型已定义（legacy/guest/hybrid） | 角色-权限-场景矩阵可直接用于开发与验收 |
| 第3部分（核心流程） | M3 | Phase A | 冻结主流程与异常恢复流程 | 主流程步骤 + 异常分支 | Analyze/History 基础可跑 | 主流程与异常路径均有可执行用例 |
| 第4部分（简历管理与解析） | M4 | Phase B | Resume CRUD + 版本 + 解析闭环 | Resume API/页面/测试 | 已有 Resume CRUD 基础 | 上传/解析状态/结果查询与错误处理可验收 |
| 第5部分（JD与岗位画像） | M5 | Phase B | JD 实体化 + 知识复用 | JD/Knowledge 文档与接口链路 | Knowledge/RAG 规格已存在 | JD 入库→检索→Analyze 复用最小链路打通 |
| 第6部分（匹配分析与建议） | M6 | Phase B | Analyze 解释性与降级稳定 | `scoreBreakdown`/insights/retrievalMeta | `/api/analyze` 可用 | 命中与降级双路径稳定、可追溯 |
| 第7部分（面试流程） | M7 | Phase C | Interview 生命周期可执行 | interview-flow + session APIs + tests | 基础会话与问答链路存在 | create/list/detail/pause/resume/finish 全链路验收通过 |
| 第8部分（历史追溯导出） | M8 | Phase C | 历史检索、详情、导出、差异化追溯 | history/export 能力与证据模板 | `/api/history` 与导出已可用 | requestId/session/version 追溯闭环可验收 |
| 第9部分（认证与安全） | M9 | Phase D | Auth refresh/隔离/限流/审计闭环 | auth-api/session 规格 + 测试 + runbook | login/me/logout 基线存在 | refresh 成功/失败恢复路径 + 隔离策略通过门禁 |
| 第10部分（质量、发布、运维） | M10 | Phase D | 发布门禁、回滚、观测、CI 统一执行 | checklist/readiness/runbook/sprint 回填 | 发布文档已形成基线 | Go/Conditional Go/No-Go 可被值班同学独立执行 |

---

## 3. 波次推进建议（PRD10）

| Wave | 对应里程碑 | 本波目标 | 验收输出 |
|---|---|---|---|
| Wave-P1 | M1~M3 | 对齐口径，锁定范围与流程 | 范围冻结、流程用例清单、角色矩阵 |
| Wave-P2 | M4~M6 | 打通主功能链路 | Resume/JD/Analyze 可运行证据 + 自动化回归 |
| Wave-P3 | M7~M8 | 场景扩展与追溯闭环 | Interview 生命周期验收 + 历史导出证据 |
| Wave-P4 | M9~M10 | 发布与运维收口 | Auth 安全门禁 + Release/Rollback 演练结论 |

> 建议每波都回填：`目标 / 改动文件 / 验收清单 / 证据路径 / 风险 / 下一波交接`。

---

## 4. 关键依赖与风险

1. **Auth refresh 与会话状态一致性**
   - 风险：过期续期失败导致主流程中断。
   - 依赖：前后端重试策略、token/cookie 配置、错误码标准化。

2. **RAG/Knowledge 与 Analyze 集成稳定性**
   - 风险：检索失败影响分析质量或时延抖动。
   - 依赖：索引版本治理、降级开关、E2E 场景覆盖。

3. **Interview 与 History 追溯一致性**
   - 风险：跨 session / 版本追溯不完整。
   - 依赖：统一 ID、审计字段、详情接口契约。

4. **发布执行与回滚演练证据不足**
   - 风险：线上故障时无法快速恢复。
   - 依赖：readiness 门禁、runbook 演练、值班签收机制。

---

## 5. 统一验收门禁（PRD10）

- [ ] 仅第1~10部分纳入本轮排期（第11部分不计入本次交付）。
- [ ] 每个部分至少有 1 条“可执行用例 + 验收状态 + 证据路径”。
- [ ] 所有 P0 问题关闭后，方可进入功能放行评审。
- [ ] Release Checklist / Rollback Runbook / Readiness Report 三件套齐备。
- [ ] `sprint-progress.md` 已按 PRD10 波次回填并可追溯。

---

## 6. 与现有文档映射（落地指引）

- 交付计划：`docs/prd10-delivery-plan.md`（本文）
- 验收矩阵：`docs/prd10-acceptance-matrix.md`
- 历史差距：`docs/prd-gap-analysis.md`
- 发布门禁：`docs/release-checklist.md` / `docs/release-readiness-report.md`
- 回滚应急：`docs/rollback-runbook.md`
- 进度回填：`sprint-progress.md`

> 本计划作为 PRD10 执行总索引：先锁范围，再推进波次，再用矩阵验收。