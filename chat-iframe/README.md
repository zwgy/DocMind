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

```bash
corepack pnpm typecheck
```

执行 TypeScript 类型检查，不输出构建产物。

```bash
corepack pnpm test
```

运行父页面脚本的附件 DOM 识别测试。

```bash
corepack pnpm lint
```

运行 ESLint。

```bash
corepack pnpm build
```

先执行 `vue-tsc --noEmit` 类型检查，再执行 `vite build` 生成生产产物。

## 5. 项目结构

```text
chat-iframe/
  Dockerfile
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
8. “问网页/问文件”开启时，iframe 会把页面摘要、选中文档、匹配结果和抽取摘要拼入 query，同时写入 `meta`。这样做是因为当前后端聊天链路一定消费 `query`，而 `meta` 更适合作为追踪和后续增强字段。

## 7. 父页面集成方式

### 7.1 显式传入页面内容和附件

```html
<script src="https://docmind.example.com/chat-iframe/docmind-chat-iframe-parent.js"></script>
<script>
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    user: 'user-001',
    token: 'docmind-token',
    agentId: 'default-chatbot',
    targetOrigin: 'https://docmind.example.com',
    originAllowlist: ['https://production.example.com'],
    initialState: 'minimized'
  })

  chat.setPageContent({
    title: document.title,
    url: location.href,
    html: document.documentElement.outerHTML
  })

  chat.setFiles([
    {
      id: '202606100417',
      name: '来文文件名.docx',
      sourceUrl: 'http://example/default.ashx?202606100417',
      sourceKey: '202606100417',
      sizeText: '200.16KB',
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
    user: 'user-001',
    token: 'docmind-token',
    targetOrigin: 'https://docmind.example.com'
  })
</script>
```

父脚本会优先扫描生产系统常见结构：

```html
<div class="items">
  <div class="item" id="202606100417_BOX" attachment="202606100417">
    <a href="###" onclick="YZSoft.File.download('http://example/default.ashx?202606100417')">
      "来文.pdf"
      <span class="size">-200.16KB</span>
    </a>
  </div>
</div>
```

识别规则：

1. 优先扫描 `.items .item[attachment] a`，避免把页面普通导航误判为附件。
2. 降级扫描页面里的 `a`。
3. 从 `.size` 或文本中提取大小，兼容 `-200.16KB`。
4. 从 `YZSoft.File.download('...')` 中提取下载地址，因为旧系统 `href` 常是 `###`。
5. 按 `attachment`、`id` 去掉 `_BOX`、URL query、路径片段的顺序提取 `sourceKey`。
6. 只保留 `doc/docx/pdf/xls/xlsx/ppt/pptx/txt/md/csv`。

## 8. 父页面参数

```js
const chat = new DocMindChatIframe({
  iframeSrc: 'https://docmind.example.com/chat-iframe/',
  user: 'user-001',
  token: 'docmind-token',
  agentId: 'default-chatbot',
  targetOrigin: 'https://docmind.example.com',
  originAllowlist: ['https://production.example.com'],
  position: 'bottom-right',
  width: 460,
  height: 680,
  offsetX: 24,
  offsetY: 24,
  initialState: 'minimized',
  includePageContent: true,
  includeFiles: true,
  selectedFileIds: ['202606100417'],
  buttonHtml: null
})
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `iframeSrc` | `/` | iframe 页面地址 |
| `user` | `null` | 生产系统当前用户标识 |
| `token` | `null` | docMind 认证 token；iframe 调后端时作为 Bearer Token |
| `agentId` | `null` | 可选，聊天使用的智能体 ID；为空时 iframe 使用 `default-chatbot` |
| `targetOrigin` | `*` | 父页面发消息给 iframe 的目标 origin；生产环境建议写死 |
| `originAllowlist` | `[]` | 下发给 iframe 的父页面来源白名单，用于 iframe 校验生产系统来源 |
| `position` | `bottom-right` | 悬浮入口位置 |
| `width` / `height` | `460` / `680` | 普通窗口尺寸 |
| `offsetX` / `offsetY` | `24` / `24` | 距离视口边缘的偏移 |
| `initialState` | `minimized` | `minimized`、`normal`、`maximized`、`closed` |
| `includePageContent` | `true` | 是否自动发送页面内容 |
| `includeFiles` | `true` | 是否自动扫描页面附件 |
| `selectedFileIds` | `[]` | 默认选中的附件 ID |
| `buttonHtml` | `null` | 自定义悬浮按钮 HTML；为空时使用内联 SVG |

## 9. 父页面函数

| 方法 | 说明 |
| --- | --- |
| `init()` | 挂载 iframe；`autoInit` 默认为 true，一般无需手动调用 |
| `open()` | 打开普通窗口 |
| `close()` | 关闭窗口 |
| `minimize()` | 最小化为悬浮按钮 |
| `maximize()` | 最大化为全屏 |
| `restore()` | 恢复普通窗口 |
| `destroy()` | 移除 DOM 和消息监听，SPA 切页时应调用 |
| `setUser(user)` | 更新用户并重发配置 |
| `setPageContent(content)` | 显式设置页面内容，支持字符串或对象 |
| `setFiles(files)` | 覆盖附件列表 |
| `addFile(file)` | 追加附件 |
| `on(event, callback)` | 监听 `stateChange`、`conversationCreated`、`messageSent` 等事件 |

## 10. 通信协议

父页面发送给 iframe：

| 消息 | 载荷 | 用途 |
| --- | --- | --- |
| `INIT_CONFIG` | `{ user, token, agentId, includePageContent, includeFiles, selectedFileIds, originAllowlist }` | 初始化配置 |
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

## 11. 悬浮图标、SVG 和字体

悬浮入口默认使用内联 SVG：

```html
<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor">
  <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>
  <path d="M8 9h8M8 13h5"/>
</svg>
```

它显示为圆形按钮里的对话气泡。选择内联 SVG，是为了让生产系统只部署一个父页面脚本即可，不再额外处理图片路径、静态目录映射和跨域缓存。

当前不引入 font 文件。系统字体已经满足第一版工具界面，单独字体会增加部署文件和缓存策略。后续如果有明确品牌视觉要求，再补字体资源。

## 12. 部署方式

`chat-iframe` 需要部署到生产系统能访问的位置，推荐仍由 docMind 侧独立发布，而不是拷贝进生产系统代码仓库。生产系统只要引用脚本和 iframe 地址即可，这就是“无感嵌入”。

推荐访问形态：

```text
https://docmind.example.com/chat-iframe/
https://docmind.example.com/chat-iframe/docmind-chat-iframe-parent.js
```

### 12.1 Docker 部署

构建镜像：

```bash
docker build -t docmind-chat-iframe ./chat-iframe
```

运行：

```bash
docker run -d --name docmind-chat-iframe -p 10002:80 docmind-chat-iframe
```

访问：

```text
http://localhost:10002/chat-iframe/
http://localhost:10002/chat-iframe/docmind-chat-iframe-parent.js
```

### 12.2 Nginx 代理

`nginx.conf` 默认把 `/api/` 代理到 `http://api:5050/api/`。如果独立部署在生产系统网络中，需要改成真实 docMind 后端地址：

```nginx
location /api/ {
    proxy_pass http://docmind-api.example.com/api/;
}
```

保留 `proxy_buffering off` 是为了让 `/api/agent/runs/{runId}/events` 的 SSE 流式聊天不要被 Nginx 缓冲。

#### 角色与拓扑

Nginx 在 chat-iframe 里同时充当**静态服务器**（托管 Vue 应用）和**反向代理**（把 `/api/` 转发到 docMind 后端）。三者关系如下：

```text
┌─────────────────────┐       ┌──────────────────────┐       ┌──────────────────────┐
│   生产系统（OA/办公） │       │      chat-iframe     │       │   docMind 后端 api   │
│                     │       │                      │       │                      │
│  浏览器：用户访问     │  ①    │  Nginx（容器内 80）    │  ③    │  FastAPI（5050）      │
│  引入一行 <script>  │───────▶│   ├─ /chat-iframe/   │───────▶│  /api/incoming-docs/ │
│  → 出现悬浮按钮      │       │   │   Vue SPA 静态   │       │      extractions/    │
│                     │  ②    │   └─ /api/ 反向代理   │  HTTP │      query           │
│  postMessage ───────┼───────▶│       到 docmind 后端 │       │                      │
│                     │       │                      │       │                      │
└─────────────────────┘       └──────────────────────┘       └──────────────────────┘
```

| 实体 | 角色 | 端口 |
| --- | --- | --- |
| 生产系统（OA） | 被嵌入方，提供用户上下文和附件 | — |
| chat-iframe | 嵌入桥梁，自带 Nginx 静态 + 反代 | 80 |
| docMind 后端 api | 真正处理来文抽取查询 | 5050 |

#### 数据流

从用户点开悬浮按钮到看到结果，完整链路：

1. 父脚本从父页面 DOM 采集附件（`.items .item[attachment] a`），得到 `[{ id, name, sourceUrl, sourceKey, sizeText }]`。
2. 父脚本通过 `postMessage` 把 `INIT_CONFIG` / `PAGE_CONTENT` / `PAGE_FILES_UPDATED` 推给 iframe。
3. iframe 内的 `useIframeBridge` 接收消息，存入 Pinia store，默认选中第一个附件。
4. iframe 调用后端（相对路径）：
   ```js
   fetch('/api/incoming-documents/extractions/query', {
     method: 'POST',
     headers: { Authorization: 'Bearer <token>' },
     body: { files: [...] }
   })
   ```
5. Nginx 匹配 `location /api/`，把请求 `proxy_pass` 到 docMind 后端，并补上 `X-Real-IP` / `X-Forwarded-For` 头。
6. docMind 后端处理 `/api/incoming-documents/extractions/query`，查询数据库、匹配来文，返回 `{ status, extraction }`。
7. 结果原路返回，iframe 渲染面板，展示匹配状态和结构化抽取结果。

生产系统浏览器视角看到的请求域名始终是 chat-iframe 自己，跨域 / CORS 都被 Nginx 在同源内消化掉了。

### 12.3 手动静态部署

```bash
corepack pnpm install
corepack pnpm build
```

将以下文件发布到同一个静态服务目录：

```text
dist/index.html
dist/assets/*
public/docmind-chat-iframe-parent.js
```

如果发布路径是 `/chat-iframe/`，需要保证：

```text
/chat-iframe/index.html
/chat-iframe/assets/*
/chat-iframe/docmind-chat-iframe-parent.js
```

## 13. 安全注意

- 生产环境必须设置 `targetOrigin`，不要长期使用默认 `*`。
- 生产环境建议设置 `originAllowlist`，限制 iframe 接收的父页面消息来源。
- `token` 会进入 iframe 并用于后端请求，应使用短期有效 token 或 docMind 登录 token。
- 默认页面内容是 `document.documentElement.outerHTML`，如页面含敏感信息，应使用 `setPageContent()` 传脱敏文本，或关闭 `includePageContent`。
- 前端只负责携带上下文和展示结果，后端仍是最终权限边界。

## 14. 测试覆盖

```bash
corepack pnpm test
```

当前覆盖两类最容易出错的边界：

父页面脚本的生产系统附件 DOM 识别：

- `.items .item[attachment] a`
- `YZSoft.File.download('...')`
- `_BOX` 后缀
- `-200.16KB` 无空格大小
- 文件名外层引号清理
- 多附件默认选中第一项

聊天链路的最小契约：

- 创建默认智能体会话时携带 Bearer Token
- “问网页/问文件”开启时把上下文拼入 query
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
