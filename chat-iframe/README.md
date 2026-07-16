# docMind Chat Iframe 集成与部署文档

## 1. 项目定位

`chat-iframe` 是 docMind AI 文档智能助手的独立嵌入前端，用来替代原 `ai-chat-iframe` 项目在生产系统中的嵌入能力。

它由两部分组成：

1. **父页面集成脚本**：`public/docmind-chat-iframe-parent.js`
   - 由生产系统页面直接通过 `<script>` 引入。
   - 负责创建 iframe、悬浮入口、窗口控制、页面内容采集、附件采集和 `postMessage` 通信。
   - 保持原生 JavaScript，原因是生产系统不需要编译、不需要安装依赖，直接加载即可。

2. **iframe 内部应用**：`src/`
   - Vue 3 + TypeScript + Vite 应用。
   - 接收父页面传入的页面内容和附件列表。
   - 调用 docMind 后端 `/api/incoming-documents/extractions/query`、`/api/chat/*`、`/api/agent/runs/*`。
   - 展示来文匹配状态、结构化抽取结果，并提供第一版聊天体验。

第一版已接入聊天会话、流式回答、模型选择、输入框附件、问网页和问文件开关。当前已补齐 web 聊天核心体验中的 Markdown/代码/公式渲染、推理过程、工具调用摘要、停止生成、重试、复制、反馈、图片上传、附件预览以及会话重命名/删除/置顶。复杂能力如状态面板、文件工作区、人审、子智能体详情和完整 @ 提及仍保留在主站，不在 iframe 轻量嵌入版中全量复刻。

## 2. 技术栈

| 类别 | 技术 |
| --- | --- |
| 框架 | Vue 3 Composition API |
| 语言 | TypeScript，父页面集成脚本除外 |
| 构建 | Vite |
| 状态管理 | Pinia |
| 图标 | lucide-vue-next，父页面悬浮入口使用内联 SVG |
| Markdown | markdown-it、highlight.js core、KaTeX |
| 测试 | Node 内置 `node:test` |
| 部署 | Docker + Nginx，支持 `/api` 反向代理 |
| 包管理 | pnpm，通过 Corepack 固定版本 |

## 3. 为什么 src 使用 TypeScript，父脚本仍使用 JavaScript

`src` 是标准 Vue 项目，使用 TypeScript 更适合维护接口契约、消息类型、附件对象和抽取结果结构。

`public/docmind-chat-iframe-parent.js` 保持 JavaScript，是因为它是生产系统的无感嵌入边界：生产系统只需要引入一个静态脚本，不应该被要求接入构建链路或模块系统。

## 4. Corepack、pnpm 和命令说明

`Corepack` 是 Node 自带的包管理器代理。项目的 `package.json` 中声明了：

```json
{
  "packageManager": "pnpm@10.11.0"
}
```

执行 `corepack pnpm ...` 时，Corepack 会自动使用这个版本的 pnpm。这样不同开发机和构建机不会因为全局 pnpm 版本不同导致锁文件漂移。

为什么不用 npm：

- 仓库现有前端项目使用 pnpm。
- `chat-iframe` 已生成 `pnpm-lock.yaml`，继续使用 pnpm 能保持依赖解析一致。
- 使用 npm 会额外生成 `package-lock.json`，反而引入第二套锁文件。

常用命令：

```bash
corepack pnpm install
```

安装依赖。首次开发、切换分支或 `package.json` 变更后执行。

```bash
corepack pnpm dev
```

启动 Vite 开发服务。开发时 `/api` 会按 `vite.config.ts` 代理到 `VITE_API_URL`，未设置时使用 `http://api:5050`。
宿主机直连本机 docMind 后端时应显式指定：

```bash
VITE_API_URL=http://localhost:5050 corepack pnpm dev --host 0.0.0.0 --port 5174
```

本地模拟外部系统嵌入时访问：

```text
http://localhost:5174/chat-iframe/example.html
```

`example.html` 会加载 `docmind-chat-iframe-parent.js`，模拟生产系统附件 DOM，并挂载 `public/关于做好2026年度供电6C系统评定工作的通知/` 下的真实来文附件作为默认附件。调试页填写 `source_system/function_id/business_id/external_user_id/external_user_name` 后实例化 `DocMindChatIframe`，父脚本会按 `tokenExchangeUrl` 或 `/api/chat-iframe/token` 自动换取 DocMind token。直接打开 `/chat-iframe/` 时没有父页面配置和业务上下文，调用受保护接口会返回“请登录后再访问”，模型列表也不会加载。

