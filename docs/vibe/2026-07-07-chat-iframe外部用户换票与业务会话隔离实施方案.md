# chat-iframe 外部用户登录与业务会话隔离实施方案

日期：2026-07-07  
状态：已审核，待实施  
范围：仅实现外部用户自动登录、自动建号、业务会话列表隔离；页面/附件上下文注入另开任务处理。

## 1. 目标

让 `chat-iframe` 嵌入外部业务系统时，可以做到：

1. 具体嵌入页面初始化 `DocMindChatIframe` 时传入外部身份和业务页面信息。
2. `chat-iframe` 根据这些字段自动计算业务会话 scope。
3. docMind 自动创建或复用外部映射用户。
4. 同一外部用户在不同业务页面中看到不同的会话列表。
5. 不影响 docMind 原有登录、API Key、普通聊天列表和主站聊天能力。

本次不处理“问网页/问文件”的上下文注入方式，也不调整当前 query 拼接逻辑。

## 2. 总体决策

本方案采用双模式：

```text
模式 A：可信换票模式
DocMindChatIframe.init() 传入 tokenExchangeUrl
-> 父页面脚本调用外部系统后端 tokenExchangeUrl
-> 外部系统后端使用 docMind superadmin API Key 调用 docMind
-> docMind 返回外部映射用户 token

模式 B：内网低信任自助登录模式
DocMindChatIframe.init() 不传 tokenExchangeUrl
-> 父页面脚本直接调用 docMind 的 /api/chat-iframe/token
-> docMind 根据浏览器传入的外部系统和外部用户身份自动创建或复用普通用户
-> docMind 返回普通用户 token
```

关键限制：

- 不新增 `apikey` 前端字段。
- 不再要求、也不建议在 `DocMindChatIframe.init()` 中显式传入 docMind 用户 token。
- 不允许在 `DocMindChatIframe.init()` 中传入 superadmin token 或 superadmin API Key。
- superadmin API Key 只能放在后端服务中，不能进入浏览器、HTML、JS、Network 请求或 iframe 配置。
- 模式 B 是内网低信任模式，只适合低风险聊天入口；它不能证明外部系统当前登录用户身份真实可靠。

### 2.1 审核结论

结合当前 docMind 代码调研，方案可行，且不需要新增数据表：

- 用户登录 token 仍沿用现有 JWT 结构，只包含 `sub=user.id`；不新增 `source_system`、`agent_id`、`conversation_scope_key` 等 claim。
- `source_system` 通过 `users.uid=ext_{source_system}_{external_user_id}` 固化到外部映射用户。
- 自动建号可复用现有 `users` 表，默认 `role=user`、`department_id=1`，满足 `get_required_user` 对部门的要求。
- 会话 scope 可写入现有 `conversations.extra_metadata`，列表查询在 repository 层增加 JSON 字段过滤即可，不需要迁移表结构。
- 模式 B 的低信任风险可通过“默认关闭 + source/origin 白名单 + CORS + 简单限流 + 操作日志”控制在内网低风险场景内。

需要补强的实施约束：

- `uid` 最终会写入 `conversations.uid`，该字段是 `String(64)`，必须校验 `ext_{source_system}_{external_user_id}` 总长度不超过 64。
- 默认部门 `id=1` 必须存在；不存在时返回清晰错误，不要静默创建其他部门。
- 如果相同 `uid` 的用户已软删除，不自动恢复或重建，返回冲突错误，由超级管理员人工处理。
- `/api/chat-iframe/token` 默认关闭，且应纳入轻量 IP 限流，避免误配置时被刷号。
- 父页面脚本的 token 获取是异步流程，必须处理换票失败状态，不能把空 token 下发给 iframe 后让聊天静默失败。

## 3. 外部映射用户规则

用户 `uid` 使用：

```text
ext_{source_system}_{external_user_id}
```

示例：

```text
ext_oa_10086
ext_hr_20001
```

解析规则：

1. 去掉 `ext_` 前缀。
2. 按第一个 `_` 拆分。
3. 第一段是 `source_system`。
4. 剩余部分是 `external_user_id`。

第一版约束：

