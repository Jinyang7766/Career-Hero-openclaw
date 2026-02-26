# PRD v2.0（第1-10部分）验收矩阵

> 版本：v1.0（2026-02-26）
>
> 范围：仅覆盖 PRD v2.0 第1~10部分，**明确忽略第11部分**。
>
> 用途：建立“需求点 -> 用例 -> 验收状态 -> 证据路径”的统一追踪口径。

---

## 状态定义

- ✅ 已通过：已有实现 + 可复现证据（测试/文档/截图/日志）
- 🟡 部分通过：有基础能力，但仍存在关键缺口
- ⏳ 未开始：尚无可验收实现或证据
- ⛔ 阻塞：存在 P0 阻断，当前不可放行

---

## PRD10 验收总表

| PRD部分 | 需求点（摘要） | 核心用例（最小验收） | 当前状态 | 证据路径 | 关键缺口 / 下一步 |
|---|---|---|---|---|---|
| 第1部分 | 目标、范围、成功标准冻结 | UC1: 明确 In/Out Scope；UC2: 形成统一 DoD 口径 | ✅ | `docs/prd10-delivery-plan.md` | 后续仅做变更记录，不再改范围口径 |
| 第2部分 | 用户角色与会话模型 | UC1: 访客/登录用户可识别；UC2: 会话隔离生效 | 🟡 | `docs/auth-session-spec.md`；`backend/tests/test_prd_followup_gate.py::test_wave5_session_isolation_history_and_interview` | 角色权限矩阵还需落到前端可见性规则 |
| 第3部分 | 端到端核心流程 | UC1: 输入简历+JD完成分析；UC2: 异常时有恢复动作 | 🟡 | `backend/tests/test_api.py::test_minimal_e2e_chain_analyze_history_detail_export`；`docs/user-facing-ux-guidelines.md` | 异常恢复 UX 仍需页面级证据 |
| 第4部分 | 简历管理与解析 | UC1: Resume CRUD+版本；UC2: 解析状态可查询 | 🟡 | `backend/tests/test_api.py::test_resume_crud_and_versioning`；`docs/wave2-test-plan.md` | PDF/DOCX 解析链路与异步任务仍待落地 |
| 第5部分 | JD 管理与岗位画像 | UC1: JD 可管理；UC2: 可作为检索输入参与分析 | 🟡 | `docs/knowledge-base-spec.md`；`backend/tests/test_prd_followup_gate.py::test_wave5_knowledge_update_delete_contract_and_flow` | JD 实体化主链路仍需更完整实现与前端入口 |
| 第6部分 | 匹配分析与优化建议 | UC1: 返回 score/scoreBreakdown/insights；UC2: 检索失败可降级 | 🟡 | `backend/tests/test_api.py::test_analyze_success_writes_history_and_detail`；`backend/tests/test_api.py::test_analyze_rag_mode_switch_and_topk` | retrievalMeta 稳定性与质量指标仍需持续化 |
| 第7部分 | 面试流程（创建、问答、暂停、恢复、完成） | UC1: create/list/detail；UC2: pause/resume/answer/finish 生命周期闭环 | 🟡 | `docs/interview-flow-spec.md`；`backend/tests/test_api.py::test_interview_list_detail_pause_resume_and_answer_chain`；`backend/tests/test_prd_followup_gate.py::test_wave5_interview_history_finished_sessions` | 评估质量与异常恢复场景仍需扩展验收 |
| 第8部分 | 历史追溯与导出 | UC1: history 列表/详情可查；UC2: txt/json/pdf 导出 | 🟡 | `backend/tests/test_api.py::test_history_request_id_filter_and_default_limit`；`backend/tests/test_api.py::test_export_txt_json_pdf` | 版本差异对比与证据级追溯仍缺实现 |
| 第9部分 | 认证、安全、限流与隔离 | UC1: login/me/logout/refresh；UC2: 统一错误与限流策略 | 🟡 | `docs/auth-api-spec.md`；`backend/tests/test_api.py::test_auth_relogin_refresh_and_session_bound_token`；`backend/tests/test_api.py::test_rate_limit_and_duplicate_protection` | refresh 失败恢复与发布门禁联动需补强 |
| 第10部分 | 质量保障、发布与运维 | UC1: 发布清单可执行；UC2: 回滚剧本可演练并复盘 | 🟡 | `docs/release-checklist.md`；`docs/release-readiness-report.md`；`docs/rollback-runbook.md` | E2E 进 CI 与预发演练证据化仍需收口 |

---

## P0/P1 收口清单（用于放行评审）

### P0（必须先收口）
- [ ] 第4部分：解析异步状态链路（含失败重试）形成可验收闭环。
- [ ] 第9部分：refresh 失败恢复路径完成并纳入发布门禁。
- [ ] 第10部分：关键 E2E 场景纳入 CI，失败可定位到 requestId/sessionId。

### P1（建议本轮收口）
- [ ] 第5部分：JD 实体化入口与知识复用路径形成前后端闭环。
- [ ] 第7部分：面试中断恢复与评分解释的用户可读性验收。
- [ ] 第8部分：历史差异对比（结果 + 证据）能力。

---

## 使用说明（回填规则）

1. 每个 PRD 部分至少保留 2 条可执行用例（成功路径 + 异常/降级路径）。
2. 状态从“⏳/🟡 -> ✅”必须附证据路径（测试、截图、日志或报告）。
3. 发现新缺口时，先更新本矩阵，再更新 `sprint-progress.md` 波次回填。
4. 第11部分需求若出现，单独建文档，不回填到本矩阵。