```bash
corepack pnpm typecheck
```

执行 TypeScript 类型检查，不输出构建产物。

```bash
corepack pnpm test
```

运行 chat-iframe 全量回归测试。

```bash
corepack pnpm lint
```

运行 ESLint。

### P0 生产可靠性门禁

```bash
corepack pnpm test:reliability
```

该命令只运行 P0-1 至 P0-8 的回归测试，再执行类型检查和 lint。真实浏览器 E2E 必须在内网部署机执行：需要该机器的部署 URL、可用的测试业务身份，以及已安装的浏览器自动化运行器；不要在无 URL 或无测试身份的开发机上把它伪装成已通过。

### P1 核心体验门禁

```bash
corepack pnpm test:p1
```

该命令运行全部 chat-iframe 回归测试、类型检查、lint 和生产构建，覆盖模型、会话分页、来源、上传限制、消息操作和 Markdown。

内网部署机的浏览器验收不由该命令伪造：使用真实父页面、独立测试业务身份和部署 URL，依次验证 iframe 握手、普通窗/最大化、选择附件后的一次真实问答、复制与图片预览，以及代码/公式/SVG 消息不溢出。

```bash
corepack pnpm build
```

先执行 `vue-tsc --noEmit` 类型检查，再执行 `vite build` 生成生产产物。

## 5. 项目结构

```text
docker/
  chat-iframe.Dockerfile
chat-iframe/
  DEPLOYMENT.md
  nginx.conf
  package.json
  pnpm-lock.yaml
  tsconfig.json
  vite.config.ts
  index.html
  public/
    docmind-chat-iframe-parent.js
  src/
    main.ts
    App.vue
    types.ts
    apis/
      chat.ts
      incoming-documents.ts
      models.ts
    components/
      ChatInput.vue
      ChatMessages.vue
      ChatSidebar.vue
      IncomingDocumentPanel.vue
      MarkdownPreview.vue
      MessageRefs.vue
      PageFileSelector.vue
      ToolCallsPanel.vue
    composables/
      useIframeBridge.ts
    stores/
      chat.ts
      iframe-context.ts
    utils/
      chat-message.ts
      markdown.ts
    assets/css/
  test/
    chat-api.test.js
    chat-message.test.js
    incoming-documents-api.test.js
    markdown-renderer.test.js
    parent-script.test.js
```

## 6. 架构与数据流

```text
生产系统页面
  -> 引入 docmind-chat-iframe-parent.js
  -> 创建 DocMindChatIframe
  -> 父脚本挂载 iframe 和悬浮按钮
  -> 父脚本采集页面内容、附件列表
  -> postMessage 发送给 iframe

chat-iframe
  -> useIframeBridge 接收消息
  -> iframe-context 保存配置、页面内容、附件列表
  -> 默认选中第一个附件
  -> incoming-documents.ts 调用 docMind 后端
  -> IncomingDocumentPanel 展示结果
  -> chat.ts 创建/选择会话并创建 AgentRun
  -> SSE 读取 /api/agent/runs/{runId}/events
  -> ChatMessages 流式展示回答

docMind backend
  -> /api/incoming-documents/extractions/query
  -> /api/chat/thread(s)
  -> /api/agent/runs/{runId}/events
  -> 返回匹配状态、结构化抽取结果和聊天流式事件
```

页面打开后的流程：

1. iframe 加载完成，发送 `IFRAME_READY`。
2. 父脚本发送 `INIT_CONFIG`、`PAGE_CONTENT`、`PAGE_FILES_UPDATED`。
3. iframe 保存上下文。
4. 多附件默认选中第一个，因为第一版要求打开助手后无需点击即可展示匹配状态。
5. iframe 调用 `/api/incoming-documents/extractions/query`。
6. 页面展示 `matched/multiple/pending_sync/not_found` 和 `ready/running/not_found/failed`。
7. 用户提问时，iframe 创建或复用 `/api/chat/thread` 会话，调用 `/api/agent/runs` 创建运行任务，再读取 `/api/agent/runs/{runId}/events` 流式事件。
8. “问网页/问文件”开启时，iframe 会把页面内容、选中附件、匹配结果和抽取摘要写入 `meta.iframe_context`，`query` 只保留用户原始问题；后端在运行任务时把该上下文渲染进系统提示。

## 7. 父页面集成方式

### 7.1 页面内容覆盖与附件传入