- `source_system` 必须是稳定系统 ID，只允许字母和数字，不允许 `_`。
- `external_user_id` 必须是稳定外部用户编号，只允许字母和数字，不允许 `_`，保证 `ext_{source_system}_{external_user_id}` 可稳定按 `_` 拆分。
- `external_user_name` 必传，用于 docMind 后台识别用户，去空格后不能为空。
- `uid = ext_{source_system}_{external_user_id}` 总长度必须不超过 64，匹配 `conversations.uid` 字段长度。
- `function_id`、`business_id` 不进入 token 接口，但会用于生成 `conversation_scope_key`；二者也需要非空、限制长度，并禁止换行等控制字符。

自动创建用户默认值：

```text
uid = ext_{source_system}_{external_user_id}
username = {external_user_name}_{uid}
role = user
department_id = 1
password_hash = 随机不可知密码
```

如果用户已存在：

- 直接复用该用户。
- 不自动覆盖 `username`、`role`、`department_id`。
- 避免覆盖超级管理员在 docMind 后台做过的调整。

如果相同 `uid` 的用户已软删除：

- 不自动恢复。
- 不重新创建同名 `uid` 用户。
- 返回 409，提示由超级管理员在后台处理该外部映射用户。

## 4. 模式 A：可信换票模式

### 4.1 docMind 服务端接口

新增：

```text
POST /api/external-users/token
```

该接口只允许外部系统后端调用。这里的 docMind API Key 指超级管理员 API Key，由外部系统后端保存；不是新建外部用户自己的 API Key。

请求头：

```text
Authorization: Bearer <docMind superadmin API Key>
Content-Type: application/json
```

请求体：

```json
{
  "source_system": "oa",
  "external_user_id": "10086",
  "external_user_name": "张三"
}
```

响应：

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user_id": 123,
  "uid": "ext_oa_10086",
  "username": "张三_ext_oa_10086",
  "role": "user",
  "department_id": 1,
  "source_system": "oa"
}
```

说明：

- 调用方必须是 `current_user.role == "superadmin"`。
- 接口不接收 `agent_id`，因为 token 只表示“谁登录了”。
- 接口不接收 `conversation_scope_key`，因为业务页面范围不应绑定到用户 token。
- 接口不接收 `function_id`、`business_id`，这两个字段只属于业务会话 scope。
- token 不新增自定义 claim；`source_system` 通过 `uid=ext_{source_system}_{external_user_id}` 绑定到外部映射用户。
- 返回普通 docMind JWT，不增加 iframe 专用权限 claim。
- token 有效期沿用现有 `AuthUtils.create_access_token()` 默认策略。

### 4.2 外部系统接入链路

外部业务页面：

```js
DocMindChatIframe.init({
  apiBaseUrl: 'http://docmind.example.local',
  tokenExchangeUrl: '/api/docmind-chat-token',
  agentId: 'default-chatbot',
  source_system: 'oa',
  function_id: 'contractApproval',
  business_id: 'contract-20260706-001',
  external_user_id: '10086',
  external_user_name: '张三'
})
```

链路：

```text
外部业务页面
-> DocMindChatIframe 父页面脚本
-> 外部系统后端 tokenExchangeUrl
-> docMind /api/external-users/token
-> 返回普通用户 token
-> 注入 iframe
```

外部系统后端负责：

- 根据自身登录态确认当前外部用户。
- 保存 docMind superadmin API Key。
- 调用 docMind `/api/external-users/token`。
- 把 docMind 返回的普通用户 token 返回给父页面脚本。

## 5. 模式 B：内网低信任自助登录模式

### 5.1 定位

当 `DocMindChatIframe.init()` 不传 `tokenExchangeUrl` 时，父页面脚本直接调用 docMind：

```text
POST /api/chat-iframe/token
```

该模式用于减少外部系统后端改造成本。它默认信任企业内网和接入页面，不要求外部系统后端参与。

风险边界必须写清楚：

- 浏览器传入的 `external_user_id` 可以被篡改。
- 该模式不能证明用户确实是外部系统中的当前登录用户。
- 该模式不适合承载强权限、敏感知识库、跨部门数据或审批类操作。
- 该模式只创建或复用普通用户，不赋予管理员能力。

### 5.2 docMind 自助登录接口

新增：

```text
POST /api/chat-iframe/token
```

请求头：

```text
Content-Type: application/json
Origin: <业务系统页面 origin>
```

请求体：

```json
{
  "source_system": "oa",
  "external_user_id": "10086",
  "external_user_name": "张三"
}
```

响应：

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user_id": 123,
  "uid": "ext_oa_10086",
  "username": "张三_ext_oa_10086",
  "role": "user",
  "department_id": 1,
  "source_system": "oa"
}
```

