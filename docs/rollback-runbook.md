# Rollback Runbook（回滚与应急手册）

> 适用项目：Career Hero MVP（Next.js + FastAPI）
>
> Wave6 补充：在 Wave5 基础上增加 **Auth Refresh 故障止血 + E2E 门禁失败应急**。

---

## 1. 触发条件（满足任一项进入回滚评估）

- `/api/analyze` 持续异常（500 比例明显上升）
- 前端主流程不可用（无法提交或结果异常）
- Auth 问题导致大面积 401/403
- Auth refresh 异常导致会话无法续期（重试持续失败）
- Auth API 契约不一致导致前后端联调失败（字段缺失/语义冲突）
- RAG/KB 引发延迟或失败显著上升，影响核心成功率
- Wave6 E2E 关键场景连续失败且无法在窗口内修复
- 数据安全风险（误删、不可逆变更）

> 建议：10 分钟内做“修复优先 vs 立即回滚”决策；若预计修复超过 15 分钟，优先回滚。

---

## 2. 角色分工

- **Incident Commander**：决策与对外口径
- **Backend 执行人**：后端与配置回退
- **Frontend 执行人**：前端制品回退
- **数据/索引执行人**：DB、RAG、KB 索引处理
- **记录员**：时间线与证据留存

---

## 3. 总流程

1. 止血：冻结发布与新变更
2. 评估：定位前端/后端/Auth/Refresh/RAG/KB/数据范围
3. 决策：局部降级或全量回滚
4. 执行：后端 -> 前端 -> 数据/索引
5. 验证：健康 + 主流程 + 专项接口
6. 通报：恢复状态、影响面、后续计划
7. 复盘：根因与防再发动作

---

## 4. 回滚前检查（必须做）

- [ ] 目标回滚版本（Tag/Commit）：`____________`
- [ ] 当前版本（Tag/Commit）：`____________`
- [ ] 当前配置快照已保存（含 Auth/Refresh/RAG/KB 开关）
- [ ] 数据备份状态：`已备份 / 未备份`
- [ ] 对外“故障处理中”通知已发送

---

## 5. 快速降级开关（优先考虑）

> 目标：在不完全回退代码的情况下先恢复可用性。

### 5.1 关闭 RAG（保留主流程）
- 设置：`CAREER_HERO_RAG_ENABLED=false`
- 预期：analyze 走原有 rule/gemini 直推链路

### 5.2 关闭 Knowledge 检索（保留主流程）
- 设置：`CAREER_HERO_KB_ENABLED=false`
- 预期：跳过知识库检索，主流程继续可用

### 5.3 Knowledge 进入只读模式
- 设置：`CAREER_HERO_KB_WRITE_MODE=readonly`
- 预期：停止新增/更新文档，保留已发布检索能力

### 5.4 Auth 切换到兼容模式
- 设置：`CAREER_HERO_AUTH_MODE=legacy`（或 `hybrid`）
- 预期：避免因新会话机制导致全量鉴权失败

### 5.5 恢复限流阈值（如误配）
- 调整 `CAREER_HERO_RATE_LIMIT_PER_MINUTE` 到稳定基线

### 5.6 Auth Refresh 故障临时止血（Wave6）
- 操作建议：
  1. 优先确认 `/api/auth/session/current` 是否仍可用
  2. 对 refresh 持续失败用户，执行“重建 guest 会话”兜底
  3. 必要时回退 `CAREER_HERO_AUTH_MODE=legacy` 保主流程
- 预期：尽快恢复 analyze/history 可用，降低 401/403 峰值

---

## 6. 后端回滚（FastAPI）

### 6.1 代码回退
```bash
# git checkout <stable-tag-or-commit>
```

### 6.2 依赖与进程
```bash
cd backend
pip install -r requirements.txt
# 按部署方式重启服务
```

### 6.3 配置回退
- 基础：`CAREER_HERO_AI_PROVIDER`、`CAREER_HERO_DB_PATH`
- Auth：`CAREER_HERO_AUTH_MODE`、`CAREER_HERO_AUTH_SECRET`、TTL 配置
- RAG：`CAREER_HERO_RAG_ENABLED`、`CAREER_HERO_RAG_INDEX_VERSION`
- KB：`CAREER_HERO_KB_ENABLED`、`CAREER_HERO_KB_INDEX_VERSION`、`CAREER_HERO_KB_WRITE_MODE`