```html
<script src="https://docmind.example.com/chat-iframe/docmind-chat-iframe-parent.js"></script>
<script>
  const chat = new DocMindChatIframe({
    source_system: 'oa',
    function_id: 'contractApproval',
    business_id: 'contract-20260706-001',
    external_user_id: '1001',
    external_user_name: '张三',
    agentId: 'default-chatbot',
    initialState: 'minimized'
  })

  // 未调用时，父脚本会自动采集当前页面 HTML；显式传入时以这里为准。
  chat.setPageContent({
    title: document.title,
    url: location.href,
    html: document.documentElement.outerHTML
  })

  chat.setFiles([
    {
      id: '202606100417',
      name: '来文文件名.docx',
      source_url: 'http://example/default.ashx?202606100417',
      source_file_id: '202606100417',
      size_text: '200.16KB',
      selected: true
    }
  ])
</script>
```

### 7.2 只嵌入脚本，自动扫描生产系统附件

```html
<script src="https://docmind.example.com/chat-iframe/docmind-chat-iframe-parent.js"></script>
<script>
  new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    apiBaseUrl: 'https://docmind.example.com',
    source_system: 'oa',
    function_id: 'contractApproval',
    business_id: 'contract-20260706-001',
    external_user_id: '1001',
    external_user_name: '张三'
  })
</script>
```

父脚本会用外部业务身份自动换取 docMind token。聊天、模型列表、附件抽取查询等接口都经过 `get_required_user`，因此换票失败时，iframe 会显示认证错误，模型选择器也会因为模型列表接口 401 而为空。
父脚本的窗口控制会在关闭和最小化时保留悬浮入口；普通窗口可通过顶部标题栏拖动，最小化/关闭后的悬浮入口也可直接拖动。从悬浮入口恢复普通窗口时，会先判断当前位置是否能完整显示小助手，若放不下则自动回到当前视口右下角。
iframe 内部会话列表采用按需左侧抽屉展示，默认不占用聊天区域；点击顶部对话列表按钮后再展开历史会话和新建聊天入口。
底部输入区的“问文件”会打开页面附件选择弹窗，显示当前页面识别到的附件名称，可多选/取消；未选择任何附件时会自动取消文件上下文，不再把附件摘要拼入提问。模型选择采用输入框右下角的模型按钮和搜索弹窗。左下角回形针按钮参考主站 `AttachmentOptionsComponent`，先弹出“添加附件 / 上传图片”小菜单；添加附件再打开拖拽上传弹窗，确认后进入当前消息的待发送附件列表。

父脚本不自动解析宿主页面 DOM。接入方需要在初始化后调用 `setFiles()` 显式传入附件列表，避免不同业务系统的页面结构差异导致误识别。文件对象至少包含 `name/source_url/source_file_id`；`source_system/source_function_id/source_doc_id` 可由初始化参数自动补齐。

## 8. 父页面参数