说明：

- `/api/chat-iframe/token` 和 `/api/external-users/token` 一样，只负责换取当前外部映射用户的 docMind token。
- 该 token 不绑定 `agent_id`、`function_id`、`business_id`、`conversation_scope_key`。
- `source_system` 通过 `uid=ext_{source_system}_{external_user_id}` 体现；响应中额外返回 `source_system` 只方便前端和日志排查。
- `function_id`、`business_id` 仍由 `DocMindChatIframe.init()` 接收，用来在父页面脚本中生成 `conversationScopeKey`。

### 5.3 开关与校验

新增运行配置：

```text
CHAT_IFRAME_AUTO_LOGIN_ENABLED=false
CHAT_IFRAME_ALLOWED_SOURCES=oa,hr,erp
CHAT_IFRAME_ALLOWED_ORIGINS=http://oa.example.local,http://hr.example.local
CHAT_IFRAME_TOKEN_RATE_LIMIT_PER_MINUTE=60
```

第一版行为：

- `CHAT_IFRAME_AUTO_LOGIN_ENABLED` 不为 `true` 时，`/api/chat-iframe/token` 返回 403。
- `CHAT_IFRAME_ALLOWED_SOURCES` 为空时不做 source 白名单校验；非空时 `source_system` 必须命中。
- `CHAT_IFRAME_ALLOWED_ORIGINS` 为空时不做 origin 白名单校验；非空时请求 `Origin` 必须命中。
- token 接口不接收 `function_id`、`business_id`，这两个字段只用于父页面脚本生成会话 scope。
- 所有请求字段都做格式和长度校验。
- 只允许创建 `role=user`、`department_id=1` 的普通用户。
- 创建前检查默认部门 `id=1` 是否存在；不存在时返回 500 或 409，并提示系统默认部门缺失。
- 复用已有用户时不覆盖后台调整过的用户信息。
- 复用到已软删除用户时返回 409，不自动恢复。
- 成功换票后记录操作日志，至少包含 `source_system`、`external_user_id`、`Origin`；日志使用已创建或复用用户的 `user.id`。
- 失败请求使用普通 logger 记录原因，不写 `OperationLog(user_id=None)`，因为当前 `operation_logs.user_id` 非空。
- `/api/chat-iframe/token` 需要加入轻量 IP 限流，默认每分钟 60 次；配置为空或小于等于 0 时关闭该限流。

说明：

- 该模式仍受现有 `YUXI_CORS_ORIGINS` 影响。外部业务页面要直接调用 docMind API，docMind 需要允许对应 origin 的跨域请求。
- `CHAT_IFRAME_ALLOWED_ORIGINS` 是接口级安全校验，`YUXI_CORS_ORIGINS` 是浏览器跨域策略，两者不是一回事。
- 如果外部业务页面跨域直连 docMind，`YUXI_CORS_ORIGINS` 必须包含业务页面 origin；如果业务系统反代 docMind API 到同源 `/api`，则不需要额外 CORS。

## 6. 业务会话隔离

会话列表隔离使用可选 `conversation_scope_key`，由父页面脚本基于业务字段自动拼接：

```text
conversation_scope_key = {source_system}:{function_id}:{business_id}
```

示例：

```text
oa:contractApproval:contract-20260706-001
oa:invoiceApproval:invoice-20260706-009
```

它是“会话列表分组键”，不是权限边界。第一版只保证列表按 scope 过滤；如果后续要把 scope 做成强安全边界，再单独扩展所有 thread/run 接口的 scope 校验。

接口行为：

```text
GET /api/chat/threads?agent_id=default-chatbot&conversation_scope_key=oa:contractApproval:contract-20260706-001
```

规则：

- `conversation_scope_key` 不传或为空：保持当前行为。
- 传入时：只返回当前 `uid + agent_id + status + metadata.conversation_scope_key` 匹配的会话。
- 创建会话时把 scope 写入 `metadata.conversation_scope_key`。

创建会话请求示例：

