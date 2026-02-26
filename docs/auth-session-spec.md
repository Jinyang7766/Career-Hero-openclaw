# 认证与会话规格说明（Auth Session Spec）

> 版本：v0.1（Wave4 文档冻结版）
>
> 目标：在不破坏现有 MVP 体验的前提下，为多用户隔离、历史追溯、发布可回滚提供统一会话基线。

---

## 1. 设计目标

1. 支持 **Guest Session** 与后续登录态并存。
2. 保留现有 `requestId/sessionId` 可观测链路，避免追踪断裂。
3. 建立可执行的会话生命周期（创建、续期、失效、撤销）。
4. 对历史数据实现最小归属隔离（按 session/user 维度）。

---

## 2. 非目标（v0.1 不覆盖）

- 不实现复杂 RBAC 权限体系。
- 不引入多租户组织模型。
- 不在本版中实现第三方登录全量流程（仅预留接口）。

---

## 3. 术语与实体

- **User**：已认证用户（可为空，表示访客）。
- **Session**：客户端可持续使用的一段认证上下文。
- **Access Token**：短期访问凭证。
- **Refresh Token**：续期凭证，受更严格保护。

建议核心实体：

### 3.1 `auth_users`（建议）
- `id`（UUID）
- `provider`（email/github/google/internal）
- `provider_subject`
- `email`（可空）
- `created_at` / `updated_at`

### 3.2 `auth_sessions`（建议）
- `id`（UUID）
- `user_id`（可空，访客会话）
- `status`（ACTIVE/IDLE/EXPIRED/REVOKED）
- `issued_at`
- `expires_at`
- `last_seen_at`
- `ip_hash`
- `ua_hash`
- `revoked_at` / `revoke_reason`

### 3.3 `auth_refresh_tokens`（建议）
- `id`（UUID）
- `session_id`
- `token_hash`
- `expires_at`
- `rotated_from`（可空）
- `revoked_at`

---

## 4. Session 状态机

| 状态 | 含义 | 终态 |
|---|---|---|
| `ACTIVE` | 正常可用 | 否 |
| `IDLE` | 超过活跃阈值但可恢复 | 否 |
| `EXPIRED` | 到达过期时间 | 是 |
| `REVOKED` | 主动撤销或风控封禁 | 是 |

### 4.1 允许迁移
- `ACTIVE -> IDLE`：超过 idle timeout
- `IDLE -> ACTIVE`：收到合法 refresh/访问
- `ACTIVE/IDLE -> EXPIRED`：超过 hard TTL
- `ACTIVE/IDLE -> REVOKED`：登出、风控、管理员操作

### 4.2 非法迁移
- `EXPIRED/REVOKED` 不可恢复为 `ACTIVE`（需新建 Session）

---

## 5. Token 与安全策略（建议基线）

- Access Token TTL：30 分钟
- Refresh Token TTL：14 天
- Refresh Token 每次续期执行轮换（rotation）
- Cookie：`HttpOnly + Secure + SameSite=Lax`
- 对写接口启用 CSRF 防护（双提交 Token 或 header 校验）
- 敏感日志脱敏（禁止记录原始 token）

---

## 6. API 草案

### 6.1 会话创建（访客）
- `POST /api/auth/session/guest`
- 响应：`sessionId`, `accessToken`, `expiresAt`

### 6.2 当前会话
- `GET /api/auth/session/current`
- 响应：`sessionId`, `userId?`, `status`, `expiresAt`, `scopes[]`

### 6.3 刷新会话
- `POST /api/auth/session/refresh`
- 响应：新 `accessToken`，可选新 `refreshToken`

### 6.4 登出
- `POST /api/auth/session/logout`
- 行为：将 session 标记为 `REVOKED`

### 6.5 预留：登录绑定
- `POST /api/auth/session/bind-user`
- 作用：访客会话升级为登录会话（保留历史关联）

错误返回沿用统一结构：`code/message/requestId`。

---

## 7. 与现有功能的兼容要求

1. analyze/history 继续支持当前调用方式，不强制前端一次性切换。
2. 若无有效 Session，默认创建访客会话（可配置开关）。
3. 历史查询至少支持按 `sessionId` 过滤，后续再扩展到 `userId`。
4. 现有 `requestId` 不变，新增 `sessionId` 透传链路。

---

## 8. 可观测性与告警建议

核心指标：
- Session 创建成功率
- Refresh 成功率
- 401/403 比例
- 被撤销 Session 数量
- 平均会话生命周期时长

日志字段建议：
- `requestId`, `sessionId`, `userId?`, `auth_status`, `error_code`, `duration_ms`

---

## 9. 发布与回滚要点

### 发布前
- 完成 secret 注入与轮换策略校验。
- 预发验证 guest/refresh/logout 三条最小链路。

### 回滚时
- 支持切回 legacy session 模式（通过配置开关）。
- 已签发 Token 在回滚模式下应有兼容兜底（不导致全量 401）。

---

## 10. 验收建议（Wave5 实施前置）

- [ ] Auth Session 状态机可被接口行为完整覆盖。
- [ ] guest 与 legacy 模式可灰度切换。
- [ ] 历史记录可按 session 归属查询。
- [ ] 回滚脚本可在预发完成至少一次演练。
