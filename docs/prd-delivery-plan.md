# PRD 分阶段交付路线（Wave7 更新）

> 目标：从 MVP 演进到 PRD 4.1~4.5 完整可交付状态，并保证接口、发布、回滚、交接、用户可用性五条轨道可执行。

---

## 阶段总览（含当前状态）

| 阶段 | 目标 | 当前状态 | 里程碑输出 |
|---|---|---|---|
| Phase A | 简历管理与版本/解析基础 | 进行中（实现态） | Resume CRUD、多版本、TXT 解析、历史基线 |
| Phase B | JD 管理与复用 | 设计完成（Wave5）+ 门禁补强（Wave6） | JD/知识库接口草案 + 发布前检查口径 |
| Phase C | 分析引擎增强 | 设计完成（Wave5）+ 验收补强（Wave6） | RAG/Knowledge 契约 + Wave6 E2E 门禁定义 |
| Phase D | 历史关联与追溯 | 设计完成（Wave5）+ 回退补强（Wave6） | Auth refresh 失败/回退的追溯验证路径 |
| Phase E | 质量与发布工程化 | 文档就绪（Wave6） | Release Checklist + Rollback Runbook + Readiness Report |

---

## Wave 节奏更新

- Wave1（完成）：PRD 差距盘点 + 交付路线 + Phase A 最小 CRUD。
- Wave2（完成）：解析状态链路与历史能力补强（基础可用）。
- Wave3（完成，文档轨）：JD 实体化设计与 Interview Flow 状态机冻结。
- Wave4（完成，文档轨）：Auth Session / RAG Retriever 规格 + 发布回滚补强。
- Wave5（完成，文档轨）：Auth API + Knowledge Base 契约冻结与发布轨补齐。
- **Wave6（本次回填，文档与交接轨）**：
  - 回填 `prd-gap-analysis` / `prd-delivery-plan` 的 Wave6 进度与剩余差距
  - 新增 `release-readiness-report` 汇总门禁与上线建议
  - 更新发布与回滚文档中的 auth refresh / e2e 专项
  - sprint-progress 增加 Wave6 模板与交接回填
- **Wave7（本次回填，用户可用转轨）**：
  - 新增 `user-facing-ux-guidelines`，定义信息层级/文案语气/技术字段默认隐藏规则
  - 新增 `release-readiness-ux-checklist`，将 UX 验收纳入发布门禁
  - 在 PRD 差距与路线中补充“工程可用 -> 用户可用”转轨说明
  - sprint-progress 增加 Wave7 文档回填记录
- Wave8（建议）：进入“最小实现 + 自动化门禁 + UX 规范落地验证”执行闭环。

---

## Wave6 交付清单（已回填）

### 1) PRD 进度与差距回填
- 文档：
  - `docs/prd-gap-analysis.md`
  - `docs/prd-delivery-plan.md`
- 核心内容：
  - Wave6 进度增量（门禁与交接轨）
  - 各模块剩余差距与下一波 DoD
  - Wave7 执行优先级建议

### 2) 发布就绪报告新增
- 文档：`docs/release-readiness-report.md`（新增）
- 核心内容：
  - 门禁汇总：契约、配置、验证、监控、回滚
  - 放行策略：文档发布可放行、功能发布条件放行
  - 上线前 P0/P1 行动清单

### 3) 发布与回滚轨 Wave6 补强
- 文档更新：
  - `docs/release-checklist.md`
  - `docs/rollback-runbook.md`
- 核心增量：
  - Auth refresh 冒烟检查与失败路径验收
  - Wave6 E2E 门禁（续期成功/失败、降级路径）
  - 回滚后 refresh 场景验证项

### 4) 进度回填与交接模板升级
- 文档更新：`sprint-progress.md`
- 核心增量：
  - 新增 Wave6 执行模板
  - 增加 Wave6 文档与交接轨回填记录

---

## Wave7 交付清单（已回填）

### 1) UX 规范基线新增
- 文档：`docs/user-facing-ux-guidelines.md`（新增）
- 核心内容：
  - 信息层级（L1 决策 / L2 解释 / L3 技术）
  - 文案语气与状态文案模板
  - 技术字段默认隐藏与受控露出规则

### 2) UX 发布验收清单新增
- 文档：`docs/release-readiness-ux-checklist.md`（新增）
- 核心内容：
  - 发布前 UX 验收项（层级/文案/隐藏策略/恢复路径）
  - Go / Conditional Go / No-Go 判定标准
  - 发布后 T+30/T+24h 抽检项

### 3) PRD 转轨说明回填
- 文档：
  - `docs/prd-gap-analysis.md`
  - `docs/prd-delivery-plan.md`
- 核心内容：
  - 明确“工程可用 -> 用户可用”转轨目标与 DoD
  - 新增 Wave8 的 UX 落地执行项

### 4) 进度回填
- 文档：`sprint-progress.md`
- 核心内容：
  - 新增 Wave7 文档回填记录与验收建议