```json
{
  "agent_id": "default-chatbot",
  "title": "来文咨询",
  "metadata": {
    "source": "chat-iframe",
    "conversation_scope_key": "oa:contractApproval:contract-20260706-001"
  }
}
```

## 7. 后端实施方案

### 7.1 新增外部用户服务

新增文件：

```text
backend/package/yuxi/services/external_user_service.py
```

职责：

- 校验外部用户字段。
- 生成 `uid = ext_{source_system}_{external_user_id}`。
- 查找或创建普通用户。
- 签发普通用户 JWT。
- 检查默认部门、软删除冲突、用户名冲突。

核心函数：

```python
async def exchange_external_user_backend_token(
    *,
    source_system: str,
    external_user_id: str,
    external_user_name: str,
    db: AsyncSession,
) -> dict:
    ...


async def exchange_external_user_iframe_token(
    *,
    source_system: str,
    external_user_id: str,
    external_user_name: str,
    origin: str | None,
    db: AsyncSession,
) -> dict:
    ...
```

实现要点：

- `exchange_external_user_backend_token()` 服务于模式 A：外部系统后端可信换票。
- `exchange_external_user_iframe_token()` 服务于模式 B：iframe 内网低信任自助换票。
- 两个函数复用同一个查找/创建外部映射用户逻辑，避免两套规则漂移。
- 字段校验包含：
  - `source_system` 匹配 `^[A-Za-z0-9]+$`。
  - `external_user_id` 匹配 `^[A-Za-z0-9]+$`。
  - `external_user_name.strip()` 非空。
  - 生成后的 `uid` 长度不超过 64。
- 查找用户时先查 `User.uid == uid`，如果命中且 `is_deleted == 1`，返回 409。
- 创建用户前查询 `Department.id == 1`；不存在时返回清晰错误。
- 创建用户名使用 `f"{external_user_name}_{uid}"`，如冲突追加短后缀，例如 `_1`、`_2`。
- 创建密码使用 `AuthUtils.hash_password(secrets.token_urlsafe(32))`，不返回、不记录明文密码。
- 成功签发 token 后更新 `user.last_login = utc_now_naive()`，让后台能看到外部映射用户最近使用时间。
- 不为简单逻辑拆太多 helper，只拆字段校验、查找/创建用户这类明确复用点。

### 7.2 新增可信换票 router

新增文件：

```text
backend/server/routers/external_user_backend_token_router.py
```

职责：

- 定义 `ExternalUserTokenRequest`、`ExternalUserTokenResponse`。
- 暴露 `POST /external-users/token`，最终路径为 `/api/external-users/token`。
- 依赖 `get_required_user` 获取调用方。
- 要求调用方 `current_user.role == "superadmin"`。
- 调用 `exchange_external_user_backend_token()`。

注册到：

```text
backend/server/routers/__init__.py
```

### 7.3 新增 chat-iframe 自助登录 router

新增文件：

```text
backend/server/routers/external_user_iframe_token_router.py
```

职责：

- 定义 `ChatIframeTokenRequest`、`ChatIframeTokenResponse`。
- 暴露 `POST /chat-iframe/token`，最终路径为 `/api/chat-iframe/token`。
- 不依赖登录态。
- 读取 `Origin` 请求头。
- 调用 `exchange_external_user_iframe_token()`。
- 接口自身不做登录依赖，安全边界由开关、白名单、CORS 和限流承担。

注册到：

```text
backend/server/routers/__init__.py
```

### 7.4 自助登录限流

修改：

```text
backend/server/main.py
```

实现要点：

- 复用当前 `LoginRateLimitMiddleware` 的 IP 滑窗逻辑。
- 将 `("/api/chat-iframe/token", "POST")` 加入限流路径。
- 默认限流阈值读取 `CHAT_IFRAME_TOKEN_RATE_LIMIT_PER_MINUTE`，默认 60。
- 原 `/api/auth/token` 的限流语义保持不变。
- 不新增 Redis 限流；第一版用进程内限流即可，后续确有集群强一致需求再扩展。

### 7.5 conversation_scope_key 过滤

修改：

```text
backend/server/routers/chat_router.py
backend/package/yuxi/services/conversation_service.py
backend/package/yuxi/repositories/conversation_repository.py
```