```js
const chat = new DocMindChatIframe({
  tokenExchangeUrl: null,
  source_system: 'oa',
  function_id: 'contractApproval',
  business_id: 'contract-20260706-001',
  external_user_id: '1001',
  external_user_name: '张三',
  agentId: 'default-chatbot',
  position: 'bottom-right',
  width: 460,
  height: 680,
  offsetX: 24,
  offsetY: 24,
  initialState: 'minimized',
  buttonHtml: null
})
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `iframeSrc` | SDK 脚本所在目录 | iframe 页面地址；仅脚本与 iframe 不在同一目录时覆盖 |
| `apiBaseUrl` | iframe 的 origin | docMind API 基础地址；仅 iframe 与 API 不同域时覆盖 |
| `tokenExchangeUrl` | `null` | 外部系统后端换票地址；为空时父脚本直接调用 `/api/chat-iframe/token` |
| `source_system` | `''` | 外部系统 ID，只允许字母和数字 |
| `function_id` | `''` | 外部系统功能 ID，用于生成业务会话 scope |
| `business_id` | `''` | 当前业务页面 ID，用于生成业务会话 scope |
| `external_user_id` | `''` | 外部系统用户 ID，只允许字母和数字 |
| `external_user_name` | `''` | 外部系统用户显示名，用于 docMind 后台识别 |
| `agentId` | `null` | 可选，聊天使用的智能体 ID；为空时 iframe 使用 `default-chatbot` |
| `position` | `bottom-right` | 悬浮入口位置 |
| `width` / `height` | `460` / `680` | 普通窗口期望尺寸；实际显示会受当前视口约束，避免嵌入页面中显示不全 |
| `offsetX` / `offsetY` | `24` / `24` | 距离视口边缘的偏移 |
| `initialState` | `minimized` | `minimized`、`normal`、`maximized`、`closed` |
| `buttonHtml` | `null` | 自定义悬浮按钮 HTML；为空时使用内联 SVG AI 标识 |

## 9. 父页面函数

| 方法 | 说明 |
| --- | --- |
| `init()` | 幂等挂载方法；构造时已自动调用，一般无需手动调用 |
| `open()` | 打开普通窗口 |
| `close()` | 关闭窗口 |
| `minimize()` | 最小化为悬浮按钮 |
| `maximize()` | 最大化为全屏 |
| `restore()` | 恢复普通窗口 |
| `destroy()` | 移除 DOM 和消息监听，SPA 切页时应调用 |
| `setPageContent(content)` | 显式设置页面内容，支持字符串或对象 |
| `setFiles(files)` | 覆盖附件列表 |
| `addFile(file)` | 追加附件 |
| `on(event, callback)` | 监听 `stateChange`、`conversationCreated`、`messageSent` 等事件 |

## 10. 通信协议

父页面发送给 iframe：

| 消息 | 载荷 | 用途 |
| --- | --- | --- |
| `INIT_CONFIG` | `{ token, apiBaseUrl, agentId, conversationScopeKey }` | 初始化配置；`token` 由父脚本自动换票后下发 |
| `PAGE_CONTENT` | `{ title?, url?, html?, text? }` | 页面内容 |
| `PAGE_FILES_UPDATED` | `IncomingPageFile[]` | 页面附件列表 |
| `FILE_LIST` | `IncomingPageFile[]` | 兼容旧消息名 |
| `WINDOW_STATE` | `{ state }` | 父窗口状态 |

iframe 发送给父页面：

| 消息 | 用途 |
| --- | --- |
| `IFRAME_READY` | iframe 已准备接收上下文 |
| `REQUEST_PAGE_CONTENT` | 请求父页面补发页面内容 |
| `REQUEST_FILE_LIST` | 请求父页面补发附件列表 |
| `MINIMIZE` / `MAXIMIZE` / `RESTORE` / `CLOSE` | 窗口控制 |
| `CONVERSATION_CREATED` | iframe 创建新会话后的可选通知 |
| `MESSAGE_SENT` | iframe 发送消息后的可选通知 |

## 11. 悬浮图标和字体

悬浮入口默认使用内联 SVG：

```html
<svg viewBox="0 0 1024 1024" aria-hidden="true" fill="currentColor">...</svg>
```

它显示为单层渐变按钮里的 AI 标识。选择内联 SVG，是为了让生产系统只部署一个父页面脚本即可，不再额外处理图片路径、静态目录映射和跨域缓存。

当前不引入 font 文件。系统字体已经满足第一版工具界面，单独字体会增加部署文件和缓存策略。后续如果有明确品牌视觉要求，再补字体资源。

## 12. 部署方式

完整命令、环境变量、访问地址和验收方式见 [DEPLOYMENT.md](./DEPLOYMENT.md)。当前支持：

1. 随 DocMind 一起部署：开发环境使用独立 Vite 端口，生产环境由 `web-prod` 统一代理 `/chat-iframe/`。
2. 单独部署 chat-iframe 镜像：静态前端独立发布，但仍通过 `/api/` 访问 DocMind 后端。

开发环境保留 `test1/`、`test2/`、`example.html` 和 `example-files/`；生产 Docker 构建会移除这些调试资源。

## 13. 安全注意

- iframe 消息目标会从 iframe 地址自动推导；来源限制由后端 `CHAT_IFRAME_ALLOWED_ORIGINS` 强制执行。
- 父脚本换取的 docMind token 会进入 iframe 并用于后端请求；生产环境优先使用 `tokenExchangeUrl`，避免把高权限凭据暴露到浏览器。
- 默认页面内容是 `document.documentElement.outerHTML`；如页面含敏感信息，应使用 `setPageContent()` 传脱敏文本覆盖自动采集结果。
- 前端只负责携带上下文和展示结果，后端仍是最终权限边界。

## 14. 测试覆盖

```bash
corepack pnpm test
```

当前覆盖两类最容易出错的边界：

父页面脚本的附件传入契约：

- 只通过 `setFiles()` 接收附件列表
- 自动补齐 `source_system/source_function_id/source_doc_id`
- 多附件默认选中第一项

聊天链路的最小契约：

- 创建默认智能体会话时携带自动换票得到的 Bearer Token
- “问网页/问文件”开启时通过 `meta.iframe_context` 传递页面和附件上下文，`query` 只保留用户原始问题
- 解析 `/api/agent/runs/{runId}/events` 的 SSE 文本增量、推理内容、工具调用和工具结果
- 发送消息时携带模型、附件元数据和图片内容
- 停止生成、会话重命名/删除/置顶、消息反馈使用主站兼容接口
- Markdown 表格、代码块、公式和危险 HTML 处理有纯函数测试覆盖

## 15. 与参考项目的主要差异

| 项目 | ai-chat-iframe | docMind chat-iframe |
| --- | --- | --- |
| iframe 内部语言 | TypeScript | TypeScript |
| 父页面脚本 | 原生 JavaScript | 原生 JavaScript |
| 第一版核心能力 | 聊天 | 来文结构化结果展示 + iframe 聊天 |
| 后端接口 | `/ai/chatFlow/*` | `/api/incoming-documents/extractions/query`、`/api/chat/*`、`/api/agent/runs/*` |
| 部署 | Docker + Nginx | Docker + Nginx |
| 附件处理 | URL 上传给聊天后端 | 匹配 docMind 来文并展示抽取结果；输入框附件复用 docMind 线程附件接口 |

## 16. 新版外部用户换票与业务会话隔离

新接入方式不再从父页面显式传入 `token`。父页面实例化 `DocMindChatIframe` 时必须传入外部业务身份，父脚本会在 iframe ready 后自动获取 DocMind token，并生成 `conversationScopeKey = {source_system}:{function_id}:{business_id}`。iframe 创建会话时把该 scope 写入会话 metadata，拉取会话列表时用同一个 scope 过滤，从而隔离同一外部用户在不同业务页面下的历史对话。

```js
new DocMindChatIframe({
  source_system: 'oa',
  function_id: 'contractApproval',
  business_id: 'contract-20260706-001',
  external_user_id: '1001',
  external_user_name: '张三',
  agentId: 'default-chatbot'
})
```

如果传入 `tokenExchangeUrl`，父脚本会向该外部系统后端地址 POST `{ source_system, external_user_id, external_user_name }`，由外部后端再调用 DocMind `/api/external-users/token` 换票。该模式适合生产环境和更强审计要求。

如果不传 `tokenExchangeUrl`，父脚本会直接 POST `${apiBaseUrl || ''}/api/chat-iframe/token`。该模式需要 DocMind 后端设置：

```env
CHAT_IFRAME_AUTO_LOGIN_ENABLED=true
CHAT_IFRAME_ALLOWED_SOURCES=oa
CHAT_IFRAME_ALLOWED_ORIGINS=https://oa.example.com
CHAT_IFRAME_TOKEN_RATE_LIMIT_PER_MINUTE=60
```

`CHAT_IFRAME_ALLOWED_SOURCES` 和 `CHAT_IFRAME_ALLOWED_ORIGINS` 留空时不校验对应维度。自动创建的外部账号 uid 为 `ext_{source_system}_{external_user_id}`，默认普通用户、默认部门 `id=1`，后续由超级管理员在 DocMind 后台调整角色或部门。

## 17. iframe_context 上下文策略

`chat-iframe` 发送聊天消息时，`query` 只保留用户原始问题，不再把“页面上下文”或“文件上下文”拼入问题正文。页面和附件上下文统一放入 `/api/agent/runs` 的 `meta.iframe_context`，由后端在每轮运行时渲染成当前线程的系统提示片段。

- “问网页”开启时，iframe 会把父页面传入的 `title/url/text/html` 放入 `iframe_context.page`。后端优先使用已解析文本；只有 HTML 时会先解析为 Markdown。短页面直接进入系统提示，长页面会写入当前线程沙箱文件，并在提示中给出 `read_file` 可读取路径。
- “问文件”开启时，iframe 会把本轮选中的全部页面附件放入 `iframe_context.files`，而不是只传第一个附件。已匹配知识库且有摘要的附件会携带摘要、`kbId/fileId`；摘要不足时，模型可按提示使用 `open_kb_document` 读取全文。
- 如果附件没有摘要但父页面提供了 `source_url`，iframe 会以 `cache: no-store` 下载附件内容，再用 multipart 调用 `POST /api/incoming-documents/ingest` 上传文件；附件地址必须同源或允许浏览器跨域读取。解析尚未完成时，本轮提示只说明文件正在准备，不要求模型猜测内容。
- 兼容过渡期仍保留 `meta.page_content`、`meta.selected_file`、`meta.extraction_result` 字段，但新的问答上下文应以 `iframe_context` 为准。