### 6.4 验证
- `GET /health` = 200
- `POST /api/analyze` 成功
- `GET /api/history` 成功
- （如启用）`GET /api/auth/session/current` 成功
- （如启用）`POST /api/auth/session/refresh` 成功或按预期降级（Wave6）
- （如启用）`POST /api/rag/retrieve` 可用或已按预期降级
- （如启用）`POST /api/knowledge/retrieve` 可用或已按预期降级

---

## 7. 前端回滚（Next.js）

### 7.1 制品回退
```bash
# git checkout <stable-tag-or-commit>
cd frontend
npm install
npm run build
# 切换部署制品到稳定版本
```

### 7.2 配置核对
- `NEXT_PUBLIC_API_BASE_URL` 指向已恢复后端
- 前端接口契约版本与后端一致（Auth/Knowledge）

### 7.3 验证
- 首页可访问
- 输入简历/JD 后可返回结果
- 历史加载正常
- 会话过期后 refresh 或兜底路径可用（Wave6）
- 会话失效提示/重试路径可用（如启用 Auth）
- 检索降级提示路径可用（如启用 RAG/KB）

---

## 8. 数据与索引回滚

### 8.1 SQLite 数据恢复（如需）
1. 停止后端写入
2. 备份当前异常 DB 文件
3. 用最近可用备份替换
4. 启动并验证核心接口

### 8.2 RAG 索引回切（如需）
1. 记录当前索引版本号
2. 切换 `CAREER_HERO_RAG_INDEX_VERSION` 到稳定版本
3. 抽样验证检索命中与时延
4. 若仍异常，关闭 RAG 开关保可用

### 8.3 Knowledge 索引与文档回切（如需）
1. 记录当前 KB 索引版本与任务状态
2. 切换 `CAREER_HERO_KB_INDEX_VERSION` 到稳定版本
3. 必要时切只读模式，阻止继续写入
4. 抽样验证 `knowledgeBase/document/chunk` 追溯链完整
5. 若仍异常，关闭 KB 开关保可用

---

## 8.4 Wave6 场景化回退剧本（新增）

### 剧本 R1：refresh 连续失败（401/403 峰值）
1. 立即冻结发布并通知 Incident Commander。
2. 执行 `AUTH_MODE -> legacy` 或启用 guest 重建兜底。
3. 验证 `/api/auth/session/current` 与 `/api/analyze` 恢复。
4. 15 分钟内复核 401/403 比例是否回落。

### 剧本 R2：E2E-C/E2E-D 失败但主流程可用
1. 先降级检索能力（`RAG_ENABLED=false` 或 `KB_ENABLED=false`）。
2. 保留 analyze/history 主路径，关闭高风险增强能力。
3. 在发布窗口内判定“条件放行”或“全量回滚”。

### Wave6 回退目标
- 目标 RTO：`<= 15 分钟`
- 目标恢复标准：核心主流程可用 + 关键错误率回落至基线区间。

## 9. 回滚后快速验收清单

- [ ] `/health` 正常
- [ ] `/api/analyze` 正常
- [ ] 历史读取/详情正常
- [ ] 前端主路径正常
- [ ] 401/403 比例回落（Auth）
- [ ] refresh 失败率回落（Wave6）
- [ ] degraded 比例或延迟恢复（RAG/KB）
- [ ] Auth/Knowledge 契约关键字段恢复一致

---

## 10. 沟通模板

### 10.1 故障处理中
> 【Career Hero】检测到发布后异常，已进入应急处理。当前采取：`__`。预计 `__` 分钟后更新进展。

### 10.2 回滚完成
> 【Career Hero】已回滚至版本 `__`，核心功能恢复。Auth/Refresh/RAG/KB 状态：`__`。后续将提供复盘与修复计划。

---

## 11. 复盘最小模板

- 事件编号：`____________`
- 发生时间：`____________`
- 影响范围：`____________`
- 触发模块：`Backend / Frontend / Auth / Refresh / RAG / KB / Data`
- 根因：`____________`
- 为什么发布前未拦截：`____________`
- 回滚耗时：`____________`
- 永久修复项（Owner + ETA）：`____________`