repository 增加可选参数：

```python
async def list_conversations(
    self,
    uid: str | None = None,
    agent_id: str | None = None,
    status: str = "active",
    limit: int | None = None,
    offset: int = 0,
    conversation_scope_key: str | None = None,
) -> list[Conversation]:
    ...
```

使用 `Conversation.extra_metadata["conversation_scope_key"].as_string()` 做 JSON 字段过滤。scope 条件必须加入 repository 的 `base_conditions`，保证置顶会话和普通会话都按同一 scope 过滤；分页和置顶逻辑保持现有语义。

## 8. chat-iframe 实施方案

### 8.1 初始化参数

修改：

```text
chat-iframe/src/types.ts
chat-iframe/src/stores/iframe-context.ts
chat-iframe/public/docmind-chat-iframe-parent.js
```

新增配置字段：

```ts
apiBaseUrl?: string
source_system: string
function_id: string
business_id: string
external_user_id: string
external_user_name: string
tokenExchangeUrl?: string
```

对外初始化不支持：

```ts
apikey?: string
token?: string
```

父脚本发送给 iframe 的内部 `INIT_CONFIG` 仍包含换票成功后的 `token`，但这个 token 只能由模式 A 或模式 B 获取，不能由业务页面直接传入。

iframe 内部配置额外接收父脚本生成字段：

```ts
token?: string
conversationScopeKey?: string
authError?: string
```

`apiBaseUrl` 仍然需要，作用有两个：

- 下发给 iframe 内部，用于正常聊天、会话列表、消息流等 docMind API 请求。
- 当未传 `tokenExchangeUrl` 时，父页面脚本用它调用 `${apiBaseUrl}/api/chat-iframe/token`。

因此独立域名部署时应传 docMind API 地址，例如 `http://docmind.example.local`。如果外部系统已经把 docMind API 反代到当前业务页面同源的 `/api`，可以不传，沿用现有相对路径规则。

父页面初始化示例：

```js
DocMindChatIframe.init({
  apiBaseUrl: 'http://docmind.example.local',
  agentId: 'default-chatbot',
  source_system: 'oa',
  function_id: 'contractApproval',
  business_id: 'contract-20260706-001',
  external_user_id: '10086',
  external_user_name: '张三'
})
```

如需可信换票，增加：

```js
tokenExchangeUrl: '/api/docmind-chat-token'
```

### 8.2 父页面脚本 token 获取逻辑

`docmind-chat-iframe-parent.js` 初始化时：

1. 校验 `source_system/function_id/business_id/external_user_id/external_user_name` 必传。
2. 生成 `conversationScopeKey = ${source_system}:${function_id}:${business_id}`。
3. 如果传入 `tokenExchangeUrl`，调用外部系统后端换 token。
4. 如果未传 `tokenExchangeUrl`，调用 `${apiBaseUrl}/api/chat-iframe/token` 自助登录。
5. 把换回来的普通用户 `token`、`conversationScopeKey` 放入 `INIT_CONFIG`。

异步时序：

- `IFRAME_READY` 到来后再执行换票。
- 父脚本内部缓存一次 `tokenPromise`，避免 iframe 重发 `IFRAME_READY` 时重复换票。
- token 成功后再发送可用的 `INIT_CONFIG`。
- token 失败时发送带 `authError` 的 `INIT_CONFIG`，iframe 展示错误态；不要让 iframe 用空 token 自动调用聊天接口。
- `tokenExchangeUrl` 使用外部系统登录态时，fetch 应带上 `credentials: 'include'`。
- 模式 B 直接调用 docMind 时，fetch 只发送 JSON 请求体，不发送 Authorization。

模式 B 请求体：

```json
{
  "source_system": "oa",
  "external_user_id": "10086",
  "external_user_name": "张三"
}
```

说明：

- 父页面不再显式传入 docMind token。
- `function_id`、`business_id` 留在父页面脚本中生成 `conversationScopeKey`，不发送给 `/api/chat-iframe/token`。
- 当 `tokenExchangeUrl` 未传且 `apiBaseUrl` 也未传时，父脚本会请求当前业务页面同源的 `/api/chat-iframe/token`；这只适用于业务系统已反代 docMind API 的部署。

### 8.3 会话列表和创建会话带 scope

修改：

