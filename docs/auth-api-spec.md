# 认证接口规格说明（Auth API Spec）

> 版本：v0.1（Wave5 文档交付轨）
>
> 依赖文档：`docs/auth-session-spec.md`
>
> 目标：冻结 Auth 对外接口与字段定义，确保前后端、测试、发布与回滚口径一致。

---

## 1. 范围与非目标

### 1.1 范围（v0.1）
- Session 级接口：创建、查询、续期、登出、访客绑定用户。
- 鉴权模式：`legacy` / `guest` / `hybrid`。
- 统一错误结构：`code/message/requestId`。
- 与核心业务接口（`/api/analyze`、`/api/history`）的 authContext 透传约定。

### 1.2 非目标（v0.1）
- 不覆盖第三方 OAuth 完整流程。
- 不覆盖复杂 RBAC 权限模型。
- 不定义管理后台用户体系。

---

## 2. 鉴权模式行为矩阵

| 模式 | 未携带 Token | Session 失效 | 适用阶段 |
|---|---|---|---|
| `legacy` | 放行（按当前 MVP 行为） | 放行/弱校验 | 兼容期 |
| `guest` | 自动创建 guest session | 401 + 引导重建会话 | 新会话基线 |
| `hybrid` | 优先识别 token，缺失时降级 guest | 401 或自动重建（按配置） | 灰度期 |

> 建议在响应体 `authContext.authMode` 明确当前执行模式，便于联调与排障。

---

## 3. 通用约定

### 3.1 Header
- `Authorization: Bearer <accessToken>`（可选，取决于模式）
- `X-Request-Id`（可选，未传则服务端生成）
- `X-Session-Id`（可选，调试用途）

### 3.2 时间与格式
- 时间字段统一使用 ISO-8601 UTC：`2026-02-25T05:30:00Z`
- ID 建议使用 UUID 字符串。

### 3.3 统一错误结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | string | 是 | 机器可读错误码（如 `AUTH_UNAUTHORIZED`） |
| `message` | string | 是 | 人类可读说明 |
| `requestId` | string | 是 | 请求追踪 ID |

### 3.4 建议错误码
- `AUTH_UNAUTHORIZED`
- `AUTH_FORBIDDEN`
- `AUTH_SESSION_EXPIRED`
- `AUTH_REFRESH_EXPIRED`
- `AUTH_TOKEN_INVALID`
- `AUTH_RATE_LIMITED`

---

## 4. 对象字段定义

### 4.1 `AuthSession`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 会话 ID |
| `userId` | string \| null | 是 | 用户 ID；访客为 null |
| `status` | enum | 是 | `ACTIVE/IDLE/EXPIRED/REVOKED` |
| `authMode` | enum | 是 | `legacy/guest/hybrid` |
| `issuedAt` | string | 是 | 签发时间 |
| `expiresAt` | string | 是 | 过期时间 |
| `lastSeenAt` | string | 否 | 最近活跃时间 |
| `scopes` | string[] | 否 | 权限范围（v0.1 可为空） |

### 4.2 `TokenBundle`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `accessToken` | string | 是 | 短期访问令牌 |
| `accessTokenExpiresAt` | string | 是 | access token 过期时间 |
| `refreshToken` | string | 否 | 可选（若采用 Cookie 可不返回明文） |
| `refreshTokenExpiresAt` | string | 否 | refresh token 过期时间 |
| `tokenType` | string | 是 | 固定 `Bearer` |

### 4.3 `AuthContext`（业务接口透传）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string \| null | 是 | 当前会话 ID |
| `userId` | string \| null | 是 | 当前用户 ID |
| `authMode` | enum | 是 | 当前模式 |
| `authStatus` | enum | 是 | `authenticated/guest/legacy/degraded` |

---

## 5. 接口定义

## 5.1 `POST /api/auth/session/guest`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `clientFingerprint` | string | 否 | 客户端指纹（匿名） |
| `resumeId` | string | 否 | 可选，首个绑定简历 |
| `resumeVersionNo` | integer | 否 | 可选，首个绑定版本 |

### 200 响应

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session` | `AuthSession` | 是 | 新建访客会话 |
| `tokens` | `TokenBundle` | 是 | 返回或通过 Cookie 写入 |
| `requestId` | string | 是 | 请求 ID |

### 异常
- `429 AUTH_RATE_LIMITED`
- `500 INTERNAL_ERROR`

---

## 5.2 `GET /api/auth/session/current`

### 请求
- Header 可携带 `Authorization` 或由 Cookie 自动识别。

### 200 响应

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session` | `AuthSession` | 是 | 当前会话信息 |
| `requestId` | string | 是 | 请求 ID |

### 异常
- `401 AUTH_UNAUTHORIZED`
- `401 AUTH_SESSION_EXPIRED`

---

## 5.3 `POST /api/auth/session/refresh`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `refreshToken` | string | 否 | 若使用 Cookie，可省略 |
| `rotate` | boolean | 否 | 默认 true |

### 200 响应

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session` | `AuthSession` | 是 | 刷新后的会话 |
| `tokens` | `TokenBundle` | 是 | 新 token 对 |
| `requestId` | string | 是 | 请求 ID |

### 异常
- `401 AUTH_REFRESH_EXPIRED`
- `401 AUTH_TOKEN_INVALID`

---

## 5.4 `POST /api/auth/session/logout`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `logoutAll` | boolean | 否 | 是否登出全部会话（v0.1 可忽略） |

### 200 响应

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `revoked` | boolean | 是 | 是否成功撤销 |
| `sessionId` | string | 是 | 被撤销会话 ID |
| `requestId` | string | 是 | 请求 ID |

### 异常
- `401 AUTH_UNAUTHORIZED`

---

## 5.5 `POST /api/auth/session/bind-user`

> 预留：把 guest session 升级为登录态（保留历史关联）。

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `provider` | string | 是 | `email/github/google/internal` |
| `providerToken` | string | 是 | 第三方 token 或登录凭证 |
| `email` | string | 否 | 可选补充 |

### 200 响应

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session` | `AuthSession` | 是 | userId 已绑定 |
| `tokens` | `TokenBundle` | 是 | 新 token 对 |
| `requestId` | string | 是 | 请求 ID |

### 异常
- `401 AUTH_UNAUTHORIZED`
- `403 AUTH_FORBIDDEN`

---

## 6. 与业务接口的契约补充

### 6.1 `POST /api/analyze`
- 入参建议新增：`sessionId?`, `enableRetrieval?`, `knowledgeBaseIds?`
- 出参建议新增：`authContext`（见 4.3）

### 6.2 `GET /api/history`
- 建议支持过滤：`sessionId?`, `userId?`（`userId` 受权限限制）
- 每条记录建议返回：`sessionId`, `userId`, `requestId`

---

## 7. 安全与限流基线

- Access Token TTL：30 分钟（建议）
- Refresh Token TTL：14 天（建议）
- Cookie：`HttpOnly + Secure + SameSite=Lax`
- 写接口启用 CSRF 防护（Cookie 鉴权场景）
- Auth 相关接口建议单独限流（按 IP + session 维度）

---

## 8. Wave5 文档验收建议

- [ ] 所有 Auth 接口请求/响应字段已冻结并通过评审。
- [ ] 前后端对 `authMode`、`authStatus`、错误码口径一致。
- [ ] release-checklist 与 rollback-runbook 已引用本规范。
- [ ] 可基于本规范直接编写 API smoke 用例。