---

## Wave7 转轨说明（工程可用 -> 用户可用）

### 转轨目标
- **工程可用**（Wave6）：系统具备契约、门禁、回滚可执行能力。
- **用户可用**（Wave7）：用户在首屏即可完成判断与下一步行动，异常场景可恢复，技术信息默认不打扰。

### 转轨实施路径（文档层）
1. 新增 UX 规范基线：`docs/user-facing-ux-guidelines.md`
2. 新增 UX 发布验收：`docs/release-readiness-ux-checklist.md`
3. 将 UX 验收纳入 release-readiness 评审入口，与 checklist/runbook 并行执行

### 转轨完成定义（文档 DoD）
- PRD 与交付路线中已明确“信息层级 + 文案语气 + 技术字段隐藏”三项硬规则。
- 发布前验收存在可执行清单，且具备 Go/Conditional-Go/No-Go 判定标准。
- sprint-progress 中具备可追溯的 Wave7 回填记录。

---

## 下一阶段（Wave8）建议执行计划

### Wave8-A：Auth refresh 最小实现闭环
- 目标：按 `auth-api-spec` 与 Wave6 门禁，完成 refresh 实际可运行链路。
- DoD：
  - `/api/auth/session/refresh` 行为与契约一致
  - 过期会话可刷新并重试成功
  - refresh 失败场景具备可恢复路径（重建会话/重新登录）

### Wave8-B：KB/RAG 最小 E2E 落地
- 目标：把 Wave6 文档门禁转为自动化执行。
- DoD：
  - 至少 4 条关键 E2E 用例纳入自动执行
  - 覆盖命中与降级双路径
  - 报告与截图可归档、可追溯

### Wave8-C：发布工程化收口
- 目标：形成可稳定执行的预发与上线流程。
- DoD：
  - CI 覆盖 backend pytest + frontend build + Wave6 E2E smoke
  - 完成 1 次预发回滚演练并记录 RTO
  - 发布后 30 分钟观测项包含 refresh 指标

### Wave8-D：UX 规范实现与验收闭环
- 目标：把 Wave7 文档规范落实到核心页面与发布流程。
- DoD：
  - 核心页面满足信息层级（L1/L2/L3）并完成走查截图归档
  - 成功/处理中/降级/失败/空态文案统一并通过产品评审
  - 技术字段默认隐藏策略在预发验证通过
  - `release-readiness-ux-checklist` 有完整勾选记录与判定结论

---

## Wave6 交接轨结构变化（新增）

### 交付包拆分（由“单文档交付”升级为“可执行交接包”）
1. **PRD 进度包**：`prd-gap-analysis` + `prd-delivery-plan`
2. **发布门禁包**：`release-checklist` + `release-readiness-report`
3. **故障应急包**：`rollback-runbook`
4. **进度证据包**：`sprint-progress`（模板 + 回填记录）

### 交接执行要求
- 每个交付包必须具备“负责人 + 验收时间 + 证据路径”。
- 对“条件通过”项需在 Wave7 首个迭代前完成收口，不得跨两波遗留。
- 发布前 Gate Review 以 Readiness Report 作为唯一汇总入口。

## Wave8 启动前门禁（新增）
- [ ] Auth refresh 最小实现已在预发验证通过（含失败回退）
- [ ] Wave6 E2E 4 场景已纳入自动化并输出报告
- [ ] 回滚演练至少完成 1 次，RTO 与恢复步骤已复盘
- [ ] checklist/runbook 的执行人已完成交接演练
- [ ] `docs/release-readiness-ux-checklist.md` 已完成首轮验收勾选
- [ ] 核心页面技术字段默认隐藏策略已完成走查

## 风险与依赖（Wave7→Wave8）

1. **refresh 链路实现风险（状态不一致/重试雪崩）**
   - 依赖：会话存储一致性、前端重试策略、Cookie/Token 配置。
2. **E2E 稳定性风险（环境波动导致误报）**
   - 依赖：测试数据基线、超时重试策略、隔离环境。
3. **契约到实现漂移风险**
   - 依赖：接口版本冻结流程、变更审查门禁、发布前契约回归。
4. **发布窗口与演练覆盖不足风险**
   - 依赖：值班排班、预发容量、回滚演练执行质量。
5. **工程口径与用户口径脱节风险（UX 漂移）**
   - 依赖：产品文案评审机制、设计走查、前端实现一致性校验。

---

## 退出条件（进入“PRD 完整交付执行态”）

- Auth refresh、Knowledge 检索、analyze 降级三条最小生产链路可运行。
- Wave6 E2E 门禁纳入 CI，并可输出版本化证据。
- Release Checklist 与 Rollback Runbook 可由值班同学独立执行。
- UX 验收清单可独立执行并产出 Go/Conditional-Go/No-Go 结论。
- sprint-progress 中 Wave4/5/6/7/8 区块可持续回填，形成交付证据链。