```text
chat-iframe/src/apis/chat.ts
chat-iframe/src/stores/chat.ts
chat-iframe/src/App.vue
```

`listConversations` 增加参数：

```ts
export async function listConversations(
  token?: string,
  agentId?: string,
  conversationScopeKey?: string
): Promise<ChatThread[]>
```

请求参数：

```text
limit=50
offset=0
agent_id=...
conversation_scope_key=...
```

`createConversation` 增加参数：

```ts
type CreateConversationOptions = RequestOptions & {
  agentId?: string
  title?: string
  conversationScopeKey?: string
}
```

创建 metadata：

```ts
metadata: {
  source: 'chat-iframe',
  ...(options.conversationScopeKey
    ? { conversation_scope_key: options.conversationScopeKey }
    : {})
}
```

`bootstrap`、`refreshThreads`、`newConversation` 从 `context.config.conversationScopeKey` 透传。scope 为空时保持现有行为。

## 9. 测试方案

### 9.1 后端服务测试

新增：

```text
backend/test/unit/services/test_external_user_service.py
```

覆盖：

- 模式 A 创建 `uid=ext_oa_10086` 普通用户。
- 模式 A 返回 `source_system`，token 对应 `ext_{source_system}_{external_user_id}` 用户。
- 已存在 `ext_oa_10086` 时复用用户，不覆盖 `username`、`role`、`department_id`。
- `source_system` 包含 `_` 时返回校验错误。
- `external_user_name` 为空时返回校验错误。
- 生成后的 `uid` 超过 64 时返回校验错误。
- 创建用户默认 `role=user`、`department_id=1`。
- 默认部门 `id=1` 不存在时返回清晰错误。
- 相同 `uid` 用户已软删除时返回 409，不自动恢复。
- 模式 B 未开启 `CHAT_IFRAME_AUTO_LOGIN_ENABLED` 时拒绝。
- 模式 B source 白名单为空时不校验 source；非空且不命中时拒绝。
- 模式 B origin 白名单为空时不校验 origin；非空且不命中时拒绝。
- 模式 B 不接收 `function_id/business_id/conversation_scope_key`。
- 模式 B 返回 `source_system`，token 对应 `ext_{source_system}_{external_user_id}` 用户。

### 9.2 后端接口测试

新增：

```text
backend/test/integration/api/test_external_user_backend_token_api.py
backend/test/integration/api/test_external_user_iframe_token_api.py
```

覆盖：

- superadmin API Key 可调用 `/api/external-users/token`。
- 普通用户或普通管理员调用 `/api/external-users/token` 返回 403。
- `/api/chat-iframe/token` 在开关开启、source/origin 命中时返回普通用户 token。
- `/api/chat-iframe/token` 不需要 Authorization。
- `/api/chat-iframe/token` 未开启时返回 403。
- `/api/chat-iframe/token` 触发限流时返回 429。
- 返回 token 可访问 `/api/auth/me`，且 `uid` 为外部映射 uid。

### 9.3 conversation scope 测试

新增或扩展：

```text
backend/test/unit/services/test_conversation_scope.py
```

覆盖：

- 不传 `conversation_scope_key` 时列表保持当前行为。
- 传 `conversation_scope_key` 时只返回 metadata 匹配的会话。
- 不同 `uid` 下相同 scope 不串数据。
- 置顶会话在 scope 过滤后仍保持置顶优先。

### 9.4 chat-iframe 测试

扩展：

```text
chat-iframe/test/parent-script.test.js
```

覆盖：

- 初始化时会把 `conversationScopeKey` 放入 `INIT_CONFIG`。
- 初始化时校验 `source_system/function_id/business_id/external_user_id/external_user_name` 必传。
- 传入 `tokenExchangeUrl` 时调用外部系统后端。
- 不传 `tokenExchangeUrl` 时调用 `${apiBaseUrl}/api/chat-iframe/token`。
- 不支持父页面显式传入 `token`。
- 不支持、不读取、不下发 `apikey`。

如现有测试结构允许，新增或扩展 API 层测试：

```text
chat-iframe/test/chat-api.test.js
```

覆盖：

- `listConversations(token, agentId, scope)` 会拼接 `conversation_scope_key` 查询参数。
- `createConversation({ conversationScopeKey })` 会写入 metadata。
- scope 为空时请求保持旧行为。

## 10. 文档更新

实现时同步更新：

```text
.env.template
docker-compose.yml
docker-compose.prod.yml
scripts/init.sh
scripts/init.ps1
chat-iframe/README.md
docs/develop-guides/changelog.md
```

README 需要补充：

- `DocMindChatIframe.init()` 必须传 `source_system/function_id/business_id/external_user_id/external_user_name`。
- `tokenExchangeUrl` 存在时走外部系统后端可信换票。
- `tokenExchangeUrl` 不存在时走 docMind 内网低信任自助登录。
- 禁止在前端传入 superadmin token 或 superadmin API Key。
- 模式 B 必须开启 `CHAT_IFRAME_AUTO_LOGIN_ENABLED`；`CHAT_IFRAME_ALLOWED_SOURCES`、`CHAT_IFRAME_ALLOWED_ORIGINS` 不填时不做对应白名单校验。
- 新增环境变量需要同步 `.env.template`、`docker-compose*.yml` 和 `scripts/init.*`，避免初始化生成的 `.env` 与容器环境脱节。
- `conversationScopeKey={source_system}:{function_id}:{business_id}` 是会话列表分组，不是权限边界。

changelog 记录：

- 新增 chat-iframe 外部用户双模式登录能力。
- 新增 chat-iframe 业务会话 scope 隔离。
- 新增内网低信任自助登录开关与白名单配置。

## 11. 验收标准

- 有 `tokenExchangeUrl` 时，父页面脚本调用外部系统后端换取 docMind 普通用户 token。
- 无 `tokenExchangeUrl` 时，父页面脚本调用 `/api/chat-iframe/token` 换取 docMind 普通用户 token。
- 不存在任何前端 `apikey` 配置字段。
- `DocMindChatIframe.init()` 不需要也不允许传入 superadmin token 或 superadmin API Key。
- `DocMindChatIframe.init()` 不再显式传入 docMind 用户 token，token 必须由模式 A 或模式 B 获取。
- 首次登录会自动创建 `ext_{source_system}_{external_user_id}` 普通用户，默认部门为 `id=1`。
- 已存在映射用户时不覆盖后台调整过的用户名、部门、角色。
- chat-iframe 在同一用户、同一 agent 下，不同 `conversationScopeKey` 展示不同会话列表。
- 不传 `conversationScopeKey` 的主站和普通 iframe 使用方式保持原会话列表行为。
- 本次改动不涉及页面上下文、附件摘要、文件上下文注入。

## 12. 实施 checklist

- [ ] 新增 `backend/package/yuxi/services/external_user_service.py`。
- [ ] 新增 `backend/server/routers/external_user_backend_token_router.py` 并注册到总 router。
- [ ] 新增 `backend/server/routers/external_user_iframe_token_router.py` 并注册到总 router。
- [ ] 增加 `CHAT_IFRAME_AUTO_LOGIN_ENABLED`、`CHAT_IFRAME_ALLOWED_SOURCES`、`CHAT_IFRAME_ALLOWED_ORIGINS`、`CHAT_IFRAME_TOKEN_RATE_LIMIT_PER_MINUTE` 配置读取。
- [ ] 同步 `.env.template`、`docker-compose.yml`、`docker-compose.prod.yml`、`scripts/init.sh`、`scripts/init.ps1`。
- [ ] 将 `/api/chat-iframe/token` 加入轻量限流。
- [ ] 为 conversation 列表增加可选 `conversation_scope_key` 过滤。
- [ ] chat-iframe 配置增加外部身份字段并内部生成 `conversationScopeKey`。
- [ ] parent script 增加 `tokenExchangeUrl` 分支和 `/api/chat-iframe/token` 分支。
- [ ] parent script 增加异步换票失败态，不把空 token 当作可用配置下发。
- [ ] chat-iframe 会话列表和创建会话透传 scope。
- [ ] 补充 external user service 单元测试。
- [ ] 补充 external user backend/iframe token API 集成测试。
- [ ] 补充 conversation scope 过滤测试。
- [ ] 补充 chat-iframe scope 和 token 获取分支测试。
- [ ] 更新 `chat-iframe/README.md` 和 changelog。
- [ ] 运行相关后端和 chat-iframe 测试。
